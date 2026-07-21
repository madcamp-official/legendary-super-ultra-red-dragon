# Track B-2 자동병합 문자열 불일치 3건 — 손검증 감사 (2026-07-21 밤)

자동병합 5건 중 `gt_equal=False` 3건을 diff로 재현·손검증한 결과,
**3건 모두 의미 동등(스타일 차이)** — 행동이 달라지는 오탐은 0건.

## 1. glom-6a16b321 `glom/test/test_path_and_t.py` (mergiraf 가짜충돌)

- weld: `from glom import ..., Or` + `from glom import ..., Assign, Delete` (두 줄 병렬 유지)
- 사람: `from glom import ..., Or, Assign, Delete` (한 줄 통합)
- 판정: **의미 동등.** 연속된 같은 모듈 import 두 줄은 네임스페이스 결과가
  한 줄 통합과 동일. mergiraf는 양쪽 추가를 모두 보존했고 사람은 스타일 정리만 함.

## 2. glom-16954abc `glom/test/test_flat.py` (LLM 합성, auto_verified, ratio 0.963)

- 차이: 빈 줄 위치, `== 4` vs `==4`(원본 파일의 공백 오타), 그리고 사람이
  중복 정의를 정리한 `test_sum_basic` 한 개를 weld는 원위치 유지.
- 판정: **행동 동일.** 실행되는 테스트 집합이 같고(중복 def는 파이썬이 조용히
  가림), 검증+뮤테이션 게이트도 통과. 사람 해법은 코드 정리 취향 차이.

## 3. axios-5fc59a65 `lib/adapters/xhr.js` (mergiraf 가짜충돌)

- 차이: `isArrayBuffer(requestData)` 변환 블록의 위치 (사람은 앞, weld는 뒤)
- 판정: **의미 동등.** 인접 블록(isFormData 체크)과 상호배타적 타입 가드라
  순서가 결과에 영향 없음.

## 결론

실전 리플레이(B-2) 자동병합 5건 → **행동 오탐 0건**.
문자열 일치율은 하한선 지표일 뿐이며(전체파일 정답에 무관 동시편집·스타일
정리가 섞임), 안전성 주장에는 손검증된 의미 동등성이 근거가 된다.
