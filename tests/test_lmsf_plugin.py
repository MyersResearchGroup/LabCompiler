"""The LMSF destination CSV as a view of a plate design."""

import csv
import json
from decimal import Decimal
from io import StringIO
from pathlib import Path

import pytest
from pydantic import ValidationError

from lab import uL
from lab.plates import PlateDesign, find_serial_dilutions, parallelize, well_target
from lab.plugins.lmsf import Condition, material_name, read_layout, split_material, write_layout
from lab.units import units

CSV = Path(__file__).resolve().parents[1] / "examples/concentration_series/layout.csv"


def test_layout_round_trips_byte_for_byte_and_describes_the_plate():
    text = CSV.read_text()
    design = read_layout(text, name="cultures", final_volume=200 * uL)
    assert len(design.wells) == 96 and design.solutes == ("Sal", "TMP", "Van")
    assert design.materials == tuple(f"example-strain / example-plasmid-{i}" for i in range(1, 5))
    a5 = design["A5"]
    assert a5.concentration("Van").quantity == units("0.78125 umol/liter")
    assert a5.concentration("TMP").unit == "gram/liter"
    assert write_layout(design, solute_order=("Van", "Sal", "TMP")) == text
    van = parallelize(find_serial_dilutions(design, "Van"))
    assert [(c.levels[0], c.steps, c.zero_wells) for c in van] == [
        (("A12", "C12", "E12", "G12"), 8, ()),
        (("B12", "D12", "F12", "H12"), 5, ("B7", "D7", "F7", "H7")),
    ]
    sal = find_serial_dilutions(design, "Sal")
    assert [(c.levels[0][0], c.steps, c.fold) for c in sal][:2] == [("A1", 3, 2), ("B1", 3, 2)]
    assert sal[1].top == Decimal("0.0000142857142857143")


def test_condition_model_parses_typed_records_and_round_trips_source_columns():
    with CSV.open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    row = Condition.model_validate(next(r for r in rows if r["well"] == "A5"))
    assert row.position == 33 and row.inducer1_concentration == Decimal("0.78125")
    saved = json.loads(row.model_dump_json(by_alias=True))
    assert saved["inducer1Concentration"] == "0.78125"
    assert row.inducer1_concentration * row.inducer1_units == units("0.78125 umol/liter")
    assert Condition.model_validate_json(row.model_dump_json(by_alias=True)) == row
    assert split_material(material_name("s", "p")) == ("s", "p")
    with pytest.raises(ValueError, match="strain / plasmid"):
        split_material("no separator")


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
def test_layout_rejects_invalid_records_with_row_location(key, value):
    with CSV.open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    rows[0][key] = value
    with pytest.raises(ValidationError):
        Condition.model_validate(rows[0])
    text = _text(rows)
    with pytest.raises(ValueError, match="CSV row 2"):
        read_layout(text, name="cultures", final_volume=200 * uL)


def _text(rows):
    out = StringIO()
    writer = csv.DictWriter(out, fieldnames=rows[0].keys(), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()


def test_layout_rejects_wrong_schema_empty_files_and_repeated_wells():
    text = CSV.read_text()
    with pytest.raises(ValueError, match="CSV schema"):
        read_layout(text.replace("inducer1Units", "unknownColumn"), name="c", final_volume=1 * uL)
    with pytest.raises(ValueError, match="no destination rows"):
        read_layout(text.splitlines()[0] + "\n", name="c", final_volume=1 * uL)
    lines = text.splitlines(True)
    with pytest.raises(ValueError, match="specified twice"):
        read_layout("".join(lines[:2] + lines[1:2]), name="c", final_volume=1 * uL)


def test_write_layout_requires_three_solutes_and_a_material():
    design = PlateDesign.build(
        "p", final_volume=1 * uL, wells=[well_target("A1", concentrations={"Van": 1 * units("uM")})]
    )
    with pytest.raises(ValueError, match="exactly three solutes"):
        write_layout(design, solute_order=("Van", "Sal", "TMP"))
    with pytest.raises(ValueError, match="three distinct"):
        write_layout(design, solute_order=("Van", "Van", "TMP"))
