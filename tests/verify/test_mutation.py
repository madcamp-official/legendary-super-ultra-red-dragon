import ast

from weld.types import MergeCandidate
from weld.verify.mutation import (
    _apply_site,
    _changed_line_numbers,
    _collect_mutation_sites,
    compute_mutation_score,
)


def test_changed_line_numbers_without_base_treats_whole_file_as_changed():
    content = "a\nb\nc\n"
    assert _changed_line_numbers("", content) == {1, 2, 3}


def test_changed_line_numbers_only_flags_actually_changed_lines():
    base = "x = 1\ny = 2\nz = 3\n"
    candidate = "x = 1\ny = 999\nz = 3\n"
    assert _changed_line_numbers(base, candidate) == {2}


def test_collect_mutation_sites_ignores_lines_outside_changed_region():
    source = "def f(a, b):\n    unused = a < b\n    return a >= b\n"
    tree = ast.parse(source)
    # 3번째 줄(return)만 변경됐다고 가정 — 2번째 줄의 비교는 무시돼야 함
    sites = _collect_mutation_sites(tree, changed_lines={3})
    assert all(site.lineno == 3 for site in sites)
    operators = {site.operator for site in sites}
    assert "comparison_flip" in operators


def test_collect_mutation_sites_flags_null_check_separately_from_comparison_flip():
    source = "def f(x):\n    return x is None\n"
    tree = ast.parse(source)
    sites = _collect_mutation_sites(tree, changed_lines={2})
    assert len(sites) == 1
    assert sites[0].operator == "null_check_removal"


def test_collect_mutation_sites_flags_boolean_and_numeric_literals():
    source = "def f():\n    return True\n"
    tree = ast.parse(source)
    sites = _collect_mutation_sites(tree, changed_lines={2})
    assert any(s.operator == "boolean_flip" for s in sites)

    source2 = "def f():\n    return 18\n"
    tree2 = ast.parse(source2)
    sites2 = _collect_mutation_sites(tree2, changed_lines={2})
    ops2 = {s.operator for s in sites2}
    assert "literal_to_zero" in ops2
    assert "literal_to_minus_one" in ops2


def test_apply_site_produces_syntactically_valid_mutated_source():
    source = "def f(a, b):\n    return a >= b\n"
    tree = ast.parse(source)
    sites = _collect_mutation_sites(tree, changed_lines={2})
    flip_site = next(s for s in sites if s.operator == "comparison_flip")
    mutated = _apply_site(tree, flip_site)
    ast.parse(mutated)  # 문법 오류면 여기서 예외
    assert "a > b" in mutated
    # 원본 트리는 안 건드렸는지 확인 (deepcopy로 복사본만 수정)
    assert ast.unparse(tree) == source.strip()


def test_compute_mutation_score_returns_empty_when_no_sites_or_tests():
    candidate = MergeCandidate(id="c1", content="x = 1\n", file_path="foo.py")
    score = compute_mutation_score(candidate, relevant_tests=[], repo_path=".")
    assert score.mutants_total == 0
    assert score.mutants_killed == 0


def test_compute_mutation_score_end_to_end(tmp_path):
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "pkg" / "__init__.py").write_text("")
    (repo / "conftest.py").write_text(
        "import os, sys\nsys.path.insert(0, os.path.dirname(__file__))\n"
    )

    base_content = "def is_adult(age):\n    return age > 17\n"
    candidate_content = "def is_adult(age):\n    return age >= 18\n"
    (repo / "pkg" / "foo.py").write_text(candidate_content)
    (repo / "tests" / "test_foo.py").write_text(
        "from pkg.foo import is_adult\n\n"
        "def test_is_adult():\n"
        "    assert is_adult(18) is True\n"
        "    assert is_adult(17) is False\n"
    )

    candidate = MergeCandidate(id="c1", content=candidate_content, file_path="pkg/foo.py")
    score = compute_mutation_score(
        candidate,
        relevant_tests=["tests/test_foo.py::test_is_adult"],
        repo_path=str(repo),
        base_content=base_content,
    )

    assert score.mutants_total > 0
    assert score.mutants_killed > 0
    assert score.score > 0.0
