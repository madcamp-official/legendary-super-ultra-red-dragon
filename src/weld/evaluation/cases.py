"""평가 사례/결과 데이터 모델.

EvalCase = "정답을 아는 과거 충돌 하나". 실제 데이터는 AgenticFlict / Merge-Bench
같은 공개 데이터셋에서 채운다(이서영님 '평가셋 구축' 태스크). 여기서는 하네스가
소비할 인터페이스만 확정한다 — 데이터셋 파서가 이 EvalCase 리스트를 뱉으면
harness/metrics가 그대로 받아 돌아간다.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

# 파이프라인이 한 사례에 대해 최종적으로 취한 행동.
#   auto_spurious : 가짜 충돌로 분류해 자동 병합
#   auto_verified : 진짜 충돌이지만 검증 게이트를 통과한 후보를 자동 채택
#   escalated     : 통과 후보가 없어 사람에게 넘김
#   error         : 파이프라인이 예외로 실패
EvalAction = Literal["auto_spurious", "auto_verified", "escalated", "error"]


@dataclass(frozen=True)
class EvalCase:
    """정답이 알려진 과거 충돌 하나."""

    id: str
    base: str
    ours: str
    theirs: str
    file_path: str
    relevant_tests: list[str] = field(default_factory=list)

    expected_spurious: bool = False
    """정답 라벨: 이 충돌이 실제로 가짜(구조적으로 안 겹침)였는가."""
    ground_truth_resolution: str | None = None
    """과거에 실제로 채택된 병합 결과(파일 전체). 자동 병합이 맞았는지 대조용."""
    repo_coverage: float | None = None
    """이 사례가 나온 저장소의 테스트 커버리지(0~1). 커버리지-자동해결률
    상관관계 실험(발표용 그래프)에 쓴다."""
    source_repo: str = ""
    """이 충돌이 나온 저장소(경로 또는 URL). 나중에 검증을 다시 돌리려면
    이 저장소를 아래 source_commit 시점으로 체크아웃해야 한다."""
    source_commit: str = ""
    """충돌이 일어난 병합 커밋의 전체 SHA. 재현·재실행용 출처."""


@dataclass(frozen=True)
class EvalOutcome:
    """파이프라인이 한 사례에 대해 낸 결과."""

    case_id: str
    action: EvalAction
    correct: bool | None = None
    """자동 병합(auto_*)일 때 결과가 정답과 일치했는가. 에스컬레이션/에러이거나
    정답을 모르면 None."""
    repo_coverage: float | None = None
    """상관관계 분석용으로 사례에서 그대로 이어받는 커버리지."""


def save_cases(cases: list[EvalCase], path: str) -> None:
    """EvalCase 목록을 JSON 파일로 저장한다(평가셋 고정·재사용용).

    채굴은 매번 저장소를 클론해야 하지만, 한 번 모아 이 함수로 저장해두면
    같은 세트로 지표를 반복 산출할 수 있다. 충돌 내용(base/ours/theirs/정답)이
    문자열이라 그대로 직렬화된다. 검증을 다시 돌리려면 각 케이스의
    source_repo/source_commit으로 저장소를 체크아웃하면 된다.
    """
    data = [dataclasses.asdict(c) for c in cases]
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2))


def load_cases(path: str) -> list[EvalCase]:
    """save_cases로 저장한 JSON 평가셋을 다시 EvalCase 목록으로 불러온다."""
    data = json.loads(Path(path).read_text())
    return [EvalCase(**item) for item in data]


def load_cases_from_dataset(path: str) -> list[EvalCase]:
    """AgenticFlict / Merge-Bench 형식 데이터셋을 EvalCase 리스트로 로드한다.

    TODO(이서영님 '평가셋 구축' 태스크): 실제 데이터셋 포맷을 파싱해 위 EvalCase를
    채운다. 하네스/지표 쪽은 이 함수의 반환 타입(list[EvalCase])에만 의존하므로,
    이 함수만 채우면 나머지는 그대로 돌아간다.
    """
    raise NotImplementedError
