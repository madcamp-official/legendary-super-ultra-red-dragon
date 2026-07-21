"""Track B-2 — 실제 저장소 병합충돌 리플레이 (진짜 파이프라인 전체).

glom(Python)·axios(JS)의 실제 병합 히스토리에서 충돌을 채굴하고,
각 충돌의 병합 부모(p1, ours 쪽) 시점으로 저장소를 체크아웃한 뒤
**cli.py와 동일한 실전 경로**를 돌린다:

  select_relevant_tests(impact) → classify → generate(qwen)
  → run_candidates_parallel(sandbox) → compute_mutation_scores_parallel
  → decide_among(policy)

그리고 자동 병합됐다면 사람이 실제 채택한 결과(ground_truth)와 비교한다.

Track B-1(씨딩)과 달리 여기서는 통제가 없다 — 실제 코드, 실제 테스트,
실제 충돌로 파이프라인 전체(팀 3인 파트 전부)를 검증하는 트랙이다.
"""
from __future__ import annotations

import dataclasses
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

WELD = "/Users/kimminjae/Documents/몰입캠프/3주차/weld"
sys.path.insert(0, f"{WELD}/src")
os.chdir(WELD)

from weld.candidates.generate import generate_candidates  # noqa: E402
from weld.classify.mergiraf import classify_conflict  # noqa: E402
from weld.evaluation.mining import mine_conflicts  # noqa: E402
from weld.policy.trust import decide_among  # noqa: E402
from weld.types import MergeCandidate  # noqa: E402
from weld.verify.impact import select_relevant_tests  # noqa: E402
from weld.verify.mutation import compute_mutation_scores_parallel  # noqa: E402
from weld.verify.sandbox import run_candidates_parallel, run_in_sandbox  # noqa: E402

OUT = f"{WELD}/results/overnight-0721/track_b2_real.jsonl"
SCRATCH_OLD = "/private/tmp/claude-501/-Users-kimminjae-Documents------1--/3402dea4-1e99-4fc0-b9e7-eff4db43282c/scratchpad"
WORK = "/private/tmp/claude-501/-Users-kimminjae-Documents------1--/10bf48a6-ad74-4827-acbb-873ca1494c72/scratchpad/b2_work"

REPOS = [
    ("glom", f"{SCRATCH_OLD}/eval_bug/glom", (".py",), "python"),
    ("axios", f"{SCRATCH_OLD}/eval_js/axios", (".js", ".mjs"), "javascript"),
]

_CASE_TIMEOUT_NOTE = "케이스당 상한 없음 — 밤샘 배치라 느긋하게"


def _git(repo: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)


def _prepare_work_clone(name: str, src: str) -> str:
    """원본 채굴 저장소를 건드리지 않도록 작업 사본을 만든다(1회)."""
    dst = f"{WORK}/{name}"
    if not os.path.isdir(dst):
        Path(WORK).mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dst, symlinks=True)
    return dst


def _checkout_parent(repo: str, merge_sha: str) -> str | None:
    p1 = _git(repo, "rev-parse", f"{merge_sha}^1").stdout.strip()
    if not p1:
        return None
    r = _git(repo, "checkout", "-f", p1)
    if r.returncode != 0:
        return None
    # 생성물 정리하되 node_modules는 보존 (재설치 비용/네트워크 회피)
    _git(repo, "clean", "-fdq", "-e", "node_modules", "-e", ".weld_cache")
    return p1


def _baseline_failed_pytests(repo: str) -> set[str] | None:
    """p1 시점 전체 pytest를 한 번 돌려 빨간 테스트 노드ID를 수집.
    (weld는 초록 main을 가정 — 그 시점에 이미 깨져 있던 테스트는
    선별 목록에서 제외해 공정한 판정을 만든다.)  suite 자체가 못 돌면 None."""
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--tb=no", "-p", "no:cacheprovider"],
        cwd=repo, capture_output=True, text=True, timeout=600,
    )
    failed = set(re.findall(r"^FAILED (\S+)", r.stdout, re.M))
    err = set(re.findall(r"^ERROR (\S+)", r.stdout, re.M))
    if r.returncode not in (0, 1):  # 2=collection error 등 — 환경 문제
        return None
    return failed | err


def run_real_case(case, repo: str, lang: str) -> dict:
    rec: dict = {"id": case.id, "file_path": case.file_path, "lang": lang}
    t0 = time.time()

    # 1) 실전과 동일: 변경 줄 근사 → 관련 테스트 선별 (impact.py)
    matcher = difflib.SequenceMatcher(a=case.base.splitlines(), b=case.ours.splitlines())
    changed = {
        line
        for tag, _, _, b0, b1 in matcher.get_opcodes()
        if tag != "equal"
        for line in range(b0 + 1, b1 + 1)
    }
    t = time.time()
    relevant = select_relevant_tests(
        [case.file_path], repo_path=repo, changed_lines={case.file_path: changed}
    )
    rec["select_seconds"] = round(time.time() - t, 1)

    # 초록 main 가정 보정: p1 시점에 이미 깨져 있던 pytest는 선별에서 제외
    if lang == "python":
        baseline_failed = _baseline_failed_pytests(repo)
        if baseline_failed is None:
            rec["skip"] = "baseline-suite-broken"
            return rec
        rec["baseline_failed"] = len(baseline_failed)
        relevant = [
            tid for tid in relevant
            if not any(tid.startswith(f.split("::")[0]) and f in tid or tid == f
                       for f in baseline_failed)
        ] if baseline_failed else relevant
    rec["relevant_tests"] = len(relevant)

    # 2) 분류
    cls = classify_conflict(case.base, case.ours, case.theirs, file_path=case.file_path)
    rec["classification"] = {"is_spurious": cls.is_spurious}
    if cls.is_spurious:
        cand = MergeCandidate(
            id="mergiraf-spurious", content=cls.resolved_content or "",
            strategy="mergiraf", file_path=case.file_path,
        )
        v = run_in_sandbox(cand, repo_path=repo, tests=relevant)
        rec["spurious_verification"] = {
            "compiled": v.compiled, "tests_passed": v.tests_passed,
        }
        if v.compiled and v.tests_passed:
            rec["action"] = "auto_spurious"
            rec["gt_equal"] = _gt_equal(cand.content, case)
            rec["wall_seconds"] = round(time.time() - t0, 1)
            return rec

    # 3) 후보 생성 (qwen)
    t = time.time()
    candidates = [
        dataclasses.replace(c, file_path=case.file_path)
        for c in generate_candidates(case.base, case.ours, case.theirs,
                                     file_path=case.file_path)
    ]
    rec["generate_seconds"] = round(time.time() - t, 1)
    rec["strategies"] = [c.strategy for c in candidates]

    # 4) 검증(sandbox) + 뮤테이션 — 실전 병렬 경로 그대로
    t = time.time()
    verifications = run_candidates_parallel(candidates, repo_path=repo, tests=relevant)
    rec["verify_seconds"] = round(time.time() - t, 1)
    t = time.time()
    mutations = compute_mutation_scores_parallel(
        candidates, relevant, repo_path=repo, base_content=case.base
    )
    rec["mutation_seconds"] = round(time.time() - t, 1)

    rec["candidates"] = [
        {
            "id": c.id, "strategy": c.strategy,
            "compiled": v.compiled, "tests_passed": v.tests_passed,
            "mutants": m.mutants_total, "killed": m.mutants_killed,
            "score": round(m.score, 3),
        }
        for c, v, m in zip(candidates, verifications, mutations)
    ]

    # 5) 판정 (policy)
    d = decide_among(candidates, verifications, mutations)
    rec["action"] = "auto_verified" if d.accepted else "escalated"
    rec["decision_reason"] = d.reason
    if d.accepted:
        content = next(c.content for c in candidates if c.id == d.candidate_id)
        rec["gt_equal"] = _gt_equal(content, case)
        rec["gt_ratio"] = round(
            difflib.SequenceMatcher(
                None, content.strip(), (case.ground_truth_resolution or "").strip()
            ).ratio(), 4,
        )
    rec["wall_seconds"] = round(time.time() - t0, 1)
    return rec


def _gt_equal(content: str, case) -> bool | None:
    gt = case.ground_truth_resolution
    if gt is None:
        return None
    return content.strip() == gt.strip()


def main() -> None:
    Path(OUT).parent.mkdir(parents=True, exist_ok=True)
    done: set[str] = set()
    if os.path.exists(OUT):
        for l in open(OUT):
            try:
                done.add(json.loads(l)["id"])
            except Exception:  # noqa: BLE001
                pass

    limit = int(os.environ.get("TRACK_B2_LIMIT", "0"))
    only_repo = os.environ.get("TRACK_B2_REPO", "")

    out_f = open(OUT, "a")
    for name, src, exts, lang in REPOS:
        if only_repo and name != only_repo:
            continue
        work = _prepare_work_clone(name, src)
        try:
            cases = mine_conflicts(src, extensions=exts)
        except Exception as e:  # noqa: BLE001
            print(f"[{name}] 채굴 실패: {e}", flush=True)
            continue
        if limit:
            cases = cases[:limit]
        print(f"[{name}] {len(cases)}케이스", flush=True)
        for i, case in enumerate(cases):
            cid = f"{name}-{case.id}"
            if cid in done:
                continue
            p1 = _checkout_parent(work, case.source_commit)
            if p1 is None:
                rec = {"id": cid, "skip": "checkout-failed"}
            else:
                try:
                    rec = run_real_case(case, work, lang)
                    rec["id"] = cid
                    rec["merge_commit"] = case.source_commit[:10]
                except Exception as e:  # noqa: BLE001
                    rec = {"id": cid,
                           "error": f"{type(e).__name__}: {str(e)[:200]}"}
            rec["repo"] = name
            out_f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out_f.flush()
            print(f"  [{i + 1}/{len(cases)}] {cid}: "
                  f"{rec.get('action') or rec.get('skip') or rec.get('error', '?')[:60]} "
                  f"({rec.get('wall_seconds', '?')}s)", flush=True)

    out_f.close()
    res = [json.loads(l) for l in open(OUT)]
    from collections import Counter, defaultdict
    print("\n===== Track B-2 요약 =====", flush=True)
    by_repo: dict[str, Counter] = defaultdict(Counter)
    for r in res:
        key = r.get("action") or r.get("skip") or "error"
        by_repo[r.get("repo", "?")][key] += 1
    for repo, cnt in sorted(by_repo.items()):
        print(f"  {repo}: {dict(cnt)}")
    auto = [r for r in res if r.get("action") in ("auto_spurious", "auto_verified")]
    wrong = [r for r in auto if r.get("gt_equal") is False]
    print(f"  자동병합 {len(auto)}건 중 사람과 문자열 불일치 {len(wrong)}건 "
          f"(불일치=오탐 아님: 전체파일 정답의 무관 편집 노이즈 포함)")


if __name__ == "__main__":
    main()
