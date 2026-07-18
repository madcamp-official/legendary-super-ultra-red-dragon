"""담당: 이재준

후보 하나를 격리된 Docker 컨테이너에서 실행해 컴파일+테스트 통과 여부를 확인한다.
후보가 여러 개면 병렬로 돌린다 (일정상 최대 리스크 지점).
"""

from __future__ import annotations

from weld.types import MergeCandidate, TestId, VerificationResult


def run_in_sandbox(
    candidate: MergeCandidate, repo_path: str, tests: list[TestId] | None = None
) -> VerificationResult:
    """후보 하나를 샌드박스에서 실행한다. tests(pytest 노드 ID)가 주어지면 그 테스트만 돈다."""
    raise NotImplementedError


def run_candidates_parallel(
    candidates: list[MergeCandidate], repo_path: str, tests: list[TestId] | None = None
) -> list[VerificationResult]:
    """후보 여러 개를 병렬 샌드박스에서 동시에 검증한다."""
    raise NotImplementedError
