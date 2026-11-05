"""Tip policies: the composition ledger, tip numbering, pre-wetting, and target pick-ups."""

from decimal import Decimal
from io import StringIO

import pytest

from lab import Protocol, compile, uL
from lab.deck import Container, Deck, DeckSite
from lab.equipment import LiquidHandler
from lab.labware import PLATE_96, LabwareKind, LabwareSpec
from lab.model import TipPolicy
from lab.samples import Location
from lab.tips import assign_tips, composition_trace, prewet, tip_count


def additive_protocol(policy: TipPolicy) -> Protocol:
    """Medium into three wells, additive one into all, additive two into two of them."""
    p = Protocol("additives", tip_policy=policy)
    medium = p.container(
        "medium", contents="medium", volume=5000 * uL, capacity=15000 * uL, components=("medium",)
    )
    first = p.container(
        "van",
        contents="Van in medium",
        volume=300 * uL,
        capacity=360 * uL,
        components=("medium", "Van"),
    )
    second = p.container(
        "sal",
        contents="Sal in medium",
        volume=300 * uL,
        capacity=360 * uL,
        components=("medium", "Sal"),
    )
    plate = p.plate("plate", capacity=360 * uL)
    wells = [plate["A1"], plate["A2"], plate["A3"]]
    p.distribute(medium, wells, volume=100 * uL)
    for well in wells:
        p.transfer(first, well, volume=10 * uL)
    p.transfer(second, plate["A1"], volume=5 * uL)
    p.transfer(second, plate["A2"], volume=5 * uL)
    # Back to the first additive: A1 and A2 now hold Sal, A3 does not.
    p.transfer(first, plate["A3"], volume=10 * uL)
    p.transfer(first, plate["A1"], volume=10 * uL)
    p.mix(plate["A1"], volume=50 * uL, cycles=2)
    p.transfer(first, plate["A2"], volume=10 * uL)
    return p


def test_composition_ledger_unions_sources_into_destinations():
    ledger = composition_trace(additive_protocol(TipPolicy.FRESH_PER_STEP).snapshot())
    a1 = Location("plate", "A1")
    assert ledger[0][a1] == frozenset()
    assert ledger[1][a1] == {"medium"}
    assert ledger[4][a1] == {"medium", "Van"}
    assert ledger[-1][a1] == {"medium", "Van", "Sal"}
    assert ledger[-1][Location("van", "A1")] == {"medium", "Van"}


@pytest.mark.parametrize(
    "policy, numbers, count",
    [
        (TipPolicy.FRESH_PER_STEP, (1, 2, 3, 4, 5, 6, 7, 8, 9, 10), 10),
        # One tip for the whole first-additive run; the second additive takes a fresh tip for
        # each well that already holds the first; back at the first additive, a well holding
        # the second forces a fresh tip; a destination mix ends the run.
        (TipPolicy.REUSE_WHEN_CONTAINED, (1, 2, 2, 2, 3, 4, 5, 6, 7, 8), 8),
        (TipPolicy.REUSE_PER_SOURCE, (1, 2, 2, 2, 3, 3, 4, 4, 5, 6), 6),
    ],
)
def test_tip_numbers_follow_the_policy(policy, numbers, count):
    recorded = additive_protocol(policy).snapshot()
    assert recorded.tip_policy is policy
    assert assign_tips(recorded) == numbers
    assert tip_count(recorded) == count
    assert assign_tips(recorded, TipPolicy.FRESH_PER_STEP) == tuple(range(1, 11))


def test_prewet_is_a_source_mix_that_keeps_the_tip():
    p = Protocol("prewet", tip_policy=TipPolicy.REUSE_WHEN_CONTAINED)
    stock = p.container("stock", contents="dye", volume=500 * uL, capacity=1500 * uL)
    plate = p.plate("plate", capacity=360 * uL)
    prewet(p, stock, volume=20 * uL)
    p.transfer(stock, plate["A1"], volume=20 * uL)
    p.transfer(stock, plate["A2"], volume=20 * uL)
    p.mix(plate["A2"], volume=20 * uL, cycles=1)
    p.transfer(stock, plate["A3"], volume=20 * uL)
    recorded = p.snapshot()
    assert assign_tips(recorded) == (1, 1, 1, 2, 3)
    final = dict(compile(p, to=None).final_volumes)
    assert final[Location("stock", "A1")] == 500 - 60
    assert compile(p, to=None).files["protocol.html"].count("Tips") >= 1


def test_compile_can_override_the_recorded_policy_and_reports_it():
    p = additive_protocol(TipPolicy.FRESH_PER_STEP)
    default = compile(p, to=None)
    overridden = compile(p, to=None, tip_policy=TipPolicy.REUSE_WHEN_CONTAINED)
    assert default.protocol.tip_policy is TipPolicy.FRESH_PER_STEP
    assert overridden.protocol.tip_policy is TipPolicy.REUSE_WHEN_CONTAINED
    assert '"tip_policy": "reuse_when_contained"' in overridden.plan_json
    assert ">10<" in default.files["protocol.html"] and ">8<" in overridden.files["protocol.html"]
    with pytest.raises(TypeError, match="TipPolicy"):
        compile(p, to=None, tip_policy="reuse")
    with pytest.raises(TypeError, match="TipPolicy"):
        Protocol("x", tip_policy="fresh")
    with pytest.raises(ValueError, match="distinct"):
        q = Protocol("dup")
        q.container("s", contents="x", volume=1 * uL, capacity=10 * uL, components=("a", "a"))


try:
    from opentrons.simulate import simulate
except ImportError:
    simulate = None


@pytest.mark.integration
@pytest.mark.skipif(simulate is None, reason="Opentrons SDK not installed")
def test_opentrons_picks_up_one_tip_per_number(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    p = additive_protocol(TipPolicy.REUSE_WHEN_CONTAINED)
    single = LabwareSpec(kind=LabwareKind.PLATE, rows=1, columns=1, capacity_ul=Decimal(360))
    conical = LabwareSpec(
        kind=LabwareKind.CONICAL_RACK, rows=1, columns=1, capacity_ul=Decimal(15000)
    )
    deck = Deck(
        containers=(
            Container(id="medium", labware=conical, site=DeckSite.RESERVOIR),
            Container(id="van", labware=single, site=DeckSite.PLATES),
            Container(id="sal", labware=single, site=DeckSite.PLATES),
            Container(id="plate", labware=PLATE_96, site=DeckSite.MORE_PLATES),
        )
    )
    bundle = compile(p, deck=deck, liquid_handler=LiquidHandler.OT2, to=None)
    log, _ = simulate(StringIO(bundle.files["protocol.py"]))
    texts = [event["payload"]["text"] for event in log]
    assert sum(text.startswith("Picking up tip") for text in texts) == 8
    assert sum(text.startswith("Dropping tip") for text in texts) == 8
    assert "8 fresh tips required" in bundle.target.setup
