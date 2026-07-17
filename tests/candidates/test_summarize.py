import pytest

from weld.candidates.summarize import summarize_intent


def test_summarize_intent_is_not_implemented_yet():
    with pytest.raises(NotImplementedError):
        summarize_intent(base="", ours="", theirs="")
