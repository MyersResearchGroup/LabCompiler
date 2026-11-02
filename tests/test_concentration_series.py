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
from lab.model import Mix, Transfer
from lab.samples import Location
from lab.units import units

ROOT = Path(__file__).resolve().parents[1]
CSV = ROOT / "examples/concentration_series/layout.csv"
CONFIG = ROOT / "examples/concentration_series/config.toml"


def configuration():
    return inputs.ProtocolConfig.model_validate(tomllib.loads(CONFIG.read_text()))


def updated(config, **changes):
    return type(config).model_validate(config.model_dump() | changes)


def layout(path=CSV):
    with path.open(newline="", encoding="utf-8-sig") as stream:
        return inputs.PlateLayout.model_validate({"conditions": list(csv.DictReader(stream))})


def protocol():
    config = configuration()
    return recipe.protocol(inputs.prepare_wells(layout(), config), config)


try:
    from opentrons.simulate import simulate
except ImportError:
    simulate = None


def test_preparation_matches_independent_doses_inventory_and_identity():
    bundle = compile(protocol(), to=None)
    transfers = [s for s in bundle.protocol.steps if isinstance(s, Transfer)]

    # Expected Van dose in uL; fixed additions are 20 Sal + 2 TMP + 2 DMSO + 10 culture.
    expected = {
        "A1": (None, "0"),
        "A5": ("A1", "1.5625"),
        "A6": ("A1", "3.125"),
        "A7": ("A1", "6.25"),
        "A8": ("A1", "12.5"),
        "A9": ("A1", "25"),
        "A10": ("A1", "50"),
        "A11": ("A2", "10"),
        "A12": ("A2", "20"),
    }
    targets = {
        row.well: (row.inducer1_concentration * row.inducer1_units).to("umol/liter").magnitude
        for row in layout().conditions
    }
    for well, (stock, dose) in expected.items():
        moves = [s for s in transfers if s.destination == Location("cultures", well)]
        sources = [Location("medium", "A1"), Location("reagents", "A5")]
        volumes = [Decimal(166) - Decimal(dose), Decimal(2)]
        if stock is not None:
            sources.append(Location("reagents", stock))
            volumes.append(Decimal(dose))
            stock_um = 100 if stock == "A1" else 1000
            assert Decimal(dose) * stock_um / 200 == targets[well]
        sources.extend(Location("reagents", source) for source in ("A3", "A4", "B1"))
        volumes.extend((Decimal(20), Decimal(2), Decimal(10)))
        assert [s.source for s in moves] == sources
        assert [s.volume for s in moves] == volumes
        assert sum(s.volume for s in moves) == 200
        assert (volumes[1] + Decimal(2)) / 200 == Decimal("0.02")

    remaining = dict(bundle.final_volumes)
    assert {well: remaining[Location("cultures", well)] for well in expected} == dict.fromkeys(
        expected, Decimal(200)
    )
    for well, volume in (
        ("A1", "201.5625"),
        ("A2", "170"),
        ("A3", "120"),
        ("A4", "82"),
        ("A5", "82"),
        ("B1", "210"),
    ):
        assert remaining[Location("reagents", well)] == Decimal(volume)
    assert remaining[Location("medium", "A1")] == Decimal("1634.4375")
    assert len(bundle.manifest.samples) == 9
    assert {p.location.well for p in bundle.manifest.placements} == set(expected)
    assert all(
        s.material_identity == "example-plasmid-1"
        and s.parent_ids == ("inoculum-B1",)
        and s.replicate is None
        for s in bundle.manifest.samples
    )
    assert '"name": "DMSO"' in bundle.protocol.description
    assert "DMSO" in bundle.files["protocol.html"]
    assert all(s.liquid_class is not None for s in bundle.protocol.steps)
    assert all(isinstance(s, (Transfer, Mix)) for s in bundle.protocol.steps)
    mixes = [s for s in bundle.protocol.steps if isinstance(s, Mix)]
    assert [(s.location, s.volume, s.cycles) for s in mixes] == [
        item
        for well in expected
        for item in ((Location("reagents", "B1"), 50, 3), (Location("cultures", well), 100, 3))
    ]


@pytest.mark.integration
@pytest.mark.skipif(simulate is None, reason="Opentrons SDK not installed")
def test_generated_ot2_actions_match_transfers_mixes_and_tips(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    bundle = compile(
        protocol(), deck=configuration().deck, liquid_handler=LiquidHandler.OT2, to=None
    )
    log, _ = simulate(StringIO(bundle.files["protocol.py"]))
    texts = [event["payload"]["text"] for event in log]
    aspirate = re.compile(r"Aspirating ([0-9.]+) uL from ([A-H]\d+) of .+ on slot (\d+) at ")
    dispense = re.compile(r"Dispensing ([0-9.]+) uL into ([A-H]\d+) of .+ on slot (\d+) at ")
    pending, moves = None, []
    for text in texts:
        start, end = aspirate.search(text), dispense.search(text)
        if start:
            pending = (start[3], start[2], Decimal(start[1]))
        elif end:
            assert pending is not None
            destination = (end[3], end[2])
            if destination != pending[:2]:
                assert Decimal(end[1]) == pending[2]
                moves.append((*pending[:2], *destination, pending[2]))
    bindings = {
        b.location: tuple(b.physical.removeprefix("slot ").split("/"))
        for b in bundle.target.bindings
    }
    expected = [
        (*bindings[s.source], *bindings[s.destination], s.volume)
        for s in bundle.protocol.steps
        if isinstance(s, Transfer)
    ]
    assert moves == expected
    mixes = [s for s in bundle.protocol.steps if isinstance(s, Mix)]
    actual_mixes = [text for text in texts if text.startswith("Mixing ")]
    assert len(actual_mixes) == len(mixes)
    for actual, expected_mix in zip(actual_mixes, mixes, strict=True):
        assert actual.startswith(f"Mixing {expected_mix.cycles} times with a volume of ")
        volume = re.search(r"volume of ([0-9.]+)", actual)
        assert volume and Decimal(volume[1]) == expected_mix.volume
    tips = len(expected) + len(mixes)
    assert sum(t.startswith("Picking up tip") for t in texts) == tips
    assert sum(t.startswith("Dropping tip") for t in texts) == tips


@pytest.mark.parametrize(
    "change, message",
    [
        (lambda s: updated(s, cultures=()), "no source"),
        (lambda s: updated(s, stocks=()), "no stock"),
        (lambda s: updated(s, vehicle_fraction=Decimal("0.005")), "exceed"),
        (lambda s: updated(s, final_volume=30 * uL), "available above its dead volume"),
        (
            lambda s: updated(
                s,
                cultures=(updated(s.cultures[0], volume=25 * uL),),
            ),
            "available above its dead volume",
        ),
        (lambda s: updated(s, vehicle=updated(s.vehicle, well="A1")), "assigned more than once"),
    ],
)
def test_preparation_rejects_infeasible_or_unknown_inputs(change, message):
    with pytest.raises((ValueError, CompileError), match=message):
        config = change(configuration())
        compile(recipe.protocol(inputs.prepare_wells(layout(), config), config), to=None)


@pytest.mark.parametrize(
    "old, new, message",
    [
        (
            "solute_and_vehicle_free_medium_and_cultures = true",
            "solute_and_vehicle_free_medium_and_cultures = false",
            "carryover",
        ),
        ('source_dead_volume = "20 uL"', 'source_dead_volume = "-1 uL"', "nonnegative"),
        ('vehicle_fraction = "0.02"', 'vehicle_fraction = "NaN"', "finite"),
        ('inoculum_volume = "10 uL"', 'inoculum_volume = "200 uL"', "less than"),
        ('liquid_class = "aqueous"', 'liquid_class = "missing"', "missing"),
        ('dispense_flow_rate = "30 uL/s"', 'dispense_flow_rate = "30 uL"', "compatible"),
        ('dispense_flow_rate = "30 uL/s"', 'dispense_fow_rate = "30 uL/s"', "Extra inputs"),
    ],
)
def test_preparation_file_validates_explicit_inputs(tmp_path, old, new, message):
    path = tmp_path / "config.toml"
    path.write_text(CONFIG.read_text().replace(old, new))
    with pytest.raises((ValueError, TypeError, KeyError), match=message):
        inputs.ProtocolConfig.model_validate(tomllib.loads(path.read_text()))


@pytest.mark.parametrize(
    "key,value",
    [
        ("well", "Z1"),
        ("column", "2"),
        ("position", "2"),
        ("inducer1Concentration", "nan"),
        ("inducer1Concentration", "-1"),
        ("inducer1Units", "uL"),
        ("inducer1Id", "Sal"),
        ("plasmid", "none"),
    ],
)
def test_csv_rejects_invalid_records_with_row_location(tmp_path, key, value):
    with CSV.open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    rows[0][key] = value
    path = tmp_path / "invalid.csv"
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    with pytest.raises(ValidationError) as caught:
        layout(path)
    assert all(error["loc"][:2] == ("conditions", 0) for error in caught.value.errors())


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
    bundle = compile(recipe.protocol(inputs.prepare_wells(layout(path), config), config), to=None)
    van = [
        s
        for s in bundle.protocol.steps
        if isinstance(s, Transfer) and s.source == Location("reagents", "A1")
    ]
    assert [(s.destination.well, s.volume) for s in van] == [("A1", 10), ("A5", 10)]
    assert len(bundle.manifest.samples) == 2


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
    for location, volume in bundle.final_volumes:
        resource = handler.deck.get_resource(location.resource).get_item(location.well)
        assert Decimal(str(resource.tracker.get_used_volume())) == volume


def test_config_round_trip_preserves_toml_shape_and_resolves_classes_explicitly():
    raw = tomllib.loads(CONFIG.read_text())
    config = inputs.ProtocolConfig.model_validate(raw)
    saved = config.model_dump(mode="json")
    assert saved.keys() == raw.keys()
    for field in ("medium", "vehicle", "source_mix", "destination_mix"):
        assert saved[field].keys() == raw[field].keys()
    for field in ("stocks", "cultures"):
        assert all(
            actual.keys() == source.keys()
            for actual, source in zip(saved[field], raw[field], strict=True)
        )
    assert config.medium.name == raw["medium"]["name"]
    assert config.medium.dead_volume == 500 * uL
    assert config.medium.liquid_class == "aqueous"
    assert config.source_mix.liquid_class == "culture"
    assert config.stocks[0].well == raw["stocks"][0]["well"]
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
    bundle = compile(
        recipe.protocol(inputs.prepare_wells(layout(), native_config), native_config), to=None
    )
    medium_class = bundle.protocol.steps[0].liquid_class
    assert isinstance(medium_class, HamiltonLiquidClass)
    assert medium_class.name == "HighVolumeFilter_Water_DispenseSurface_Part"
    mixing_class = next(
        step for step in bundle.protocol.steps if isinstance(step, Mix)
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
    bundle = compile(recipe.protocol(inputs.prepare_wells(layout(), config), config), to=None)
    recorded = json.loads(bundle.protocol.description.split("Protocol configuration: ", 1)[1])
    assert recorded == config.model_dump(mode="json")
    assert recorded["final_volume"] == "180 microliter"
    assert recorded["medium"]["name"] == "Specified medium"
    assert all(
        dict(bundle.final_volumes)[Location("cultures", well)] == 180 for well in config.wells
    )
    with pytest.raises(ValidationError, match="less than final"):
        updated(config, inoculum_volume=200 * uL)


def test_config_schema_describes_nested_inputs_and_unit_strings():
    schema = inputs.ProtocolConfig.model_json_schema()
    assert schema["additionalProperties"] is False
    assert schema["properties"]["final_volume"]["type"] == "string"
    assert schema["$defs"]["MediumConfig"]["properties"]["dead_volume"]["type"] == "string"
    assert schema["$defs"]["MediumConfig"]["properties"]["liquid_class"]["type"] == "string"


def test_csv_model_parses_typed_records_and_round_trips_source_columns():
    parsed = layout()
    row = next(condition for condition in parsed.conditions if condition.well == "A5")
    assert row.position == 33
    assert row.inducer1_concentration == Decimal("0.78125")
    assert row.inducer1_concentration * row.inducer1_units == units("0.78125 umol/liter")
    saved = parsed.model_dump_json(by_alias=True)
    assert "inducer1Concentration" in json.loads(saved)["conditions"][0]
    assert inputs.PlateLayout.model_validate_json(saved) == parsed


@pytest.mark.parametrize("count, message", [(0, "at least 1"), (2, "Repeated CSV destination")])
def test_csv_model_rejects_empty_layouts_and_repeated_destinations(count, message):
    row = layout().conditions[0]
    with pytest.raises(ValidationError, match=message):
        inputs.PlateLayout.model_validate({"conditions": [row] * count})


def test_protocol_consumes_prepared_inputs_without_parsing_or_recalculating(monkeypatch):
    config = configuration()
    prepared = inputs.prepare_wells(layout(), config)

    def unexpected(*args, **kwargs):
        pytest.fail("Protocol construction must consume the already prepared inputs")

    with monkeypatch.context() as patch:
        patch.setattr(inputs.ProtocolConfig, "model_validate", unexpected)
        patch.setattr(inputs.PlateLayout, "model_validate", unexpected)
        patch.setattr(inputs, "prepare_wells", unexpected)
        patch.setattr(inputs, "dilution_volume", unexpected)
        patch.setattr(Path, "open", unexpected)
        patch.setattr(Path, "read_bytes", unexpected)
        p = recipe.protocol(prepared, config)
    bundle = compile(p, to=None)
    assert all(
        dict(bundle.final_volumes)[Location("cultures", well)] == 200 for well in config.wells
    )


def test_cli_config_and_csv_provenance_match_the_prepared_inputs(tmp_path, monkeypatch):
    output = tmp_path / "compiled"
    monkeypatch.setattr(
        sys,
        "argv",
        ["concentration_series", "--csv", str(CSV), "--config", str(CONFIG), "--out", str(output)],
    )
    cli.main()
    plan = json.loads((output / "plan.json").read_text())
    description = plan["protocol"]["description"]
    assert CSV.name in description
    assert hashlib.sha256(CSV.read_bytes()).hexdigest() in description
    assert json.loads(
        description.split("Protocol configuration: ", 1)[1]
    ) == configuration().model_dump(mode="json")
    assert len(plan["protocol"]["steps"]) == 71


@pytest.mark.parametrize(
    "config_change, csv_change, message",
    [
        (None, ("0.78125", "NaN"), "finite"),
        (None, ("inducer1Units", "unknownColumn"), "CSV schema"),
        (None, ("A5,A,5", "A5,A,6"), "Inconsistent position"),
        (None, ("example-plasmid-1", "unknown-plasmid"), "no source"),
        (('"A1", "A5"', '"A1", "A13"'), None, "wells.1"),
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

    monkeypatch.setattr(cli, "protocol", unexpected)
    with pytest.raises(SystemExit) as caught:
        cli.main()
    assert caught.value.code == 2
    assert message in capsys.readouterr().err
