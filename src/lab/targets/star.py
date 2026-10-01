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
from lab.model import RecordedProtocol, encode
from lab.operations import (
    Distribute,
    ManualInstruction,
    Mix,
    SetTemperature,
    Thermocycle,
    Transfer,
    Wait,
)
from lab.samples import Location
from lab.target import Binding, TargetPlan
from lab.targets.liquid_handler import LiquidHandler
from lab.units import magnitude, number, uL
from lab.validation import CompileError, step_error

_sdk_import_error: ImportError | None = None
try:
    import pylabrobot.resources as plr
    from pylabrobot.resources import (
        Container,
        ItemizedResource,
        Resource,
        TipRack,
    )
    from pylabrobot.resources.hamilton import HamiltonSTARDeck
except ImportError as exc:
    _sdk_import_error = exc


@dataclass
class STAR:
    """Bind protocol resources to a supplied physical STAR or STARlet configuration.

    The caller places carriers, holders, labware, and tips on a PyLabRobot deck.
    ``labware`` maps protocol resource names to those exact physical resources.
    Compilation preserves this hierarchy and uses one configured pipetting channel.
    Thermal operations require an external device callback when the artifact runs.
    """

    deck: Any
    labware: dict[str, Any]
    tip_racks: tuple[Any, ...]
    min_volume: Any
    max_volume: Any
    channel: int = 0
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
        if type(self.channel) is not int or not 0 <= self.channel < 8:
            raise CompileError("STAR channel must be an integer from 0 to 7")
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
            "channel": self.channel,
            "min_volume_ul": number(minimum),
            "max_volume_ul": number(maximum),
            "tip_policy": "fresh tip per transfer or mix",
            "tips": [spot.name for spot in tips],
            "bindings": encode(tuple(bindings)),
            "external_thermal_resources": self.external_thermal_resources,
        }
        commands = []
        tip_index = 0
        for index, step in enumerate(protocol.steps):
            commands.append(f"# lab:step {step.identity}")
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
            if isinstance(step, (Transfer, Mix, Distribute)):
                if tip_index >= len(tips):
                    raise step_error(index, step, "Not enough fresh tips")
                spot = tips[tip_index]
                tip_index += 1
                tip_max = Decimal(str(spot.get_tip().maximal_volume))
                if not minimum <= step.volume <= min(maximum, tip_max):
                    raise step_error(
                        index, step, "Volume is outside the configured channel/tip range"
                    )
                amount = number(step.volume)
                channels = f"use_channels=[{self.channel}]"
                commands.append(
                    f"await lh.pick_up_tips([deck.get_resource({spot.name!r})], {channels})"
                )
                if isinstance(step, Transfer):
                    height = (
                        ""
                        if step.destination_height_mm is None
                        else f", liquid_height=[{number(step.destination_height_mm)}]"
                    )
                    commands.extend(
                        (
                            f"await lh.aspirate([{refs[step.source]}], "
                            f"vols=[{amount}], {channels})",
                            f"await lh.dispense([{refs[step.destination]}], "
                            f"vols=[{amount}], {channels}{height})",
                        )
                    )
                elif isinstance(step, Distribute):
                    for destination in step.destinations:
                        commands.extend(
                            (
                                f"await lh.aspirate([{refs[step.source]}], "
                                f"vols=[{amount}], {channels})",
                                f"await lh.dispense([{refs[destination]}], "
                                f"vols=[{amount}], {channels})",
                            )
                        )
                else:
                    commands.extend(
                        (
                            f"for _ in range({step.cycles}):",
                            f"    await lh.aspirate([{refs[step.location]}], "
                            f"vols=[{amount}], {channels})",
                            f"    await lh.dispense([{refs[step.location]}], "
                            f"vols=[{amount}], {channels})",
                        )
                    )
                commands.append(f"await lh.discard_tips({channels}, allow_nonzero_volume=False)")
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
                    f"await set_temperature(deck.get_resource({plate.name!r}), "
                    f"temperature={number(step.celsius)})"
                )
            else:
                raise step_error(index, step, "Unsupported step")
            commands.append(f"# lab:end {step.identity}")
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
        has_cycle = any(isinstance(step, Thermocycle) for step in protocol.steps)
        has_hold = any(isinstance(step, SetTemperature) for step in protocol.steps)
        thermal_import = "from inspect import iscoroutinefunction\n" if has_thermal else ""
        source = (
            '"""Generated PyLabRobot protocol. Direct execution uses a software backend."""\n\n'
            "import asyncio\nimport json\nfrom importlib.metadata import version\n"
            f"{thermal_import}\n"
            "from pylabrobot.liquid_handling import LiquidHandler\n"
            "from pylabrobot.liquid_handling.backends import LiquidHandlerChatterboxBackend\n"
            "from pylabrobot.resources import Resource\n\n"
            f"DECK_JSON = {serialized_deck!r}\n"
            f"SDK_VERSION = {version('pylabrobot')!r}\n\n"
            "async def run(backend, *, confirm=None, thermocycle=None, "
            "set_temperature=None, sleep=asyncio.sleep):\n"
            "    if version('pylabrobot') != SDK_VERSION:\n"
            "        raise RuntimeError(f'This artifact requires pylabrobot=={SDK_VERSION}')\n"
        )
        if any(isinstance(step, ManualInstruction) for step in protocol.steps):
            source += (
                "    if confirm is None:\n"
                "        raise ValueError('Supply confirm for explicit operator steps')\n"
            )
        if has_cycle:
            source += (
                "    if not iscoroutinefunction(thermocycle):\n"
                "        raise ValueError('Supply an async thermocycle callback for the external "
                "thermal device; it must return the plate to its original position')\n"
            )
        if has_hold:
            source += (
                "    if not iscoroutinefunction(set_temperature):\n"
                "        raise ValueError('Supply an async set_temperature callback; "
                "the hold must persist while the plate remains accessible for pipetting')\n"
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
        if has_cycle:
            source += (
                "async def preview_thermocycle(plate, *, profile, cycles, lid_temperature):\n"
                "    print(f'Simulation only: thermal profile for {plate.name}: {profile}; "
                "{cycles} cycles; lid {lid_temperature} C')\n\n"
            )
        if has_hold:
            source += (
                "async def preview_set_temperature(plate, *, temperature):\n"
                "    print(f'Simulation only: persistent hold for {plate.name}: "
                "{temperature} C')\n\n"
            )
        thermal_callback = ", thermocycle=preview_thermocycle" if has_cycle else ""
        thermal_callback += ", set_temperature=preview_set_temperature" if has_hold else ""
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
                f"Hamilton deck with {self.deck.num_rails} rails; channel index {self.channel}",
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
    if len(layout.channels) != 1:
        raise CompileError(
            "The STAR backend currently supports one independent channel per layout."
        )
    channel = layout.channels[0]
    if not 0 <= channel.index < 8:
        raise CompileError("STAR channel must be an integer from 0 to 7")
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
        well_maps={
            placement.container: placement.wells
            for placement in layout.placements
            if placement.wells
        },
        external_thermal_resources=layout.external_thermal_resources,
    )


def lower_deck(deck: Deck, *, volumes: tuple[Decimal, ...]) -> STAR:
    """Resolve plate carriers, with explicit runtime contracts for thermal plates."""
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
            DeckSite.THERMOCYCLER,
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
        external_thermal_resources=tuple(
            container.id
            for container in deck.containers
            if isinstance(container, DeckContainer) and container.site == DeckSite.THERMOCYCLER
        ),
    )
    return lower_layout(deck, layout)
