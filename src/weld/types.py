"""Weld 파이프라인 전체가 공유하는 데이터 계약.

모든 모듈(classify/candidates/verify/policy/escalate)이 이 파일의 타입을 통해서만
서로 통신한다. 인터페이스 변경은 팀 전체에 영향을 주므로, 새 필드가 필요하면
각자 파일에서 조용히 바꾸지 말고 합의 후 여기서만 수정한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field


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


@dataclass(frozen=True)
class VerificationResult:
    """verify.sandbox 모듈이 후보 하나를 실행한 결과."""

    candidate_id: str
    compiled: bool
    tests_passed: bool
    tests_run: list[str] = field(default_factory=list)
    tests_failed: list[str] = field(default_factory=list)
    duration_s: float = 0.0
    error: str | None = None


@dataclass(frozen=True)
class MutationScore:
    """verify.mutation 모듈이 후보 하나에 대해 계산한 뮤테이션 테스팅 점수."""

    candidate_id: str
    mutants_total: int
    mutants_killed: int
    survived_mutants: list[str] = field(default_factory=list)

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
