"""Plate designs: the desired end state of a plate, the patterns in it, and its preparation."""

from lab.plates.advice import advise_design
from lab.plates.arrange import (
    Arrangement,
    ConditionSpec,
    ControlSpec,
    GradientSpec,
    InferredRequirements,
    Requirements,
    arrange,
    infer_requirements,
)
from lab.plates.design import PlateDesign, Target, WellTarget, canonical_concentration, well_target
from lab.plates.dilution import SerialDilutionPlan, plan_serial_dilution, record_serial_dilution
from lab.plates.patterns import (
    ReplicateGroup,
    SerialDilutionCandidate,
    find_replicate_groups,
    find_serial_dilutions,
    parallelize,
)
from lab.plates.premix import PremixPlan, plan_premix, record_premix
from lab.plates.recipe import (
    AssemblyReport,
    Mixing,
    Settings,
    Stock,
    Strategy,
    WellPlan,
    assemble,
    select_stock,
)

__all__ = [
    "Arrangement",
    "AssemblyReport",
    "ConditionSpec",
    "ControlSpec",
    "GradientSpec",
    "InferredRequirements",
    "Mixing",
    "PlateDesign",
    "PremixPlan",
    "ReplicateGroup",
    "Requirements",
    "SerialDilutionCandidate",
    "SerialDilutionPlan",
    "Settings",
    "Stock",
    "Strategy",
    "Target",
    "WellPlan",
    "WellTarget",
    "advise_design",
    "arrange",
    "assemble",
    "canonical_concentration",
    "find_replicate_groups",
    "find_serial_dilutions",
    "infer_requirements",
    "parallelize",
    "plan_premix",
    "plan_serial_dilution",
    "record_premix",
    "record_serial_dilution",
    "select_stock",
    "well_target",
]
