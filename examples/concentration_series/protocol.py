"""Record preparation operations for a plate design using the configured sources."""

from examples.concentration_series.inputs import ProtocolConfig, liquid_classes, sources
from lab import Protocol, uL
from lab.plates import AssemblyReport, PlateDesign, assemble
from lab.samples import Sample


def protocol(design: PlateDesign, config: ProtocolConfig) -> tuple[Protocol, AssemblyReport]:
    """Prepare every well of ``design`` before inoculation and final mixing."""
    classes = liquid_classes(config)
    p = Protocol(
        "CSV culture preparation",
        description=f"Prepare {len(design.wells)} culture wells "
        f"at {config.final_volume:~P} each. "
        "Concentrations refer to final volume after inoculation.\n\n"
        f"Protocol configuration: {config.model_dump_json(indent=2)}",
    )
    supplied = sources(p, config, classes)

    # Declare the culture plate; each selected well holds one experimental condition.
    destinations = p.plate("cultures", shape=design.shape, capacity=360 * uL)

    # Identify each parent culture so prepared wells retain their biological lineage.
    for material, well in supplied.inocula.items():
        culture = supplied.culture_wells[material]
        p.add_sample(
            Sample(
                id=f"inoculum-{culture.well}",
                material_identity=culture.plasmid,
                label=material,
            ),
            at=well,
            is_input=True,
        )

    report = assemble(
        p,
        design,
        plate=destinations,
        stocks=supplied.stocks,
        medium=supplied.medium,
        diluent=supplied.diluent,
        inocula=supplied.inocula,
        settings=config.settings(classes),
        vehicle=supplied.vehicle,
        waste=supplied.waste,
        medium_class=classes[config.medium.liquid_class],
        vehicle_class=classes[config.vehicle.liquid_class],
        culture_class=next((classes[c.liquid_class] for c in config.cultures), None),
    )

    # Record each well's condition and link it to the source inoculum.
    for plan in report.wells:
        target = plan.target
        assert target.material is not None
        culture = supplied.culture_wells[target.material]
        p.add_sample(
            Sample(
                id=f"culture-{target.well}",
                material_identity=culture.plasmid,
                label=f"{target.well}: {target.material}; "
                + ", ".join(f"{t.solute} {t.quantity:~P}" for t in target.targets),
                parent_ids=(f"inoculum-{culture.well}",),
            ),
            at=destinations[target.well],
            is_output=True,
        )
    return p, report
