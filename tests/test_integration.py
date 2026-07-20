"""통합 테스트 — 값 충돌에 대해 파이프라인 전체가 실제로 이어 도는지 확인한다.

분류(mergiraf) → 후보 생성(generate) → 검증(sandbox) → 뮤테이션(mutation) →
판정(trust)까지 실제로 돌린다. impact.py(select_relevant_tests)가 아직 stub라,
EvalCase.relevant_tests를 직접 줘서 그 단계만 우회한다(harness.run_case가
case.relevant_tests가 있으면 impact.py를 안 부름).

값 충돌이라 generate가 LLM을 안 부르고 ours/theirs를 그대로 후보로 낸다 →
GEMINI_API_KEY 불필요, 네트워크 불필요. 이 두 테스트가 통과하면 "각자 파트가
붙였을 때도 실제로 맞물려 돈다"는 통합 증명이 된다.
"""

from __future__ import annotations

import subprocess

from weld.evaluation.cases import EvalCase
from weld.evaluation.harness import run_case

BASE = "def shipping_fee():\n    return 3000\n"
OURS = "def shipping_fee():\n    return 5000\n"  # B — 엄격한 테스트에서 정답이길 기대
THEIRS = "def shipping_fee():\n    return 4000\n"  # A


def _make_repo(tmp_path, test_body):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "conftest.py").write_text(
        "import os, sys\nsys.path.insert(0, os.path.dirname(__file__))\n"
    )
    (repo / "pricing.py").write_text(BASE)
    (repo / "test_pricing.py").write_text(
        "from pricing import shipping_fee\n\n\ndef test_shipping_fee():\n" + test_body
    )
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.local"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
    return repo


def test_value_conflict_with_strict_test_auto_merges(tmp_path):
    """테스트가 값을 못 박으면 → 검증 통과한 후보를 자동 채택."""
    repo = _make_repo(tmp_path, "    assert shipping_fee() == 5000\n")
    case = EvalCase(
        id="strict",
        base=BASE,
        ours=OURS,
        theirs=THEIRS,
        file_path="pricing.py",
        relevant_tests=["test_pricing.py::test_shipping_fee"],
        ground_truth_resolution=OURS,
    )
    outcome = run_case(case, repo_path=str(repo))
    assert outcome.action == "auto_verified"
    assert outcome.correct is True


def test_value_conflict_with_loose_test_escalates(tmp_path):
    """테스트가 값을 제약 못 하면 → 뮤테이션도 못 잡음 → 정직하게 에스컬레이션."""
    repo = _make_repo(tmp_path, "    assert isinstance(shipping_fee(), int)\n")
    case = EvalCase(
        id="loose",
        base=BASE,
        ours=OURS,
        theirs=THEIRS,
        file_path="pricing.py",
        relevant_tests=["test_pricing.py::test_shipping_fee"],
    )
    outcome = run_case(case, repo_path=str(repo))
    assert outcome.action == "escalated"


def test_value_conflict_where_both_candidates_pass_escalates(tmp_path):
    """스왑 테스트 핵심 케이스 — 테스트가 "양수인지"만 보고 정확한 값은 안 봐서
    ours(5000)/theirs(4000) 둘 다 개별적으로 통과 + 뮤테이션 점수도 충족한다.
    둘 다 통과했다는 것 자체가 "경쟁하는 값 중 뭐가 맞는지 테스트로는 구분
    못 한다"는 신호라, 어느 한쪽도 자동 채택하지 않고 에스컬레이션해야 한다."""
    repo = _make_repo(tmp_path, "    assert shipping_fee() > 0\n")
    case = EvalCase(
        id="both-pass",
        base=BASE,
        ours=OURS,
        theirs=THEIRS,
        file_path="pricing.py",
        relevant_tests=["test_pricing.py::test_shipping_fee"],
    )
    outcome = run_case(case, repo_path=str(repo))
    assert outcome.action == "escalated"
