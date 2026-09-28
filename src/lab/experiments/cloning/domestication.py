"""Sequence edit proposals that require an explicit caller selection."""

from dataclasses import dataclass, replace

from lab.experiments.cloning._dna import DnaSequence
from lab.experiments.cloning.sequences import sequence_record
from lab.provenance import (
    Activity,
    Component,
    Document,
    DocumentSnapshot,
    EvidenceState,
    Ref,
    Sequence,
    Usage,
)
from lab.provenance.types import require_iri
from lab.provenance.vocabulary import IUPAC_DNA, LAB


@dataclass(frozen=True, kw_only=True)
class SequenceEdit:
    position: int
    before: str
    after: str

    def __post_init__(self) -> None:
        if type(self.position) is not int or self.position < 0:
            raise ValueError("Edit positions must be nonnegative integers")
        if (
            self.before not in tuple("ACGT")
            or self.after not in tuple("ACGT")
            or self.before == self.after
        ):
            raise ValueError("An edit replaces one unambiguous base with a different base")


@dataclass(frozen=True, kw_only=True)
class EditProposal:
    """A single-base candidate; no claim is made about biological function.

    Positions are zero-based. Further sites can remain after this edit. Applying
    a proposal creates a new design; it does not alter inventory or a recipe.
    """

    component: Ref[Component]
    sequence: Ref[Sequence]
    enzyme: str
    edit: SequenceEdit
    remaining_sites: int

    def apply(self, document: DocumentSnapshot, *, identity: str) -> DocumentSnapshot:
        require_iri(identity)
        original = document.get(self.component.identity, Component)
        sequence = document.get(self.sequence.identity, Sequence)
        if sequence.ref not in original.sequences:
            raise ValueError("The proposal sequence does not belong to its component")
        if (
            sequence.elements[self.edit.position : self.edit.position + 1].upper()
            != self.edit.before
        ):
            raise ValueError("Edit proposal no longer matches the input sequence")
        if identity == original.identity:
            raise ValueError("An edited design needs a new identity")
        elements = (
            sequence.elements[: self.edit.position]
            + self.edit.after
            + sequence.elements[self.edit.position + 1 :]
        )
        activity = Activity(
            identity=identity + "/edit",
            types=(LAB + "sequenceEdit",),
            usage=(Usage(entity=original.ref), Usage(entity=sequence.ref)),
            evidence_state=EvidenceState.RECORDED,
        )
        edited_sequence = replace(
            sequence,
            identity=identity + "/sequence",
            namespace=None,
            elements=elements,
            derived_from=(sequence.ref,),
            generated_by=(activity.ref,),
        )
        # Coordinate-bearing annotations require review after an edit. Keep the
        # original linked rather than copying annotations onto changed bases.
        edited = Component(
            identity=identity,
            types=original.types,
            roles=original.roles,
            name=original.name,
            sequences=(edited_sequence.ref,),
            derived_from=(original.ref,),
            generated_by=(activity.ref,),
        )
        result = Document.from_snapshot(document)
        result.add(activity, edited_sequence, edited)
        return result.freeze()


def propose_edits(
    component: Ref[Component],
    *,
    document: DocumentSnapshot,
    enzyme: str,
    editable_positions: tuple[int, ...],
) -> tuple[EditProposal, ...]:
    """Enumerate substitutions only at positions explicitly declared editable.

    Retain candidates that reduce this enzyme's cut count. The caller must review
    coding/regulatory effects and other assembly-system constraints before use.
    """
    record = sequence_record(component, document)
    design = document.get(component.identity, Component)
    sequence = next(
        sequence
        for ref in design.sequences
        if (sequence := document.get(ref.identity, Sequence)).encoding == IUPAC_DNA
    )
    baseline = len(record.cuts(enzyme))
    if not isinstance(editable_positions, tuple) or len(set(editable_positions)) != len(
        editable_positions
    ):
        raise ValueError("Editable positions must be a tuple of unique positions")
    result: list[EditProposal] = []
    for position in sorted(editable_positions):
        if type(position) is not int or not 0 <= position < len(sequence.elements):
            raise ValueError("Editable position lies outside the sequence")
        before = sequence.elements[position].upper()
        for after in "ACGT":
            if after == before:
                continue
            candidate = record.elements[:position] + after + record.elements[position + 1 :]
            remaining = len(DnaSequence(candidate, record.circular).cuts(enzyme))
            if remaining < baseline:
                result.append(
                    EditProposal(
                        component=component,
                        sequence=sequence.ref,
                        enzyme=enzyme,
                        edit=SequenceEdit(position=position, before=before, after=after),
                        remaining_sites=remaining,
                    )
                )
    return tuple(result)
