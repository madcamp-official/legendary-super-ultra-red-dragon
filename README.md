# legendary-super-ultra-red-dragon — Weld

몰입캠프 26s-w3-c2-01 프로젝트 repository

검증되지 않은 git 병합은 절대 자동으로 착지하지 못하게 막는 무결성 안전장치.
`git merge driver`로 동작하며 UI 없음.

## 사용 흐름 (Getting Started)

weld는 **git 머지 드라이버 + 사용자 본인의 LLM 키** 조합이다. 셋업은 두
레이어로 나뉜다 — 그리고 **API 키는 weld가 주는 게 아니라 사용자가 자기
Gemini(또는 사내 qwen 등) 키를 가져와 넣는다.**

| 레이어 | 언제 | 무엇 |
|---|---|---|
| **① 컴퓨터당 1회** | 이 머신에 weld 처음 깔 때 | 설치 + 내 API 키 등록 |
| **② 저장소당 1회** | weld를 쓸 프로젝트마다 | 그 저장소에 머지 드라이버 켜기 |
| **③ 이후** | 평소처럼 | `git merge` — 충돌 시 weld가 자동 검증 |

### ① 컴퓨터당 1회 — 설치 + 키

```bash
git clone <이 저장소> && cd weld
./install.sh
```
`install.sh`가 다 한다: 파이썬 venv(`~/.weld-venv`, iCloud 밖) + 의존성 +
mergiraf 확인 + `weld` 명령 등록(`~/.local/bin`) + **전역 LLM 설정 파일**
`~/.config/weld/env` 생성. 새 터미널을 열거나 `source ~/.zshrc` 하면 `weld` 명령이 잡힌다.

그다음 **내 API 키를 전역 설정 한 곳에** 넣는다 (모든 저장소가 이걸 공유 —
저장소마다 `.env` 만들 필요 없음):
```bash
open -e ~/.config/weld/env
```
```
GEMINI_API_KEY=<본인 키>
GEMINI_MODEL=gemini-flash-lite-latest
```
- **키 발급**: [aistudio.google.com/apikey](https://aistudio.google.com/apikey).
  무료 등급은 모델·프로젝트당 하루 요청 한도가 있다(예: `gemini-3.5-flash`는 20/일).
  많이 돌릴 거면 유료 등급 또는 가벼운 모델(`gemini-flash-lite-latest`)을 쓴다.
- **사내/커스텀 OpenAI 호환 모델**을 쓸 거면 위 대신
  `WELD_LLM_BASE_URL` / `WELD_LLM_MODEL` / `WELD_LLM_API_KEY` 를 넣는다(그러면 Gemini 대신 그걸 사용).
- 키가 없거나 한도 초과여도 **안전** — LLM 호출이 실패하면 weld는 자동병합 대신
  사람에게 에스컬레이션한다(오탐 없음).

### 어떤 모델을 쓸까 (`GEMINI_MODEL`)

구조합성 품질과 호출 한도의 트레이드오프다. weld는 구조충돌 1건당 후보를 2개
(온도 0.2/1.7) 만들어 **LLM을 2회 호출**하므로, 무료 20회면 실질 **~10병합/일**이다.

| 모델 | 합성 품질 | 무료 한도 | 비고 |
|---|---|---|---|
| `gemini-3.5-flash` (기본) | 높음 | 낮음 (~20/일·모델·프로젝트) | 복잡한 상호작용도 잘 합성. 실측 기준 |
| `gemini-flash-lite-latest` | 중간 | 더 관대(정확 수치는 콘솔 확인) | 빠르고 싸지만 복잡한 케이스에서 미묘한 오류 가능. weld가 `thinking_config`를 자동 생략해 호환 |
| 커스텀 OpenAI 호환(예: 사내 qwen) | 높음 | 무제한(자체 서버) | `GEMINI_*` 대신 `WELD_LLM_BASE_URL/MODEL/API_KEY`. 사내망/VPN 필요 |
| 유료 Gemini (billing 연결+잔액) | 높음 | 사실상 해제 | 쓴 만큼 과금 |

- **무료 등급 한도는 모델·프로젝트·일 단위**다. 소진되면 429가 나고, weld는
  안전하게 에스컬레이션한다(오탐 없음).
- **새 프로젝트 = 별도 무료 버킷.** [AI Studio](https://aistudio.google.com/apikey)에서
  "Create API key in **new project**"로 만들면 그 프로젝트의 새 한도가 생긴다
  (키만 새로 만들고 같은 프로젝트면 한도를 공유하므로 소용없다).
- **429가 "prepayment credits depleted"면** 무료 한도가 아니라 **유료(선불) 잔액 0** 이라는 뜻 —
  모델을 바꿔도 안 되고, 결제에서 크레딧을 충전해야 한다.

권장:
- **연습·데모를 많이 돌릴 때** → `gemini-flash-lite-latest`, 또는 프로젝트를 여러 개
  만들어 팀원이 각자 다른 키 사용
- **발표·실사용 최고 품질** → `gemini-3.5-flash` 또는 커스텀 qwen
- **한도 없이 마음껏** → 커스텀 qwen(VPN) 또는 유료 Gemini

### ② 저장소당 1회 — 드라이버 켜기

```bash
cd <프로젝트>
weld install
```
그 저장소의 `.git/config`에 "충돌 나면 weld를 불러라"를 등록하고
`.gitattributes`(`* merge=weld`)를 만든다. **왜 저장소마다?** git 머지 드라이버
설정은 `.git/config`에 있어 **clone에 안 따라오기** 때문 — 그 저장소를 clone한
사람마다 한 번씩 실행해야 한다(`.gitattributes`는 커밋되어 공유됨).

### ③ 이후 — 그냥 평소처럼

```bash
git merge some-branch
```
충돌이 나면 weld가 자동으로 끼어든다: 분류(가짜/진짜) → LLM 후보 합성 →
샌드박스 검증 → 뮤테이션 → 판정. 검증을 통과하면 자동병합, 아니면 익숙한
충돌 마커를 남기고 사람에게 넘긴다. **사용자는 weld를 의식할 필요 없이 `git merge`만 하면 된다.**

> 실제 충돌을 직접 만들어보는 실습은 데모 저장소의 `TUTORIAL.md` 참고.

### 문제 해결

| 증상 | 해결 |
|---|---|
| `command not found: weld` | 새 터미널을 열거나 `source ~/.zshrc` (PATH 반영) |
| 병합이 매번 에스컬레이션 | `~/.config/weld/env`의 LLM 키/한도 확인. 값충돌 등 LLM-무관 케이스가 되는지 먼저 확인 |
| `429 RESOURCE_EXHAUSTED` | 무료 한도 소진 → 내일 리셋 / 새 프로젝트 키 / 가벼운 모델. `prepayment depleted`면 유료 잔액 충전 |
| `400 INVALID_ARGUMENT` (flash-lite) | weld가 자동 처리함(최신 버전). 안 되면 `git pull`로 weld 갱신 |
| `mergiraf` 없음 | `brew install mergiraf` (install.sh가 확인·설치) |
| venv 읽기가 멈춤 | venv를 iCloud(Documents) 밖에 — install.sh는 `~/.weld-venv`(홈)에 만든다 |

### 수동 설치 (개발/기여자용)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

> ⚠️ `.venv`를 iCloud(Documents) 아래 두면 파일 eviction으로 읽기가 멈출 수 있고,
> 한글 폴더명(예: `몰입캠프`) 경로면 editable install이 간헐 실패한다
> (`ModuleNotFoundError: weld`). 둘 다 겪으면 `install.sh`(홈에 venv 생성)를
> 쓰거나 `PYTHONPATH=src`로 우회한다.

### mergiraf 설치 (필수)

가짜/진짜 충돌 분류는 외부 도구 `mergiraf`(tree-sitter 기반 구조적 병합)를
호출한다. **이게 PATH에 없으면 모든 충돌이 "진짜 충돌"로 fail-safe되어
가짜 충돌 자동 처리 기능이 통째로 무효화된다** — 그러니 각자 로컬에 꼭 깔 것.

```bash
# macOS
brew install mergiraf

# cargo (그 외 플랫폼)
cargo install mergiraf
```

설치 확인: `mergiraf --version`. 다른 경로에 설치했다면 환경변수
`WELD_MERGIRAF_BIN`으로 실행 파일 경로를 지정할 수 있다.

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
│   └── generate.py         # [이서영] LLM 병합 후보 생성 (LLM 호출은 여기 한 곳뿐)
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

## 판정 정책 (`policy/trust.py`) — 같은 줄 충돌을 어떻게 다루나

핵심 원칙: **검증이 "이 병합이 옳다"를 적극적으로 증명한 경우에만 자동 병합하고,
판단이 필요한 충돌은 무조건 사람에게 넘긴다.** 북극성 지표가 오탐률 0%
(틀린 걸 자동 병합 안 하기)라, 애매하면 항상 에스컬레이션 쪽으로 편향한다.

같은 줄을 A/B가 다르게 고친 경우(진짜 충돌)를 **별도 분기로 특수 처리하지
않는다.** 일반 파이프라인(후보 생성 → 검증 게이트)을 그대로 타고, 아래 규칙으로
판정한다.

**근본 전제**: 의도가 테스트에 담겨 있지 않으면, 실행만으로는 그 의도를 복원할
수 없다. 예) `timeout = 60`(A) vs `timeout = 90`(B)이 둘 다 테스트를 통과하면,
"60이 맞나 90이 맞나"의 정답은 코드/테스트가 아니라 개발자 머릿속에 있다.
그래서 Weld는 **누가 이겼는지 맞히려 하지 않고**, "이게 테스트로 판정 가능한
충돌인지"만 판별한다.

판정 규칙:

| 상황 | 그 줄의 뮤테이션 점수 / 스왑 테스트 | 판정 |
|---|---|---|
| 후보 통과 + 그 줄이 테스트로 제약됨 | 높음 (스왑 시 테스트 깨짐) | **자동 병합** |
| 후보 통과했지만 값 판단(3 vs 5) — 테스트가 그 줄 무관 | 낮음 (스왑해도 통과) | **에스컬레이션** |
| 게이트 통과 후보 없음 | — | **에스컬레이션** |
| 서로 모순되는 후보가 여럿 통과 | 애매 | **에스컬레이션** |

**"둘 다 그대로 통과"하는 경우**(형이 짚은 핵심 케이스): 테스트가 A와 B를
구분하지 못한다는 뜻이다. 이때 "동작이 진짜 동등해서 아무거나 OK"인지 "동작은
다른데 테스트가 그 차이를 안 건드릴 뿐"인지를 기계는 가릴 수 없으므로, 자동
병합하지 않고 **에스컬레이션한다.** 넘길 때 빈손으로 주지 않고 **후보안(base
대비 diff)과 검증/뮤테이션 결과를 함께 보여주고**, 최종 선택은 사람에게
맡긴다(`escalate/report.py`의 역할). LLM 호출은 후보 생성(`candidates/
generate.py`) 한 곳으로 한정하기로 해서, 이전에 있던 LLM 기반 의도 요약
(`candidates/summarize.py`)은 제거했다 — 에스컬레이션 리포트의 "의도 요약"
칸은 항상 비어 있다.

> 이 판별을 구체화하는 것이 "스왑 테스트": B의 병합 결과에서 충돌 줄만 A 버전으로
> 바꿔치기해 테스트를 돌려, 테스트가 A/B를 구분하는지(깨지는지) 확인한다.
> 뮤테이션 엔진과 같은 메커니즘("이 줄을 바꾸면 테스트가 잡아내는가")을 재활용한다.

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
