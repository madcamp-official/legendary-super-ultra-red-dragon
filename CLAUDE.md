# CLAUDE.md — Weld 작업 가이드

Claude Code가 이 저장소에서 세션을 시작할 때 자동으로 읽는 파일. 팀원 각자의
Claude가 공유하는 규칙과, 아직 미구현인 파트의 구현 가이드를 담는다.

## 프로젝트 한 줄

검증되지 않은 git 병합은 자동으로 착지하지 못하게 막는 무결성 안전장치
(`git merge driver`, UI 없음). 파이프라인: 3-way diff → 가짜/진짜 분류
(Mergiraf) → 진짜면 LLM 후보 생성 → 검증 게이트(컴파일+테스트+뮤테이션)
→ 통과 시 자동 병합, 아니면 사람에게 에스컬레이션.

## 작업 규칙 (전원 공통)

- **담당 파일만 수정한다.** 담당 표는 `README.md`의 "구조 & 담당" 참고.
- **공용 파일(`src/weld/types.py`, `src/weld/cli.py`, `pyproject.toml`)은 팀
  합의 없이 단독 수정 금지.** 특히 `types.py`(데이터 계약)를 바꾸면 전원에게
  영향 간다.
- 큰 작업을 몰아서 한 번에 커밋하지 말고 기능 단위로 자주 커밋+푸시. 푸시 전
  `git pull --rebase`.
- 테스트/린트: `pytest` + `ruff check src tests`. 한글 경로 때문에
  `ModuleNotFoundError: No module named 'weld'`가 뜨면 `PYTHONPATH=src`를 붙인다.
- **mergiraf 바이너리가 로컬 PATH에 있어야 한다**(`brew install mergiraf`).
  없으면 모든 충돌이 "진짜 충돌"로 fail-safe되어 가짜 충돌 자동 처리가 무효화된다.

## 미구현 파트 구현 가이드

### 판정 로직 — `policy/trust.py` (담당: 이서영)

`decide(verification, mutation)`는 후보 하나를 **자동 채택**할지 **에스컬레이션**
할지 정한다. 상세 정책은 `README.md`의 "판정 정책" 섹션에 표로 정리돼 있으니
**구현 전 반드시 읽을 것.** 핵심만:

- 원칙: **검증이 "이 병합이 옳다"를 적극적으로 증명한 경우에만 자동 병합.**
  북극성 지표가 오탐률 0%라 애매하면 무조건 에스컬레이션으로 편향한다.
- 대략적 규칙: `verification.tests_passed`이고 `mutation.score`가 임계값 이상
  이면 채택, 아니면 에스컬레이션. 임계값은 평가(Day 6)에서 튜닝(Facebook 실측
  생존율 30~40% 참고).
- **같은 줄을 A/B가 다른 의도로 고쳤는데 둘 다 통과하는 경우**: 테스트가 A와
  B를 구분하지 못한다는 뜻이므로 **자동 병합하지 말고 에스컬레이션.** "테스트에
  무관하다 ≠ 실제로 동등하다"이므로 동등하다고 베팅하면 오탐이 된다.
  - 이걸 정밀하게 판별하는 것이 **"스왑 테스트"**: 통과한 후보에서 충돌 줄만
    경쟁 버전(A↔B)으로 바꿔치기해 테스트를 돌려, 테스트가 둘을 구분하는지
    (깨지는지) 확인한다. 뮤테이션 엔진(`verify/mutation.py`)과 같은 메커니즘
    이라 그쪽 헬퍼를 재활용/협업하면 된다(김민재와 상의).

### 에스컬레이션 — `escalate/report.py` (담당: 이서영)

- 에스컬레이션은 빈손으로 넘기지 않는다. 후보안(base 대비 diff)과 검증/뮤테이션
  결과를 곁들여(`build_escalation_report`) 사람이 값 판단(예: `timeout 60 vs
  90`)만 하면 되도록 만든다.
- LLM 호출은 **후보 생성(`candidates/generate.py`) 한 곳으로만 한정**한다 —
  원래 있던 `candidates/summarize.py`(LLM 기반 의도 요약)는 팀 논의 후 제거함.
  에스컬레이션 시 "의도 요약"은 항상 빈 값이고, 리포트는 후보 diff·검증
  결과만으로 판단 근거를 제공한다.

### 후보 생성 — `candidates/generate.py` (담당: 이서영)

- `MergeCandidate.content`는 **병합이 적용된 파일 전체**여야 한다(스니펫 금지).
  LLM에 "바뀐 줄만" 뱉게 하면 `cli.py`가 파일에 덮어쓸 때 깨진다. `types.py`의
  해당 필드 독스트링 참고.

### 테스트 영향 분석 — `verify/impact.py` (담당: 이재준)

- 반환값은 파일명이 아니라 **개별 테스트 함수(pytest 노드 ID)** 목록.
  자세한 건 `README.md`의 "테스트의 단위" 섹션 참고.
