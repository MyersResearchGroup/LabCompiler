"""Heat-shock transformation recorded as ordinary protocol steps.

Competent cells are distributed by chassis, DNA is added from named plasmid
wells, the plate runs the cold / heat-shock / cold profile, recovery medium
is added, and the plate incubates. Well order and tube counts follow the OT-2
heat-shock protocol. ``plasmid_locations`` maps a plasmid URI to the wells
that already hold it, which is how an assembly plate is consumed.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from itertools import groupby

from sbol3 import Component

from lab.experiments.cloning.addresses import microliters, uri_name, well_at, well_name
from lab.experiments.cloning.types import Transformation
from lab.protocol import Plate, Protocol
from lab.samples import OutputManifest, Sample
from lab.units import celsius, minutes, uL

_CELL_MIX = Decimal(50)


@dataclass(frozen=True)
class Strain:
    strain: str
    chassis: str
    plasmids: tuple[str, ...]
    plasmid_uris: tuple[str, ...]


@dataclass(frozen=True)
class CellMove:
    tube_index: int
    destination: int
    chassis: str
    strain: str


@dataclass(frozen=True)
class DnaMove:
    source_well: str
    destination: int
    plasmid: str


@dataclass(frozen=True)
class MediaMove:
    tube_index: int
    destination: int
    name: str


@dataclass(frozen=True)
class Shock:
    cold_temperature: object
    cold_duration: object
    heat_temperature: object
    heat_duration: object
    chill_temperature: object
    chill_duration: object
    recovery_temperature: object
    recovery_duration: object


@dataclass(frozen=True)
class TransformationLayout:
    strains: tuple[Strain, ...]
    cell_moves: tuple[CellMove, ...]
    dna_moves: tuple[DnaMove, ...]
    media_moves: tuple[MediaMove, ...]
    cell_volume: Decimal
    dna_volume: Decimal
    media_volume: Decimal
    tube_volume_cells: Decimal
    tube_volume_media: Decimal
    dna_stocks: tuple[tuple[str, str, Decimal], ...]
    cell_stocks: tuple[tuple[int, str, Decimal], ...]
    media_stocks: tuple[tuple[int, str, Decimal], ...]
    contents: dict[str, tuple[str, ...]]
    shock: Shock
    chill_dna: bool


def layout_transformation(
    transformation_data: Sequence[Mapping[str, object]],
    plasmid_locations: Mapping[str, Sequence[str]] | None = None,
    *,
    volume_dna: object = 20 * uL,
    replicates: int = 2,
    starting_well: int = 0,
    initial_dna_well: int = 0,
    transfer_volume_dna: object = 2 * uL,
    transfer_volume_competent_cell: object = 20 * uL,
    tube_volume_competent_cell: object = 100 * uL,
    transfer_volume_recovery_media: object = 60 * uL,
    tube_volume_recovery_media: object = 1200 * uL,
    cold_temperature: object = celsius(4),
    cold_duration: object = 30 * minutes,
    heat_temperature: object = celsius(42),
    heat_duration: object = 1 * minutes,
    chill_temperature: object = celsius(4),
    chill_duration: object = 2 * minutes,
    recovery_temperature: object = celsius(37),
    recovery_duration: object = 60 * minutes,
    tube_wells: int = 24,
    dna_rows: int = 8,
) -> TransformationLayout:
    if not isinstance(transformation_data, list) or not transformation_data:
        raise ValueError(
            "transformation_data must be a non-empty list of transformation dictionaries"
        )
    if type(replicates) is not int or replicates < 1:
        raise ValueError("Replicates must be a positive integer")
    strains = _strains(transformation_data, plasmid_locations)
    location_replicates = _location_replicates(strains, plasmid_locations)
    cell_volume = microliters(transfer_volume_competent_cell)
    dna_volume = microliters(transfer_volume_dna)
    media_volume = microliters(transfer_volume_recovery_media)
    cell_tube = microliters(tube_volume_competent_cell)
    media_tube = microliters(tube_volume_recovery_media)
    if cell_tube < _CELL_MIX:
        raise ValueError("Each competent-cell tube must hold at least 50 µL so it can be mixed.")
    per_cell_tube = int(cell_tube // cell_volume)
    per_media_tube = int(media_tube // media_volume)
    if per_cell_tube < 1 or per_media_tube < 1:
        raise ValueError("Tube volume must cover at least one transfer.")
    chassis = sorted({strain.chassis for strain in strains})
    reactions_by_chassis = {
        name: sum(strain.chassis == name for strain in strains) * location_replicates * replicates
        for name in chassis
    }
    cell_tubes = {
        name: (count + per_cell_tube - 1) // per_cell_tube
        for name, count in reactions_by_chassis.items()
    }
    total = sum(reactions_by_chassis.values())
    media_tubes = (total + per_media_tube - 1) // per_media_tube
    if sum(cell_tubes.values()) + media_tubes > tube_wells:
        raise ValueError(
            f"Competent-cell and media tubes need more than {tube_wells} tube-rack wells."
        )
    plasmids = sorted({name for strain in strains for name in strain.plasmids})
    if plasmid_locations is None and initial_dna_well + len(plasmids) > 24:
        raise ValueError("DNA plasmids do not fit on the 24-well block.")
    if starting_well + total > 96:
        raise ValueError("Transformation reactions do not fit on the thermocycler plate.")

    dna_wells = _dna_wells(strains, plasmids, plasmid_locations, initial_dna_well, dna_rows)
    dna_amount = microliters(volume_dna)
    dna_stocks = tuple(
        (well, plasmid, dna_amount) for plasmid, wells in dna_wells.items() for well in wells
    )
    cursor = 0
    tube_of: dict[str, tuple[int, ...]] = {}
    cell_stocks: list[tuple[int, str, Decimal]] = []
    for name in chassis:
        indexes = tuple(range(cursor, cursor + cell_tubes[name]))
        tube_of[name] = indexes
        for offset, index in enumerate(indexes, start=1):
            cell_stocks.append((index, f"Competent Cell {name}_{offset}", cell_tube))
        cursor += cell_tubes[name]
    media_indexes = tuple(range(cursor, cursor + media_tubes))
    media_stocks = [
        (index, f"Media_{offset}", media_tube)
        for offset, index in enumerate(media_indexes, start=1)
    ]
    cell_moves: list[CellMove] = []
    dna_moves: list[DnaMove] = []
    media_moves: list[MediaMove] = []
    contents: dict[str, list[str]] = {}
    used = dict.fromkeys(chassis, 0)
    destination = starting_well
    for strain in strains:
        for location in range(location_replicates):
            for _replicate in range(replicates):
                tube_index = tube_of[strain.chassis][used[strain.chassis] // per_cell_tube]
                cell_moves.append(CellMove(tube_index, destination, strain.chassis, strain.strain))
                used[strain.chassis] += 1
                for plasmid in strain.plasmids:
                    dna_moves.append(DnaMove(dna_wells[plasmid][location], destination, plasmid))
                destination += 1
    destination = starting_well
    for tube_number, tube_index in enumerate(media_indexes, start=1):
        remaining = total - (tube_number - 1) * per_media_tube
        for _ in range(min(per_media_tube, remaining)):
            media_moves.append(MediaMove(tube_index, destination, f"Media_{tube_number}"))
            destination += 1
    for cell in cell_moves:
        label = contents.setdefault(well_name(cell.destination), [cell.strain])
        label.append(f"Competent_Cell_{cell.chassis}")
    for dna_move in dna_moves:
        contents[well_name(dna_move.destination)].append(dna_move.plasmid)
    for media in media_moves:
        contents[well_name(media.destination)].append(media.name)

    return TransformationLayout(
        strains,
        tuple(cell_moves),
        tuple(dna_moves),
        tuple(media_moves),
        cell_volume,
        dna_volume,
        media_volume,
        cell_tube,
        media_tube,
        dna_stocks,
        tuple(cell_stocks),
        tuple(media_stocks),
        {well: tuple(labels) for well, labels in contents.items()},
        Shock(
            cold_temperature,
            cold_duration,
            heat_temperature,
            heat_duration,
            chill_temperature,
            chill_duration,
            recovery_temperature,
            recovery_duration,
        ),
        plasmid_locations is None,
    )


def record_transformation(
    protocol: Protocol,
    layout: TransformationLayout,
    dna: Plate,
    tubes: Plate,
    products: Plate,
) -> dict[str, tuple[str, ...]]:
    """Append cell, DNA, heat-shock, recovery-medium, and recovery steps."""
    if layout.chill_dna:
        protocol.set_temperature(dna, celsius(4))
    protocol.set_temperature(products, celsius(4))
    for _tube, cell_group in groupby(layout.cell_moves, key=lambda cell: cell.tube_index):
        cells = tuple(cell_group)
        source = well_at(tubes, cells[0].tube_index)
        protocol.mix(source, volume=_CELL_MIX * uL, cycles=3)
        for cell in cells:
            protocol.transfer(
                source, well_at(products, cell.destination), volume=layout.cell_volume * uL
            )
    for dna_move in layout.dna_moves:
        source = dna[dna_move.source_well]
        destination = well_at(products, dna_move.destination)
        protocol.mix(source, volume=layout.dna_volume * uL, cycles=3)
        protocol.transfer(source, destination, volume=layout.dna_volume * uL)
    shock = layout.shock
    protocol.thermocycle(
        products,
        [
            (shock.cold_temperature, shock.cold_duration),
            (shock.heat_temperature, shock.heat_duration),
            (shock.chill_temperature, shock.chill_duration),
        ],
        block_volume=30 * uL,
    )
    for _tube, media_group in groupby(layout.media_moves, key=lambda media: media.tube_index):
        media_moves = tuple(media_group)
        protocol.distribute(
            well_at(tubes, media_moves[0].tube_index),
            [well_at(products, media.destination) for media in media_moves],
            volume=layout.media_volume * uL,
            air_gap=10 * uL,
        )
    protocol.thermocycle(
        products,
        [(shock.recovery_temperature, shock.recovery_duration)],
        block_volume=30 * uL,
    )
    return layout.contents


def build_transformation(
    transformations: Sequence[Transformation],
    plasmid_locations: Mapping[str, Sequence[str]] | None = None,
    *,
    inputs: OutputManifest | None = None,
    name: str = "Heat-shock transformation",
    load_dna: bool = True,
    **params: object,
) -> Protocol:
    """Standalone transformation. Set ``load_dna`` false when the DNA plate is already filled."""
    designs: dict[str, Component] = {}
    if inputs is not None:
        if plasmid_locations is not None:
            raise ValueError("Pass either an input manifest or plasmid_locations.")
        plasmid_locations = _plasmid_locations(inputs)
    for transformation in transformations:
        for part in (transformation.strain, transformation.chassis, *transformation.plasmids):
            label = uri_name(part.identity)
            if label in designs and designs[label].identity != part.identity:
                raise ValueError(f"Two part IRIs extract to the same name {label!r}.")
            designs[label] = part
    layout = layout_transformation(
        [transformation._inputs() for transformation in transformations],
        plasmid_locations,
        **params,  # type: ignore[arg-type]
    )
    protocol = Protocol(name, description="Heat-shock transformation into a thermocycler plate.")
    if plasmid_locations is None:
        dna = protocol.plate("dna", shape=(4, 6), capacity=1500 * uL, dead_volume=0 * uL)
    else:
        dna = protocol.plate("dna", shape=(8, 12), capacity=100 * uL, dead_volume=0 * uL)
    tubes = protocol.plate("tubes", shape=(4, 6), capacity=1500 * uL, dead_volume=0 * uL)
    products = protocol.plate("products", shape=(8, 12), capacity=100 * uL, dead_volume=0 * uL)
    if load_dna:
        for well, material, volume in layout.dna_stocks:
            protocol.load(dna[well], material, volume=volume * uL)
    for index, material, volume in (*layout.cell_stocks, *layout.media_stocks):
        protocol.load(well_at(tubes, index), material, volume=volume * uL)
    _declare_samples(protocol, layout, dna, tubes, products, inputs, designs)
    record_transformation(protocol, layout, dna, tubes, products)
    return protocol


def _plasmid_locations(manifest: OutputManifest) -> dict[str, list[str]]:
    if len({placement.location.resource for placement in manifest.placements}) > 1:
        raise ValueError("Transformation inputs must occupy a single source container.")
    locations = {placement.sample_id: placement.location for placement in manifest.placements}
    result: dict[str, list[str]] = {}
    for sample in manifest.samples:
        result.setdefault(sample.material_identity, []).append(locations[sample.id].well)
    return result


def _declare_samples(
    protocol: Protocol,
    layout: TransformationLayout,
    dna: Plate,
    tubes: Plate,
    products: Plate,
    inputs: OutputManifest | None,
    designs: dict[str, Component],
) -> None:
    upstream = {} if inputs is None else {sample.id: sample for sample in inputs.samples}
    by_well = (
        {}
        if inputs is None
        else {
            placement.location.well: upstream[placement.sample_id]
            for placement in inputs.placements
        }
    )
    dna_ids: dict[str, str] = {}
    for well, material, _volume in layout.dna_stocks:
        if well in dna_ids:
            continue
        design = designs[material]
        sample = Sample(
            id=f"dna-{well}",
            material_identity=design.identity,
            label=design.name or material,
            role="dna",
            design=design.identity,
        )
        if inputs is not None:
            source = by_well[well]
            sample = replace(
                source,
                id=sample.id,
                parent_ids=(),
                role="dna",
                source_sample_id=source.id,
                source_protocol_id=inputs.protocol_id,
            )
        protocol.add_sample(sample, at=dna[well], is_input=True)
        dna_ids[well] = sample.id
    for role, stocks in (("cells", layout.cell_stocks), ("media", layout.media_stocks)):
        for index, material, _volume in stocks:
            chassis = (
                next((cell.chassis for cell in layout.cell_moves if cell.tube_index == index), None)
                if role == "cells"
                else None
            )
            design = designs.get(chassis) if chassis is not None else None
            protocol.add_sample(
                Sample(
                    id=f"{role}-{index}",
                    material_identity=design.identity
                    if design is not None
                    else f"{role}:{material}",
                    design=design.identity if design is not None else None,
                    label=(design.name if design is not None else None) or material,
                    role=role,
                ),
                at=well_at(tubes, index),
                is_input=True,
            )
    for cell in layout.cell_moves:
        design = designs[cell.strain]
        parents = [f"cells-{cell.tube_index}"]
        parents.extend(
            dna_ids[move.source_well]
            for move in layout.dna_moves
            if move.destination == cell.destination
        )
        parents.extend(
            f"media-{move.tube_index}"
            for move in layout.media_moves
            if move.destination == cell.destination
        )
        protocol.add_sample(
            Sample(
                id=f"reaction-{cell.destination}",
                material_identity=design.identity,
                design=design.identity,
                label=design.name or cell.strain,
                parent_ids=tuple(dict.fromkeys(parents)),
                role="reaction",
                contents=layout.contents[well_name(cell.destination)],
            ),
            at=well_at(products, cell.destination),
            is_output=True,
        )


def _strains(
    transformation_data: Sequence[Mapping[str, object]],
    plasmid_locations: Mapping[str, Sequence[str]] | None,
) -> tuple[Strain, ...]:
    strains: list[Strain] = []
    plasmid_uris: dict[str, str] = {}
    chassis_uris: dict[str, str] = {}
    for index, entry in enumerate(transformation_data):
        for field in ("Strain", "Chassis", "Plasmids"):
            if field not in entry:
                raise ValueError(f"Transformation {index} missing {field!r}")
        plasmids = entry["Plasmids"]
        if not isinstance(plasmids, list) or not plasmids:
            raise ValueError(f"Transformation {index}: 'Plasmids' must be a non-empty list")
        strain_uri = str(entry["Strain"])
        chassis_uri = str(entry["Chassis"])
        chassis = uri_name(chassis_uri)
        if chassis in chassis_uris and chassis_uris[chassis] != chassis_uri:
            raise ValueError(f"Two chassis URIs extract to the same name {chassis!r}.")
        chassis_uris[chassis] = chassis_uri
        names: list[str] = []
        uris: list[str] = []
        for plasmid in plasmids:
            plasmid_uri = str(plasmid)
            name = uri_name(plasmid_uri)
            if name in plasmid_uris and plasmid_uris[name] != plasmid_uri:
                raise ValueError(f"Two plasmid URIs extract to the same name {name!r}.")
            plasmid_uris[name] = plasmid_uri
            if plasmid_locations is not None and plasmid_uri not in plasmid_locations:
                raise ValueError(f"Plasmid URI {plasmid_uri!r} is not in plasmid_locations.")
            names.append(name)
            uris.append(plasmid_uri)
        strains.append(Strain(uri_name(strain_uri), chassis, tuple(names), tuple(uris)))
    return tuple(strains)


def _location_replicates(
    strains: Sequence[Strain], plasmid_locations: Mapping[str, Sequence[str]] | None
) -> int:
    if plasmid_locations is None:
        return 1
    counts: dict[str, int] = {}
    for strain in strains:
        for name, uri in zip(strain.plasmids, strain.plasmid_uris, strict=True):
            counts[name] = len(plasmid_locations[uri])
    unique = set(counts.values())
    if len(unique) > 1:
        detail = ", ".join(f"{name}: {count} wells" for name, count in counts.items())
        raise ValueError(f"Plasmid locations must share one replicate count. Found: {detail}")
    if unique == {0}:
        raise ValueError("Each plasmid location list must contain at least one well.")
    return unique.pop() if unique else 1


def _dna_wells(
    strains: Sequence[Strain],
    plasmids: Sequence[str],
    plasmid_locations: Mapping[str, Sequence[str]] | None,
    initial_dna_well: int,
    dna_rows: int,
) -> dict[str, tuple[str, ...]]:
    if plasmid_locations is None:
        return {
            name: (well_name(initial_dna_well + index, dna_rows),)
            for index, name in enumerate(plasmids)
        }
    uri_of = {
        name: uri
        for strain in strains
        for name, uri in zip(strain.plasmids, strain.plasmid_uris, strict=True)
    }
    return {name: tuple(plasmid_locations[uri_of[name]]) for name in plasmids}
