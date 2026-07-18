"""담당: 이서영

verify(컴파일+테스트)와 mutation(결함 주입 검증) 결과를 종합해 후보 하나를
자동 채택할지, 사람에게 에스컬레이션할지 최종 판정한다.
"""

from __future__ import annotations

from weld.types import MutationScore, TrustDecision, VerificationResult


def decide(verification: VerificationResult, mutation: MutationScore) -> TrustDecision:
    """검증+뮤테이션 결과를 종합해 채택 여부를 판정한다."""
    raise NotImplementedError
