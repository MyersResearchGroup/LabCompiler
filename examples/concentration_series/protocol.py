"""Record preparation operations from validated, prepared inputs."""

from collections.abc import Sequence

from examples.concentration_series.inputs import PreparedWell, ProtocolConfig
from lab import HamiltonLiquidClass, LiquidClass, Protocol, Quantity, Well, uL
from lab.config import PortableLiquidClassConfig
from lab.liquid_classes import LiquidClassSpec
from lab.samples import Sample


def protocol(prepared_wells: Sequence[PreparedWell], config: ProtocolConfig) -> Protocol:
    """Add each reagent across the plate before inoculation and final mixing."""

    # Resolve named configuration entries into the Lab settings used below.
    liquid_classes: dict[str, LiquidClassSpec] = {
        name: LiquidClass(
            name,
            aspiration_flow_rate=spec.aspiration_flow_rate,
            dispense_flow_rate=spec.dispense_flow_rate,
        )
        if isinstance(spec, PortableLiquidClassConfig)
        else HamiltonLiquidClass(spec.name)
        for name, spec in config.liquid_classes.items()
    }
    p = Protocol(
        "CSV culture preparation",
        description=f"Prepare {len(prepared_wells)} culture wells "
        f"at {config.final_volume:~P} each. "
        "Concentrations refer to final volume after inoculation.\n\n"
        f"Protocol configuration: {config.model_dump_json(indent=2)}",
    )

    # Declare the source plate for prepared stocks, vehicle, and inocula.
    reagents = p.plate("reagents", capacity=360 * uL, dead_volume=config.source_dead_volume)

    # Declare the culture plate; each selected well holds one experimental condition.
    destinations = p.plate("cultures", capacity=360 * uL)

    # Record the available base medium used to bring cultures to their final volume.
    medium = p.container(
        "medium",
        contents=config.medium.name,
        volume=config.medium.volume,
        capacity=15000 * uL,
        dead_volume=config.medium.dead_volume,
    )

    # Record the supplied vehicle used to balance the solvent fraction across conditions.
    p.load(reagents[config.vehicle.well], config.vehicle.name, volume=config.vehicle.volume)
    for stock in config.stocks:

        # Record each prepared solute stock, including its solvent contribution.
        p.load(
            reagents[stock.well],
            f"{stock.solute}: {stock.concentration}; "
            f"{config.vehicle.name} {stock.vehicle_fraction} v/v",
            volume=stock.volume,
        )
    for culture in config.cultures:

        # Record the supplied inoculum for this strain/plasmid pair.
        p.load(
            reagents[culture.well], f"{culture.strain} / {culture.plasmid}", volume=culture.volume
        )

        # Identify the parent culture so prepared wells retain their biological lineage.
        p.add_sample(
            Sample(
                id=f"inoculum-{culture.well}",
                material_identity=culture.plasmid,
                label=f"{culture.strain} / {culture.plasmid}",
            ),
            at=reagents[culture.well],
            is_input=True,
        )

    def add_across_plate(
        source: Well, doses: Sequence[tuple[Well, Quantity]], liquid_class: LiquidClassSpec
    ) -> None:
        """Share a tip across equal doses; retain individual transfers for unique doses."""
        groups: dict[Quantity, list[Well]] = {}
        for destination, dose in doses:
            if dose.magnitude:
                groups.setdefault(dose, []).append(destination)
        for dose, wells in groups.items():
            if len(wells) == 1:

                # Supply a condition-specific dose from this source.
                p.transfer(source, wells[0], volume=dose, liquid_class=liquid_class)
            else:

                # Supply the same reagent dose to these conditions with one tip.
                p.distribute(source, wells, volume=dose, liquid_class=liquid_class)

    # Establish the medium volume in every selected well before adding other reagents.
    add_across_plate(
        medium,
        [(destinations[prepared.well], prepared.medium_volume) for prepared in prepared_wells],
        liquid_classes[config.medium.liquid_class],
    )

    # Balance the solvent fraction across the selected conditions.
    add_across_plate(
        reagents[config.vehicle.well],
        [(destinations[prepared.well], prepared.vehicle_volume) for prepared in prepared_wells],
        liquid_classes[config.vehicle.liquid_class],
    )
    for stock in config.stocks:

        # Add this working stock to every condition that uses it before changing sources.
        add_across_plate(
            reagents[stock.well],
            [
                (destinations[prepared.well], dose)
                for prepared in prepared_wells
                for selected, dose in prepared.stock_volumes
                if selected.well == stock.well
            ],
            liquid_classes[stock.liquid_class],
        )

    for prepared in prepared_wells:
        well, culture = prepared.well, prepared.culture
        destination = destinations[well]

        # Resuspend the source culture before withdrawing inoculum.
        p.mix(
            reagents[culture.well],
            volume=config.source_mix.volume,
            cycles=config.source_mix.cycles,
            liquid_class=liquid_classes[config.source_mix.liquid_class],
        )

        # Inoculate this condition with the specified culture volume.
        p.transfer(
            reagents[culture.well],
            destination,
            volume=config.inoculum_volume,
            liquid_class=liquid_classes[culture.liquid_class],
        )

        # Mix cells and reagents to homogenize the final culture.
        p.mix(
            destination,
            volume=config.destination_mix.volume,
            cycles=config.destination_mix.cycles,
            liquid_class=liquid_classes[config.destination_mix.liquid_class],
        )

        # Record this well's condition and link it to the source inoculum.
        p.add_sample(
            Sample(
                id=f"culture-{well}",
                material_identity=culture.plasmid,
                label=f"{well}: {culture.strain} / {culture.plasmid}; "
                + ", ".join(f"{name} {value}" for name, value in prepared.concentrations.items()),
                parent_ids=(f"inoculum-{culture.well}",),
            ),
            at=destination,
            is_output=True,
        )
    return p
