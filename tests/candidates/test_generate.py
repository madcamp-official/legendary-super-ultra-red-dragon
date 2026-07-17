import pytest

from weld.candidates.generate import generate_candidates


def test_generate_candidates_is_not_implemented_yet():
    with pytest.raises(NotImplementedError):
        generate_candidates(base="", ours="", theirs="")
