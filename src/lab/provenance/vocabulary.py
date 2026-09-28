"""SBOL, PROV, and Lab terms used by the public provenance model.

Ontology terms remain ordinary absolute IRIs, so callers can use other ontologies
without registering Python classes or changing a process-wide namespace.
"""

from enum import StrEnum

SBOL = "http://sbols.org/v3#"
PROV = "http://www.w3.org/ns/prov#"
OM = "http://www.ontology-of-units-of-measure.org/resource/om-2/"
LAB = "https://the-lab-compiler.github.io/lab-py/ns#"

DNA = "https://identifiers.org/SBO:0000251"
RNA = "https://identifiers.org/SBO:0000250"
PROTEIN = "https://identifiers.org/SBO:0000252"
SMALL_MOLECULE = "https://identifiers.org/SBO:0000247"
FUNCTIONAL_ENTITY = "https://identifiers.org/SBO:0000241"
IUPAC_DNA = "https://identifiers.org/edam:format_1207"
IUPAC_PROTEIN = "https://identifiers.org/edam:format_1208"
INLINE = SBOL + "inline"
REVERSE_COMPLEMENT = SBOL + "reverseComplement"
PRECEDES = SBOL + "precedes"


class EvidenceState(StrEnum):
    """Nature of an assertion, independent of experimental verification."""

    UNKNOWN = LAB + "unknown"
    PLANNED = LAB + "planned"
    RECORDED = LAB + "recorded"
    SIMULATED = LAB + "simulated"


class AgentKind(StrEnum):
    PERSON = PROV + "Person"
    ORGANIZATION = PROV + "Organization"
    SOFTWARE = PROV + "SoftwareAgent"
