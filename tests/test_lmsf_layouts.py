"""Audit the source layouts independently of the single executable recipe."""

import csv
import tomllib
from collections import Counter
from decimal import Decimal
from pathlib import Path

import pytest

from examples.concentration_series import inputs

ROOT = Path(__file__).resolve().parents[1] / "docs/lmsf/assay-options"
LAYOUTS = {
    "pooled-may": "2026-05-11_Mar-Pro_1_DNA-5-plates_growth-plate_5.csv",
    "fitness-may": "2026-05-14_Mar-Pro_OD-test-5-plates_growth-plate_5.csv",
    "fitness-july": "2026-07-23_Mar-Pro_OD-test-5-plates_growth-plate_5.csv",
    "expression": "2026-08-13_Cytom-12-plasmids_cytom-plate_1-3.csv",
    "pooled-august": "2026-08-19_Mar-Pro_1_DNA-5-plates_growth-plate_5.csv",
}


def rows(name):
    with (ROOT / LAYOUTS[name]).open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def condition(row):
    return tuple(
        Decimal(row[key])
        for key in ("inducer1Concentration", "inducer2Concentration", "selectorConc")
    )


@pytest.mark.parametrize("name", LAYOUTS)
def test_all_480_rows_preserve_coordinates_schema_and_units(name):
    layout = rows(name)
    assert len(layout) == 96
    assert {r["well"] for r in layout} == {f"{r}{c}" for r in "ABCDEFGH" for c in range(1, 13)}
    factors = (
        (("inducerId", "inducerConcentration", "inducerUnits"),)
        if name == "expression"
        else (
            ("inducer1Id", "inducer1Concentration", "inducer1Units"),
            ("inducer2Id", "inducer2Concentration", "inducer2Units"),
        )
    ) + (("selectorId", "selectorConc", "selectorUnits"),)
    schema = {"position", "well", "row", "column", "strain", "plasmid"}
    schema.update(key for factor in factors for key in factor)
    for position, row in enumerate(layout, start=1):
        assert set(row) == schema and all(value is not None for value in row.values())
        assert int(row["position"]) == position
        assert row["well"] == f"{row['row']}{int(row['column'])}"
        assert position == 8 * (int(row["column"]) - 1) + ord(row["row"]) - 64
        assert (row["strain"] == "none") == (row["plasmid"] == "none")
        for name_key, value_key, unit_key in factors:
            value = Decimal(row[value_key])
            assert value.is_finite() and value >= 0
            if name == "expression":
                assert row[name_key] == "none" and value == 0
                assert row[unit_key] in ("N/A", "none")
            else:
                expected = {
                    "inducer1Id": ("Van", "umol/L"),
                    "inducer2Id": ("Sal", "umol/L"),
                    "selectorId": ("TMP", "ug/mL"),
                }
                assert (row[name_key], row[unit_key]) == expected[name_key]


@pytest.mark.parametrize(
    "name,materials,conditions,combinations",
    [
        ("pooled-may", 1, 24, 24),
        ("fitness-may", 4, 23, 92),
        ("fitness-july", 4, 24, 96),
        ("pooled-august", 1, 24, 24),
    ],
)
def test_material_assignments_and_distinct_conditions(name, materials, conditions, combinations):
    layout = rows(name)
    assert {r["strain"] for r in layout} == {"Mar-Clo"}
    assert len({r["plasmid"] for r in layout}) == materials
    assert len({condition(r) for r in layout}) == conditions
    assert len({(r["plasmid"], condition(r)) for r in layout}) == combinations
    if name.startswith("pooled"):
        assert set(Counter(condition(r) for r in layout).values()) == {4}
        pool = "Profluent_lib_1_mix" if name == "pooled-may" else "Profluent_lib_2_mix"
        assert {r["plasmid"] for r in layout} == {pool}
    else:
        materials_by_row = dict(
            zip(
                "ABCDEFGH",
                (
                    "pDRAC18-2-A",
                    "pDRAC18-2-A",
                    "pDRAC27-2-A",
                    "pDRAC27-2-A",
                    "pDRAC28-2-A",
                    "pDRAC28-2-A",
                    "pDRAC33-2-A",
                    "pDRAC33-2-A",
                ),
                strict=True,
            )
        )
        assert all(r["plasmid"] == materials_by_row[r["row"]] for r in layout)


def test_v6_layouts_differ_only_in_material_and_retain_asymmetric_series():
    july, august = rows("fitness-july"), rows("pooled-august")
    assert [{k: v for k, v in r.items() if k != "plasmid"} for r in july] == [
        {k: v for k, v in r.items() if k != "plasmid"} for r in august
    ]
    assert all(a["plasmid"] != b["plasmid"] for a, b in zip(july, august, strict=True))
    low = [r for r in july if r["row"] == "A" and condition(r)[1:] == (10, 3)]
    high = [r for r in july if r["row"] == "B" and condition(r)[1:] == (25, 3)]
    assert len(low) == 9 and len(high) == 6
    assert low[0]["well"] == "A1"  # one baseline occurrence, shared by analysis uses


def test_may_duplicate_baselines_and_unresolved_cytometry_positions():
    baselines = [r for r in rows("fitness-may") if condition(r) == (0, 0, 3)]
    assert {r["well"] for r in baselines} == {f"{r}2" for r in "ABCDEFGH"}
    assigned = [r for r in rows("expression") if r["plasmid"] != "none"]
    assert len(assigned) == 12 and len({r["plasmid"] for r in assigned}) == 5
    assert {r["well"] for r in assigned} == {f"{r}{c}" for r in "BCDEFG" for c in (2, 5)}
    assert {r["strain"] for r in assigned} == {"NEB-10-beta"}
    assert sum(r["plasmid"] == "none" for r in rows("expression")) == 84


def test_example_uses_the_complete_low_sal_series_for_one_material():
    config = inputs.ProtocolConfig.model_validate(
        tomllib.loads((ROOT.parents[2] / "examples/concentration_series/config.toml").read_text())
    )
    selected = [r for r in rows("fitness-july") if r["row"] == "A" and condition(r)[1:] == (10, 3)]
    assert tuple(r["well"] for r in selected) == config.wells
    layout = inputs.PlateLayout.model_validate({"conditions": rows("fitness-july")})
    parsed = {r.well: r for r in layout.conditions}
    for row in selected:
        assert parsed[row["well"]].plasmid == row["plasmid"] == config.cultures[0].plasmid
        assert (parsed[row["well"]].inducer1_concentration * parsed[row["well"]].inducer1_units).to(
            "umol/liter"
        ).magnitude == condition(row)[0]
