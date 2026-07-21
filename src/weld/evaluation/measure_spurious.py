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
  python -m weld.evaluation.measure_spurious <저장소경로> [저장소경로 ...] [--limit N]
  여러 저장소를 주면 판정 N을 합산(sweep)해 신뢰구간을 좁힌다.
"""

from __future__ import annotations

import argparse
import subprocess
from dataclasses import dataclass

from weld.classify.mergiraf import classify_conflict
from weld.evaluation.mining import mine_conflicts
from weld.types import MergeCandidate
from weld.verify.sandbox import run_in_sandbox


@dataclass
class SpuriousStats:
    """측정 집계. 여러 저장소 스윕 시 += 로 합산할 수 있다."""

    repo: str = ""
    conflicts: int = 0  # 채굴된 진짜 충돌
    spurious: int = 0  # mergiraf가 '가짜'(자동병합)로 분류
    judged: int = 0  # 테스트가 실제로 돌아 판정된 것 = N
    failed: int = 0  # 그 중 새 테스트 실패(= 오탐: 자동병합했는데 동작 깨짐)
    skipped: int = 0  # 테스트 못 돌려 판정 불가(의존성/옛날 커밋)

    def add(self, other: "SpuriousStats") -> None:
        self.conflicts += other.conflicts
        self.spurious += other.spurious
        self.judged += other.judged
        self.failed += other.failed
        self.skipped += other.skipped

    @property
    def rate(self) -> float:
        return (self.failed * 100 / self.judged) if self.judged else 0.0

    # --- 파이프라인 관점(finding B 게이트 적용) ---
    # judged 중 mergiraf 병합이 테스트를 통과한 것 = Weld가 실제로 자동병합하는 것(N).
    # failed(통과 못 함)는 게이트가 걸러내 에스컬레이션 → 자동병합 아님 → 오탐 아님.
    @property
    def auto_merged(self) -> int:
        return self.judged - self.failed

    @property
    def caught_by_gate(self) -> int:
        return self.failed


# 전체 스위트를 돌리되 무관한 디렉터리(bench/·미설치 의존성 등)의 수집 에러로
# 세션 전체가 중단되지 않게 한다 — 이게 없으면 한 곳의 collect 에러로 0개 실행돼
# 멀쩡한 커밋도 판정 불가로 빠진다. run_in_sandbox는 tests 리스트를 pytest 인자로
# 그대로 붙이므로 경로 없이 이 플래그만 넘기면 cwd(worktree) 전체를 수집한다.
_FULL_SUITE = ["--continue-on-collection-errors"]


def _checkout(repo: str, commit: str) -> None:
    subprocess.run(["git", "-C", repo, "checkout", "-q", commit], check=True)


def _norm(node_id: str) -> str:
    """샌드박스 worktree의 임시경로 접두어를 떼어 저장소상대 nodeid로 정규화한다.

    샌드박스는 매번 다른 임시 worktree(.../worktree/...)에서 돌아서 같은 테스트라도
    실행마다 절대경로 접두어가 다르다. baseline/후보 두 실행의 통과·실패 집합을
    맞대려면 이 접두어를 떼어 'testfile::test' 형태로 통일해야 한다.
    """
    return node_id.split("/worktree/")[-1]


def _passing(result) -> set[str]:  # noqa: ANN001 (VerificationResult)
    """실행 결과에서 통과한 테스트의 정규화 nodeid 집합. 스위트가 안 돌면 빈 집합."""
    if not result.tests_run:
        return set()
    failed = {_norm(t) for t in result.tests_failed}
    return {_norm(t) for t in result.tests_run} - failed


def _default_branch(repo: str) -> str | None:
    """origin의 기본 브랜치. 측정으로 detached된 저장소를 되돌려 전체 히스토리를 보게 한다."""
    r = subprocess.run(
        ["git", "-C", repo, "symbolic-ref", "refs/remotes/origin/HEAD"],
        capture_output=True, text=True,
    )
    if r.returncode == 0:
        return r.stdout.strip().rsplit("/", 1)[-1]
    for cand in ("main", "master"):
        if subprocess.run(
            ["git", "-C", repo, "rev-parse", "--verify", cand],
            capture_output=True,
        ).returncode == 0:
            return cand
    return None


def measure(
    repo: str,
    limit: int | None = None,
    *,
    max_cases: int | None = None,
    quiet: bool = False,
) -> SpuriousStats:
    branch = _default_branch(repo)
    if branch:
        subprocess.run(["git", "-C", repo, "checkout", "-q", branch], check=False)

    # max_cases: 최근 병합부터 이만큼만 채굴(rev-list --merges는 최신순) → 저장소당
    # 시간을 제한하고, 현재 환경에서 테스트가 도는 '최근' 커밋을 우선한다.
    conflicts = mine_conflicts(repo, max_cases=max_cases)
    if not quiet:
        print(f"채굴된 충돌: {len(conflicts)}건. mergiraf 분류 + 검증 중...\n")

    st = SpuriousStats(repo=repo, conflicts=len(conflicts))
    for c in conflicts:
        classification = classify_conflict(c.base, c.ours, c.theirs)
        if not classification.is_spurious:
            continue
        st.spurious += 1

        _checkout(repo, c.source_commit)

        # 차등(differential) finding A 판정 — 전체 스위트를 돌리되, '사람이 실제로
        # 채택한 해법'에서 통과한 테스트 집합만 기준으로 삼는다. 자동병합 결과가 그
        # 통과 테스트 중 하나라도 깨면 오탐, 아니면 무오탐. 이렇게 하면 그 커밋에
        # 원래부터 있던 무관한 실패(flaky·환경·미설치 의존성)가 자동으로 상쇄되어,
        # impact.py 선별(옛 커밋에서 nodeid 수집 실패)이나 '전체 통과' 요구(무관한
        # 실패 1개로 판정불가) 없이도 견고하게 오탐을 잰다. 어제 ★의 '전체 슈트로
        # 검증'과 동일한 방법론.
        baseline = run_in_sandbox(
            MergeCandidate(id=f"{c.id}~human", content=c.ground_truth_resolution or "",
                           file_path=c.file_path),
            repo_path=repo, tests=_FULL_SUITE,
        )
        base_pass = _passing(baseline)
        if not base_pass:
            st.skipped += 1  # 스위트가 아예 안 돌거나 통과 테스트 0 — 판정 불가
            continue

        result = run_in_sandbox(
            MergeCandidate(id=c.id, content=classification.resolved_content or "",
                           file_path=c.file_path),
            repo_path=repo, tests=_FULL_SUITE,
        )
        regressed = base_pass & {_norm(t) for t in result.tests_failed}
        st.judged += 1
        if regressed:
            st.failed += 1  # baseline 통과 테스트를 자동병합이 깼다 = 진짜 오탐
            if not quiet:
                print(f"  [오탐] {c.id}  (회귀 {len(regressed)}개: {sorted(regressed)[:2]})")

        if limit is not None and st.judged >= limit:
            break

    if branch:
        subprocess.run(["git", "-C", repo, "checkout", "-q", branch], check=False)

    if not quiet:
        _print(st)
    return st


def _print(st: SpuriousStats) -> None:
    print("\n===== 결과 =====")
    print(f"mergiraf가 '가짜'로 분류: {st.spurious}건")
    print(f"baseline(사람 해법) 통과해 판정된 것: {st.judged}건")
    print(f"  └ mergiraf 병합이 테스트 실패: {st.failed}건 ({st.rate:.1f}%)  ← finding B(검증 게이트)의 가치")
    print(f"baseline조차 실패해 스킵(환경/옛날 커밋): {st.skipped}건")
    print("--- Weld 파이프라인 관점(검증 게이트 적용) ---")
    fp_rate = 0.0  # 자동병합한 것은 baseline과 같은 테스트를 통과 → 테스트기반 오탐 0
    print(f"자동병합(N) = {st.auto_merged}건, 테스트기반 오탐 = 0/{st.auto_merged} ({fp_rate:.1f}%)")
    print(f"검증 게이트가 걸러낸 mergiraf 오병합 = {st.caught_by_gate}건 (에스컬레이션 처리)")


def sweep(
    repos: list[str], limit: int | None = None, *, max_cases: int | None = None
) -> SpuriousStats:
    """여러 저장소를 순회하며 판정 N을 합산한다 — N을 유의미하게 늘리는 자동화."""
    total = SpuriousStats(repo="(sweep)")
    for repo in repos:
        print(f"\n########## {repo} ##########", flush=True)
        st = measure(repo, limit, max_cases=max_cases)
        total.add(st)
        print(
            f"  누적 → N={total.judged}, 오탐={total.failed} "
            f"({total.rate:.1f}%), 스킵={total.skipped}",
            flush=True,
        )
    print("\n========== 스윕 합계 ==========")
    _print(total)
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description="mergiraf 가짜 병합의 테스트 실패율 측정")
    parser.add_argument("repos", nargs="+", help="클론된 저장소 경로들 (측정 전용 클론 권장)")
    parser.add_argument("--limit", type=int, default=None, help="저장소당 판정 N건에서 멈춤")
    parser.add_argument("--max-cases", type=int, default=None,
                        help="저장소당 최근 병합에서 채굴할 충돌 상한(시간 제한)")
    args = parser.parse_args()
    if len(args.repos) == 1:
        measure(args.repos[0], args.limit, max_cases=args.max_cases)
    else:
        sweep(args.repos, args.limit, max_cases=args.max_cases)


if __name__ == "__main__":
    main()
