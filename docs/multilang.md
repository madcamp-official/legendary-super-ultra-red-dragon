# 다국어 확장 현황 (김민재 파트, 2026-07-21 새벽)

## 무엇이 되나 (지금 기준)

| 파이프라인 단계 | 담당 모듈 | 다국어 상태 |
|---|---|---|
| 분류 (가짜/진짜) | `classify/mergiraf.py` | ✅ **30+개 언어** — `classify_conflict(..., file_path=)`로 실제 확장자를 넘기면 mergiraf가 해당 문법으로 분류. 미지원 확장자는 fail-safe(진짜 충돌) |
| 후보 생성 (diff3+LLM) | `candidates/generate.py` (이서영) | ✅ 원래 언어 무관 (수정 안 함) |
| 뮤테이션 검증 | `verify/mutation.py` → `verify/mutation_ts.py` | ✅ `.py`는 기존 ast 엔진, 그 외는 tree-sitter 엔진으로 자동 라우팅. **JS/TS는 실행 판정까지**(node --test), Go/Rust/Java는 사이트 수집만(런타임 설치 시 `langs.py`에 test_command만 채우면 활성화) |
| 테스트 선별 | `verify/impact.py` (이재준) | ❌ 아직 Python(coverage.py) 전용 — 비Python은 전체 스위트 실행으로 대체 중 |
| 샌드박스 검증 | `verify/sandbox.py` (이재준) | ❌ 아직 pytest 고정 — 비Python은 `evaluation/multilang.py`의 자체 러너로 대체 중 |
| 판정 정책 | `policy/trust.py` (이서영) | ✅ 언어 무관 (수정 안 함) |

핵심 설계: **언어 추가 = `src/weld/langs.py`의 레지스트리 항목 1개.**
확장자, tree-sitter 문법 이름, 테스트 실행 명령만 적으면 분류·뮤테이션이 열린다.

## 아침 테스트 방법

```bash
cd 3주차/weld

# 1) JS E2E 데모 (데모 저장소 자동 생성 → 분류→LLM→검증→뮤테이션→판정)
#    가짜충돌 자동병합 / 진짜충돌+강한테스트 / 진짜충돌+약한테스트 3케이스
PYTHONPATH=src python -m weld.evaluation.multilang --demo

# 2) 다국어 뮤테이션 단위 테스트 (실제 node --test 실행 포함)
PYTHONPATH=src python -m pytest tests/verify/test_mutation_ts.py -v
```

주의: Documents 아래 `.venv`는 iCloud 파일 스톨 문제가 있어(별도 공유),
스크래치패드의 로컬 venv 또는 `pip install tree-sitter tree-sitter-language-pack`
된 아무 로컬 파이썬을 쓰는 게 안전하다.

## tree-sitter 뮤테이션 엔진 요약 (`mutation_ts.py`)

- **텍스트 스플라이스**: tree-sitter의 바이트 오프셋으로 토큰만 제자리 치환 —
  언어별 unparse 불필요, 문법 추가 비용 0
- 오퍼레이터 7종: 비교 반전(`<`→`>=`, `===`→`!==` 등) / 논리(`&&`↔`||`) /
  산술(`+`↔`-`, `*`↔`/`) / bool 반전 / 문자열→빈 문자열 / `0`→`-1`
- 판정: 언어별 테스트 명령 전체 실행 exit code. **줄 커버리지 확인이 없어
  미실행 뮤턴트는 '생존'으로 집계** → 점수 하락 → 에스컬레이션 방향의
  보수적 편향 (의도된 동작, 오탐 방향으로는 안 샌다)
- fail-safe: tree-sitter 미설치/미지원 언어/baseline 실패 → 신호 없음(0/0)
  → trust의 mutants=0 분기가 처리

## 팀원에게 필요한 후속 작업 (다국어 완성 조건)

- **이재준**: sandbox.py의 테스트 실행을 `langs.py` 레지스트리 기반으로 분기
  (pytest → 언어별 test_command), impact.py는 비Python일 때 "전체 테스트 반환"
  폴백이면 초기 버전으로 충분
- **이서영**: cli.py에서 `classify_conflict(...)`에 `file_path=path` 전달
  (1줄), `weld install`의 .gitattributes 패턴을 `*.py` 외 확장자로 확대
- **공용**: pyproject.toml에 `tree-sitter`, `tree-sitter-language-pack` 의존성
  추가 (팀 합의 후)
