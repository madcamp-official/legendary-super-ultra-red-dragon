from weld.classify.mergiraf import classify_conflict


def test_classify_conflict_is_not_implemented_yet():
    import pytest

    with pytest.raises(NotImplementedError):
        classify_conflict(base="", ours="", theirs="")
