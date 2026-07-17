"""담당: 팀원A (핵심 기여)

뮤테이션 테스팅-라이트 엔진. "이 줄이 테스트로 실행됐나"가 아니라 "이 줄에
결함을 주입해도 테스트가 진짜로 잡아내나"를 확인한다. 심화 과제로 시간
예산 안에서 신뢰구간 기반 조기 종료 + 약하게 테스트된 영역 우선 배분
(적응형 뮤턴트 스케줄링)이 있다.
"""

from __future__ import annotations

from weld.types import MergeCandidate, MutationScore


def compute_mutation_score(
    candidate: MergeCandidate, relevant_tests: list[str], repo_path: str
) -> MutationScore:
    """candidate의 변경 영역에 뮤턴트를 주입하고 relevant_tests가 잡아내는지 측정한다."""
    raise NotImplementedError
