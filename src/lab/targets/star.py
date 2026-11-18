"""Lower Lab layouts into PyLabRobot resources and compile without connecting to hardware."""

import json
from dataclasses import dataclass, field
from decimal import Decimal
from importlib.metadata import version
from typing import Any

from lab.deck import (
    Carrier,
    Channel,
    Deck,
    DeckLayout,
    DeckSite,
    HolderSite,
    Placement,
    Rail,
)
from lab.deck import (
    Container as DeckContainer,
)
from lab.deck import (
    TipRack as DeckTipRack,
)
from lab.documents import describe
from lab.equipment import CarrierModel, LabwareModel, TipRackModel
from lab.labware import LabwareKind
from lab.limits import PipettingLimits
from lab.liquid_classes import HamiltonLiquidClass, LiquidClass
from lab.model import (
    LIQUID_STEPS,
    Binding,
    Distribute,
    LiquidStep,
    ManualInstruction,
    Mix,
    MultiMix,
    RecordedProtocol,
    SetTemperature,
    TargetPlan,
    Thermocycle,
    Transfer,
    Wait,
    encode,
    lanes,
)
from lab.samples import Location
from lab.targets.liquid_handler import LiquidHandler
from lab.tips import assign_tips
from lab.units import magnitude, number, uL
from lab.validation import CompileError, step_error

_sdk_import_error: ImportError | None = None
try:
    import pylabrobot.resources as plr
    from pylabrobot.liquid_handling.liquid_classes.hamilton import (
        HamiltonLiquidClass as PLRHamiltonLiquidClass,
    )
    from pylabrobot.liquid_handling.liquid_classes.hamilton import star as star_liquid_classes
    from pylabrobot.resources import (
        Container,
        ItemizedResource,
        Resource,
        TipRack,
    )
    from pylabrobot.resources.hamilton import HamiltonSTARDeck
except ImportError as exc:
    _sdk_import_error = exc


def hamilton_liquid_class_names() -> tuple[str, ...]:
    """List built-in STAR symbols in the installed, optional PyLabRobot SDK."""
    if _sdk_import_error is not None:
        raise ImportError(
            "Install lab-compiler[star] to list Hamilton classes"
        ) from _sdk_import_error
    return tuple(
        sorted(
            name
            for name, value in vars(star_liquid_classes).items()
            if not name.startswith("_") and isinstance(value, PLRHamiltonLiquidClass)
        )
    )


@dataclass
class STAR:
    """Bind protocol resources to a supplied physical STAR or STARlet configuration.

    The caller places carriers, holders, labware, and tips on a PyLabRobot deck.
    ``labware`` maps protocol resource names to those exact physical resources.
    Compilation preserves this hierarchy. ``channels`` lists the independent pipetting
    channels in use (``channel`` alone names one); a lane set runs across them in strokes of
    up to that many lanes. Tips follow the protocol's recorded ``TipPolicy``.
    Thermal operations require an external device callback when the artifact runs.
    """

    deck: Any
    labware: dict[str, Any]
    tip_racks: tuple[Any, ...]
    min_volume: Any
    max_volume: Any
    channel: int = 0
    channels: tuple[int, ...] = ()
    well_maps: dict[str, tuple[str, ...]] = field(default_factory=dict)
    external_thermal_resources: tuple[str, ...] | None = None

    @property
    def liquid_handler(self) -> LiquidHandler:
        return LiquidHandler.STAR

    def prepare(self, protocol: RecordedProtocol) -> TargetPlan:
        if _sdk_import_error is not None:
            raise ImportError(
                "Install lab-compiler[star] to compile for Hamilton STAR"
            ) from _sdk_import_error
        if not isinstance(self.deck, HamiltonSTARDeck):
            raise CompileError("STAR requires a PyLabRobot STARDeck or STARLetDeck")
        channels = self.channels or (self.channel,)
        if any(type(c) is not int or not 0 <= c < 8 for c in channels) or len(set(channels)) != len(
            channels
        ):
            raise CompileError("STAR channels must be distinct integers from 0 to 7")
        minimum = magnitude(self.min_volume, "microliter")
        maximum = magnitude(self.max_volume, "microliter")
        if minimum > maximum:
            raise CompileError("Minimum volume exceeds maximum volume")
        if set(self.labware) != {resource.name for resource in protocol.resources}:
            raise CompileError("Labware bindings must match the protocol's resources exactly")
        if not self.well_maps.keys() <= self.labware.keys():
            raise CompileError("Well mappings must name bound resources")

        def on_deck(resource: Any) -> None:
            if self.deck.get_resource(resource.name) is not resource:
                raise CompileError(f"{resource.name!r} does not belong to the supplied deck")

        bindings = []
        refs: dict[Location, str] = {}
        for resource in protocol.resources:
            physical = self.labware[resource.name]
            on_deck(physical)
            physical_wells = self.well_maps.get(resource.name) or resource.wells
            if len(physical_wells) != len(resource.wells):
                raise CompileError(f"Wrong number of bound wells for {resource.name}")
            for well, physical_well in zip(resource.wells, physical_wells, strict=True):
                if isinstance(physical, Container) and len(resource.wells) == 1:
                    container = physical
                elif isinstance(physical, ItemizedResource):
                    try:
                        container = physical.get_item(physical_well)
                    except (IndexError, ValueError, KeyError) as exc:
                        raise CompileError(
                            f"{physical_well} is not present in {physical.name}"
                        ) from exc
                else:
                    raise CompileError("Bind one container or an indexed collection of wells")
                if not isinstance(container, Container):
                    raise CompileError(f"{container.name} cannot contain liquid")
                on_deck(container)
                location = Location(resource.name, well)
                refs[location] = f"deck.get_resource({container.name!r})"
                bindings.append(
                    Binding(
                        location,
                        container.name,
                        Decimal(str(container.max_volume)),
                        resource.dead_volume,
                    )
                )
        tips = []
        seen: set[str] = set()
        for rack in self.tip_racks:
            if not isinstance(rack, TipRack):
                raise CompileError("Tips must be supplied as PyLabRobot TipRack objects")
            on_deck(rack)
            for spot in rack.get_all_items():
                if spot.name in seen:
                    raise CompileError("A tip rack may only be supplied once")
                seen.add(spot.name)
                if spot.has_tip():
                    tips.append(spot)
        deck_data = self.deck.serialize()
        # Ensure the artifact can reconstruct its geometry without serialized Python callbacks.
        Resource.deserialize(json.loads(json.dumps(deck_data)))
        # PyLabRobot 0.2.1 indexes children by the insertion order of its "ordering" mapping.
        # Preserve the SDK's JSON verbatim even when the enclosing plan sorts its own keys.
        serialized_deck = json.dumps(deck_data, separators=(",", ":"), allow_nan=False)
        configuration = {
            "sdk_version": version("pylabrobot"),
            "deck_json": serialized_deck,
            "channel": channels[0],
            "channels": list(channels),
            "min_volume_ul": number(minimum),
            "max_volume_ul": number(maximum),
            "tip_policy": protocol.tip_policy.value,
            "tips": [spot.name for spot in tips],
            "bindings": encode(tuple(bindings)),
            "external_thermal_resources": self.external_thermal_resources,
        }
        commands = []
        native_definitions = {}
        native_initializers = []
        tip_index = 0
        assignment = assign_tips(protocol)
        held: tuple[int, int] | None = None  # (tip number, channels in use)

        def settings(step: LiquidStep, count: int) -> tuple[str, str]:
            if isinstance(step.liquid_class, LiquidClass):
                spec = step.liquid_class
                return (
                    f", flow_rates=[{number(spec.aspiration_flow_rate_ul_s)}] * {count}",
                    f", flow_rates=[{number(spec.dispense_flow_rate_ul_s)}] * {count}",
                )
            if isinstance(step.liquid_class, HamiltonLiquidClass):
                name = step.liquid_class.name
                native = getattr(star_liquid_classes, name, None)
                if not isinstance(native, PLRHamiltonLiquidClass):
                    raise CompileError(f"Unknown Hamilton STAR liquid class: {name}")
                if name not in native_definitions:
                    definition = vars(native).copy()
                    native_definitions[name] = definition
                    native_initializers.append(
                        f"    {name!r}: HamiltonLiquidClass(**{definition!r}),"
                    )
                both = f", hamilton_liquid_classes=[LIQUID_CLASSES[{name!r}]] * {count}"
                return both, both
            return "", ""

        def drop() -> None:
            nonlocal held
            if held is not None:
                used = list(channels[: held[1]])
                commands.append(
                    f"await lh.discard_tips(use_channels={used}, allow_nonzero_volume=False)"
                )
                held = None

        def hold(number_of_tip: int, count: int, volume: Decimal) -> None:
            nonlocal held, tip_index
            if held == (number_of_tip, count):
                return
            drop()
            if tip_index + count > len(tips):
                raise CompileError("Not enough fresh tips")
            spots = tips[tip_index : tip_index + count]
            tip_index += count
            for spot in spots:
                tip_max = Decimal(str(spot.get_tip().maximal_volume))
                if not minimum <= volume <= min(maximum, tip_max):
                    raise CompileError("Volume is outside the configured channel/tip range")
            names = ", ".join(f"deck.get_resource({spot.name!r})" for spot in spots)
            commands.append(
                f"await lh.pick_up_tips([{names}], use_channels={list(channels[:count])})"
            )
            held = (number_of_tip, count)

        def stroke(
            step: LiquidStep,
            number_of_tip: int,
            sources: tuple[Location, ...],
            destinations: tuple[Location, ...],
        ) -> None:
            """Aspirate from ``sources`` and dispense to ``destinations`` on parallel channels."""
            count = len(sources)
            hold(number_of_tip, count, step.volume)
            aspiration, dispense = settings(step, count)
            used = list(channels[:count])
            vols = f"vols=[{number(step.volume)}] * {count}"
            from_wells = ", ".join(refs[source] for source in sources)
            to_wells = ", ".join(refs[destination] for destination in destinations)
            commands.append(
                f"await lh.aspirate([{from_wells}], {vols}, use_channels={used}{aspiration})"
            )
            commands.append(
                f"await lh.dispense([{to_wells}], {vols}, use_channels={used}{dispense})"
            )

        for index, step in enumerate(protocol.steps):
            if isinstance(step, (Thermocycle, SetTemperature)):
                if (
                    self.external_thermal_resources is not None
                    and step.resource not in self.external_thermal_resources
                ):
                    raise step_error(
                        index,
                        step,
                        f"{step.resource} needs a declared external thermal handoff "
                        "in its DeckLayout",
                    )
                if self.well_maps.get(step.resource):
                    raise step_error(
                        index,
                        step,
                        "Thermal operations require a whole-plate binding without a well remapping",
                    )
            commands.append(f"print({describe(step)!r})")
            if isinstance(step, LIQUID_STEPS):
                number_of_tip = assignment[index]
                assert number_of_tip is not None
                try:
                    pairs = lanes(step)
                    if isinstance(step, (Mix, MultiMix)):
                        mixed = tuple(lane_source for lane_source, _ in pairs)
                        for offset in range(0, len(mixed), len(channels)):
                            chunk = mixed[offset : offset + len(channels)]
                            hold(number_of_tip, len(chunk), step.volume)
                            aspiration, dispense = settings(step, len(chunk))
                            used = list(channels[: len(chunk)])
                            vols = f"vols=[{number(step.volume)}] * {len(chunk)}"
                            at = ", ".join(refs[well] for well in chunk)
                            commands.extend(
                                (
                                    f"for _ in range({step.cycles}):",
                                    f"    await lh.aspirate([{at}], {vols}, "
                                    f"use_channels={used}{aspiration})",
                                    f"    await lh.dispense([{at}], {vols}, "
                                    f"use_channels={used}{dispense})",
                                )
                            )
                    elif isinstance(step, (Transfer, Distribute)):
                        # One channel, one tip: a distribution refills between destinations.
                        for lane_source, lane_destination in pairs:
                            assert lane_destination is not None
                            stroke(step, number_of_tip, (lane_source,), (lane_destination,))
                    else:
                        # Independent channels take up to len(channels) lanes per stroke.
                        for offset in range(0, len(pairs), len(channels)):
                            group = pairs[offset : offset + len(channels)]
                            lane_sources = tuple(lane_source for lane_source, _ in group)
                            lane_destinations = tuple(d for _, d in group if d is not None)
                            if len(set(lane_sources)) != len(lane_sources):
                                # One trough feeding several lanes: the channels aspirate in turn.
                                for lane_source, lane_destination in group:
                                    assert lane_destination is not None
                                    stroke(step, number_of_tip, (lane_source,), (lane_destination,))
                            else:
                                stroke(step, number_of_tip, lane_sources, lane_destinations)
                except CompileError as exc:
                    raise step_error(index, step, str(exc)) from exc
                if protocol.tip_policy.value == "fresh_per_step":
                    drop()
            elif isinstance(step, Wait):
                commands.append(f"await sleep({number(step.seconds)})")
            elif isinstance(step, Thermocycle):
                plate = self.labware[step.resource]
                if not isinstance(plate, ItemizedResource):
                    raise step_error(index, step, "A thermal resource must bind a whole plate")
                if any(
                    name != step.resource and (other is plate or plate in other.get_all_children())
                    for name, other in self.labware.items()
                ) or any(
                    name != step.resource and other in plate.get_all_children()
                    for name, other in self.labware.items()
                ):
                    raise step_error(index, step, "The thermal plate must have its own binding")
                profile = ", ".join(
                    f"({number(hold.celsius)}, {number(hold.seconds)})" for hold in step.profile
                )
                lid = "None" if step.lid_celsius is None else number(step.lid_celsius)
                commands.append(
                    f"await thermocycle(deck.get_resource({plate.name!r}), "
                    f"profile=[{profile}], cycles={step.cycles}, lid_temperature={lid})"
                )
            elif isinstance(step, ManualInstruction):
                commands.append(f"confirm({step.text!r})")
            elif isinstance(step, SetTemperature):
                plate = self.labware[step.resource]
                if not isinstance(plate, ItemizedResource):
                    raise step_error(index, step, "A held temperature must bind a whole plate")
                commands.append(
                    f"await thermocycle(deck.get_resource({plate.name!r}), "
                    f"profile=[({number(step.celsius)}, 0)], cycles=1, lid_temperature=None)"
                )
            else:
                raise step_error(index, step, "Unsupported step")
        drop()
        initialize = []
        for resource in protocol.resources:
            fills = {fill.well: fill.volume for fill in resource.fills}
            for well in resource.wells:
                value = number(fills.get(well, Decimal(0)))
                initialize.append(
                    f"{refs[Location(resource.name, well)]}.tracker.set_volume({value})"
                )
        has_thermal = any(
            isinstance(step, (Thermocycle, SetTemperature)) for step in protocol.steps
        )
        thermal_import = "from inspect import iscoroutinefunction\n" if has_thermal else ""
        liquid_import = ""
        if native_definitions:
            configuration["hamilton_liquid_classes"] = native_definitions
            liquid_import = (
                "from pylabrobot.liquid_handling.liquid_classes.hamilton "
                "import HamiltonLiquidClass\n"
                "LIQUID_CLASSES = {\n" + "\n".join(native_initializers) + "\n}\n"
            )
        source = (
            '"""Generated PyLabRobot protocol. Direct execution uses a software backend."""\n\n'
            "import asyncio\nimport json\nfrom importlib.metadata import version\n"
            f"{thermal_import}\n"
            f"{liquid_import}\n"
            "from pylabrobot.liquid_handling import LiquidHandler\n"
            "from pylabrobot.liquid_handling.backends import LiquidHandlerChatterboxBackend\n"
            "from pylabrobot.resources import Resource\n\n"
            f"DECK_JSON = {serialized_deck!r}\n"
            f"SDK_VERSION = {version('pylabrobot')!r}\n\n"
            "async def run(backend, *, confirm=None, thermocycle=None, sleep=asyncio.sleep):\n"
            '    """The thermal callback matches lid to block when lid_temperature is None;\n'
            "    Turn lid heating off below the device's heated lid range;\n"
            "    keep the final temperature settings.\n"
            '    """\n'
            "    if version('pylabrobot') != SDK_VERSION:\n"
            "        raise RuntimeError(f'This artifact requires pylabrobot=={SDK_VERSION}')\n"
        )
        if any(isinstance(step, ManualInstruction) for step in protocol.steps):
            source += (
                "    if confirm is None:\n"
                "        raise ValueError('Supply confirm for explicit operator steps')\n"
            )
        if has_thermal:
            source += (
                "    if not iscoroutinefunction(thermocycle):\n"
                "        raise ValueError('Supply an async thermocycle callback for the external "
                "thermal device; it must return the plate to its original position')\n"
            )
        source += (
            "    deck = Resource.deserialize(json.loads(DECK_JSON))\n"
            + "\n".join(f"    {line}" for line in initialize)
            + "\n    lh = LiquidHandler(backend=backend, deck=deck)\n"
            "    await lh.setup()\n"
            "    try:\n"
            + "\n".join(f"        {line}" for line in commands)
            + "\n    finally:\n        await lh.stop()\n"
            "    return lh\n\n"
        )
        if has_thermal:
            source += (
                "async def preview_thermocycle(plate, *, profile, cycles, lid_temperature):\n"
                "    print(f'Simulation only: thermal profile for {plate.name}: {profile}; "
                "{cycles} cycles; lid {lid_temperature} C')\n\n"
            )
        thermal_callback = ", thermocycle=preview_thermocycle" if has_thermal else ""
        source += (
            "if __name__ == '__main__':\n"
            "    asyncio.run(run(LiquidHandlerChatterboxBackend(), confirm=input"
            f"{thermal_callback}))\n"
        )
        return TargetPlan(
            "Hamilton STAR (PyLabRobot)",
            tuple(bindings),
            json.dumps(configuration, sort_keys=True),
            source,
            (
                f"Hamilton deck with {self.deck.num_rails} rails; "
                f"channel indices {', '.join(str(c) for c in channels)}",
                *(
                    (
                        "External thermal device: supply an async callback; return the plate "
                        "to its original deck position before pipetting resumes",
                    )
                    if has_thermal
                    else ()
                ),
                *(
                    f"{child.name}: {child.model or type(child).__name__} at {child.location} mm"
                    for child in self.deck.children
                    if child.name not in {"trash", "trash96", "core_gripper", "teaching_rack"}
                ),
                f"{tip_index} fresh tips from {', '.join(rack.name for rack in self.tip_racks)}",
            ),
            limits=(
                PipettingLimits(
                    label=f"STAR channels {', '.join(str(c) for c in channels)}",
                    min_volume_ul=minimum,
                    max_volume_ul=maximum,
                    trusted_minimum_ul=minimum,
                    channels=len(channels),
                ),
            ),
        )


_LAYOUT_LABWARE = {
    LabwareModel.CORNING_96_360_UL: "Cor_96_wellplate_360ul_Fb",
    LabwareModel.NEST_12_RESERVOIR_15_ML: "nest_12_troughplate_15000uL_Vb",
    LabwareModel.AZENTA_96_PCR_200_UL: "Azenta4titudeFrameStar_96_wellplate_200ul_Vb",
}
_LAYOUT_CARRIERS = {
    CarrierModel.HAMILTON_PLATE_5: "PLT_CAR_L5AC_A00",
    CarrierModel.HAMILTON_TIP_5: "TIP_CAR_480_A00",
}
_LAYOUT_TIPS = {
    TipRackModel.HAMILTON_50_UL: "hamilton_96_tiprack_50uL",
    TipRackModel.HAMILTON_300_UL: "hamilton_96_tiprack_300uL",
}


def lower_layout(deck: Deck, layout: DeckLayout) -> STAR:
    """Build the PyLabRobot resource tree from Lab-owned equipment and placements."""
    if _sdk_import_error is not None:
        raise ImportError(
            "Install lab-compiler[star] to compile for Hamilton STAR"
        ) from _sdk_import_error
    if layout.pipettes:
        raise CompileError("STAR layouts configure independent Channel objects.")
    if layout.modules:
        raise CompileError(
            "The STAR backend does not yet support on-deck Module models; "
            "configure a declared external thermal handoff."
        )
    if not 1 <= len(layout.channels) <= 8:
        raise CompileError("The STAR backend supports one to eight independent channels.")
    channel = layout.channels[0]
    if any(not 0 <= c.index < 8 for c in layout.channels):
        raise CompileError("STAR channel must be an integer from 0 to 7")
    if any(
        (c.min_volume_ul, c.max_volume_ul, c.tip_racks)
        != (channel.min_volume_ul, channel.max_volume_ul, channel.tip_racks)
        for c in layout.channels
    ):
        raise CompileError("STAR channels in one layout share volume limits and tip racks.")
    robot = plr.STARDeck()
    carriers: dict[str, Any] = {}
    try:
        for carrier in layout.carriers:
            if not isinstance(carrier.location, Rail):
                raise CompileError("STAR carriers require a Rail location.")
            physical = getattr(plr, _LAYOUT_CARRIERS[carrier.model])(name=carrier.id)
            robot.assign_child_resource(physical, rails=carrier.location.index)
            carriers[carrier.id] = physical

        def place(resource: Any, location: Any, expected_carrier: CarrierModel) -> None:
            if not isinstance(location, HolderSite) or location.holder not in carriers:
                raise CompileError("STAR labware and tips require a carrier HolderSite.")
            model = next(item.model for item in layout.carriers if item.id == location.holder)
            if model != expected_carrier:
                raise CompileError(f"{resource.name} requires a {expected_carrier.value} carrier.")
            carriers[location.holder][location.index] = resource

        labware: dict[str, Any] = {}
        for placement in layout.placements:
            factory = _LAYOUT_LABWARE.get(placement.model)
            if factory is None:
                raise CompileError(f"STAR does not support labware model {placement.model.value}.")
            physical = getattr(plr, factory)(name=placement.container)
            place(physical, placement.location, CarrierModel.HAMILTON_PLATE_5)
            labware[placement.container] = physical
        tips: dict[str, Any] = {}
        for rack in layout.tip_racks:
            factory = _LAYOUT_TIPS.get(rack.model)
            if factory is None:
                raise CompileError(f"STAR does not support tip rack model {rack.model.value}.")
            physical = getattr(plr, factory)(name=rack.id)
            place(physical, rack.location, CarrierModel.HAMILTON_TIP_5)
            tips[rack.id] = physical
    except CompileError:
        raise
    except (ValueError, IndexError) as exc:
        raise CompileError(f"Invalid STAR layout: {exc}") from exc
    return STAR(
        deck=robot,
        labware=labware,
        tip_racks=tuple(tips[name] for name in channel.tip_racks),
        min_volume=channel.min_volume_ul * uL,
        max_volume=channel.max_volume_ul * uL,
        channel=channel.index,
        channels=tuple(c.index for c in layout.channels),
        well_maps={
            placement.container: placement.wells
            for placement in layout.placements
            if placement.wells
        },
        external_thermal_resources=layout.external_thermal_resources,
    )


def lower_deck(deck: Deck, *, volumes: tuple[Decimal, ...]) -> STAR:
    """Resolve an ambient plate preset; other equipment needs a Lab DeckLayout."""
    if len(deck.containers) > 10:
        raise CompileError("The STAR plate preset holds at most ten plates; provide a DeckLayout.")
    placements = []
    carriers = [
        Carrier(id="tip_carrier", model=CarrierModel.HAMILTON_TIP_5, location=Rail(3)),
        Carrier(id="plate_carrier", model=CarrierModel.HAMILTON_PLATE_5, location=Rail(15)),
    ]
    if len(deck.containers) > 5:
        carriers.append(
            Carrier(id="overflow_carrier", model=CarrierModel.HAMILTON_PLATE_5, location=Rail(30))
        )
    for index, container in enumerate(deck.containers):
        if not isinstance(container, DeckContainer) or container.site not in (
            DeckSite.PLATES,
            DeckSite.MORE_PLATES,
        ):
            raise CompileError(
                f"No STAR preset for {container.id}; provide a Lab DeckLayout "
                "for its equipment and thermal requirements."
            )
        if container.labware.kind not in (LabwareKind.PLATE, LabwareKind.PCR_PLATE):
            raise CompileError(f"No STAR plate preset for {container.labware.kind.value}.")
        placements.append(
            Placement(
                container=container.id,
                model=LabwareModel.CORNING_96_360_UL
                if container.labware.kind == LabwareKind.PLATE
                else LabwareModel.AZENTA_96_PCR_200_UL,
                location=HolderSite(
                    "plate_carrier" if index < 5 else "overflow_carrier", index % 5
                ),
            )
        )
    maximum = max(volumes, default=Decimal(1))
    layout = DeckLayout(
        liquid_handler=LiquidHandler.STAR,
        placements=tuple(placements),
        carriers=tuple(carriers),
        tip_racks=(
            DeckTipRack(
                id="tips",
                model=TipRackModel.HAMILTON_50_UL
                if maximum <= 50
                else TipRackModel.HAMILTON_300_UL,
                location=HolderSite("tip_carrier", 0),
            ),
        ),
        channels=(
            Channel(
                index=0,
                min_volume_ul=Decimal(1),
                max_volume_ul=Decimal(50 if maximum <= 50 else 300),
                tip_racks=("tips",),
            ),
        ),
    )
    return lower_layout(deck, layout)
