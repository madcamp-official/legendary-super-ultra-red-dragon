"""담당: 팀원B

변경된 파일과 관련된 테스트만 의존성 그래프로 선별한다 (MVP: import 그래프
기반, 스트레치: call-graph 정밀화). 뮤테이션 테스팅과 병렬 검증 둘 다의
속도를 이 선별 결과가 떠받친다.
"""

from __future__ import annotations


def select_relevant_tests(changed_files: list[str], repo_path: str) -> list[str]:
    """changed_files와 의존 관계가 있는 테스트 파일/노드 ID 목록을 반환한다."""
    raise NotImplementedError
