"""Pinned LabOP primitives and explicit Lab extensions for unmatched semantics."""

from dataclasses import dataclass
from importlib.resources import files

from rdflib import Graph, Namespace, URIRef

UML = Namespace("http://bioprotocols.org/uml#")
LABOP = Namespace("http://bioprotocols.org/labop#")
SBOL = Namespace("http://sbols.org/v3#")
OM = Namespace("http://www.ontology-of-units-of-measure.org/resource/om-2/")
LAB = Namespace("https://the-lab-compiler.github.io/lab-py/ns#")
EXT = "https://the-lab-compiler.github.io/lab-py/labop/primitives/"
LIQUID = "https://bioprotocols.org/labop/primitives/liquid_handling/"


@dataclass(frozen=True)
class Parameter:
    name: str
    type: URIRef
    required: bool = True


EXTENSIONS = {
    "Wait": (Parameter("duration", OM.Measure),),
    "SetTemperature": (
        Parameter("samples", LABOP.SampleCollection),
        Parameter("temperature", OM.Measure),
    ),
    "Thermocycle": (
        Parameter("samples", LABOP.SampleCollection),
        Parameter("profile", LAB.ThermalProfile),
        Parameter("cycles", UML.ValueSpecification),
        Parameter("lidTemperature", OM.Measure, False),
        Parameter("blockVolume", OM.Measure, False),
    ),
    "Distribute": (
        Parameter("source", LABOP.SampleCollection),
        Parameter("destinations", LABOP.SampleCollection),
        Parameter("amount", OM.Measure),
        Parameter("airGap", OM.Measure, False),
    ),
    "OperatorPause": (Parameter("instruction", UML.ValueSpecification),),
}

DESCRIPTIONS = {
    "Wait": "Wait for the specified duration without changing material.",
    "SetTemperature": "Set a persistent temperature on the collection's controlling module. "
    "The hold continues after the action completes.",
    "Thermocycle": "Apply the ordered temperature and duration holds for the specified cycles "
    "to the whole resource; then release temperature control. "
    "No evaporation or yield change is inferred.",
    "Distribute": "Transfer the same liquid amount from one source to each ordered destination, "
    "reusing one tip. Air gap is air and does not change the liquid ledger. "
    "A target may split aspirations within its capacity.",
    "OperatorPause": "Pause for the stated operator instruction; no material change is modeled.",
}


def upstream_liquid_primitives() -> Graph:
    graph = Graph()
    graph.parse(
        data=files("lab.labop").joinpath("resources/primitives.ttl").read_text(), format="turtle"
    )
    return graph
