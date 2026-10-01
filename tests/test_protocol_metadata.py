from dataclasses import replace
from io import StringIO

import pytest

import lab
from lab import Protocol, seconds, uL
from lab.experiments.cloning.decks import assembly_deck
from lab.operations import Origin
from lab.targets import LiquidHandler


def test_semantic_identity_is_independent_of_authoring_paths():
    p = Protocol("Same meaning")
    p.wait(1 * seconds)
    original = p.snapshot()
    moved = replace(
        original,
        steps=tuple(
            replace(
                step,
                origin=Origin("/another/machine/experiment.py", 800),
            )
            for step in original.steps
        ),
    )
    assert moved.digest == original.digest
    assert moved.steps[0].identity == original.steps[0].identity
    p.wait(2 * seconds)
    assert original.digest != p.snapshot().digest
    with pytest.raises(ValueError, match="unique"):
        replace(original, steps=(*original.steps, *original.steps))


@pytest.mark.integration
def test_flex_switches_volume_modes_before_picking_up_fresh_tips():
    simulator = pytest.importorskip("opentrons.simulate")

    p = Protocol("Mixed volumes")
    source = p.plate("reagents", shape=(4, 6), capacity=1500 * uL)
    target = p.plate("products", capacity=100 * uL)
    p.load(source["A1"], "water", volume=200 * uL)
    for index, volume in enumerate((1, 4, 5, 30, 50), 1):
        p.transfer(source["A1"], target[f"A{index}"], volume=volume * uL)
    artifact = lab.compile(p, deck=assembly_deck(), liquid_handler=LiquidHandler.FLEX)
    source_text = artifact.files["protocol.py"]
    assert source_text.count("configure_for_volume") == 5
    simulator.simulate(StringIO(source_text))
