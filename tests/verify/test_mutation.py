import ast
from unittest.mock import patch

from weld.types import MergeCandidate
from weld.verify.mutation import (
    _apply_site,
    _changed_line_numbers,
    _collect_mutation_sites,
    _prioritize_sites,
    _wilson_interval,
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


# --- 적응형 스케줄링 ---


def test_wilson_interval_unknown_when_no_samples():
    assert _wilson_interval(0, 0) == (0.0, 1.0)


def test_wilson_interval_bounds_are_valid_and_ordered():
    low, high = _wilson_interval(9, 10)
    assert 0.0 <= low <= high <= 1.0
    # 10개 중 9개 잡힘 → 점추정 0.9, 구간이 0.5보다는 확실히 위여야 함
    assert low > 0.5


def test_wilson_interval_narrows_with_more_samples():
    _, high_small = _wilson_interval(4, 5)
    low_small, _ = _wilson_interval(4, 5)
    width_small = high_small - low_small
    low_big, high_big = _wilson_interval(80, 100)
    width_big = high_big - low_big
    assert width_big < width_small


def test_prioritize_sites_puts_weakly_covered_lines_first():
    source = "def f(a, b):\n    x = a < b\n    y = a > b\n    z = a == b\n"
    tree = ast.parse(source)
    sites = _collect_mutation_sites(tree, changed_lines={2, 3, 4})
    # 2번 줄: 3개 테스트가 커버, 3번 줄: 1개, 4번 줄: 2개 → 약한 순서는 3 < 4 < 2
    line_coverage = {2: 3, 3: 1, 4: 2}
    ordered = _prioritize_sites(sites, line_coverage)
    linenos = [s.lineno for s in ordered]
    assert linenos == sorted(linenos, key=lambda ln: line_coverage[ln])
    assert linenos[0] == 3  # 제일 약하게 커버된 줄이 먼저


def test_prioritize_sites_drops_zero_coverage_sites():
    source = "def f(a, b):\n    x = a < b\n    y = a > b\n"
    tree = ast.parse(source)
    sites = _collect_mutation_sites(tree, changed_lines={2, 3})
    line_coverage = {2: 1, 3: 0}  # 3번 줄은 어떤 테스트도 안 지나감
    ordered = _prioritize_sites(sites, line_coverage)
    assert all(s.lineno == 2 for s in ordered)


def test_prioritize_sites_falls_back_when_profiling_empty():
    source = "def f(a, b):\n    return a < b\n"
    tree = ast.parse(source)
    sites = _collect_mutation_sites(tree, changed_lines={2})
    # 프로파일링 실패(빈 dict) → 원래 순서 그대로
    assert _prioritize_sites(sites, {}) == sites


def _make_repo_with_many_sites(tmp_path):
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "__init__.py").write_text("")
    content = (
        "def f(a, b):\n"
        "    p = a < b\n"
        "    q = a > b\n"
        "    r = a == b\n"
        "    s = a >= b\n"
        "    t = a <= b\n"
        "    return p or q or r or s or t\n"
    )
    (repo / "pkg" / "foo.py").write_text(content)
    candidate = MergeCandidate(id="c1", content=content, file_path="pkg/foo.py")
    return repo, candidate


def test_budget_caps_number_of_mutant_runs(tmp_path):
    repo, candidate = _make_repo_with_many_sites(tmp_path)

    call_count = {"n": 0}

    def fake_run(tmp_repo, target_file, lineno, tests):
        call_count["n"] += 1
        return True, True  # 실행됨 + 잡힘

    with (
        patch("weld.verify.mutation._profile_line_coverage", return_value={}),
        patch("weld.verify.mutation._run_tests_with_coverage", side_effect=fake_run),
    ):
        score = compute_mutation_score(
            candidate,
            relevant_tests=["tests/test_foo.py::test_f"],
            repo_path=str(repo),
            budget=3,
        )

    assert call_count["n"] == 3
    assert score.mutants_total == 3


def test_early_stopping_halts_once_confident(tmp_path):
    repo, candidate = _make_repo_with_many_sites(tmp_path)

    call_count = {"n": 0}

    def always_killed(tmp_repo, target_file, lineno, tests):
        call_count["n"] += 1
        return True, True  # 매번 실행됨 + 잡힘 → kill-rate가 빠르게 1.0로 확신

    # 이 후보엔 뮤턴트 사이트가 10개 이상 나오지만,
    # 전부 잡히면 최소 표본(5) 넘긴 직후 신뢰구간이 임계값 위로 확정돼 멈춰야 한다.
    with (
        patch("weld.verify.mutation._profile_line_coverage", return_value={}),
        patch("weld.verify.mutation._run_tests_with_coverage", side_effect=always_killed),
    ):
        score = compute_mutation_score(
            candidate,
            relevant_tests=["tests/test_foo.py::test_f"],
            repo_path=str(repo),
            trust_threshold=0.5,
        )

    assert call_count["n"] < 10  # 전부 다 돌리지 않고 조기 종료
    assert score.mutants_killed == score.mutants_total
