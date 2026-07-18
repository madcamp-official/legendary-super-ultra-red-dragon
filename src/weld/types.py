"""Weld 파이프라인 전체가 공유하는 데이터 계약.

모든 모듈(classify/candidates/verify/policy/escalate)이 이 파일의 타입을 통해서만
서로 통신한다. 인터페이스 변경은 팀 전체에 영향을 주므로, 새 필드가 필요하면
각자 파일에서 조용히 바꾸지 말고 합의 후 여기서만 수정한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

TestId = str
"""테스트를 가리키는 식별자는 파일명이 아니라 **pytest 노드 ID**다.
예: "tests/verify/test_sandbox.py::test_run_in_sandbox_is_not_implemented_yet".
파일 단위로는 "그 파일의 테스트가 이 줄을 실행했는지" 여부를 알 수 없어서
(verify/impact.py의 import 그래프는 후보 파일을 좁히는 1차 필터일 뿐),
mutation.py가 결함 주입 후 어떤 테스트가 실제로 그 줄을 지나갔는지 확인하려면
개별 테스트 함수 단위까지 내려가야 한다."""


@dataclass(frozen=True)
class ClassificationResult:
    """classify 모듈의 출력 — 충돌이 가짜인지 진짜인지."""

    is_spurious: bool
    resolved_content: str | None = None
    """가짜 충돌(is_spurious=True)일 때 Mergiraf가 만든 병합 결과."""
    reason: str = ""


@dataclass(frozen=True)
class MergeCandidate:
    """candidates 모듈이 생성한 진짜 충돌에 대한 병합 후보 하나."""

    id: str
    content: str
    strategy: str = "llm"
    """후보를 만든 전략 이름 (예: "llm-conservative", "llm-aggressive")."""
    file_path: str = ""
    """이 후보가 적용될 저장소 내 상대 경로. candidates/generate.py는 신경 쓸 필요
    없고(기본값 빈 문자열), cli.py가 git이 넘겨준 실제 경로(%P)로 채워 넣는다.
    verify/sandbox.py, verify/mutation.py가 후보 내용을 실제 파일 위치에 써서
    테스트를 돌리려면 이 경로가 필요하다."""


@dataclass(frozen=True)
class VerificationResult:
    """verify.sandbox 모듈이 후보 하나를 실행한 결과."""

    candidate_id: str
    compiled: bool
    tests_passed: bool
    tests_run: list[TestId] = field(default_factory=list)
    tests_failed: list[TestId] = field(default_factory=list)
    duration_s: float = 0.0
    error: str | None = None


@dataclass(frozen=True)
class MutationScore:
    """verify.mutation 모듈이 후보 하나에 대해 계산한 뮤테이션 테스팅 점수."""

    candidate_id: str
    mutants_total: int
    mutants_killed: int
    survived_mutants: list[str] = field(default_factory=list)
    """생존한 뮤턴트 설명 문자열(예: "mutation.py:42 `<` -> `<=`"). 테스트 ID 아님."""

    @property
    def score(self) -> float:
        if self.mutants_total == 0:
            return 0.0
        return self.mutants_killed / self.mutants_total


@dataclass(frozen=True)
class TrustDecision:
    """policy.trust 모듈의 최종 판정 — 자동 채택할지, 에스컬레이션할지."""

    accepted: bool
    candidate_id: str | None
    reason: str


@dataclass(frozen=True)
class EscalationReport:
    """검증 실패 시 사람에게 넘기는 리포트 — 빈손으로 넘기지 않는다."""

    intent_summary: str
    candidates: list[MergeCandidate]
    verifications: list[VerificationResult]
    mutation_scores: list[MutationScore]
