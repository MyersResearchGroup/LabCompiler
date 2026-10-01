"""Independent expected strands captured from pydna 5.5.0 / Biopython 1.84."""

import pytest

from lab.experiments.cloning._dna import DnaSequence


@pytest.mark.parametrize(
    "enzyme,elements,circular,expected",
    [
        (
            "PstI",
            "GGCTGCAGAAAAGCTGCAGTT",
            False,
            (
                (None, 7, "GGCTGCA", "GCC", 0),
                (7, 18, "GAAAAGCTGCA", "GCTTTTCTGCA", 4),
                (18, None, "GTT", "AACTGCA", 4),
            ),
        ),
        (
            "SmaI",
            "GGCCCGGGTTTCCCGGGAA",
            False,
            (
                (None, 5, "GGCCC", "GGGCC", 0),
                (5, 14, "GGGTTTCCC", "GGGAAACCC", 0),
                (14, None, "GGGAA", "TTCCC", 0),
            ),
        ),
        (
            "EcoRI",
            "AATTCCCCGGAATTCG",
            True,
            (
                (0, 10, "AATTCCCCGG", "AATTCCGGGG", -4),
                (10, 0, "AATTCG", "AATTCG", -4),
            ),
        ),
        (
            "PstI",
            "CTGCAGAAAAGCTGCAGTT",
            True,
            (
                (5, 16, "GAAAAGCTGCA", "GCTTTTCTGCA", 4),
                (16, 5, "GTTCTGCA", "GAACTGCA", 4),
            ),
        ),
        (
            "BsaI",
            "TTTGGTCTCAACGTTACGTTTGAGACCAAA",
            False,
            (
                (None, 10, "TTTGGTCTCA", "ACGTTGAGACCAAA", 0),
                (10, 16, "ACGTTA", "AACGTA", -4),
                (16, None, "CGTTTGAGACCAAA", "TTTGGTCTCA", -4),
            ),
        ),
    ],
)
def test_digest_and_ligation_match_independent_strand_reference(
    enzyme, elements, circular, expected
):
    fragments = DnaSequence(elements, circular).digest(enzyme)
    assert (
        tuple(
            (left, right, fragment.watson, fragment.crick, fragment.overhang)
            for left, right, fragment in fragments
        )
        == expected
    )
    joined = fragments[0][2]
    for _, _, fragment in fragments[1:]:
        joined = joined.ligate(fragment)
    if circular:
        assert len(joined.close()) == len(elements) and joined.close() in elements * 2
    else:
        assert joined.linear_sequence() == elements


def test_reverse_complement_preserves_three_prime_ends():
    fragment = DnaSequence("GGCTGCAGAAAAGCTGCAGTT", False).digest("PstI")[0][2]
    reversed_fragment = fragment.reverse_complement()
    assert (reversed_fragment.watson, reversed_fragment.crick, reversed_fragment.overhang) == (
        "GCC",
        "GGCTGCA",
        4,
    )
    assert reversed_fragment.reverse_complement() == fragment


def test_incompatible_ends_and_unpaired_linear_products_fail_explicitly():
    eco = DnaSequence("GGGAATTCCCCGAATTCGG", False).digest("EcoRI")[1][2]
    pst = DnaSequence("GGCTGCAGAAAAGCTGCAGTT", False).digest("PstI")[1][2]
    with pytest.raises(ValueError, match="Incompatible ends"):
        eco.ligate(pst)
    with pytest.raises(ValueError, match="end repair"):
        eco.linear_sequence()
