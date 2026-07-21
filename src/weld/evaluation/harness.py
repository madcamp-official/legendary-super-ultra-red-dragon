"""평가 러너 — 사례를 Weld 파이프라인에 통과시켜 EvalOutcome을 뽑는다.

⚠ 스케치: run_case는 전체 파이프라인(classify → generate → verify → mutation
→ policy)을 호출하므로, 이서영님(candidates/policy)·이재준님(verify) 파트가
스텁인 동안에는 대부분의 사례가 action="error"로 나온다. 통합(화요일) 이후
그대로 켜면 된다. 지표 계산(metrics.py)은 이 러너 없이도 이미 완결/검증돼
있으니, 그때까지는 손으로 만든 EvalOutcome으로도 리포트를 뽑을 수 있다.

cli.py의 merge 파이프라인과 같은 로직을 쓰되, 파일에 쓰거나 exit하는 대신
"무슨 행동을 했고 그게 정답과 맞았는지"를 EvalOutcome으로 기록한다.
"""

from __future__ import annotations

import dataclasses

from weld.candidates.generate import generate_candidates
from weld.classify.mergiraf import classify_conflict
from weld.evaluation.cases import EvalCase, EvalOutcome
from weld.policy.trust import decide_among
from weld.verify.impact import select_relevant_tests
from weld.verify.mutation import compute_mutation_score
from weld.verify.sandbox import run_candidates_parallel


def run_case(case: EvalCase, repo_path: str) -> EvalOutcome:
    """사례 하나를 파이프라인에 돌려 결과를 기록한다.

    repo_path: 이 사례의 테스트를 실제로 돌릴 수 있는 (체크아웃된) 저장소 경로.
    """
    try:
        classification = classify_conflict(case.base, case.ours, case.theirs)
        if classification.is_spurious:
            correct = _matches_ground_truth(classification.resolved_content, case)
            return EvalOutcome(
                case_id=case.id,
                action="auto_spurious",
                correct=correct,
                repo_coverage=case.repo_coverage,
            )

        candidates = [
            dataclasses.replace(c, file_path=case.file_path)
            for c in generate_candidates(case.base, case.ours, case.theirs, file_path=case.file_path)
        ]
        relevant_tests = case.relevant_tests or select_relevant_tests(
            [case.file_path], repo_path=repo_path
        )
        verifications = run_candidates_parallel(
            candidates, repo_path=repo_path, tests=relevant_tests
        )
        mutation_scores = [
            compute_mutation_score(c, relevant_tests, repo_path=repo_path, base_content=case.base)
            for c in candidates
        ]

        decision = decide_among(candidates, verifications, mutation_scores)
        if decision.accepted:
            accepted_candidate = next(c for c in candidates if c.id == decision.candidate_id)
            correct = _matches_ground_truth(accepted_candidate.content, case)
            return EvalOutcome(
                case_id=case.id,
                action="auto_verified",
                correct=correct,
                repo_coverage=case.repo_coverage,
            )

        return EvalOutcome(
            case_id=case.id, action="escalated", repo_coverage=case.repo_coverage
        )
    except Exception:
        return EvalOutcome(case_id=case.id, action="error", repo_coverage=case.repo_coverage)


def _matches_ground_truth(produced: str | None, case: EvalCase) -> bool | None:
    """자동 병합 결과가 과거 실제 채택안과 일치하는가. 정답을 모르면 None."""
    if case.ground_truth_resolution is None or produced is None:
        return None
    return produced.strip() == case.ground_truth_resolution.strip()


def run_all(cases: list[EvalCase], repo_path: str) -> list[EvalOutcome]:
    """사례 여러 개를 순차 실행한다. (병렬화는 이재준님 샌드박스가 안정된 뒤 검토.)"""
    return [run_case(case, repo_path) for case in cases]
