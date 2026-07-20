"""mergiraf가 '가짜'로 판정한 병합의 테스트 실패율을 측정한다.

finding B(가짜 충돌 경로도 검증)를 구현할 가치가 있는지 정하는 근거를 만든다.
mergiraf가 가짜로 처리한 병합이 테스트를 자주 실패한다면 → finding B가 실제
silent bug를 잡는다는 뜻(구현 가치 높음). 거의 안 실패하면 → 얇은 안전장치로
충분하거나 후순위.

동작: 저장소의 각 충돌에 대해
  1) classify_conflict로 mergiraf 분류 → 가짜(is_spurious)만 대상
  2) 그 병합이 일어난 시점(source_commit)으로 저장소를 체크아웃
  3) mergiraf가 만든 병합 결과를 후보처럼 샌드박스에 태워 관련 테스트 실행
  4) pass/fail 집계

주의:
- mergiraf 바이너리가 PATH에 있어야 한다(brew install mergiraf).
- 테스트가 실제로 돌아야 판정 가능하다. 의존성이 있는 저장소는 그 의존성이
  설치돼 있어야 하고, 아주 오래된 커밋(nose 등 죽은 프레임워크)은 테스트가
  안 돌아 '스킵'으로 빠진다 — 우선 의존성 적은 저장소(more-itertools 등)로 시작.
- 이 스크립트는 저장소를 각 커밋으로 체크아웃하므로, 끝나면 저장소가 원래
  브랜치가 아닌 상태로 남는다(측정 전용 클론에서 돌릴 것).

사용:
  python -m weld.evaluation.measure_spurious <클론된_저장소_경로> [--limit N]
"""

from __future__ import annotations

import argparse
import subprocess

from weld.classify.mergiraf import classify_conflict
from weld.evaluation.mining import mine_conflicts
from weld.types import MergeCandidate
from weld.verify.impact import select_relevant_tests
from weld.verify.sandbox import run_in_sandbox


def _checkout(repo: str, commit: str) -> None:
    subprocess.run(["git", "-C", repo, "checkout", "-q", commit], check=True)


def measure(repo: str, limit: int | None = None) -> None:
    conflicts = mine_conflicts(repo)
    print(f"채굴된 충돌: {len(conflicts)}건. mergiraf 분류 + 검증 중...\n")

    spurious = judged = failed = skipped = 0
    for c in conflicts:
        classification = classify_conflict(c.base, c.ours, c.theirs)
        if not classification.is_spurious:
            continue
        spurious += 1

        _checkout(repo, c.source_commit)
        tests = select_relevant_tests([c.file_path], repo_path=repo)
        candidate = MergeCandidate(
            id=c.id, content=classification.resolved_content or "", file_path=c.file_path
        )
        result = run_in_sandbox(candidate, repo_path=repo, tests=tests)

        if not result.tests_run:
            skipped += 1  # 테스트가 안 돌아감(의존성/옛날 커밋) — 판정 불가
            continue
        judged += 1
        if not result.tests_passed:
            failed += 1
            print(f"  [실패] {c.id}  ({len(result.tests_failed)}개 테스트 실패)")

        if limit is not None and judged >= limit:
            break

    print("\n===== 결과 =====")
    print(f"mergiraf가 '가짜'로 분류: {spurious}건")
    print(f"테스트 실행되어 판정된 것: {judged}건")
    rate = (failed * 100 / judged) if judged else 0
    print(f"  └ 그 중 테스트 실패: {failed}건 ({rate:.1f}%)  ← 이게 finding B의 가치")
    print(f"테스트 못 돌려 스킵(의존성/옛날): {skipped}건")


def main() -> None:
    parser = argparse.ArgumentParser(description="mergiraf 가짜 병합의 테스트 실패율 측정")
    parser.add_argument("repo", help="클론된 저장소 경로 (측정 전용 클론 권장)")
    parser.add_argument("--limit", type=int, default=None, help="판정 N건에서 멈춤(빠른 확인용)")
    args = parser.parse_args()
    measure(args.repo, args.limit)


if __name__ == "__main__":
    main()
