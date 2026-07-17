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

## 구조 & 담당

파일이 겹치지 않도록 모듈별로 담당을 나눴다. **다른 사람의 파일은 인터페이스
(함수 시그니처)만 보고 자기 파일에서 구현하고, `src/weld/types.py`(공용
데이터 계약)는 셋이 합의 없이 혼자 수정하지 않는다.**

```
src/weld/
├── cli.py                 # 오케스트레이션만, 파이프라인 배선 (건드릴 일 거의 없음)
├── types.py                # 공용 데이터 계약 — 수정 시 팀 합의 필요
├── classify/mergiraf.py    # [팀원A] 가짜/진짜 충돌 분류 (Mergiraf 연동)
├── candidates/
│   ├── generate.py         # [나] LLM 병합 후보 생성
│   └── summarize.py        # [나] 에스컬레이션용 의도 요약
├── verify/
│   ├── sandbox.py          # [팀원B] 격리 샌드박스, 병렬 검증
│   ├── impact.py           # [팀원B] 테스트 영향 분석(선별 실행)
│   └── mutation.py         # [팀원A] 뮤테이션 테스팅-라이트 (핵심 기여)
├── policy/trust.py         # [나] 검증+뮤테이션 결과 종합 → 채택/에스컬레이션
└── escalate/report.py      # [나] 실패 시 사람에게 줄 리포트
```

`tests/`는 `src/weld/`와 같은 구조로 미러링돼 있다 — 자기 모듈의 테스트는
자기 폴더 안에서만 늘리면 된다.

## 작업 방식 (충돌 최소화)

- 각자 자기 담당 파일/폴더만 수정한다. `types.py`, `cli.py`는 공용이라
  손댈 일이 생기면 미리 얘기하고 바꾼다.
- 큰 작업을 끝까지 다 끝낸 뒤 한 번에 커밋하지 말고, 기능 단위로 자주
  작게 커밋 + 푸시한다 (충돌 범위가 작아짐).
- 푸시 전에는 `git pull --rebase`로 먼저 최신 상태를 받는다.
