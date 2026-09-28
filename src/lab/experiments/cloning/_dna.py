"""Small, explicit double-stranded restriction/ligation calculations.

Both strands are written 5' to 3'. ``overhang`` is Watson-start minus
Crick-start when both strands are aligned left to right. Enzyme recognition and
Watson cleavage positions come from Biopython. No end repair is implicit.
"""

from dataclasses import dataclass

from Bio.Restriction.Restriction import RestrictionBatch
from Bio.Seq import Seq


def reverse_complement(sequence: str) -> str:
    return str(Seq(sequence).reverse_complement())


@dataclass(frozen=True)
class Duplex:
    watson: str
    crick: str
    overhang: int = 0

    @property
    def left_end(self) -> tuple[str, str]:
        if self.overhang < 0:
            return "5", self.watson[: -self.overhang]
        if self.overhang > 0:
            return "3", self.crick[-self.overhang :]
        return "blunt", ""

    @property
    def right_end(self) -> tuple[str, str]:
        difference = len(self.watson) - len(self.crick) + self.overhang
        if difference > 0:
            return "3", self.watson[-difference:]
        if difference < 0:
            return "5", self.crick[:-difference]
        return "blunt", ""

    def reverse_complement(self) -> "Duplex":
        return Duplex(self.crick, self.watson, self.overhang + len(self.watson) - len(self.crick))

    def ligate(self, other: "Duplex") -> "Duplex":
        if not compatible(self.right_end, other.left_end):
            raise ValueError(f"Incompatible ends: {self.right_end} and {other.left_end}")
        return Duplex(self.watson + other.watson, other.crick + self.crick, self.overhang)

    def close(self) -> str:
        if not compatible(self.right_end, self.left_end) or len(self.watson) != len(self.crick):
            raise ValueError("Fragment ends cannot close into a circular duplex")
        return self.watson

    def linear_sequence(self) -> str:
        if self.left_end[0] != "blunt" or self.right_end[0] != "blunt":
            raise ValueError("Linear products with unpaired ends need explicit end repair")
        return self.watson


def compatible(right: tuple[str, str], left: tuple[str, str]) -> bool:
    return right[0] == left[0] and reverse_complement(right[1]) == left[1]


@dataclass(frozen=True)
class DnaSequence:
    elements: str
    circular: bool

    def cuts(self, enzyme: str) -> tuple[int, ...]:
        restriction = next(iter(RestrictionBatch([enzyme])))
        positions = {
            position - 1
            for position in restriction.search(Seq(self.elements), linear=not self.circular)
        }
        if self.circular:
            return tuple(sorted(position % len(self.elements) for position in positions))
        return tuple(
            sorted(
                position
                for position in positions
                if 0 <= position <= len(self.elements)
                and 0 <= position - restriction.ovhg <= len(self.elements)
            )
        )

    def digest(self, enzyme: str) -> tuple[tuple[int | None, int | None, Duplex], ...]:
        restriction = next(iter(RestrictionBatch([enzyme])))
        # Enzymes with two cleavage events or unknown cuts need a different model.
        if restriction.ovhg is None or restriction.scd5 is not None or restriction.scd3 is not None:
            raise ValueError(f"{enzyme} does not have one supported, known cleavage pair")
        cuts = self.cuts(enzyme)
        if not cuts:
            return ()
        bounds: tuple[int | None, ...] = (*cuts, cuts[0]) if self.circular else (None, *cuts, None)
        result: list[tuple[int | None, int | None, Duplex]] = []
        length = len(self.elements)

        def region(start: int, end: int) -> str:
            if self.circular:
                return "".join(self.elements[index % length] for index in range(start, end))
            return self.elements[start:end]

        for left, right in zip(bounds, bounds[1:], strict=False):
            watson_start = 0 if left is None else left
            watson_end = length if right is None else right
            if self.circular and watson_end <= watson_start:
                watson_end += length
            crick_start = 0 if left is None else watson_start - restriction.ovhg
            crick_end = length if right is None else watson_end - restriction.ovhg
            if min(watson_end, crick_end) <= max(watson_start, crick_start):
                raise ValueError("Overlapping cuts do not leave a double-stranded fragment")
            fragment = Duplex(
                region(watson_start, watson_end),
                reverse_complement(region(crick_start, crick_end)),
                watson_start - crick_start,
            )
            result.append((left, right, fragment))
        return tuple(result)
