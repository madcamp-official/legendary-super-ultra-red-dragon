# legendary-super-ultra-red-dragon — Weld

몰입캠프 26s-w3-c2-01 프로젝트 repository

검증되지 않은 git 병합은 절대 자동으로 착지하지 못하게 막는 무결성 안전장치.
`git merge driver`로 동작하며 UI 없음.

## 팀원

| 이름 | GitHub |
|---|---|
| 이재준 | dannyiscard | 
| 김민재 |Kminj2296|
|이서영|sksy930|


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

## 구조 & 담당

파일이 겹치지 않도록 모듈별로 담당을 나눴다. **다른 사람의 파일은 인터페이스
(함수 시그니처)만 보고 자기 파일에서 구현하고, `src/weld/types.py`(공용
데이터 계약)는 셋이 합의 없이 혼자 수정하지 않는다.**

```
src/weld/
├── cli.py                  # 오케스트레이션만, 파이프라인 배선 (건드릴 일 거의 없음)
├── types.py                # 공용 데이터 계약 — 수정 시 팀 합의 필요
├── classify/mergiraf.py    # 가짜/진짜 충돌 분류 (Mergiraf 연동)
├── candidates/
│   └── generate.py         # LLM 병합 후보 생성 (LLM 호출은 여기 한 곳뿐)
├── verify/
│   ├── sandbox.py          # 격리 샌드박스, 병렬 검증
│   ├── impact.py           # 테스트 영향 분석(선별 실행)
│   └── mutation.py         # 뮤테이션 테스팅-라이트 (핵심 기여)
├── policy/trust.py         # 검증+뮤테이션 결과 종합 → 채택/에스컬레이션
└── escalate/report.py      # 실패 시 사람에게 줄 리포트
```

`tests/`는 `src/weld/`와 같은 구조로 미러링돼 있다 — 자기 모듈의 테스트는
자기 폴더 안에서만 늘리면 된다.

### 기능
- 병합 과정에서 충돌이 날 시, mergiraf를 활용하여 1차 검증
- 1차 검증 통과(가짜 충돌로 판별 시) 테스트케이스로 테스트 진행, 통과 시 병합, 아닐 시 진짜 충돌로 판정
- 진짜 충돌로 판별 시, LLM이 충돌을 해결한 병합 후보들 생성.
- 각각의 병합 후보를 테스트케이스로 검증 후, 통과한 후보들에 한해서 뮤턴트를 생성하여 뮤턴트 테스팅 진행
- 하나의 후보만 살아남았을 시 병합, 아닐 시 반려.

## 회고 문서

> 개발 과정에서의 어려움, 해결 방법, 역할 분담, 다음에 개선할 점 (KPT 방법론 참고)

### Keep
- **파일 단위 분업**: 파일 단위로 분업을 하니 개발하는 1주일 동안 merge conflict가 발생한 적이 전혀 없었다. 

### Problem
- **실제 QA를 너무 늦게 시작함**: 6일차까지 파이프라인의 성능을 검증하기 위한 테스트에만 집중하여, 실제 QA를 7일차가 되어서야 시작했다. 그러다보니 예상치 못한 여러 문제점들이 발생하여 이를 급하게 수정했어야 했다.

### Try
- **완제품 제작 전 그때그때 QA 해보기**: 이번에 했던 방식과 달리 미리미리 QA를 진행하면 우리가 가졌던 문제를 미연에 방지할 수 있을 것 같다.