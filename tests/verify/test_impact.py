import pytest

from weld.verify.impact import select_relevant_tests


def test_select_relevant_tests_is_not_implemented_yet():
    with pytest.raises(NotImplementedError):
        select_relevant_tests([], repo_path=".")
