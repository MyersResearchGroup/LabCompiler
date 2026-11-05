"""Independent preparation balances and the generated robot operations."""

import csv
import hashlib
import json
import re
import sys
import tomllib
from decimal import Decimal
from io import StringIO
from pathlib import Path

import pytest
from pydantic import ValidationError

from examples.concentration_series import __main__ as cli
from examples.concentration_series import inputs
from examples.concentration_series import protocol as recipe
from lab import CompileError, HamiltonLiquidClass, LiquidClass, compile, uL
from lab.config import PortableLiquidClassConfig
from lab.deck import Deck, HolderSite, Rail, Slot
from lab.equipment import LiquidHandler
from lab.model import Distribute, Mix, Transfer
from lab.plugins.lmsf import Condition, read_layout
from lab.samples import Location

ROOT = Path(__file__).resolve().parents[1]
CSV = ROOT / "examples/concentration_series/layout.csv"
CONFIG = ROOT / "examples/concentration_series/config.toml"


def configuration(strategy: str | None = None):
    raw = tomllib.loads(CONFIG.read_text())
    if strategy is not None:
        raw["preparation"]["strategy"] = strategy
    return inputs.ProtocolConfig.model_validate(raw)


def updated(config, **changes):
    return type(config).model_validate(config.model_dump() | changes)


def design(path=CSV, config=None):
    config = config or configuration()
    return read_layout(
        path.read_text(encoding="utf-8-sig"), name="cultures", final_volume=config.final_volume
    )


def conditions():
    with CSV.open(newline="", encoding="utf-8-sig") as stream:
        return [Condition.model_validate(row) for row in csv.DictReader(stream)]


def protocol(strategy: str | None = None):
    config = configuration(strategy)
    return recipe.protocol(design(config=config), config)[0]


def delivered_transfers(steps):
    """Expand distributions to compare per-well doses independently of batching."""
    return [
        Transfer(step.source, destination, step.volume, step.origin, step.liquid_class)
        for step in steps
        if isinstance(step, (Transfer, Distribute))
        for destination in (
            (step.destination,) if isinstance(step, Transfer) else step.destinations
        )
    ]


def liquid_steps(steps):
    return [step for step in steps if isinstance(step, (Transfer, Distribute, Mix))]


try:
    from opentrons.simulate import simulate
except ImportError:
    simulate = None


def test_direct_preparation_matches_independent_doses_inventory_and_identity():
    bundle = compile(protocol("direct"), to=None)
    transfers = delivered_transfers(bundle.protocol.steps)

    rows = conditions()
    expected = {f"{row}{column}" for row in "ABCDEFGH" for column in range(1, 13)}
    assert {row.well for row in rows} == expected
    assert configuration().wells is None
    samples = {sample.id: sample for sample in bundle.manifest.samples}
    for row in rows:
        moves = [s for s in transfers if s.destination == Location("cultures", row.well)]

        # Independently balance all CSV targets against the supplied working stocks.
        van_stock = "A1" if row.inducer1_concentration <= 25 else "A2"
        van = row.inducer1_concentration * 200 / (100 if van_stock == "A1" else 1000)
        sal = row.inducer2_concentration * 200 / 100
        tmp = row.selector_concentration * 200 / 300
        culture_source = f"A{6 + (ord(row.row) - ord('A')) // 2}"
        doses = {
            Location("medium", "A1"): Decimal(186) - van - sal,
            Location("reagents", "A5"): Decimal(4) - tmp,
            Location("reagents", van_stock): van,
            Location("reagents", "A3"): sal,
            Location("reagents", "A4"): tmp,
            Location("reagents", culture_source): Decimal(10),
        }
        assert {s.source: s.volume for s in moves} == {
            source: dose for source, dose in doses.items() if dose
        }
        assert sum(s.volume for s in moves) == 200
        assert (doses[Location("reagents", "A5")] + tmp) / 200 == Decimal("0.02")
        sample = samples[f"culture-{row.well}"]
        assert sample.material_identity == row.plasmid
        assert sample.parent_ids == (f"inoculum-{culture_source}",)
        assert sample.replicate is None

    remaining = dict(bundle.final_volumes)
    assert {well: remaining[Location("cultures", well)] for well in expected} == dict.fromkeys(
        expected, Decimal(200)
    )
    for well, volume in (
        ("A1", "596.25"),
        ("A2", "680"),
        ("A3", "820"),
        ("A4", "832"),
        ("A5", "784"),
        ("A6", "760"),
        ("A7", "760"),
        ("A8", "760"),
        ("A9", "760"),
    ):
        assert abs(remaining[Location("reagents", well)] - Decimal(volume)) < Decimal("1e-12")
    assert abs(remaining[Location("medium", "A1")] - Decimal("547.75")) < Decimal("1e-12")
    assert remaining[Location("medium", "A2")] == 10000
    assert remaining[Location("waste", "A1")] == 0
    assert len(bundle.manifest.samples) == 96
    assert {p.location.well for p in bundle.manifest.placements} == set(expected)
    assert '"name": "DMSO"' in bundle.protocol.description
    assert "DMSO" in bundle.files["protocol.html"]
    assert all(s.liquid_class is not None for s in bundle.protocol.steps)
    assert all(isinstance(s, (Transfer, Distribute, Mix)) for s in bundle.protocol.steps)
    mixes = [s for s in bundle.protocol.steps if isinstance(s, Mix)]
    assert [(s.location, s.volume, s.cycles) for s in mixes] == [
        item
        for row in rows
        for item in (
            (Location("reagents", f"A{6 + (ord(row.row) - ord('A')) // 2}"), 50, 3),
            (Location("cultures", row.well), 100, 3),
        )
    ]


def test_direct_additions_are_grouped_by_source_and_dose_before_inoculation():
    steps = protocol("direct").steps
    assert len(steps) == 327
    assert all(
        isinstance(step, Distribute) and step.source.resource == "medium" for step in steps[:20]
    )
    distributions = [step for step in steps if isinstance(step, Distribute)]
    assert len(distributions) == 39
    assert len({(step.source, step.volume) for step in distributions}) == len(distributions)
    assert {destination.well for step in steps[:20] for destination in step.destinations} == {
        row.well for row in conditions()
    }
    first_inoculum_mix = next(index for index, step in enumerate(steps) if isinstance(step, Mix))
    assert first_inoculum_mix == 39
    assert all(
        step.source.resource == "medium" or step.source.well in {"A1", "A2", "A3", "A4", "A5"}
        for step in steps[:first_inoculum_mix]
    )


def test_auto_strategy_builds_the_van_gradients_as_serial_dilutions():
    config = configuration()
    assert config.preparation.strategy == "auto"
    p, report = recipe.protocol(design(config=config), config)
    bundle = compile(p, to=None)
    assert [
        (g.candidate.solute, g.candidate.width, g.candidate.steps, g.candidate.fold)
        for g in report.gradients
    ] == [("Van", 4, 8, 2), ("Van", 4, 5, 2)]
    assert report.skipped_gradients == ()
    plans = {plan.target.well: plan for plan in report.wells}
    gradient_wells = {
        w for g in report.gradients for w in (*g.candidate.wells, *g.candidate.zero_wells)
    }
    assert len(gradient_wells) == 4 * 8 + 4 * 5 + 4
    assert all(plans[w].gradient_ul == 100 for w in gradient_wells)
    assert all(plans[w].gradient_ul == 0 for w in plans if w not in gradient_wells)
    assert plans["A12"].medium_ul == 200 - 10 - 100 - 20 - 2 - 2

    remaining = dict(bundle.final_volumes)
    assert all(remaining[Location("cultures", w)] == 200 for w in plans)
    assert remaining[Location("waste", "A1")] == 8 * 100
    # The gradient draws its diluent from the second medium well, not the dosing medium.
    assert remaining[Location("medium", "A2")] < 10000
    van = [
        s
        for s in delivered_transfers(bundle.protocol.steps)
        if s.source in {Location("reagents", "A1"), Location("reagents", "A2")}
    ]
    tops = {w for g in report.gradients for w in g.candidate.levels[0]}
    assert {s.destination.well for s in van if s.destination.well in gradient_wells} == tops
    assert all(s.volume == 40 for s in van if s.destination.well in tops)
    chain = [
        s
        for s in bundle.protocol.steps
        if isinstance(s, Transfer) and s.source.resource == "cultures"
    ]
    assert len(chain) == 8 * 4 + 5 * 4
    assert {s.volume for s in chain} == {Decimal(100)}
    assert all(s.liquid_class is not None for s in bundle.protocol.steps)
    assert len(bundle.protocol.steps) == 420
    assert "Serial dilutions" not in p.description  # the CLI adds the gradient summary
    assert len(bundle.manifest.samples) == 96


@pytest.mark.integration
@pytest.mark.skipif(simulate is None, reason="Opentrons SDK not installed")
@pytest.mark.parametrize("strategy", ["direct", "auto"])
def test_generated_ot2_actions_match_transfers_mixes_and_tips(tmp_path, monkeypatch, strategy):
    monkeypatch.chdir(tmp_path)
    bundle = compile(
        protocol(strategy), deck=configuration().deck, liquid_handler=LiquidHandler.OT2, to=None
    )
    log, _ = simulate(StringIO(bundle.files["protocol.py"]))
    texts = [event["payload"]["text"] for event in log]
    aspirate = re.compile(r"Aspirating ([0-9.]+) uL from ([A-H]\d+) of .+ on slot (\d+) at ")
    dispense = re.compile(r"Dispensing ([0-9.]+) uL into ([A-H]\d+) of .+ on slot (\d+) at ")
    pending, remaining, moves = None, Decimal(0), []
    for text in texts:
        start, end = aspirate.search(text), dispense.search(text)
        if start:
            assert remaining == 0
            pending, remaining = (start[3], start[2]), Decimal(start[1])
        elif end:
            assert pending is not None
            destination = (end[3], end[2])
            volume = Decimal(end[1])
            assert 0 < volume <= remaining
            remaining -= volume
            if destination != pending:
                moves.append((*pending, *destination, volume))
    assert remaining == 0
    bindings = {
        b.location: tuple(b.physical.removeprefix("slot ").split("/"))
        for b in bundle.target.bindings
    }
    expected = [
        (*bindings[s.source], *bindings[s.destination], s.volume)
        for s in delivered_transfers(bundle.protocol.steps)
    ]
    assert moves == expected
    mixes = [s for s in bundle.protocol.steps if isinstance(s, Mix)]
    actual_mixes = [text for text in texts if text.startswith("Mixing ")]
    assert len(actual_mixes) == len(mixes)
    for actual, expected_mix in zip(actual_mixes, mixes, strict=True):
        assert actual.startswith(f"Mixing {expected_mix.cycles} times with a volume of ")
        volume = re.search(r"volume of ([0-9.]+)", actual)
        assert volume and Decimal(volume[1]) == expected_mix.volume
    tips = len(liquid_steps(bundle.protocol.steps))
    assert tips == {"direct": 327, "auto": 420}[strategy]
    assert sum(t.startswith("Picking up tip") for t in texts) == tips
    assert sum(t.startswith("Dropping tip") for t in texts) == tips


@pytest.mark.parametrize(
    "change, message",
    [
        (lambda s: updated(s, cultures=()), "No inoculum source"),
        (lambda s: updated(s, stocks=()), "no stock covers"),
        (lambda s: updated(s, vehicle_fraction=Decimal("0.005")), "exceed"),
        (lambda s: updated(s, final_volume=30 * uL), "B7: additions exceed"),
        (
            lambda s: updated(
                s,
                cultures=(updated(s.cultures[0], volume=25 * uL), *s.cultures[1:]),
            ),
            "available above its dead volume",
        ),
        (lambda s: updated(s, vehicle=updated(s.vehicle, well="A1")), "assigned more than once"),
        (
            lambda s: updated(s, medium=updated(s.medium, diluent_volume=None)),
            "available above its dead volume",
        ),
    ],
)
def test_preparation_rejects_infeasible_or_unknown_inputs(change, message):
    with pytest.raises((ValueError, CompileError), match=message):
        config = change(configuration())
        compile(recipe.protocol(design(config=config), config)[0], to=None)


@pytest.mark.parametrize(
    "old, new, message",
    [
        (
            "solute_and_vehicle_free_medium_and_cultures = true",
            "solute_and_vehicle_free_medium_and_cultures = false",
            "carryover",
        ),
        ('source_dead_volume = "500 uL"', 'source_dead_volume = "-1 uL"', "nonnegative"),
        ('vehicle_fraction = "0.02"', 'vehicle_fraction = "NaN"', "finite"),
        ('inoculum_volume = "10 uL"', 'inoculum_volume = "200 uL"', "less than"),
        ('liquid_class = "aqueous"', 'liquid_class = "missing"', "missing"),
        ('dispense_flow_rate = "30 uL/s"', 'dispense_flow_rate = "30 uL"', "compatible"),
        ('dispense_flow_rate = "30 uL/s"', 'dispense_fow_rate = "30 uL/s"', "Extra inputs"),
        ('strategy = "auto"', 'strategy = "clever"', "direct"),
        ('concentrate = "2"', 'concentrate = "0.5"', "greater than or equal to 1"),
    ],
)
def test_preparation_file_validates_explicit_inputs(tmp_path, old, new, message):
    path = tmp_path / "config.toml"
    path.write_text(CONFIG.read_text().replace(old, new))
    with pytest.raises((ValueError, TypeError, KeyError), match=message):
        inputs.ProtocolConfig.model_validate(tomllib.loads(path.read_text()))


def test_csv_targets_are_read_and_duplicate_conditions_remain_separate(tmp_path):
    with CSV.open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    rows = [r for r in rows if r["well"] in ("A1", "A5")]
    for row in rows:
        row["inducer1Concentration"] = "5"
    path = tmp_path / "changed.csv"
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    config = updated(configuration(), wells=("A1", "A5"))
    selected = cli.select(design(path, config), config.wells)
    bundle = compile(recipe.protocol(selected, config)[0], to=None)
    van = [
        s
        for s in delivered_transfers(bundle.protocol.steps)
        if s.source == Location("reagents", "A1")
    ]
    assert [(s.destination.well, s.volume) for s in van] == [("A1", 10), ("A5", 10)]
    assert len(bundle.manifest.samples) == 2
    with pytest.raises(ValueError, match="absent from CSV"):
        cli.select(design(path, config), ("A1", "H12"))


@pytest.mark.integration
async def test_generated_star_example_tracks_all_delivered_volumes():
    plr = pytest.importorskip("pylabrobot.resources")
    backends = pytest.importorskip("pylabrobot.liquid_handling.backends")
    bundle = compile(
        protocol(),
        deck=configuration().deck,
        liquid_handler=LiquidHandler.STAR,
        to=None,
    )
    namespace = {"__name__": "_generated"}
    exec(bundle.files["protocol.py"], namespace)
    plr.set_volume_tracking(True)
    try:
        handler = await namespace["run"](backends.LiquidHandlerChatterboxBackend())
    finally:
        plr.set_volume_tracking(False)
    physical = {binding.location: binding.physical for binding in bundle.target.bindings}
    for location, volume in bundle.final_volumes:
        resource = handler.deck.get_resource(physical[location])

        # The SDK accumulates binary floats, while the compiler retains decimal doses.
        assert abs(Decimal(str(resource.tracker.get_used_volume())) - volume) < Decimal("1e-8")


def test_explicit_subset_preserves_requested_order_and_culture_assignments():
    config = updated(configuration(), wells=("H12", "A1"))
    selected = cli.select(design(config=config), config.wells)
    assert [well.well for well in selected.wells] == ["H12", "A1"]
    p, report = recipe.protocol(selected, config)
    assert [plan.target.material for plan in report.wells] == [
        "example-strain / example-plasmid-4",
        "example-strain / example-plasmid-1",
    ]
    bundle = compile(p, to=None)
    assert {placement.location.well for placement in bundle.manifest.placements} == {"H12", "A1"}
    # Two isolated wells contain no gradient, so the automatic strategy doses directly.
    assert report.gradients == ()


def test_config_round_trip_preserves_toml_shape_and_resolves_classes_explicitly():
    raw = tomllib.loads(CONFIG.read_text())
    config = inputs.ProtocolConfig.model_validate(raw)
    saved = config.model_dump(mode="json")
    assert saved.keys() == raw.keys() | {"wells"}
    assert saved["wells"] is None
    for field in ("medium", "vehicle", "source_mix", "destination_mix"):
        assert saved[field].keys() == raw[field].keys()
    for field in ("stocks", "cultures"):
        assert all(
            actual.keys() == source.keys()
            for actual, source in zip(saved[field], raw[field], strict=True)
        )
    assert config.medium.name == raw["medium"]["name"]
    assert config.medium.dead_volume == 500 * uL
    assert config.medium.diluent_volume == 10000 * uL
    assert config.medium.liquid_class == "aqueous"
    assert config.source_mix.liquid_class == "culture"
    assert config.stocks[0].well == raw["stocks"][0]["well"]
    assert config.preparation.concentrate == 2
    assert isinstance(config.deck, Deck)
    ot2 = config.deck.layout_for(LiquidHandler.OT2)
    star = config.deck.layout_for(LiquidHandler.STAR)
    assert ot2.placements[0].location == Slot("1")
    assert star.placements[0].location == HolderSite("plates", 0)
    assert star.carriers[0].location == Rail(15)
    assert isinstance(config.liquid_classes["aqueous"], PortableLiquidClassConfig)
    assert inputs.ProtocolConfig.model_validate_json(config.model_dump_json()) == config

    # Optional native definitions retain their config alias until explicit resolution.
    raw["liquid_classes"]["aqueous"] = {
        "kind": "hamilton",
        "name": "HighVolumeFilter_Water_DispenseSurface_Part",
    }
    native_config = inputs.ProtocolConfig.model_validate(raw)
    assert native_config.medium.liquid_class == "aqueous"
    bundle = compile(recipe.protocol(design(config=native_config), native_config)[0], to=None)
    medium_class = bundle.protocol.steps[0].liquid_class
    assert isinstance(medium_class, HamiltonLiquidClass)
    assert medium_class.name == "HighVolumeFilter_Water_DispenseSurface_Part"
    mixing_class = next(
        step
        for step in bundle.protocol.steps
        if isinstance(step, Mix) and step.location.resource == "reagents"
    ).liquid_class
    assert isinstance(mixing_class, LiquidClass)
    assert mixing_class.name == "culture"
    assert mixing_class.aspiration_flow_rate_ul_s == mixing_class.dispense_flow_rate_ul_s == 20


@pytest.mark.parametrize(
    "path, value, location, error_type",
    [
        (("unexpected",), 1, ("unexpected",), "extra_forbidden"),
        (("medium", "dead_volum"), "500 uL", ("medium", "dead_volum"), "extra_forbidden"),
        (("medium", "dead_volume"), "1 second", ("medium", "dead_volume"), "value_error"),
        (("medium", "volume"), "10 unknown_unit", ("medium", "volume"), "value_error"),
        (("medium", "volume"), 3000, ("medium", "volume"), "value_error"),
        (("source_mix", "cycles"), True, ("source_mix", "cycles"), "int_type"),
        (("source_mix", "cycles"), "3", ("source_mix", "cycles"), "int_type"),
        (
            ("solute_and_vehicle_free_medium_and_cultures",),
            "true",
            ("solute_and_vehicle_free_medium_and_cultures",),
            "bool_type",
        ),
        (
            ("liquid_classes", "aqueous", "kind"),
            "water",
            ("liquid_classes", "aqueous"),
            "union_tag_invalid",
        ),
        (
            ("liquid_classes", "aqueous", "aspiration_flow_rate"),
            "0 uL/s",
            ("liquid_classes", "aqueous", "portable", "aspiration_flow_rate"),
            "value_error",
        ),
        (("cultures", 0, "well"), "A13", ("cultures", 0, "well"), "string_pattern_mismatch"),
        (
            ("preparation", "serial_min_steps"),
            1,
            ("preparation", "serial_min_steps"),
            "greater_than_equal",
        ),
    ],
)
def test_config_validation_reports_the_invalid_field(path, value, location, error_type):
    raw = tomllib.loads(CONFIG.read_text())
    parent = raw
    for key in path[:-1]:
        parent = parent[key]
    parent[path[-1]] = value
    with pytest.raises(ValidationError) as caught:
        inputs.ProtocolConfig.model_validate(raw)
    assert (location, error_type) in {(e["loc"], e["type"]) for e in caught.value.errors()}


def test_saved_config_is_the_validated_input_used_by_the_recipe():
    config = updated(
        configuration(),
        final_volume="180 uL",
        medium=updated(configuration().medium, name="Specified medium"),
    )
    bundle = compile(recipe.protocol(design(config=config), config)[0], to=None)
    recorded = json.loads(bundle.protocol.description.split("Protocol configuration: ", 1)[1])
    assert recorded == config.model_dump(mode="json")
    assert recorded["final_volume"] == "180 microliter"
    assert recorded["medium"]["name"] == "Specified medium"
    assert all(
        dict(bundle.final_volumes)[Location("cultures", row.well)] == 180 for row in conditions()
    )
    with pytest.raises(ValidationError, match="less than final"):
        updated(config, inoculum_volume=200 * uL)


def test_config_schema_describes_nested_inputs_and_unit_strings():
    schema = inputs.ProtocolConfig.model_json_schema()
    assert schema["additionalProperties"] is False
    assert schema["properties"]["final_volume"]["type"] == "string"
    assert schema["$defs"]["MediumConfig"]["properties"]["dead_volume"]["type"] == "string"
    assert schema["$defs"]["MediumConfig"]["properties"]["liquid_class"]["type"] == "string"
    assert schema["$defs"]["PreparationConfig"]["properties"]["strategy"]["enum"] == [
        "direct",
        "serial",
        "auto",
    ]


def test_protocol_consumes_the_design_without_reading_files(monkeypatch):
    config = configuration()
    plate = design(config=config)

    def unexpected(*args, **kwargs):
        pytest.fail("Protocol construction must consume the already parsed inputs")

    with monkeypatch.context() as patch:
        patch.setattr(inputs.ProtocolConfig, "model_validate", unexpected)
        patch.setattr(Path, "open", unexpected)
        patch.setattr(Path, "read_bytes", unexpected)
        patch.setattr(Path, "read_text", unexpected)
        p, _ = recipe.protocol(plate, config)
    bundle = compile(p, to=None)
    assert all(
        dict(bundle.final_volumes)[Location("cultures", row.well)] == 200 for row in conditions()
    )


@pytest.mark.parametrize("explicit_output", [False, True])
def test_cli_replaces_existing_bundle_with_current_inputs(tmp_path, monkeypatch, explicit_output):
    monkeypatch.chdir(tmp_path)
    output = tmp_path / ("compiled" if explicit_output else "build/concentration_series/csv/manual")
    output.mkdir(parents=True)
    for name in ("plan.json", "protocol.html", "manifest.json", "protocol.py"):
        (output / name).write_text("Previous compilation")
    (output / "notes.txt").write_text("Keep these notes")
    argv = ["concentration_series", "--csv", str(CSV), "--config", str(CONFIG)]
    if explicit_output:
        argv.extend(["--out", str(output)])
    monkeypatch.setattr(sys, "argv", argv)
    cli.main()
    plan = json.loads((output / "plan.json").read_text())
    description = plan["protocol"]["description"]
    assert CSV.name in description
    assert hashlib.sha256(CSV.read_bytes()).hexdigest() in description
    assert "Serial dilutions: Van 8-step 2-fold across 4 rows" in description
    assert json.loads(
        description.split("Protocol configuration: ", 1)[1]
    ) == configuration().model_dump(mode="json")
    assert len(plan["protocol"]["steps"]) == 420
    assert "Previous compilation" not in (output / "protocol.html").read_text()
    assert len(json.loads((output / "manifest.json").read_text())["outputs"]) == 96
    assert not (output / "protocol.py").exists()
    assert (output / "notes.txt").read_text() == "Keep these notes"


def test_cli_strategy_flag_overrides_the_configured_strategy(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "concentration_series",
            "--csv",
            str(CSV),
            "--config",
            str(CONFIG),
            "--strategy",
            "direct",
        ],
    )
    cli.main()
    plan = json.loads((tmp_path / "build/concentration_series/csv/manual/plan.json").read_text())
    assert len(plan["protocol"]["steps"]) == 327
    assert "Serial dilutions" not in plan["protocol"]["description"]


@pytest.mark.parametrize(
    "config_change, csv_change, message",
    [
        (None, ("0.78125", "NaN"), "finite"),
        (None, ("inducer1Units", "unknownColumn"), "CSV schema"),
        (None, ("A5,A,5", "A5,A,6"), "Inconsistent position"),
        (None, ("example-plasmid-1", "unknown-plasmid"), "No inoculum source"),
        (
            ('final_volume = "200 uL"', 'wells = ["A1", "A13"]\nfinal_volume = "200 uL"'),
            None,
            "wells.1",
        ),
        (('solute = "Van"', 'solute = "unknown"'), None, "no stock"),
        (('vehicle_fraction = "0.02"', 'vehicle_fraction = "0.005"'), None, "exceed"),
        (('name = "2"', 'name = "1"'), None, "occupied more than once"),
        (("min_volume_ul", "min_volume_ull"), None, "min_volume_ull"),
        (('min_volume_ul = "1"', 'min_volume_ul = "301"'), None, "minimum volume exceeds"),
    ],
)
def test_cli_rejects_invalid_inputs_before_protocol_construction(
    tmp_path, monkeypatch, capsys, config_change, csv_change, message
):
    config_text, csv_text = CONFIG.read_text(), CSV.read_text()
    assert config_change is None or config_change[0] in config_text
    assert csv_change is None or csv_change[0] in csv_text
    config_path, csv_path = tmp_path / "config.toml", tmp_path / "targets.csv"
    config_path.write_text(config_text.replace(*config_change) if config_change else config_text)
    csv_path.write_text(csv_text.replace(*csv_change) if csv_change else csv_text)
    monkeypatch.setattr(
        sys, "argv", ["concentration_series", "--csv", str(csv_path), "--config", str(config_path)]
    )

    def unexpected(*args, **kwargs):
        pytest.fail("Invalid inputs must be rejected before protocol construction")

    if message not in {"No inoculum source", "no stock", "exceed"}:
        monkeypatch.setattr(cli, "protocol", unexpected)
    with pytest.raises(SystemExit) as caught:
        cli.main()
    assert caught.value.code == 2
    assert message in capsys.readouterr().err
