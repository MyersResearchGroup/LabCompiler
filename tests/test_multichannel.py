"""Lane sets: recording, validation, descriptions, and multichannel lowering."""

import re
from dataclasses import replace
from decimal import Decimal
from io import StringIO

import pytest

from lab import CompileError, Protocol, compile, uL
from lab.deck import (
    Carrier,
    Channel,
    Deck,
    DeckLayout,
    HolderSite,
    Pipette,
    Placement,
    Rail,
    Slot,
    TipRack,
)
from lab.documents.descriptions import column_of, describe
from lab.equipment import (
    PIPETTE_CHANNELS,
    CarrierModel,
    LabwareModel,
    LiquidHandler,
    Mount,
    PipetteModel,
    TipRackModel,
)
from lab.labware import PLATE_96, ContainerSpec, LabwareKind, LabwareSpec
from lab.model import MultiMix, MultiTransfer, TipPolicy
from lab.samples import Location
from lab.targets.lanes import column_stroke
from lab.validation import volume_trace

try:
    from opentrons.simulate import simulate
except ImportError:
    simulate = None


def column_protocol(*, policy: TipPolicy = TipPolicy.FRESH_PER_STEP) -> Protocol:
    """Fill column 12 from a trough, then move half of it column to column."""
    p = Protocol("columns", tip_policy=policy)
    trough = p.plate("trough", shape=(1, 12), capacity=15000 * uL)
    p.load(trough["A1"], "medium", volume=5000 * uL)
    plate = p.plate("plate", capacity=360 * uL)
    p.transfer(trough["A1"], plate.column(12), volume=200 * uL)
    p.transfer(plate.column(12), plate.column(11), volume=100 * uL)
    p.mix(plate.column(11), volume=50 * uL, cycles=2)
    p.transfer(plate.column(11), plate.column(10), volume=100 * uL)
    return p


def test_lane_sets_are_recorded_validated_and_described():
    p = column_protocol()
    fill, move, mix, _ = p.steps
    assert isinstance(fill, MultiTransfer) and len(set(fill.sources)) == 1
    assert fill.destinations == tuple(Location("plate", f"{row}12") for row in "ABCDEFGH")
    assert isinstance(move, MultiTransfer) and move.sources == fill.destinations
    assert isinstance(mix, MultiMix) and mix.cycles == 2
    assert describe(fill).startswith(
        "Transfer 200 µL along 8 lanes together from trough:A1 to plate column 12 (rows A–H)."
    )
    assert describe(move).startswith(
        "Transfer 100 µL along 8 lanes together from plate column 12 (rows A–H) to plate column 11"
    )
    assert describe(mix) == "Mix plate column 11 (rows A–H) together: 2 cycles of 50 µL."
    assert column_of((Location("p", "A1"), Location("p", "C1"), Location("p", "E1"))) == (
        "p column 1 (rows A, C, E)"
    )
    assert column_of((Location("p", "A1"), Location("p", "A2"))) is None
    final = dict(compile(p, to=None).final_volumes)
    assert final[Location("trough", "A1")] == 5000 - 8 * 200
    assert all(final[Location("plate", f"{row}12")] == 100 for row in "ABCDEFGH")
    assert all(final[Location("plate", f"{row}11")] == 0 for row in "ABCDEFGH")
    assert all(final[Location("plate", f"{row}10")] == 100 for row in "ABCDEFGH")
    assert '"kind": "MultiTransfer"' in compile(p, to=None).plan_json


def test_lane_recording_rejects_mismatched_or_degenerate_lanes():
    p = Protocol("bad")
    plate = p.plate("plate", capacity=360 * uL)
    p.load(plate["A1"], "x", volume=100 * uL)
    with pytest.raises(ValueError, match="one source per destination"):
        p.transfer(plate.column(1)[:3], plate.column(2), volume=1 * uL)
    with pytest.raises(ValueError, match="must differ"):
        p.transfer(plate.column(1), plate.column(1), volume=1 * uL)
    with pytest.raises(ValueError, match="distinct source/destination"):
        p.transfer([plate["A1"], plate["A1"]], [plate["B1"], plate["B1"]], volume=1 * uL)
    with pytest.raises(ValueError, match="at least one destination"):
        p.transfer(plate["A1"], [], volume=1 * uL)
    with pytest.raises(ValueError, match="distinct"):
        p.mix([plate["A1"], plate["A1"]], volume=1 * uL, cycles=1)
    with pytest.raises(KeyError, match="Unknown column"):
        plate.column(13)
    p.transfer(plate["A1"], [plate["B1"], plate["C1"]], volume=10 * uL)
    recorded = p.snapshot()
    with pytest.raises(CompileError, match="needs 200 µL"):
        q = Protocol("short")
        plate = q.plate("plate", capacity=360 * uL)
        q.load(plate["A1"], "x", volume=100 * uL)
        q.transfer(plate["A1"], plate.column(2), volume=25 * uL)
        compile(q, to=None)
    assert (
        volume_trace(recorded, compile(p, to=None).target.bindings)[-1][Location("plate", "B1")]
        == 10
    )


def test_column_strokes_need_full_contiguous_columns():
    p = column_protocol()
    recorded = p.snapshot()
    resources = {r.name: r for r in recorded.resources}
    fill, move, mix, _ = recorded.steps
    assert column_stroke(fill, resources).source_top == Location("trough", "A1")
    stroke = column_stroke(move, resources)
    assert stroke is not None and stroke.width == 8
    assert (stroke.source_top, stroke.destination_top) == (
        Location("plate", "A12"),
        Location("plate", "A11"),
    )
    assert column_stroke(mix, resources).destination_top is None
    q = Protocol("alternating")
    plate = q.plate("plate", capacity=360 * uL)
    for row in "ACEG":
        q.load(plate[f"{row}12"], "x", volume=200 * uL)
    q.transfer(
        [plate[f"{row}12"] for row in "ACEG"], [plate[f"{row}11"] for row in "ACEG"], volume=50 * uL
    )
    assert column_stroke(q.steps[0], {r.name: r for r in q.snapshot().resources}) is None


def ot2_layout(pipette: PipetteModel, *, racks: tuple[TipRackModel, ...]) -> DeckLayout:
    tip_racks = tuple(
        TipRack(id=f"tips_{index}", model=model, location=Slot(str(5 + index)))
        for index, model in enumerate(racks)
    )
    return DeckLayout(
        liquid_handler=LiquidHandler.OT2,
        placements=(
            Placement(
                container="trough", model=LabwareModel.NEST_12_RESERVOIR_15_ML, location=Slot("1")
            ),
            Placement(container="plate", model=LabwareModel.CORNING_96_360_UL, location=Slot("2")),
        ),
        tip_racks=tip_racks,
        pipettes=(
            Pipette(model=pipette, mount=Mount.LEFT, tip_racks=(tip_racks[0].id,)),
            Pipette(
                model=PipetteModel.P300_SINGLE_GEN2, mount=Mount.RIGHT, tip_racks=(tip_racks[1].id,)
            ),
        ),
    )


def deck_for(layouts):
    return Deck(
        containers=(
            ContainerSpec(
                id="trough",
                labware=LabwareSpec(
                    kind=LabwareKind.RESERVOIR, rows=1, columns=12, capacity_ul=Decimal(15000)
                ),
            ),
            ContainerSpec(id="plate", labware=PLATE_96),
        ),
        layouts=tuple(layouts),
    )


@pytest.mark.integration
@pytest.mark.skipif(simulate is None, reason="Opentrons SDK not installed")
def test_opentrons_runs_full_columns_in_one_stroke_and_falls_back_otherwise(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert PIPETTE_CHANNELS[PipetteModel.P300_MULTI_GEN2] == 8
    layout = ot2_layout(
        PipetteModel.P300_MULTI_GEN2,
        racks=(TipRackModel.OPENTRONS_300_UL, TipRackModel.OPENTRONS_300_UL),
    )
    p = column_protocol()
    bundle = compile(p, deck=deck_for([layout]), liquid_handler=LiquidHandler.OT2, to=None)
    source = bundle.files["protocol.py"]
    assert source.count("pipette.pick_up_tip(") == 4 and "small_pipette.pick_up_tip(" not in source
    assert "pipette.aspirate(100, labware_2.wells_by_name()['A12'])" in source
    assert "pipette.mix(2, 50, labware_2.wells_by_name()['A11'])" in source
    assert "32 fresh tips required" in bundle.target.setup
    log, _ = simulate(StringIO(source))
    texts = [event["payload"]["text"] for event in log]
    assert sum(text.startswith("Picking up tip") for text in texts) == 4
    assert sum(text.startswith("Aspirating") for text in texts) == 1 + 1 + 2 + 1
    assert bundle.target.limits[0].channels == 8

    # Without a multichannel head the same protocol runs lane by lane.
    singles = replace(
        layout,
        pipettes=(
            Pipette(model=PipetteModel.P20_SINGLE_GEN2, mount=Mount.LEFT, tip_racks=("tips_0",)),
            layout.pipettes[1],
        ),
        tip_racks=(
            replace(layout.tip_racks[0], model=TipRackModel.OPENTRONS_20_UL),
            layout.tip_racks[1],
        ),
    )
    bundle = compile(p, deck=deck_for([singles]), liquid_handler=LiquidHandler.OT2, to=None)
    log, _ = simulate(StringIO(bundle.files["protocol.py"]))
    texts = [event["payload"]["text"] for event in log]
    # The trough fill shares one tip across its lanes; column moves take a tip per lane.
    assert sum(text.startswith("Picking up tip") for text in texts) == 1 + 8 + 8 + 8
    assert sum(text.startswith("Aspirating") for text in texts) == 8 + 8 + 16 + 8


@pytest.mark.integration
async def test_star_runs_lanes_across_its_channels():
    plr = pytest.importorskip("pylabrobot.resources")
    backends = pytest.importorskip("pylabrobot.liquid_handling.backends")

    def layout(channel_count: int) -> DeckLayout:
        racks = ("tips",)
        return DeckLayout(
            liquid_handler=LiquidHandler.STAR,
            placements=(
                Placement(
                    container="trough",
                    model=LabwareModel.NEST_12_RESERVOIR_15_ML,
                    location=HolderSite("plates", 0),
                ),
                Placement(
                    container="plate",
                    model=LabwareModel.CORNING_96_360_UL,
                    location=HolderSite("plates", 1),
                ),
            ),
            carriers=(
                Carrier(id="plates", model=CarrierModel.HAMILTON_PLATE_5, location=Rail(15)),
                Carrier(id="tip_carrier", model=CarrierModel.HAMILTON_TIP_5, location=Rail(3)),
            ),
            tip_racks=(
                TipRack(
                    id="tips",
                    model=TipRackModel.HAMILTON_300_UL,
                    location=HolderSite("tip_carrier", 0),
                ),
            ),
            channels=tuple(
                Channel(
                    index=i, min_volume_ul=Decimal(1), max_volume_ul=Decimal(300), tip_racks=racks
                )
                for i in range(channel_count)
            ),
        )

    p = column_protocol()
    bundle = compile(p, deck=deck_for([layout(8)]), liquid_handler=LiquidHandler.STAR, to=None)
    source = bundle.files["protocol.py"]
    assert "use_channels=[0, 1, 2, 3, 4, 5, 6, 7]" in source
    assert source.count("await lh.pick_up_tips(") == 4
    namespace = {"__name__": "_generated"}
    exec(source, namespace)
    plr.set_volume_tracking(True)
    try:
        handler = await namespace["run"](backends.LiquidHandlerChatterboxBackend())
    finally:
        plr.set_volume_tracking(False)
    physical = {binding.location: binding.physical for binding in bundle.target.bindings}
    for location, volume in bundle.final_volumes:
        tracker = handler.deck.get_resource(physical[location]).tracker
        assert abs(Decimal(str(tracker.get_used_volume())) - volume) < Decimal("1e-8")
    two = compile(p, deck=deck_for([layout(2)]), liquid_handler=LiquidHandler.STAR, to=None)
    assert two.files["protocol.py"].count("use_channels=[0, 1]") >= 4 * 2
    assert "use_channels=[0, 1, 2]" not in two.files["protocol.py"]
    assert bundle.target.limits[0].channels == 8 and two.target.limits[0].channels == 2
    with pytest.raises(CompileError, match="one to eight"):
        compile(p, deck=deck_for([layout(9)]), liquid_handler=LiquidHandler.STAR, to=None)


def test_worksheet_shows_lane_sources_and_lane_frames():
    html = compile(column_protocol(), to=None).files["protocol.html"]
    assert "lanes together" in html
    assert re.search(r"8 lanes together", html)
    assert '"lanes": true' in html
