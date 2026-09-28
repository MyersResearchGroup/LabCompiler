"""Calculate products from explicitly selected restriction digest fragments."""

from collections.abc import Mapping
from dataclasses import dataclass

from lab._version import __version__
from lab.artifacts import digest
from lab.experiments.cloning._dna import DnaSequence, Duplex
from lab.experiments.cloning.systems import AssemblyRecipe, FragmentSelection
from lab.provenance import (
    Activity,
    Agent,
    AgentKind,
    Association,
    Component,
    DocumentSnapshot,
    EvidenceState,
    Plan,
    Ref,
    Sequence,
    TopLevel,
    Usage,
)
from lab.provenance.vocabulary import DNA, IUPAC_DNA, LAB

CIRCULAR = "https://identifiers.org/SO:0000988"
LINEAR = "https://identifiers.org/SO:0000987"


@dataclass(frozen=True, kw_only=True)
class DigestFragment:
    selection: FragmentSelection
    watson: str
    crick: str
    overhang: int

    def record(self) -> Duplex:
        return Duplex(self.watson, self.crick, self.overhang)


def sequence_record(
    component: Ref[Component],
    document: DocumentSnapshot,
    resolved: Mapping[str, Ref[Component]] | None = None,
) -> DnaSequence:
    actual = (resolved or {}).get(component.identity, component)
    design = document.get(actual.identity, Component)
    sequences = [document.get(ref.identity, Sequence) for ref in design.sequences]
    dna = [sequence for sequence in sequences if sequence.encoding == IUPAC_DNA]
    if DNA not in design.types or len(dna) != 1:
        raise ValueError(f"{design.identity} needs exactly one explicit DNA sequence")
    if not dna[0].elements or set(dna[0].elements.upper()) - set("ACGT"):
        raise ValueError(f"{design.identity} needs a complete, unambiguous DNA sequence")
    if (CIRCULAR in design.types) == (LINEAR in design.types):
        raise ValueError(
            f"{design.identity} needs exactly one explicit circular or linear topology"
        )
    return DnaSequence(dna[0].elements.upper(), circular=CIRCULAR in design.types)


def digest_fragments(
    component: Ref[Component],
    *,
    document: DocumentSnapshot,
    enzyme: str,
    resolved: Mapping[str, Ref[Component]] | None = None,
) -> tuple[DigestFragment, ...]:
    record = sequence_record(component, document, resolved)
    result: list[DigestFragment] = []
    for left, right, fragment in record.digest(enzyme):
        result.append(
            DigestFragment(
                selection=FragmentSelection(component=component, left_cut=left, right_cut=right),
                watson=fragment.watson,
                crick=fragment.crick,
                overhang=fragment.overhang,
            )
        )
    return tuple(result)


@dataclass(frozen=True, kw_only=True)
class AssemblyDesign:
    product: Component
    sequence: Sequence
    activity: Activity
    agent: Agent
    plan: Plan

    @property
    def objects(self) -> tuple[TopLevel, ...]:
        return self.product, self.sequence, self.activity, self.agent, self.plan


def calculate_assembly(
    recipe: AssemblyRecipe,
    *,
    document: DocumentSnapshot,
    resolved: Mapping[str, Ref[Component]] | None = None,
) -> AssemblyDesign:
    selected: list[Duplex] = []
    inputs: dict[str, Ref[Component]] = {}
    for selection in recipe.fragments:
        fragments = digest_fragments(
            selection.component, document=document, enzyme=recipe.enzyme, resolved=resolved
        )
        matches = [
            fragment
            for fragment in fragments
            if fragment.selection.left_cut == selection.left_cut
            and fragment.selection.right_cut == selection.right_cut
        ]
        if len(matches) != 1:
            raise ValueError(
                f"{selection.component.identity}: cut pair "
                f"({selection.left_cut}, {selection.right_cut}) "
                "does not select exactly one digest fragment"
            )
        fragment = matches[0].record()
        selected.append(fragment.reverse_complement() if selection.reverse_complement else fragment)
        actual = (resolved or {}).get(selection.component.identity, selection.component)
        inputs[actual.identity] = actual
    product = selected[0]
    try:
        for fragment in selected[1:]:
            product = product.ligate(fragment)
        elements = product.close() if recipe.circular else product.linear_sequence()
    except (ValueError, TypeError) as error:
        raise ValueError(
            f"{recipe.identity}: selected fragment ends are incompatible: {error}"
        ) from error
    try:
        intended = document.get(recipe.product.identity, Component)
    except KeyError:
        intended = None
    if intended is not None:
        inputs[intended.identity] = intended.ref
        if intended.sequences:
            expected = sequence_record(intended.ref, document)
            equal = len(expected.elements) == len(elements) and (
                elements in expected.elements * 2
                if recipe.circular
                else elements == expected.elements
            )
            if expected.circular != recipe.circular or not equal:
                raise ValueError(
                    f"{recipe.identity}: calculated sequence does not match the requested design"
                )
    calculation_id = (
        recipe.identity + "/calculation_" + digest((recipe, tuple(inputs), tuple(selected)))[:16]
    )
    agent = Agent(
        identity=calculation_id + "/calculator",
        kind=AgentKind.SOFTWARE,
        name="Lab sequence calculation",
        software_version=f"lab-compiler {__version__}; Biopython 1.84",
    )
    plan = Plan(
        identity=calculation_id + "/method",
        description=(
            f"Ordered restriction-fragment ligation using {recipe.enzyme}; "
            f"circular={recipe.circular}."
        ),
    )
    activity = Activity(
        identity=calculation_id,
        types=(LAB + "sequenceCalculation",),
        evidence_state=EvidenceState.RECORDED,
        usage=tuple(Usage(entity=ref) for ref in inputs.values()),
        association=(Association(agent=agent.ref, plan=plan.ref),),
    )
    sequence = Sequence(
        identity=calculation_id + "/sequence",
        elements=elements,
        encoding=IUPAC_DNA,
        generated_by=(activity.ref,),
    )
    design = Component(
        identity=calculation_id + "/product",
        name=None if intended is None else intended.name,
        types=(DNA, CIRCULAR if recipe.circular else LINEAR),
        sequences=(sequence.ref,),
        derived_from=tuple(inputs.values()),
        generated_by=(activity.ref,),
    )
    return AssemblyDesign(
        product=design, sequence=sequence, activity=activity, agent=agent, plan=plan
    )
