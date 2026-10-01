"""Golden Gate assembly recorded as ordinary protocol steps.

Well order, reagent order, volumes, and the thermal program follow the OT-2
loop-assembly protocols: SBOL assemblies, domestication, and odd/even
combinatorial assemblies. Each transfer is one source aliquot. A three-cycle
mix of that aliquot precedes every addition except water. After the parts are
in, the destination is mixed to clear the tip, then the plate runs 75 cycles
of 42 °C / 16 °C, a 60 °C and 80 °C inactivation, and a 4 °C hold.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from fnmatch import fnmatch
from itertools import product

from lab.experiments.cloning.addresses import microliters, uri_name, well_at
from lab.experiments.cloning.types import AssemblyRequest
from lab.protocol import Plate, Protocol, Well
from lab.samples import Sample
from lab.units import celsius, minutes, uL

WATER = "Deionized Water"
BUFFER = "T4 DNA Ligase Buffer"
LIGASE = "T4 DNA Ligase"
_PIPETTE_MAX = Decimal(20)


@dataclass(frozen=True)
class AssemblyVolumes:
    total: Decimal
    part: Decimal
    enzyme: Decimal
    ligase: Decimal
    buffer: Decimal

    @property
    def fixed_reagents(self) -> Decimal:
        return self.enzyme + self.ligase + self.buffer

    def water(self, dna_components: int) -> Decimal:
        needed = self.fixed_reagents + self.part * dna_components
        if needed >= self.total:
            raise ValueError(
                f"Reaction volume error: cannot fit {dna_components} DNA components into "
                f"{self.total} µL. Reagents take {self.fixed_reagents} µL and DNA takes "
                f"{self.part * dna_components} µL."
            )
        return self.total - needed


@dataclass(frozen=True)
class AssemblyReaction:
    destination: int
    product_key: str
    additions: tuple[tuple[str, Decimal, bool], ...]


@dataclass(frozen=True)
class AssemblyLayout:
    total: Decimal
    positions: dict[str, int]
    reactions: tuple[AssemblyReaction, ...]
    products: dict[str, tuple[int, ...]]

    @property
    def stocks(self) -> tuple[tuple[int, str, Decimal], ...]:
        totals: dict[str, Decimal] = {}
        for reaction in self.reactions:
            for material, volume, _mix in reaction.additions:
                totals[material] = totals.get(material, Decimal(0)) + volume
        return tuple(
            (index, material, totals[material]) for material, index in self.positions.items()
        )


def layout_assembly(
    assemblies: Sequence[Mapping[str, object]],
    *,
    volume_total_reaction: object = 20 * uL,
    volume_part: object = 2 * uL,
    volume_restriction_enzyme: object = 2 * uL,
    volume_t4_dna_ligase: object = 4 * uL,
    volume_t4_dna_ligase_buffer: object = 2 * uL,
    replicates: int = 1,
    starting_well: int = 0,
) -> AssemblyLayout:
    """Assign reagent wells and destination wells for one assembly format."""
    if not assemblies:
        raise ValueError("No assemblies provided")
    if type(replicates) is not int or replicates < 1:
        raise ValueError("Replicates must be a positive integer")
    if type(starting_well) is not int or starting_well < 0:
        raise ValueError("Starting well must be a non-negative integer")
    volumes = AssemblyVolumes(
        microliters(volume_total_reaction),
        microliters(volume_part),
        microliters(volume_restriction_enzyme),
        microliters(volume_t4_dna_ligase),
        microliters(volume_t4_dna_ligase_buffer),
    )
    kind = _kind(assemblies[0])
    if kind == "sbol":
        return _sbol(assemblies, volumes, replicates, starting_well)
    if kind == "domestication":
        return _domestication(assemblies, volumes, replicates, starting_well)
    return _manual(assemblies, volumes, replicates, starting_well)


def record_assembly(
    protocol: Protocol, layout: AssemblyLayout, reagents: Plate, products: Plate
) -> dict[str, tuple[Well, ...]]:
    """Append the assembly transfers and thermal program. Stocks are already loaded."""
    protocol.set_temperature(reagents, celsius(4))
    protocol.set_temperature(products, celsius(4))
    for reaction in layout.reactions:
        destination = well_at(products, reaction.destination)
        for material, volume, mix in reaction.additions:
            source = well_at(reagents, layout.positions[material])
            amount = volume * uL
            if mix:
                protocol.mix(source, volume=amount, cycles=3)
            protocol.transfer(source, destination, volume=amount)
        cycles = int(layout.total / Decimal(10))
        if cycles:
            protocol.mix(destination, volume=min(layout.total, _PIPETTE_MAX) * uL, cycles=cycles)
    profile = [(celsius(42), 2 * minutes), (celsius(16), 5 * minutes)]
    inactivation = [(celsius(60), 10 * minutes), (celsius(80), 10 * minutes)]
    protocol.thermocycle(
        products, profile, cycles=75, lid_temperature=celsius(42), block_volume=30 * uL
    )
    protocol.thermocycle(
        products, inactivation, cycles=1, lid_temperature=celsius(42), block_volume=30 * uL
    )
    protocol.set_temperature(products, celsius(4))
    return {
        key: tuple(well_at(products, index) for index in indexes)
        for key, indexes in layout.products.items()
    }


def build_assembly(
    assemblies: AssemblyRequest | Sequence[Mapping[str, object]],
    *,
    name: str = "Loop assembly",
    **params: object,
) -> Protocol:
    """A standalone assembly protocol whose plates are named ``reagents`` and ``products``."""
    designs: dict[str, str] = {}
    if isinstance(assemblies, AssemblyRequest):
        name = assemblies.id
        for assembly in assemblies.assemblies:
            designs[assembly.product.iri] = assembly.product.iri
            for part in (assembly.backbone, *assembly.parts):
                label = uri_name(part.iri)
                if label in designs and designs[label] != part.iri:
                    raise ValueError(f"Two part IRIs extract to the same name {label!r}.")
                designs[label] = part.iri
        assemblies = [
            {
                "Product": assembly.product.iri,
                "Backbone": assembly.backbone.iri,
                "PartsList": [part.iri for part in assembly.parts],
                "Restriction Enzyme": assembly.restriction_enzyme.iri,
            }
            for assembly in assemblies.assemblies
        ]
    layout = layout_assembly(assemblies, **params)  # type: ignore[arg-type]
    protocol = Protocol(name, description="Golden Gate assembly on a thermocycler plate.")
    reagents = protocol.plate("reagents", shape=(4, 6), capacity=1500 * uL, dead_volume=0 * uL)
    products = protocol.plate("products", shape=(8, 12), capacity=100 * uL, dead_volume=0 * uL)
    stock_ids: dict[str, str] = {}
    for index, material, volume in layout.stocks:
        protocol.load(well_at(reagents, index), material, volume=volume * uL)
        sample = Sample(
            id=f"stock-{index}",
            material_identity=designs.get(material, material),
            label=material,
            role="stock",
            design=designs.get(material),
        )
        protocol.add_sample(sample, at=well_at(reagents, index), is_input=True)
        stock_ids[material] = sample.id
    for reaction in layout.reactions:
        protocol.add_sample(
            Sample(
                id=f"product-{reaction.destination}",
                material_identity=reaction.product_key,
                design=designs.get(reaction.product_key),
                label=uri_name(reaction.product_key),
                parent_ids=tuple(
                    dict.fromkeys(stock_ids[material] for material, _, _ in reaction.additions)
                ),
                role="product",
                replicate=layout.products[reaction.product_key].index(reaction.destination) + 1,
            ),
            at=well_at(products, reaction.destination),
            is_output=True,
        )
    record_assembly(protocol, layout, reagents, products)
    return protocol


def _kind(assembly: Mapping[str, object]) -> str:
    keys = set(assembly)
    sbol = {"Product", "Backbone", "PartsList", "Restriction Enzyme"}
    if sbol.issubset(keys):
        return "sbol"
    if {"parts", "backbone", "restriction_enzyme"}.issubset(keys):
        return "domestication"
    if "receiver" in keys and keys - {"receiver"}:
        return "manual"
    raise ValueError(
        "Unknown assembly format. Expected SBOL keys, a domestication parts/backbone/"
        f"restriction_enzyme entry, or a manual receiver. Found keys: {sorted(keys)}"
    )


def _sbol(
    assemblies: Sequence[Mapping[str, object]],
    volumes: AssemblyVolumes,
    replicates: int,
    starting_well: int,
) -> AssemblyLayout:
    parsed: list[tuple[str, str, tuple[str, ...]]] = []
    enzymes: set[str] = set()
    parts: set[str] = set()
    for assembly in assemblies:
        enzyme = uri_name(str(assembly["Restriction Enzyme"]))
        backbone = uri_name(str(assembly["Backbone"]))
        part_list = assembly["PartsList"]
        if not isinstance(part_list, list) or not part_list:
            raise ValueError("SBOL PartsList must be a non-empty list")
        part_names = tuple(uri_name(str(part)) for part in part_list)
        enzymes.add(enzyme)
        parts.add(backbone)
        parts.update(part_names)
        parsed.append((str(assembly["Product"]), enzyme, (backbone, *part_names)))
    _fits(3 + len(enzymes), len(parts), starting_well, len(parsed) * replicates)
    positions = _positions(
        [f"Restriction Enzyme {name}" for name in sorted(enzymes)], sorted(parts)
    )
    reactions: list[AssemblyReaction] = []
    products: dict[str, list[int]] = {}
    destination = starting_well
    for product_uri, enzyme, dna in parsed:
        for _ in range(replicates):
            reactions.append(
                AssemblyReaction(
                    destination,
                    product_uri,
                    _additions(volumes, len(dna), f"Restriction Enzyme {enzyme}", dna),
                )
            )
            products.setdefault(product_uri, []).append(destination)
            destination += 1
    return AssemblyLayout(
        volumes.total,
        positions,
        tuple(reactions),
        {key: tuple(indexes) for key, indexes in products.items()},
    )


def _domestication(
    assemblies: Sequence[Mapping[str, object]],
    volumes: AssemblyVolumes,
    replicates: int,
    starting_well: int,
) -> AssemblyLayout:
    if len(assemblies) != 1:
        raise ValueError(f"Domestication supports exactly one assembly, got {len(assemblies)}")
    assembly = assemblies[0]
    parts = _one_or_many(assembly["parts"], "Parts")
    backbone = _single(assembly["backbone"], "Domestication supports only one backbone")
    enzyme = _single(
        assembly["restriction_enzyme"], "Domestication supports only one restriction enzyme"
    )
    if not parts:
        raise ValueError("No parts provided for domestication")
    _fits(5, len(parts), starting_well, len(parts) * replicates)
    enzyme_label = f"Restriction Enzyme {enzyme}"
    positions = _positions([enzyme_label], [])
    # Backbone and parts keep input order and are not sorted.
    index = len(positions)
    positions[f"Backbone {backbone}"] = index
    index += 1
    for part in parts:
        positions[f"Part {part}"] = index
        index += 1
    reactions: list[AssemblyReaction] = []
    products: dict[str, list[int]] = {}
    destination = starting_well
    for part in parts:
        dna = (f"Backbone {backbone}", f"Part {part}")
        for _ in range(replicates):
            reactions.append(
                AssemblyReaction(
                    destination,
                    part,
                    _additions(volumes, 2, f"Restriction Enzyme {enzyme}", dna),
                )
            )
            products.setdefault(part, []).append(destination)
            destination += 1
    return AssemblyLayout(
        volumes.total,
        positions,
        tuple(reactions),
        {key: tuple(indexes) for key, indexes in products.items()},
    )


def _manual(
    assemblies: Sequence[Mapping[str, object]],
    volumes: AssemblyVolumes,
    replicates: int,
    starting_well: int,
) -> AssemblyLayout:
    odd: list[tuple[str, ...]] = []
    even: list[tuple[str, ...]] = []
    parts: set[str] = set()
    for assembly in assemblies:
        receiver = str(assembly["receiver"])
        if isinstance(assembly["receiver"], list):
            receiver_values = _one_or_many(assembly["receiver"], "receiver")
        else:
            receiver_values = [receiver]
        # Role order is the mapping order, including the receiver, matching itertools.product.
        groups: list[list[str]] = []
        for role, value in assembly.items():
            values = _one_or_many(value, role)
            parts.update(values)
            groups.append(values)
        combinations = [tuple(combo) for combo in product(*groups)]
        if fnmatch(receiver, "Odd*") or any(fnmatch(item, "Odd*") for item in receiver_values):
            odd.extend(combinations)
        elif fnmatch(receiver, "Even*") or any(fnmatch(item, "Even*") for item in receiver_values):
            even.extend(combinations)
        else:
            raise ValueError(f"Assembly receiver {receiver!r} must match 'Odd*' or 'Even*'.")
    if not odd and not even:
        raise ValueError("Assembly does not have any Even or Odd receiver.")
    enzyme_labels = []
    if odd:
        enzyme_labels.append("Restriction Enzyme BSAI")
    if even:
        enzyme_labels.append("Restriction Enzyme SAPI")
    _fits(3 + len(enzyme_labels), len(parts), starting_well, (len(odd) + len(even)) * replicates)
    positions = _positions(enzyme_labels, sorted(parts))
    reactions: list[AssemblyReaction] = []
    products: dict[str, list[int]] = {}
    destination = starting_well

    def add(combinations: list[tuple[str, ...]], enzyme: str) -> None:
        nonlocal destination
        for combination in combinations:
            key = "_".join(combination)
            for _ in range(replicates):
                reactions.append(
                    AssemblyReaction(
                        destination,
                        key,
                        _additions(volumes, len(combination), enzyme, combination),
                    )
                )
                products.setdefault(key, []).append(destination)
                destination += 1

    if odd:
        add(odd, "Restriction Enzyme BSAI")
    if even:
        add(even, "Restriction Enzyme SAPI")
    return AssemblyLayout(
        volumes.total,
        positions,
        tuple(reactions),
        {key: tuple(indexes) for key, indexes in products.items()},
    )


def _additions(
    volumes: AssemblyVolumes, dna_components: int, enzyme: str, dna: Sequence[str]
) -> tuple[tuple[str, Decimal, bool], ...]:
    water = volumes.water(dna_components)
    return (
        (WATER, water, False),
        (BUFFER, volumes.buffer, True),
        (LIGASE, volumes.ligase, True),
        (enzyme, volumes.enzyme, True),
        *[(name, volumes.part, True) for name in dna],
    )


def _positions(enzymes: Sequence[str], parts: Sequence[str]) -> dict[str, int]:
    names = [WATER, BUFFER, LIGASE, *enzymes, *parts]
    if len(names) > 24:
        raise ValueError(f"Reagents and parts need {len(names)} wells; the block has 24.")
    return {name: index for index, name in enumerate(names)}


def _fits(reagent_wells: int, part_count: int, starting_well: int, destinations: int) -> None:
    if part_count > 24 - reagent_wells:
        raise ValueError(
            f"This protocol supports up to {24 - reagent_wells} parts and was given {part_count}."
        )
    available = 96 - starting_well
    if destinations > available:
        raise ValueError(
            f"The thermocycler has {available} wells from index {starting_well}; "
            f"{destinations} reactions were requested."
        )


def _one_or_many(value: object, label: str) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return list(value)
    raise ValueError(f"{label} must be a string or a list of strings")


def _single(value: object, message: str) -> str:
    if isinstance(value, list):
        if len(value) != 1:
            raise ValueError(message)
        return str(value[0])
    return str(value)
