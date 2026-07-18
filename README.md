# legendary-super-ultra-red-dragon — Weld

몰입캠프 26s-w3-c2-01 프로젝트 repository

검증되지 않은 git 병합은 절대 자동으로 착지하지 못하게 막는 무결성 안전장치.
`git merge driver`로 동작하며 UI 없음.

## 세팅

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## 테스트

```bash
pytest
ruff check src tests
```

> 로컬 경로에 한글 폴더명(예: `몰입캠프`)이 껴 있으면 pip editable install의
> 유니코드 정규화(NFC/NFD) 문제로 `ModuleNotFoundError: No module named 'weld'`가
> 간헐적으로 뜰 수 있다. 이럴 땐 `PYTHONPATH=src pytest`처럼 `PYTHONPATH=src`를
> 붙여서 우회한다.

## 구조 & 담당

파일이 겹치지 않도록 모듈별로 담당을 나눴다. **다른 사람의 파일은 인터페이스
(함수 시그니처)만 보고 자기 파일에서 구현하고, `src/weld/types.py`(공용
데이터 계약)는 셋이 합의 없이 혼자 수정하지 않는다.**

```
src/weld/
├── cli.py                 # 오케스트레이션만, 파이프라인 배선 (건드릴 일 거의 없음)
├── types.py                # 공용 데이터 계약 — 수정 시 팀 합의 필요
├── classify/mergiraf.py    # [김민재] 가짜/진짜 충돌 분류 (Mergiraf 연동)
├── candidates/
│   ├── generate.py         # [이서영] LLM 병합 후보 생성
│   └── summarize.py        # [이서영] 에스컬레이션용 의도 요약
├── verify/
│   ├── sandbox.py          # [이재준] 격리 샌드박스, 병렬 검증
│   ├── impact.py           # [이재준] 테스트 영향 분석(선별 실행)
│   └── mutation.py         # [김민재] 뮤테이션 테스팅-라이트 (핵심 기여)
├── policy/trust.py         # [이서영] 검증+뮤테이션 결과 종합 → 채택/에스컬레이션
└── escalate/report.py      # [이서영] 실패 시 사람에게 줄 리포트
```

`tests/`는 `src/weld/`와 같은 구조로 미러링돼 있다 — 자기 모듈의 테스트는
자기 폴더 안에서만 늘리면 된다.

## "테스트"의 단위 (`TestId`)

파이프라인에서 말하는 "테스트"는 파일명이 아니라 **개별 테스트 함수(pytest
노드 ID, 예: `tests/verify/test_sandbox.py::test_run_in_sandbox_is_not_implemented_yet`)**다.
`verify/impact.py`의 import 그래프는 "관련 있을 후보 파일"을 좁히는 1차
필터일 뿐이고, 실제로 `VerificationResult`/후보 검증에 넘기는 목록은 그
파일 안의 개별 테스트 함수 단위까지 펼친 것이어야 한다 — `verify/mutation.py`가
"이 줄을 어떤 테스트가 실제로 실행했는지" 확인하려면 파일 단위로는 부족하기
때문. `types.py`의 `TestId` 타입이 이 계약을 명시한다.

## 뮤테이션 엔진 설계 방향 (`verify/mutation.py`)

4개 논문(테스트 오버피팅 실증연구, 오버피팅 서베이, 뮤테이션 테스팅 원조논문
Budd&Lipton&DeMillo&Sayward 1979, Facebook 산업 적용 사례 "Mutation Monkey")을
스터디하고 나온 구체적 결론. 근거가 더 필요하면 레퍼런스 문서 참고:
https://claude.ai/code/artifact/382c91a7-e0f9-4ade-a1c9-c657f6113c16

**왜 커버리지가 아니라 뮤테이션인가**: "이 줄이 테스트로 실행됐나"는 얕은
질문이다. "이 줄에 결함을 주입해도 테스트가 잡아내나"가 진짜 질문. 실증연구
(Ahmed et al. 2025)가 LLM 기반 패치 수리에서 오버피팅률 21.8~35%를 측정했고,
오버피팅된 패치일수록 커버리지가 확실히 낮다는 것도 보였다(중앙값 1.0 vs 0.8
미만). Weld는 병합 후보가 이 함정에 빠지지 않았는지 뮤테이션으로 확인한다.

**MVP 오퍼레이터** — Facebook Mutation Monkey가 실제 프로덕션 버그로 학습해
검증한 패턴과 겹치는 것부터 (동등 뮤턴트 위험이 낮고, 실전에서 유효성 검증됨):
- 비교연산자 반전 (`<` ↔ `<=`, `<` ↔ `>` 등)
- 불리언 반전 (`FLIP_TRUE_FALSE`류)
- 상수 오프바이원 (`LITERAL_TO_ZERO`, `LITERAL_TO_MINUS_ONE`류)
- null 체크 제거 (`REMOVE_NULL_CHECK`) — Facebook 데이터에서 실제 크래시로
  이어진 제일 흔한 패턴이라 우선순위 높음

**구현 시 반드시 지킬 것**:
1. 변형은 diff에 걸린 변경 영역에만 — 파일 전체 금지 (속도, 그리고 관련 없는
   코드를 건드려서 생기는 노이즈 방지)
2. 뮤턴트 제출 전에 가벼운 문법 체크 — 무효 뮤턴트로 샌드박스 사이클 낭비 방지
3. **테스트가 실제로 그 변형된 줄을 실행했는지 로깅해서 확인** — 안 그러면
   무관한 플레이키 테스트 실패를 "잡았다"고 착각할 수 있음(Facebook 논문이
   명시적으로 겪은 문제). `verify/impact.py`의 결과와 반드시 물려서 씀.
4. 뮤테이션 점수는 **100%를 요구하지 않는다** — 동등 뮤턴트(문법만 다르고
   의미는 같은 변형)가 보통 전체의 2~5%는 나온다. 약간의 허용 오차를 둘 것.
5. 참고 수치: Facebook 실측 생존율은 30~40%(학습된/실전형 패턴 기준). 평가
   실험(Day 6)에서 이 근처면 정상, 0%나 100%에 가까우면 오퍼레이터나 검증
   로직을 의심할 것.

**정직한 한계** (04절에도 반영됨): 병합 영역에 아예 존재하지 않는 케이스(코드
자체가 없는 누락된 경로)는 뮤테이션으로 못 잡는다 — 이건 방법론의 결함이
아니라 프로그램 기반 테스트 전체의 근본적 한계.

## 작업 방식 (충돌 최소화)

- 각자 자기 담당 파일/폴더만 수정한다. `types.py`, `cli.py`는 공용이라
  손댈 일이 생기면 미리 얘기하고 바꾼다.
- 큰 작업을 끝까지 다 끝낸 뒤 한 번에 커밋하지 말고, 기능 단위로 자주
  작게 커밋 + 푸시한다 (충돌 범위가 작아짐).
- 푸시 전에는 `git pull --rebase`로 먼저 최신 상태를 받는다.
