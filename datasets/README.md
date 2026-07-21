# LLM 필수 병합 데이터셋 (`llm_required_merges.jsonl`)

다른 LLM에게 **"코드 병합 충돌을 어떻게 해결하는가"**를 가르치기 위한 지도학습 셋.
Weld 파이프라인 기준으로 **LLM이 반드시 개입해야만 하는 병합**(구조충돌)만 골라,
사람이 실제 병합 커밋에서 채택한 결과를 정답으로 붙였다.

## 왜 이 케이스들만인가

실제 git 병합 히스토리에서 채굴한 뒤, **LLM 없이 처리 가능한 것을 전부 걷어냈다**:

- ❌ **가짜충돌**(mergiraf가 구문적으로 자동 해결) — LLM 불필요
- ❌ **값충돌**(양쪽 변경을 verbatim 나열하면 끝) — LLM 추론 불필요
- ❌ **비-프로덕션 파일**(playground/example/demo/fixture/docs) — 해결이 '정리 커밋'이라 노이즈
- ❌ **퇴화된 해결**(정답이 양쪽보다 훨씬 짧음 = 병합이 아니라 대량 삭제)
- ✅ **남은 것 = 진짜 구조충돌**: 두 브랜치가 겹치는 영역을 서로 다르게 바꿔,
  사람이 의미를 이해하고 손으로 합쳐야 했던 병합

## 스키마 (JSONL, 한 줄 = 한 케이스)

| 필드 | 설명 |
|---|---|
| `id` | `<repo>-<hash>` 고유 식별자 |
| `language` | python / javascript / typescript / c / cpp |
| `file_path` | 저장소 내 파일 경로 |
| `source_repo` / `source_commit` | 출처 저장소·병합 커밋 |
| `base` | 공통 조상 파일 전체 (LLM 입력) |
| `ours` / `theirs` | 양쪽 브랜치 파일 전체 (LLM 입력) |
| `conflict_diff3` | `<<<<<<< ours / \|\|\|\|\|\|\| base / ======= / >>>>>>> theirs` 마커로 표시된 충돌 영역 — **LLM이 실제로 보는 것** |
| `ground_truth` | **정답**: 사람이 실제 병합 커밋에서 채택한 파일 전체 |
| `resolution_kind` | `synthesis`(양쪽 합성) / `chose_ours` / `chose_theirs` |
| `approx_tokens` | base+ours+theirs+정답 대략 토큰 수 |

## 학습에 쓰는 법

```python
import json
rows = [json.loads(l) for l in open("llm_required_merges.jsonl")]

# 가장 가치 있는 케이스: 양쪽을 진짜로 합성해야 하는 것
synthesis = [r for r in rows if r["resolution_kind"] == "synthesis"]

# 학습쌍:  입력 = conflict_diff3 (또는 base/ours/theirs) → 출력 = ground_truth
for r in synthesis:
    prompt = r["conflict_diff3"]        # 충돌 영역
    answer = r["ground_truth"]          # 사람이 채택한 결과
```

## 현재 통계 (2026-07-21)

- **총 203 케이스** — Python 88 / JS 83 / TS 25 / C 4 / C++ 3
- 해결유형: **synthesis 106** / chose_ours 81 / chose_theirs 16
- 출처: express 75, flask 40, zod 26, click 20, requests 18, glom 8, axios 7, redis 4, fmt 3, rich 2

## 재생성 / 확장

빌더: `scratchpad/build_llm_dataset.py` (`REGISTRY`에 저장소 추가 → 자동 클론+채굴).
증분 저장(20건마다)이라 중간에 끊겨도 보존된다.

### 알려진 한계 / 확장 여지

- **`too_big` 189건 유실**: 전체파일 15k토큰 초과분을 통째로 버림. 충돌 **훙크만**
  잘라내면(diff3 영역 + 그 영역의 정답만) 대부분 복구되고 라벨 노이즈도 준다 → v2 후보.
- **C/C++ 소량(7건)**: 해당 저장소 충돌이 대형 파일에 몰려 too_big로 빠짐. 훙크 추출 시 개선.
- `ground_truth`는 병합 커밋의 **파일 전체**라, 충돌과 무관한 동시 편집이 섞일 수 있음.
  훙크 단위 정답 추출이 이 노이즈도 제거한다.
