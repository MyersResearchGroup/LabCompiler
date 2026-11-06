"""Plate designs, pattern detection, serial dilution arithmetic, premixes, and arrangement."""

from decimal import Decimal

import pytest

from lab import Protocol, compile, uL, uM
from lab.model import Distribute, Mix, MultiMix, MultiTransfer, Transfer
from lab.plates import (
    ConditionSpec,
    ControlSpec,
    GradientSpec,
    PlateDesign,
    Requirements,
    Settings,
    Stock,
    Strategy,
    Target,
    WellTarget,
    arrange,
    assemble,
    find_replicate_groups,
    find_serial_dilutions,
    infer_requirements,
    parallelize,
    plan_premix,
    plan_serial_dilution,
    record_premix,
    record_serial_dilution,
    well_target,
)
from lab.plates.design import canonical_concentration
from lab.samples import Location
from lab.units import units

UG_ML = units("ug/mL")


def gradient_design(*, zero: bool = False, sal: tuple[Decimal, Decimal] = (Decimal(10),) * 2):
    """Two row pairs with a 2-fold Van series from column 12 down to column 5."""
    wells = []
    for row, plasmid, level in (("A", "p1", 0), ("B", "p1", 1), ("C", "p2", 0), ("D", "p2", 1)):
        for column in range(5, 13):
            van = Decimal(100) / Decimal(2) ** (12 - column)
            wells.append(
                well_target(
                    f"{row}{column}",
                    material=f"strain / {plasmid}",
                    concentrations={"Van": van * uM, "Sal": sal[level] * uM, "TMP": 3 * UG_ML},
                )
            )
        if zero:
            wells.append(
                well_target(
                    f"{row}4",
                    material=f"strain / {plasmid}",
                    concentrations={"Van": 0 * uM, "Sal": sal[level] * uM, "TMP": 3 * UG_ML},
                )
            )
    return PlateDesign.build("cultures", final_volume=200 * uL, wells=wells)


def test_design_normalizes_units_sorts_targets_and_rejects_duplicates():
    assert canonical_concentration(100 * uM) == (Decimal("0.0001"), "mole/liter")
    assert canonical_concentration(3 * UG_ML) == (Decimal("0.003"), "gram/liter")
    with pytest.raises(ValueError, match="mass/volume or amount/volume"):
        canonical_concentration(3 * uL)
    target = well_target("A1", concentrations={"Van": 1 * uM, "Sal": 2 * uM})
    assert [t.solute for t in target.targets] == ["Sal", "Van"]
    assert target.context(excluding="Van") == (None, (("Sal", Decimal("0.000002"), "mole/liter"),))
    with pytest.raises(ValueError, match="one target"):
        WellTarget(
            well="A1",
            material=None,
            targets=(
                Target(solute="Van", magnitude=Decimal(1), unit="mole/liter"),
                Target(solute="Van", magnitude=Decimal(2), unit="mole/liter"),
            ),
        )
    with pytest.raises(ValueError, match="specified twice"):
        PlateDesign.build("p", final_volume=1 * uL, wells=[target, target])
    with pytest.raises(ValueError, match="not a well of a 8x12"):
        PlateDesign.build("p", final_volume=1 * uL, wells=[well_target("I1", concentrations={})])
    design = gradient_design()
    assert design.solutes == ("Sal", "TMP", "Van")
    assert design.materials == ("strain / p1", "strain / p2")
    assert [w.well for w in design.row("A")] == [f"A{c}" for c in range(5, 13)]
    assert [w.well for w in design.column(12)] == ["A12", "B12", "C12", "D12"]
    assert "A12" in design and "H1" not in design


def test_detects_gradients_along_rows_and_merges_them_into_columns():
    design = gradient_design(zero=True, sal=(Decimal(10), Decimal(25)))
    found = find_serial_dilutions(design, "Van")
    assert [(c.levels[0][0], c.levels[-1][0], c.steps, c.fold, c.zero_wells) for c in found] == [
        ("A12", "A5", 8, Decimal(2), ("A4",)),
        ("B12", "B5", 8, Decimal(2), ("B4",)),
        ("C12", "C5", 8, Decimal(2), ("C4",)),
        ("D12", "D5", 8, Decimal(2), ("D4",)),
    ]
    assert found[0].top == Decimal("0.0001") and found[0].unit == "mole/liter"
    assert found[0].concentration(7) == Decimal("0.00000078125")
    merged = parallelize(found)
    assert len(merged) == 1
    assert merged[0].axis == "column" and merged[0].width == 4
    assert merged[0].levels[0] == ("A12", "B12", "C12", "D12")
    assert merged[0].zero_wells == ("A4", "B4", "C4", "D4")
    assert find_serial_dilutions(design, "Sal") == ()
    assert find_serial_dilutions(design, "Van", axis="column") == ()
    scattered = find_serial_dilutions(design, "Van", axis="set")
    assert {(c.steps, c.width) for c in scattered} == {(8, 1)}
    assert len(scattered) == 4
    with pytest.raises(ValueError, match="single-width row"):
        parallelize(merged)


def test_detection_tolerates_rounded_ratios_and_requires_min_steps():
    sevenths = [
        Decimal("14.2857142857143"),
        Decimal("7.14285714285714"),
        Decimal("3.57142857142857"),
    ]
    design = PlateDesign.build(
        "p",
        final_volume=200 * uL,
        wells=[
            well_target(f"A{i + 1}", concentrations={"Sal": v * uM, "Van": 0 * uM})
            for i, v in enumerate(sevenths)
        ]
        + [well_target("A4", concentrations={"Sal": 1 * uM, "Van": 5 * uM})],
    )
    (candidate,) = find_serial_dilutions(design, "Sal")
    assert candidate.fold == Decimal(2) and candidate.steps == 3
    assert candidate.levels == (("A1",), ("A2",), ("A3",))
    assert find_serial_dilutions(design, "Sal", min_steps=4) == ()
    pair = PlateDesign.build(
        "p",
        final_volume=200 * uL,
        wells=[
            well_target("B5", concentrations={"Van": 20 * uM}),
            well_target("B6", concentrations={"Van": 100 * uM}),
        ],
    )
    assert find_serial_dilutions(pair, "Van") == ()
    (five_fold,) = find_serial_dilutions(pair, "Van", min_steps=2)
    assert five_fold.fold == Decimal(5) and five_fold.levels == (("B6",), ("B5",))


def test_replicate_groups_ignore_material():
    groups = find_replicate_groups(gradient_design())
    assert len(groups) == 8
    assert all(len(g.wells) == 4 for g in groups)
    assert {g.materials for g in groups} == {("strain / p1",) * 2 + ("strain / p2",) * 2}


def test_serial_dilution_arithmetic_and_concentrate_factor():
    (candidate,) = parallelize(find_serial_dilutions(gradient_design(), "Van"))
    with pytest.raises(ValueError, match="use concentrate >= 1.12"):
        plan_serial_dilution(
            candidate, stock_concentration=1000 * uM, final_volume=200 * uL, capacity=360 * uL
        )
    plan = plan_serial_dilution(
        candidate,
        stock_concentration=1000 * uM,
        final_volume=200 * uL,
        capacity=360 * uL,
        reserved=10 * uL,
        concentrate=Decimal(2),
    )
    assert (plan.gradient_volume_ul, plan.transfer_volume_ul, plan.peak_volume_ul) == (
        100,
        100,
        200,
    )
    assert plan.top_stock_ul == 40 and plan.top_diluent_ul == 160
    assert plan.moves == 8 * 4
    with pytest.raises(ValueError, match="exceeds the stock"):
        plan_serial_dilution(
            candidate,
            stock_concentration=100 * uM,
            final_volume=200 * uL,
            capacity=360 * uL,
            concentrate=Decimal(2),
        )
    with pytest.raises(ValueError, match="later additions"):
        plan_serial_dilution(
            candidate,
            stock_concentration=1000 * uM,
            final_volume=200 * uL,
            capacity=360 * uL,
            reserved=150 * uL,
            concentrate=Decimal(2),
        )


def test_recorded_gradient_leaves_every_well_at_the_gradient_volume():
    design = gradient_design(zero=True)
    (candidate,) = parallelize(find_serial_dilutions(design, "Van"))
    plan = plan_serial_dilution(
        candidate,
        stock_concentration=1000 * uM,
        final_volume=200 * uL,
        capacity=360 * uL,
        concentrate=Decimal(2),
    )
    p = Protocol("gradient")
    plate = p.plate("cultures", capacity=360 * uL)
    stock = p.container("van", contents="Van 1000 uM", volume=1000 * uL, capacity=1500 * uL)
    medium = p.container("medium", contents="medium", volume=15000 * uL, capacity=15000 * uL)
    waste = p.container("waste", capacity=15000 * uL)
    record_serial_dilution(p, plan, plate=plate, stock=stock, diluent=medium, waste=waste)
    final = dict(compile(p, to=None).final_volumes)
    for well in (*candidate.wells, *candidate.zero_wells):
        assert final[Location("cultures", well)] == 100
    assert final[Location("waste", "A1")] == 400
    assert final[Location("van", "A1")] == 1000 - 4 * 40
    # Each level moves as one four-lane set: seven column-to-column strokes plus one to waste.
    moves = [s for s in p.steps if isinstance(s, MultiTransfer)]
    assert len(moves) == 8 and all(len(s.destinations) == 4 for s in moves)
    assert {s.volume for s in moves} == {Decimal(100)}
    assert moves[-1].destinations == (Location("waste", "A1"),) * 4
    mixes = [s for s in p.steps if isinstance(s, MultiMix)]
    assert len(mixes) == 8 and all(m.volume == 100 and m.cycles == 3 for m in mixes)
    assert isinstance(p.steps[0], Distribute) and p.steps[0].volume == 160
    assert isinstance(p.steps[2], Distribute) and p.steps[2].volume == 40
    assert not any(isinstance(s, (Transfer, Mix)) for s in p.steps)


def test_premix_scales_components_and_shares_one_portion():
    design = gradient_design()
    group = next(g for g in find_replicate_groups(design) if "A12" in g.wells)
    p = Protocol("premix")
    plate = p.plate("cultures", capacity=360 * uL)
    tubes = p.plate("tubes", shape=(4, 6), capacity=1500 * uL)
    medium = p.container("medium", contents="medium", volume=15000 * uL, capacity=15000 * uL)
    tmp = p.container("tmp", contents="TMP", volume=1000 * uL, capacity=1500 * uL)
    plan = plan_premix(
        group,
        [(medium, 146 * uL, None), (tmp, 2 * uL, None)],
        tube_capacity=p.capacity(tubes["A1"]),
    )
    assert plan.portion_ul == 148 and plan.total_ul == Decimal("651.2")
    assert plan.component_volume(plan.components[1]) == Decimal("8.8")
    record_premix(p, plan, tube=tubes["A1"], plate=plate)
    final = dict(compile(p, to=None).final_volumes)
    assert all(final[Location("cultures", w)] == 148 for w in group.wells)
    assert final[Location("tubes", "A1")] == Decimal("651.2") - 4 * 148
    with pytest.raises(ValueError, match="exceeds the 1500"):
        plan_premix(group, [(medium, 400 * uL, None)], tube_capacity=1500 * uL)


def test_arrange_reproduces_a_row_wise_gradient_layout_and_reports_overflow():
    requirements = Requirements(
        name="cultures",
        final_volume=200 * uL,
        materials=("strain / p1", "strain / p2"),
        conditions=(
            ConditionSpec(label="Sal 10", concentrations={"Sal": 10 * uM, "TMP": 3 * UG_ML}),
            ConditionSpec(label="Sal 25", concentrations={"Sal": 25 * uM, "TMP": 3 * UG_ML}),
        ),
        gradient=GradientSpec(solute="Van", top=100 * uM, fold=Decimal(2), steps=8),
        controls=(ControlSpec(label="blank", concentrations={"Van": 0 * uM, "Sal": 0 * uM}),),
    )
    assert requirements.well_count == 2 * 2 * 8 + 2
    arrangement = arrange(requirements)
    design = arrangement.design
    assert design["A12"].concentration("Van").magnitude == Decimal("0.0001")
    assert design["A5"].concentration("Van").magnitude == Decimal("0.00000078125")
    assert design["B12"].material == "strain / p1"
    assert design["B12"].concentration("Sal").magnitude == Decimal("0.000025")
    assert design["C12"].material == "strain / p2"
    assert {w.well for w in design.wells if w.concentration("TMP") is None} == {"A1", "B1"}
    assert arrangement.rows[:2] == (("A", "strain / p1", "Sal 10"), ("B", "strain / p1", "Sal 25"))
    assert len(arrangement.unused_wells) == 96 - requirements.well_count
    merged = parallelize(find_serial_dilutions(design, "Van"))
    assert [(c.width, c.steps) for c in merged] == [(4, 8)]
    with pytest.raises(ValueError, match=r"\(32 over\)"):
        arrange(
            Requirements(
                name="too many",
                final_volume=200 * uL,
                materials=tuple(f"m{i}" for i in range(8)),
                conditions=requirements.conditions,
                gradient=requirements.gradient,
            )
        )
    with pytest.raises(ValueError, match="fixes Van"):
        Requirements(
            name="x",
            final_volume=200 * uL,
            materials=("m",),
            conditions=(ConditionSpec(label="c", concentrations={"Van": 1 * uM}),),
            gradient=requirements.gradient,
        )
    with pytest.raises(ValueError, match="below 2"):
        GradientSpec(solute="Van", top=1 * uM, fold=Decimal("1.5"), steps=3)


def _sources(p: Protocol):
    reagents = p.plate("reagents", shape=(1, 12), capacity=15000 * uL, dead_volume=500 * uL)
    plate = p.plate("cultures", capacity=360 * uL)
    medium = p.container(
        "medium", contents="medium", volume=15000 * uL, capacity=15000 * uL, dead_volume=500 * uL
    )
    waste = p.container("waste", capacity=15000 * uL)
    p.load(reagents["A1"], "Van 100 uM", volume=1500 * uL)
    p.load(reagents["A2"], "Van 1000 uM", volume=1000 * uL)
    p.load(reagents["A3"], "Sal 100 uM", volume=3000 * uL)
    p.load(reagents["A4"], "TMP 300 ug/mL in DMSO", volume=1000 * uL)
    p.load(reagents["A5"], "DMSO", volume=1000 * uL)
    p.load(reagents["A6"], "strain / p1", volume=1000 * uL)
    p.load(reagents["A7"], "strain / p2", volume=1000 * uL)
    stocks = (
        Stock(solute="Van", concentration=100 * uM, source=reagents["A1"], max_target=25 * uM),
        Stock(solute="Van", concentration=1000 * uM, source=reagents["A2"], max_target=200 * uM),
        Stock(solute="Sal", concentration=100 * uM, source=reagents["A3"]),
        Stock(
            solute="TMP",
            concentration=300 * UG_ML,
            source=reagents["A4"],
            vehicle_fraction=Decimal(1),
        ),
    )
    inocula = {"strain / p1": reagents["A6"], "strain / p2": reagents["A7"]}
    return plate, medium, waste, reagents["A5"], stocks, inocula


def _settings(**changes):
    base = dict(
        final_volume=200 * uL,
        inoculum_volume=10 * uL,
        vehicle_fraction=Decimal("0.02"),
    )
    return Settings(**(base | changes))


@pytest.mark.parametrize("strategy", [Strategy.DIRECT, Strategy.AUTO, Strategy.SERIAL])
def test_assemble_reaches_the_design_with_every_strategy(strategy):
    design = gradient_design(zero=True)
    p = Protocol("assemble")
    plate, medium, waste, vehicle, stocks, inocula = _sources(p)
    report = assemble(
        p,
        design,
        plate=plate,
        stocks=stocks,
        medium=medium,
        inocula=inocula,
        settings=_settings(strategy=strategy),
        vehicle=vehicle,
        waste=waste,
    )
    final = dict(compile(p, to=None).final_volumes)
    assert all(final[Location("cultures", w.well)] == 200 for w in design.wells)
    van_moves = [
        s
        for s in p.steps
        if isinstance(s, (Transfer, Distribute))
        and s.source in {Location("reagents", "A1"), Location("reagents", "A2")}
    ]
    if strategy is Strategy.DIRECT:
        assert report.gradients == ()
        assert min(s.volume for s in van_moves) == Decimal("1.5625")
        assert final[Location("waste", "A1")] == 0
    else:
        assert [(g.candidate.width, g.candidate.steps) for g in report.gradients] == [(4, 8)]
        assert min(s.volume for s in van_moves) == 40
        assert final[Location("waste", "A1")] == 400
        plans = {w.target.well: w for w in report.wells}
        assert plans["A12"].gradient_ul == 100 and plans["A4"].gradient_ul == 100
        assert plans["A12"].medium_ul == 200 - 10 - 100 - 20 - 2 - 2
        assert all(s.solute != "Van" for s, _ in plans["A12"].doses)
    assert report.skipped_gradients == ()


def test_assemble_premixes_replicates_and_raises_on_missing_resources():
    design = gradient_design()
    p = Protocol("premix")
    plate, medium, waste, vehicle, stocks, inocula = _sources(p)
    tubes = p.plate("tubes", shape=(4, 6), capacity=1500 * uL)
    report = assemble(
        p,
        design,
        plate=plate,
        stocks=stocks,
        medium=medium,
        inocula=inocula,
        settings=_settings(strategy=Strategy.DIRECT, premix_min_group=4),
        vehicle=vehicle,
        waste=waste,
        tubes=[tubes[w] for w in tubes.wells],
    )
    assert len(report.premixes) == 8 and all(w.premixed for w in report.wells)
    final = dict(compile(p, to=None).final_volumes)
    assert all(final[Location("cultures", w.well)] == 200 for w in design.wells)
    liquid = [s.volume for s in p.steps if isinstance(s, (Transfer, Distribute, Mix))]
    assert min(liquid) == Decimal("6.875")  # 4 wells x 1.5625 uL x 1.1 overage
    with pytest.raises(ValueError, match="more tubes"):
        q = Protocol("few tubes")
        plate, medium, waste, vehicle, stocks, inocula = _sources(q)
        assemble(
            q,
            design,
            plate=plate,
            stocks=stocks,
            medium=medium,
            inocula=inocula,
            settings=_settings(premix_min_group=4),
            vehicle=vehicle,
            waste=waste,
            tubes=[],
        )
    with pytest.raises(ValueError, match="waste container"):
        q = Protocol("no waste")
        plate, medium, _, vehicle, stocks, inocula = _sources(q)
        assemble(
            q,
            design,
            plate=plate,
            stocks=stocks,
            medium=medium,
            inocula=inocula,
            settings=_settings(),
            vehicle=vehicle,
        )
    with pytest.raises(ValueError, match="No serial dilution gradient"):
        q = Protocol("flat")
        plate, medium, waste, vehicle, stocks, inocula = _sources(q)
        flat = PlateDesign.build(
            "cultures",
            final_volume=200 * uL,
            wells=[well_target("A1", material="strain / p1", concentrations={"Van": 1 * uM})],
        )
        assemble(
            q,
            flat,
            plate=plate,
            stocks=stocks,
            medium=medium,
            inocula=inocula,
            settings=_settings(strategy=Strategy.SERIAL),
            vehicle=vehicle,
            waste=waste,
        )
    with pytest.raises(ValueError, match="No inoculum source"):
        q = Protocol("missing")
        plate, medium, waste, vehicle, stocks, _ = _sources(q)
        assemble(
            q,
            design,
            plate=plate,
            stocks=stocks,
            medium=medium,
            inocula={},
            settings=_settings(),
            vehicle=vehicle,
            waste=waste,
        )


def test_auto_skips_infeasible_gradients_and_serial_reports_them():
    design = gradient_design()
    for strategy in (Strategy.AUTO, Strategy.SERIAL):
        p = Protocol("shallow")
        plate, medium, waste, vehicle, stocks, inocula = _sources(p)
        kwargs = dict(
            plate=plate,
            stocks=stocks,
            medium=medium,
            inocula=inocula,
            vehicle=vehicle,
            waste=waste,
            settings=_settings(strategy=strategy, concentrate=Decimal(1)),
        )
        if strategy is Strategy.SERIAL:
            with pytest.raises(ValueError, match="Peak volume 400"):
                assemble(p, design, **kwargs)
        else:
            report = assemble(p, design, **kwargs)
            assert report.gradients == ()
            assert [reason[:16] for _, reason in report.skipped_gradients] == ["Peak volume 400 "]
            compile(p, to=None)


def test_inferred_requirements_round_trip_an_arranged_plate_and_describe_a_hand_layout():
    requirements = Requirements(
        name="cultures",
        final_volume=200 * uL,
        materials=("strain / p1", "strain / p2"),
        conditions=(
            ConditionSpec(label="Sal 10", concentrations={"Sal": 10 * uM, "TMP": 3 * UG_ML}),
        ),
        gradient=GradientSpec(
            solute="Van", top=100 * uM, fold=Decimal(2), steps=8, include_zero=True
        ),
        controls=(ControlSpec(label="blank", concentrations={"Van": 0 * uM, "Sal": 0 * uM}),),
    )
    inferred = infer_requirements(arrange(requirements).design)
    assert inferred.exact and inferred.notes == ()
    back = inferred.requirements
    assert back is not None
    assert back.materials == requirements.materials and back.replicates == 1
    assert back.gradient is not None and (back.gradient.steps, back.gradient.include_zero) == (
        8,
        True,
    )
    assert [c.label for c in back.conditions] == ["Sal 10 µM, TMP 3 µg/mL"]
    assert [(c.label, c.replicates) for c in back.controls] == [("Sal 0 µM, Van 0 µM", 1)]
    assert back.well_count == requirements.well_count

    hand = gradient_design(zero=True, sal=(Decimal(10), Decimal(25)))
    inferred = infer_requirements(hand)
    assert inferred.exact and inferred.requirements is not None
    assert [c.label for c in inferred.requirements.conditions] == [
        "Sal 10 µM, TMP 3 µg/mL",
        "Sal 25 µM, TMP 3 µg/mL",
    ]
    assert all((c.steps, c.include_zero) == (8, True) for c in inferred.requirements.conditions)
    assert (
        infer_requirements(PlateDesign.build("e", final_volume=1 * uL, wells=[])).requirements
        is None
    )


def test_conditions_can_shorten_or_extend_the_gradient():
    requirements = Requirements(
        name="cultures",
        final_volume=200 * uL,
        materials=("strain / p1",),
        conditions=(
            ConditionSpec(label="long", concentrations={"Sal": 10 * uM}),
            ConditionSpec(
                label="short", concentrations={"Sal": 25 * uM}, steps=5, include_zero=True
            ),
        ),
        gradient=GradientSpec(solute="Van", top=100 * uM, fold=Decimal(2), steps=8),
    )
    assert requirements.well_count == 8 + 6
    design = arrange(requirements).design
    assert [w.well for w in design.row("A")] == [f"A{c}" for c in range(5, 13)]
    assert [w.well for w in design.row("B")] == [f"B{c}" for c in range(7, 13)]
    assert design["B7"].concentration("Van").magnitude == 0
    assert design["B12"].concentration("Van").magnitude == Decimal("0.0001")
    inferred = infer_requirements(design)
    assert inferred.exact and inferred.requirements is not None
    assert [(c.steps, c.include_zero) for c in inferred.requirements.conditions] == [
        (8, False),
        (5, True),
    ]
    with pytest.raises(ValueError, match="positive integer"):
        ConditionSpec(label="bad", concentrations={}, steps=0)
