"""One small, sequential backend for the OT-2 and Flex Python Protocol API."""

import json
from dataclasses import asdict, dataclass
from decimal import Decimal
from importlib.metadata import version
from typing import Any, Literal

from lab.deck import Container, Deck, DeckLayout, DeckSite, HolderSite, Slot
from lab.documents import describe
from lab.equipment import LabwareModel, ModuleModel, TipRackModel
from lab.labware import LabwareKind
from lab.model import RecordedProtocol
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
from lab.units import number
from lab.validation import CompileError, step_error, volume_trace

_sdk_import_error: ImportError | None = None
try:
    from opentrons_shared_data.labware import load_definition
except ImportError as exc:
    _sdk_import_error = exc


@dataclass(frozen=True)
class Labware:
    load_name: str
    slot: str
    wells: tuple[str, ...] = ()
    version: int = 1
    module: Literal["temperature module", "temperature module gen2"] | None = None


@dataclass
class _Pipette:
    model: str
    mount: str
    minimum: Decimal
    maximum: Decimal
    tip_name: str
    racks: tuple[Labware, ...]
    variable: str
    tips: list[tuple[str, Decimal]]


@dataclass
class Opentrons:
    """Bind logical resources to explicit labware and one or two single-channel pipettes."""

    robot: Literal["OT-2", "Flex"]
    labware: dict[str, Labware]
    tip_racks: tuple[Labware, ...]
    pipette: str
    mount: Literal["left", "right"] = "left"
    trash_slot: str = "A3"
    thermocycler: Literal["thermocycler module", "thermocycler module gen2"] | None = None
    small_pipette: str | None = None
    small_mount: Literal["left", "right"] = "right"
    small_tip_racks: tuple[Labware, ...] = ()

    @property
    def liquid_handler(self) -> LiquidHandler:
        if self.robot == "Flex":
            return LiquidHandler.FLEX
        return LiquidHandler.OT2

    def prepare(self, protocol: RecordedProtocol) -> TargetPlan:
        if _sdk_import_error is not None:
            raise ImportError(
                "Install lab-compiler[opentrons] to compile for Opentrons"
            ) from _sdk_import_error

        models: dict[str, tuple[str, int, int, str]] = {
            "p300_single_gen2": ("OT-2", 20, 300, "opentrons_96_tiprack_300ul"),
            "p20_single_gen2": ("OT-2", 1, 20, "opentrons_96_tiprack_20ul"),
            "flex_1channel_1000": ("Flex", 5, 1000, "opentrons_flex_96_tiprack_200ul"),
            "flex_1channel_50": ("Flex", 1, 50, "opentrons_flex_96_tiprack_50ul"),
        }
        pipette_specs = [(self.pipette, self.mount, self.tip_racks, "pipette")]
        if self.small_pipette is not None:
            if not self.small_tip_racks:
                raise CompileError("The second pipette needs its own tip racks")
            if self.small_mount not in ("left", "right") or self.small_mount == self.mount:
                raise CompileError("Pipettes must use opposite mounts")
            pipette_specs.append(
                (self.small_pipette, self.small_mount, self.small_tip_racks, "small_pipette")
            )
        loaded_pipettes: list[_Pipette] = []
        for model_name, mount, racks, variable in pipette_specs:
            if model_name not in models or models[model_name][0] != self.robot:
                raise CompileError(
                    "Supported pipettes: OT-2 P20 single GEN2, OT-2 P300 single GEN2, "
                    "and Flex 1-channel 50 or 1000"
                )
            if mount not in ("left", "right"):
                raise CompileError("Pipette mount must be left or right")
            _, low, high, tip_name = models[model_name]
            loaded_pipettes.append(
                _Pipette(
                    model_name,
                    mount,
                    Decimal(low),
                    Decimal(high),
                    tip_name,
                    racks,
                    variable,
                    [],
                )
            )
        if set(self.labware) != {resource.name for resource in protocol.resources}:
            raise CompileError("Labware bindings must match the protocol's resource names exactly")
        slots = (
            {str(n) for n in range(1, 12)}
            if self.robot == "OT-2"
            else {f"{row}{column}" for row in "ABCD" for column in range(1, 4)}
        )
        if self.robot == "Flex":
            if self.trash_slot not in {"A3", "B3", "C3", "D3"}:
                raise CompileError("Flex trash must occupy a column 3 slot")
            slots.remove(self.trash_slot)
        placements: dict[str, tuple[Labware, dict[str, Any], str]] = {}
        setup: list[str] = []
        if self.thermocycler is not None:
            if self.thermocycler not in ("thermocycler module", "thermocycler module gen2"):
                raise CompileError("Select a GEN1 or GEN2 Thermocycler Module explicitly")
            if self.robot == "Flex" and self.thermocycler != "thermocycler module gen2":
                raise CompileError("Flex requires a GEN2 Thermocycler Module")
            reserved = {"7", "8", "10", "11"} if self.robot == "OT-2" else {"A1", "B1"}
            slots.difference_update(reserved)
            slots.add("thermocycler")
            if sum(item.slot == "thermocycler" for item in self.labware.values()) != 1:
                raise CompileError("Bind exactly one logical plate to the thermocycler")
            setup.extend(
                (
                    f"thermocycler = context.load_module({self.thermocycler!r})",
                    "thermocycler.open_lid()",
                )
            )

        def place(item: Labware, *, tips: bool = False) -> tuple[dict[str, Any], str]:
            if item.slot not in slots:
                raise CompileError(f"Unavailable {self.robot} deck slot: {item.slot}")
            previous = placements.get(item.slot)
            if previous:
                old, definition, variable = previous
                if tips or old.load_name != item.load_name or old.version != item.version:
                    raise CompileError(f"Conflicting labware in slot {item.slot}")
                if definition["parameters"]["isTiprack"]:
                    raise CompileError("A tip rack cannot also bind a liquid resource")
                return definition, variable
            try:
                definition = dict(load_definition(item.load_name, item.version))
            except (FileNotFoundError, ValueError) as exc:
                raise CompileError(f"Unknown labware {item.load_name!r}") from exc
            if bool(definition["parameters"]["isTiprack"]) != tips:
                raise CompileError(f"{item.load_name} has the wrong labware role")
            if item.slot == "thermocycler" and (tips or "pcr" not in item.load_name.lower()):
                raise CompileError("The thermocycler requires a PCR plate")
            if item.module and (tips or item.slot == "thermocycler"):
                raise CompileError("A temperature module occupies an open deck slot")
            variable = f"labware_{len(placements) + 1}"
            placements[item.slot] = (item, definition, variable)
            if item.module:
                module_variable = f"temperature_module_{item.slot}"
                if not any(line.startswith(f"{module_variable} =") for line in setup):
                    setup.append(
                        f"{module_variable} = context.load_module({item.module!r}, {item.slot!r})"
                    )
                loader = f"{module_variable}.load_labware"
                location = ""
            else:
                loader = (
                    "thermocycler.load_labware"
                    if item.slot == "thermocycler"
                    else "context.load_labware"
                )
                location = "" if item.slot == "thermocycler" else f", {item.slot!r}"
            setup.append(
                f"{variable} = {loader}({item.load_name!r}{location}, version={item.version})"
            )
            return definition, variable

        refs: dict[Location, str] = {}
        bindings = []
        temperature_of: dict[str, str] = {}
        for resource in protocol.resources:
            item = self.labware[resource.name]
            definition, variable = place(item)
            physical_wells = item.wells or resource.wells
            if len(physical_wells) != len(resource.wells):
                raise CompileError(f"Wrong number of bound wells for {resource.name}")
            for logical, physical in zip(resource.wells, physical_wells, strict=True):
                if physical not in definition["wells"]:
                    raise CompileError(f"{physical} is not present in {item.load_name}")
                location = Location(resource.name, logical)
                refs[location] = f"{variable}.wells_by_name()[{physical!r}]"
                bindings.append(
                    Binding(
                        location,
                        f"slot {item.slot}/{physical}",
                        Decimal(str(definition["wells"][physical]["totalLiquidVolume"])),
                        resource.dead_volume,
                    )
                )
            if item.slot == "thermocycler":
                temperature_of[resource.name] = "thermocycler"
            elif item.module:
                temperature_of[resource.name] = f"temperature_module_{item.slot}"
        for pipette in loaded_pipettes:
            rack_variables = []
            for rack in pipette.racks:
                if rack.load_name != pipette.tip_name:
                    raise CompileError(f"{pipette.model} requires {pipette.tip_name}")
                definition, variable = place(rack, tips=True)
                rack_variables.append(variable)
                names = rack.wells or tuple(w for column in definition["ordering"] for w in column)
                if len(set(names)) != len(names):
                    raise CompileError("A tip position may only be supplied once")
                for name in names:
                    if name not in definition["wells"]:
                        raise CompileError(f"Unknown tip position {name}")
                    pipette.tips.append(
                        (
                            f"{variable}.wells_by_name()[{name!r}]",
                            Decimal(str(definition["wells"][name]["totalLiquidVolume"])),
                        )
                    )
            setup.append(
                f"{pipette.variable} = context.load_instrument({pipette.model!r}, "
                f"{pipette.mount!r}, tip_racks=[{', '.join(rack_variables)}])"
            )
        if self.robot == "Flex":
            setup.insert(0, f"context.load_trash_bin({self.trash_slot!r})")
        commands = []
        volumes = volume_trace(protocol, tuple(bindings))
        tip_indexes = dict.fromkeys((pipette.variable for pipette in loaded_pipettes), 0)

        def select_pipette(volume: Decimal) -> _Pipette:
            fits = [
                pipette
                for pipette in loaded_pipettes
                if pipette.minimum <= volume <= pipette.maximum
            ]
            if not fits:
                raise CompileError("Volume is outside the pipette/tip range")
            return min(fits, key=lambda pipette: pipette.maximum)

        for index, step in enumerate(protocol.steps):
            commands.append(f"# lab:step {step.identity}")
            commands.append(f"context.comment({describe(step)!r})")
            if isinstance(step, (Transfer, Mix, Distribute)):
                try:
                    pipette = select_pipette(step.volume)
                except CompileError as exc:
                    raise step_error(index, step, str(exc)) from exc
                tip_index = tip_indexes[pipette.variable]
                if tip_index >= len(pipette.tips):
                    raise step_error(index, step, "Not enough fresh tips")
                tip, tip_capacity = pipette.tips[tip_index]
                tip_indexes[pipette.variable] = tip_index + 1
                if step.volume > tip_capacity:
                    raise step_error(index, step, "Volume is outside the pipette/tip range")
                tool = pipette.variable
                if pipette.model == "flex_1channel_50":
                    commands.append(f"{tool}.configure_for_volume({number(step.volume)})")
                if isinstance(step, Distribute):
                    mode_max = (
                        Decimal(30)
                        if pipette.model == "flex_1channel_50" and step.volume < 5
                        else pipette.maximum
                    )
                    if step.volume + (step.air_gap or Decimal(0)) > min(mode_max, tip_capacity):
                        raise step_error(index, step, "Liquid and air gap exceed the volume mode")
                commands.append(f"{tool}.pick_up_tip({tip})")
                amount = number(step.volume)
                if isinstance(step, Transfer):
                    destination = refs[step.destination]
                    if step.destination_height_mm is not None:
                        destination += f".bottom(z={number(step.destination_height_mm)})"
                    commands.extend(
                        (
                            f"{tool}.aspirate({amount}, {refs[step.source]})",
                            f"{tool}.dispense({amount}, {destination})",
                        )
                    )
                elif isinstance(step, Distribute):
                    destinations = ", ".join(refs[destination] for destination in step.destinations)
                    gap = f", air_gap={number(step.air_gap)}" if step.air_gap is not None else ""
                    commands.append(
                        f"{tool}.distribute({amount}, {refs[step.source]}, [{destinations}], "
                        f"disposal_volume=0, new_tip='never'{gap})"
                    )
                else:
                    commands.append(f"{tool}.mix({step.cycles}, {amount}, {refs[step.location]})")
                commands.append(f"{tool}.drop_tip()")
            elif isinstance(step, SetTemperature):
                module = temperature_of.get(step.resource)
                if module is None:
                    raise step_error(
                        index, step, "Bind this plate to a thermocycler or temperature module"
                    )
                upper = 99 if module == "thermocycler" else 95
                if not 4 <= step.celsius <= upper:
                    raise step_error(index, step, f"Module temperature range is 4–{upper} °C")
                setter = "set_block_temperature" if module == "thermocycler" else "set_temperature"
                commands.append(f"{module}.{setter}({number(step.celsius)})")
            elif isinstance(step, Thermocycle):
                if self.thermocycler is None or self.labware[step.resource].slot != "thermocycler":
                    raise step_error(index, step, "The thermal plate must be on the thermocycler")
                if any(not 4 <= hold.celsius <= 99 for hold in step.profile):
                    raise step_error(index, step, "Opentrons block range is 4–99 °C")
                if step.lid_celsius is not None and not 37 <= step.lid_celsius <= 110:
                    raise step_error(index, step, "Opentrons heated lid range is 37–110 °C")
                modeled_volume = max(
                    volume
                    for location, volume in volumes[index].items()
                    if location.resource == step.resource
                )
                if modeled_volume > 100:
                    raise step_error(index, step, "Thermocycler working volume exceeds 100 µL")
                if step.block_volume is None:
                    maximum_volume = modeled_volume
                elif not Decimal(0) < step.block_volume <= 100:
                    raise step_error(index, step, "Thermocycler block volume must be within 100 µL")
                else:
                    maximum_volume = step.block_volume
                profile = ", ".join(
                    f"{{'temperature': {number(hold.celsius)}, "
                    f"'hold_time_seconds': {number(hold.seconds)}}}"
                    for hold in step.profile
                )
                lid = (
                    "thermocycler.deactivate_lid()"
                    if step.lid_celsius is None
                    else f"thermocycler.set_lid_temperature({number(step.lid_celsius)})"
                )
                commands.extend(
                    (
                        "thermocycler.close_lid()",
                        "try:",
                        f"    {lid}",
                        f"    thermocycler.execute_profile([{profile}], repetitions={step.cycles}, "
                        f"block_max_volume={number(maximum_volume)})",
                        "finally:",
                        "    thermocycler.deactivate()",
                        "thermocycler.open_lid()",
                    )
                )
            elif isinstance(step, Wait):
                commands.append(f"context.delay(seconds={number(step.seconds)})")
            elif isinstance(step, ManualInstruction):
                commands.append(f"context.pause({step.text!r})")
            else:
                raise step_error(index, step, "Unsupported step")
            commands.append(f"# lab:end {step.identity}")
        source = (
            '"""Generated by Lab. Each liquid operation uses a fresh tip."""\n\n'
            "from opentrons import protocol_api\n\n"
            f"metadata = {{'protocolName': {protocol.name!r}, 'author': 'Lab'}}\n"
            f"requirements = {{'robotType': {self.robot!r}, 'apiLevel': '2.21'}}\n\n"
            "def run(context: protocol_api.ProtocolContext):\n"
            + "\n".join(f"    {line}" for line in [*setup, "", *commands])
            + "\n"
        )
        configuration = {
            **asdict(self),
            "api_level": "2.21",
            "sdk_version": version("opentrons"),
            "tip_policy": "fresh tip per transfer or mix",
        }
        return TargetPlan(
            self.robot,
            tuple(bindings),
            json.dumps(configuration, sort_keys=True),
            source,
            (
                *(f"{pipette.model} mounted on the {pipette.mount}" for pipette in loaded_pipettes),
                *((f"Thermal module: {self.thermocycler}",) if self.thermocycler else ()),
                *(
                    f"Slot {slot}: {definition['metadata']['displayName']}"
                    for slot, (_, definition, _) in placements.items()
                ),
                *(
                    (f"Trash bin in slot {self.trash_slot}",)
                    if self.robot == "Flex"
                    else ("Fixed trash in slot 12",)
                ),
                f"{sum(tip_indexes.values())} fresh tips required",
            ),
        )


# Open-deck sites fill in this order. Tip racks use whatever remains.
_SITE_SLOTS: dict[str, dict[DeckSite, tuple[str, ...]]] = {
    "OT-2": {
        DeckSite.TEMPERATURE_MODULE: ("1",),
        DeckSite.THERMOCYCLER: ("thermocycler",),
        DeckSite.PLATES: ("2", "3"),
        DeckSite.MORE_PLATES: ("5", "6"),
        DeckSite.TUBE_RACK: ("3",),
        DeckSite.RESERVOIR: ("4",),
    },
    "Flex": {
        DeckSite.TEMPERATURE_MODULE: ("C1",),
        DeckSite.THERMOCYCLER: ("thermocycler",),
        DeckSite.PLATES: ("C1", "B2"),
        DeckSite.MORE_PLATES: ("C2", "B3"),
        DeckSite.TUBE_RACK: ("C2",),
        DeckSite.RESERVOIR: ("D2",),
    },
}
_LOAD_NAMES: dict[LabwareKind, str] = {
    LabwareKind.PLATE: "corning_96_wellplate_360ul_flat",
    LabwareKind.COLD_BLOCK: "opentrons_24_aluminumblock_nest_1.5ml_snapcap",
    LabwareKind.PCR_PLATE: "nest_96_wellplate_100ul_pcr_full_skirt",
    LabwareKind.TUBE_RACK: "opentrons_24_tuberack_eppendorf_1.5ml_safelock_snapcap",
    LabwareKind.CONICAL_RACK: "opentrons_15_tuberack_falcon_15ml_conical",
    LabwareKind.CULTURE_PLATE: "biorad_96_wellplate_200ul_pcr",
}
_P20_TIP_SLOTS = ("2", "9", "3", "4", "5", "6")


def lower_deck(
    deck: Deck, *, robot: Literal["OT-2", "Flex"], volumes: tuple[Decimal, ...]
) -> Opentrons:
    """Bind a deck to OT-2 or Flex labware, modules, and single-channel pipettes."""
    labware, taken = _bind_containers(deck, robot)
    thermocycler: Literal["thermocycler module", "thermocycler module gen2"] | None = None
    if any(
        isinstance(container, Container) and container.site == DeckSite.THERMOCYCLER
        for container in deck.containers
    ):
        thermocycler = "thermocycler module" if robot == "OT-2" else "thermocycler module gen2"
    if robot == "Flex":
        if "D1" in taken:
            raise CompileError("Flex tip rack slot D1 is already in use")
        small = not volumes or any(volume <= 50 for volume in volumes)
        large = any(volume > 50 for volume in volumes)
        second_tips = (
            (Labware("opentrons_flex_96_tiprack_50ul", _open_slot(("D2", "C2", "B2"), taken)),)
            if small and large
            else ()
        )
        return Opentrons(
            robot="Flex",
            pipette="flex_1channel_1000" if large else "flex_1channel_50",
            labware=labware,
            tip_racks=(
                Labware(
                    "opentrons_flex_96_tiprack_200ul"
                    if large
                    else "opentrons_flex_96_tiprack_50ul",
                    "D1",
                ),
            ),
            small_pipette="flex_1channel_50" if small and large else None,
            small_tip_racks=second_tips,
            thermocycler=thermocycler,
        )
    small = any(volume <= 20 for volume in volumes) or not any(volume > 20 for volume in volumes)
    large = any(volume > 20 for volume in volumes)
    if small and large:
        if "6" in taken or "9" in taken:
            raise CompileError("OT-2 slots 6 and 9 must be free for the P300 and P20 tip racks")
        return Opentrons(
            robot="OT-2",
            pipette="p300_single_gen2",
            mount="right",
            tip_racks=(Labware("opentrons_96_tiprack_300ul", "6"),),
            small_pipette="p20_single_gen2",
            small_mount="left",
            small_tip_racks=(Labware("opentrons_96_tiprack_20ul", "9"),),
            labware=labware,
            thermocycler=thermocycler,
        )
    if large:
        slot = _open_slot(("6", "2", "3", "4", "5", "9"), taken)
        return Opentrons(
            robot="OT-2",
            pipette="p300_single_gen2",
            labware=labware,
            tip_racks=(Labware("opentrons_96_tiprack_300ul", slot),),
            thermocycler=thermocycler,
        )
    slot = _open_slot(_P20_TIP_SLOTS, taken)
    return Opentrons(
        robot="OT-2",
        pipette="p20_single_gen2",
        labware=labware,
        tip_racks=(Labware("opentrons_96_tiprack_20ul", slot),),
        thermocycler=thermocycler,
    )


def _bind_containers(
    deck: Deck, robot: Literal["OT-2", "Flex"]
) -> tuple[dict[str, Labware], set[str]]:
    counts: dict[DeckSite, int] = {}
    taken: set[str] = set()
    labware: dict[str, Labware] = {}
    for container in deck.containers:
        if not isinstance(container, Container):
            raise CompileError(
                "An Opentrons preset needs DeckSite groups or an explicit DeckLayout."
            )
        pool = _SITE_SLOTS[robot][container.site]
        index = counts.get(container.site, 0)
        if index >= len(pool):
            raise CompileError(f"No more {robot} sites for {container.site.value}")
        slot = pool[index]
        counts[container.site] = index + 1
        if slot in taken:
            raise CompileError(f"{robot} slot {slot} is already in use")
        taken.add(slot)
        module: Literal["temperature module", "temperature module gen2"] | None = None
        if container.site == DeckSite.TEMPERATURE_MODULE:
            module = "temperature module" if robot == "OT-2" else "temperature module gen2"
        load_name = _LOAD_NAMES[container.labware.kind]
        if robot == "Flex" and container.site == DeckSite.THERMOCYCLER:
            load_name = "opentrons_96_wellplate_200ul_pcr_full_skirt"
        labware[container.id] = Labware(load_name, slot, module=module)
    return labware, taken


def _open_slot(slots: tuple[str, ...], taken: set[str]) -> str:
    for slot in slots:
        if slot not in taken:
            return slot
    raise CompileError("No open OT-2 slot for a tip rack")


_LAYOUT_LABWARE = {
    LabwareModel.CORNING_96_360_UL: "corning_96_wellplate_360ul_flat",
    LabwareModel.NEST_12_RESERVOIR_15_ML: "nest_12_reservoir_15ml",
    LabwareModel.NEST_96_PCR_100_UL: "nest_96_wellplate_100ul_pcr_full_skirt",
    LabwareModel.OPENTRONS_96_PCR_200_UL: "opentrons_96_wellplate_200ul_pcr_full_skirt",
    LabwareModel.BIORAD_96_PCR_200_UL: "biorad_96_wellplate_200ul_pcr",
    LabwareModel.OPENTRONS_24_COLD_BLOCK: "opentrons_24_aluminumblock_nest_1.5ml_snapcap",
    LabwareModel.OPENTRONS_24_TUBE_RACK: "opentrons_24_tuberack_eppendorf_1.5ml_safelock_snapcap",
    LabwareModel.OPENTRONS_15_CONICAL_RACK: "opentrons_15_tuberack_falcon_15ml_conical",
}
_LAYOUT_TIPS = {
    TipRackModel.OPENTRONS_20_UL: "opentrons_96_tiprack_20ul",
    TipRackModel.OPENTRONS_300_UL: "opentrons_96_tiprack_300ul",
    TipRackModel.FLEX_200_UL: "opentrons_flex_96_tiprack_200ul",
    TipRackModel.FLEX_50_UL: "opentrons_flex_96_tiprack_50ul",
}


def lower_layout(layout: DeckLayout) -> Opentrons:
    """Translate a Lab-owned layout; SDK names stay within this backend."""
    robot: Literal["OT-2", "Flex"] = (
        "OT-2" if layout.liquid_handler == LiquidHandler.OT2 else "Flex"
    )
    if layout.carriers or layout.channels or layout.external_thermal_resources:
        raise CompileError(
            f"{robot} does not support carriers, independent channels, "
            "or external thermal handoffs."
        )
    if not 1 <= len(layout.pipettes) <= 2:
        raise CompileError(f"Configure one or two pipettes for {robot}.")
    assigned_tips = [rack for pipette in layout.pipettes for rack in pipette.tip_racks]
    if len(set(assigned_tips)) != len(assigned_tips):
        raise CompileError("The Opentrons backend requires separate tip racks for each pipette.")
    thermocycler: Literal["thermocycler module", "thermocycler module gen2"] | None = None
    thermal_holder: str | None = None
    temperatures: dict[str, Literal["temperature module", "temperature module gen2"]] = {}
    module_slots: dict[str, str] = {}
    for module in layout.modules:
        if not isinstance(module.location, Slot):
            raise CompileError(f"{robot} modules require a Slot location.")
        module_slots[module.id] = module.location.name
        if module.model in (ModuleModel.THERMOCYCLER_GEN1, ModuleModel.THERMOCYCLER_GEN2):
            if thermal_holder is not None:
                raise CompileError(f"The {robot} backend supports one thermocycler.")
            anchor = "7" if robot == "OT-2" else "A1"
            if module.location.name != anchor:
                raise CompileError(f"The {robot} thermocycler must be anchored in slot {anchor}.")
            thermal_holder = module.id
            thermocycler = (
                "thermocycler module"
                if module.model == ModuleModel.THERMOCYCLER_GEN1
                else "thermocycler module gen2"
            )
        else:
            if robot == "Flex" and module.model != ModuleModel.TEMPERATURE_GEN2:
                raise CompileError("Flex requires a GEN2 temperature module.")
            temperatures[module.id] = (
                "temperature module"
                if module.model == ModuleModel.TEMPERATURE_GEN1
                else "temperature module gen2"
            )
    labware: dict[str, Labware] = {}
    used_modules: set[str] = set()
    for placement in layout.placements:
        load_name = _LAYOUT_LABWARE.get(placement.model)
        if load_name is None:
            raise CompileError(f"{robot} does not support labware model {placement.model.value}.")
        location = placement.location
        module_name = None
        if isinstance(location, Slot):
            slot = location.name
        elif isinstance(location, HolderSite) and location.holder in module_slots:
            used_modules.add(location.holder)
            slot = (
                "thermocycler"
                if location.holder == thermal_holder
                else module_slots[location.holder]
            )
            module_name = temperatures.get(location.holder)
        else:
            raise CompileError(f"{robot} labware requires a Slot or module HolderSite.")
        labware[placement.container] = Labware(
            load_name, slot, wells=placement.wells, module=module_name
        )
    if used_modules != set(module_slots):
        raise CompileError("Every configured Opentrons module must hold a container.")
    racks: dict[str, Labware] = {}
    for rack in layout.tip_racks:
        load_name = _LAYOUT_TIPS.get(rack.model)
        if load_name is None or not isinstance(rack.location, Slot):
            raise CompileError(f"{robot} requires supported tip racks placed in Slots.")
        racks[rack.id] = Labware(load_name, rack.location.name)
    primary = layout.pipettes[0]
    secondary = layout.pipettes[1] if len(layout.pipettes) == 2 else None
    return Opentrons(
        robot=robot,
        labware=labware,
        pipette=primary.model.value,
        mount="left" if primary.mount.value == "left" else "right",
        tip_racks=tuple(racks[name] for name in primary.tip_racks),
        thermocycler=thermocycler,
        small_pipette=secondary.model.value if secondary else None,
        small_mount="left" if secondary and secondary.mount.value == "left" else "right",
        small_tip_racks=tuple(racks[name] for name in secondary.tip_racks) if secondary else (),
    )
