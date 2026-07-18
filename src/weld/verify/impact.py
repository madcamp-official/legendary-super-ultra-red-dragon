"""담당: 이재준

변경된 파일과 관련된 테스트만 의존성 그래프로 선별한다 (MVP: import 그래프
기반, 스트레치: call-graph 정밀화). 뮤테이션 테스팅과 병렬 검증 둘 다의
속도를 이 선별 결과가 떠받친다.

반환값은 파일명이 아니라 **개별 테스트 함수(pytest 노드 ID)** 목록이다.
import 그래프로 얻는 건 "관련 있을 후보 파일"까지고, 그 파일 안의 테스트
함수 단위까지 펼치는 건 이 함수의 책임이다 — verify/mutation.py가 "이
줄을 어떤 테스트가 실제로 실행했는지" 확인하려면 파일 단위로는 부족하다.
"""

from __future__ import annotations

from weld.types import TestId


def select_relevant_tests(changed_files: list[str], repo_path: str) -> list[TestId]:
    """changed_files와 의존 관계가 있는 테스트 함수의 pytest 노드 ID 목록을 반환한다."""
    raise NotImplementedError
