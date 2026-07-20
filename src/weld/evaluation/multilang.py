"""담당: 김민재

비Python 언어용 E2E 평가 하네스.

cli.py의 병합 파이프라인(분류 → 후보 생성 → 검증 → 뮤테이션 → 판정)을
그대로 따르되, 아직 Python 전용인 팀원 파트 둘을 언어 무관 방식으로
대체한다 (팀원 코드는 수정하지 않는다):

  - verify/sandbox.py (pytest 고정)  → langs.LanguageSpec.test_command 전체 실행
  - verify/impact.py (coverage 선별) → 선별 없이 전체 스위트 (작은 저장소 전제)

컴파일 게이트는 node --check(문법 검사)로, 테스트 게이트는 언어별 테스트
명령의 exit code로 대신한다. trust.decide가 요구하는 tests_run에는 스위트
실행 여부를 나타내는 표식 하나를 넣는다(개별 테스트 ID 열거는 러너마다
달라 아직 안 한다).

사용:
  python -m weld.evaluation.multilang --demo            # JS 데모 3케이스 E2E
  python -m weld.evaluation.multilang cases.json        # EvalCase JSON 실행
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import shutil
import subprocess
import tempfile
import textwrap
import time
from pathlib import Path

from weld.candidates.generate import generate_candidates
from weld.classify.mergiraf import classify_conflict
from weld.evaluation.cases import EvalCase
from weld.langs import detect_language
from weld.policy.trust import decide_among
from weld.types import MergeCandidate, MutationScore, VerificationResult
from weld.verify.mutation import compute_mutation_score

_SUITE_TIMEOUT_S = 120


def _verify_with_lang_tests(
    candidate: MergeCandidate, repo_path: str
) -> VerificationResult:
    """샌드박스 대체: 저장소 사본에 후보를 쓰고 언어별 전체 테스트를 돌린다."""
    start = time.monotonic()
    spec = detect_language(candidate.file_path)
    if spec is None or spec.test_command is None:
        return VerificationResult(
            candidate_id=candidate.id, compiled=False, tests_passed=False,
            error=f"테스트 실행 방법을 모르는 언어: {candidate.file_path}",
        )

    with tempfile.TemporaryDirectory(prefix="weld-mlverify-") as tmp:
        repo = Path(tmp) / "repo"
        shutil.copytree(
            repo_path, repo,
            ignore=shutil.ignore_patterns(".git", "node_modules", ".venv"),
        )
        target = repo / candidate.file_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(candidate.content)

        if spec.name in ("javascript", "typescript"):
            check = subprocess.run(
                ["node", "--check", str(target)], capture_output=True, text=True
            )
            if check.returncode != 0:
                return VerificationResult(
                    candidate_id=candidate.id, compiled=False, tests_passed=False,
                    error=f"문법 오류: {check.stderr.strip()[:200]}",
                    duration_s=time.monotonic() - start,
                )

        try:
            result = subprocess.run(
                list(spec.test_command), cwd=repo, capture_output=True, text=True,
                timeout=_SUITE_TIMEOUT_S,
            )
        except (subprocess.TimeoutExpired, OSError) as e:
            return VerificationResult(
                candidate_id=candidate.id, compiled=True, tests_passed=False,
                error=f"테스트 실행 실패: {e}", duration_s=time.monotonic() - start,
            )

        passed = result.returncode == 0
        return VerificationResult(
            candidate_id=candidate.id, compiled=True, tests_passed=passed,
            tests_run=[f"{spec.name}-full-suite"],
            tests_failed=[] if passed else [f"{spec.name}-full-suite"],
            duration_s=time.monotonic() - start,
        )


def run_case(case: EvalCase, repo_path: str) -> dict:
    """케이스 하나를 파이프라인 전체에 태우고 단계별 기록을 반환한다."""
    t0 = time.monotonic()
    rec: dict = {"id": case.id, "file_path": case.file_path}

    cls = classify_conflict(case.base, case.ours, case.theirs, file_path=case.file_path)
    rec["classification"] = {"is_spurious": cls.is_spurious, "reason": cls.reason}

    if cls.is_spurious:
        cand = MergeCandidate(
            id="mergiraf-spurious", content=cls.resolved_content or "",
            strategy="mergiraf", file_path=case.file_path,
        )
        v = _verify_with_lang_tests(cand, repo_path)
        rec["spurious_verification"] = {
            "compiled": v.compiled, "tests_passed": v.tests_passed, "error": v.error,
        }
        if v.compiled and v.tests_passed:
            rec["path"] = "spurious-accepted"
            rec["decision"] = {"accepted": True, "candidate_id": cand.id,
                               "reason": "mergiraf 구조 병합 + 언어 테스트 통과"}
            rec["accepted_content"] = cand.content
            _compare_ground_truth(rec, case, cand.content)
            rec["duration_s"] = round(time.monotonic() - t0, 1)
            return rec
        rec["path"] = "spurious-fell-through"

    candidates = [
        dataclasses.replace(c, file_path=case.file_path)
        for c in generate_candidates(case.base, case.ours, case.theirs, n=2)
    ]
    rec["path"] = rec.get("path", "") + ("+" if cls.is_spurious else "") + "llm-pipeline"
    rec["candidates"] = []
    verifications: list[VerificationResult] = []
    mutations: list[MutationScore] = []
    for c in candidates:
        v = _verify_with_lang_tests(c, repo_path)
        m = compute_mutation_score(
            c, relevant_tests=["full-suite"], repo_path=repo_path,
            base_content=case.base, budget=12, trust_threshold=0.8,
        )
        verifications.append(v)
        mutations.append(m)
        rec["candidates"].append({
            "id": c.id, "strategy": c.strategy,
            "verification": {"compiled": v.compiled, "tests_passed": v.tests_passed,
                             "error": v.error},
            "mutation": {"sites_total": m.sites_total, "mutants_total": m.mutants_total,
                         "mutants_killed": m.mutants_killed,
                         "score": round(m.score, 3),
                         "survived": m.survived_mutants[:5]},
        })

    d = decide_among(candidates, verifications, mutations)
    rec["decision"] = {"accepted": d.accepted, "candidate_id": d.candidate_id,
                       "reason": d.reason}
    if d.accepted:
        content = next(c.content for c in candidates if c.id == d.candidate_id)
        rec["accepted_content"] = content
        _compare_ground_truth(rec, case, content)
    rec["duration_s"] = round(time.monotonic() - t0, 1)
    return rec


def _compare_ground_truth(rec: dict, case: EvalCase, accepted: str) -> None:
    human = (case.ground_truth_resolution or "").strip()
    rec["ground_truth"] = {"string_equal": accepted.strip() == human}


# ---------------------------------------------------------------- demo 모드

_JS_MATH = textwrap.dedent("""\
    function add(a, b) {
      return a + b;
    }

    function mul(a, b) {
      return a * b;
    }

    module.exports = { add, mul };
    """)

_JS_MATH_TESTS = textwrap.dedent("""\
    const test = require('node:test');
    const assert = require('node:assert');
    const { add, mul } = require('../src/math.js');

    test('add', () => { assert.strictEqual(add(2, 3), 5); });
    test('mul', () => { assert.strictEqual(mul(2, 3), 6); });
    """)

_JS_STATS = textwrap.dedent("""\
    function mean(values) {
      if (!values.length) {
        throw new Error("empty values");
      }
      return values.reduce((a, b) => a + b, 0) / values.length;
    }

    module.exports = { mean };
    """)

_JS_STATS_TESTS = textwrap.dedent("""\
    const test = require('node:test');
    const assert = require('node:assert');
    const { mean } = require('../src/stats.js');

    test('mean normal', () => { assert.strictEqual(mean([2, 4]), 3); });
    test('mean empty throws', () => { assert.throws(() => mean([])); });
    """)

_JS_AGES = textwrap.dedent("""\
    function validateAge(age) {
      if (age < 0) {
        throw new Error("age must be non-negative");
      }
      return age;
    }

    module.exports = { validateAge };
    """)

_JS_AGES_TESTS = textwrap.dedent("""\
    const test = require('node:test');
    const assert = require('node:assert');
    const { validateAge } = require('../src/ages.js');

    // 약한 테스트(의도적): 정상 경로만 — 오류/경계 경로는 안 본다.
    test('ok age', () => { assert.strictEqual(validateAge(30), 30); });
    """)


def build_demo_repo(root: Path) -> None:
    (root / "src").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "src" / "math.js").write_text(_JS_MATH)
    (root / "src" / "stats.js").write_text(_JS_STATS)
    (root / "src" / "ages.js").write_text(_JS_AGES)
    (root / "tests" / "math.test.js").write_text(_JS_MATH_TESTS)
    (root / "tests" / "stats.test.js").write_text(_JS_STATS_TESTS)
    (root / "tests" / "ages.test.js").write_text(_JS_AGES_TESTS)


def demo_cases() -> list[EvalCase]:
    j1 = EvalCase(
        id="js-j1-spurious", file_path="src/math.js", base=_JS_MATH,
        ours=_JS_MATH.replace("return a + b;", "return Number(a) + Number(b);"),
        theirs=_JS_MATH.replace("return a * b;", "return a * b * 1;"),
        ground_truth_resolution=_JS_MATH.replace(
            "return a + b;", "return Number(a) + Number(b);"
        ).replace("return a * b;", "return a * b * 1;"),
    )
    j2_ours = _JS_STATS.replace(
        "if (!values.length) {", "if (values === null || !values.length) {"
    ).replace('"empty values"', '"no values to average"')
    j2 = EvalCase(
        id="js-j2-real-strong-tests", file_path="src/stats.js", base=_JS_STATS,
        ours=j2_ours,
        theirs=_JS_STATS.replace("if (!values.length) {", "if (values.length === 0) {"),
        ground_truth_resolution=j2_ours,
    )
    j3_ours = _JS_AGES.replace("if (age < 0) {", "if (age < 0 || age > 150) {").replace(
        '"age must be non-negative"', '"age out of range"'
    )
    j3 = EvalCase(
        id="js-j3-real-weak-tests", file_path="src/ages.js", base=_JS_AGES,
        ours=j3_ours,
        theirs=_JS_AGES.replace("if (age < 0) {", "if (0 > age) {").replace(
            '"age must be non-negative"', '"negative age not allowed"'
        ),
        ground_truth_resolution=j3_ours,
    )
    return [j1, j2, j3]


def main() -> None:
    parser = argparse.ArgumentParser(description="비Python 언어 E2E 평가")
    parser.add_argument("cases", nargs="?", help="EvalCase JSON 파일")
    parser.add_argument("--demo", action="store_true", help="JS 데모 3케이스 실행")
    parser.add_argument("--repo", help="cases 실행 시 대상 저장소 경로")
    parser.add_argument("--out", default=None, help="결과 JSON 저장 경로")
    args = parser.parse_args()

    if args.demo:
        tmp = Path(tempfile.mkdtemp(prefix="weld-mldemo-"))
        build_demo_repo(tmp)
        cases, repo_path = demo_cases(), str(tmp)
        print(f"JS 데모 저장소: {repo_path}\n")
    elif args.cases and args.repo:
        import inspect

        fields = set(inspect.signature(EvalCase).parameters)
        raw = json.loads(Path(args.cases).read_text())
        cases = [EvalCase(**{k: v for k, v in c.items() if k in fields}) for c in raw]
        repo_path = args.repo
    else:
        parser.error("--demo 또는 (cases.json --repo 경로) 중 하나가 필요")
        return

    results = []
    for case in cases:
        print(f"[{case.id}] ({case.file_path})", flush=True)
        try:
            rec = run_case(case, repo_path)
        except Exception as e:  # LLM 503 등 일시 장애가 배치 전체를 죽이지 않게
            rec = {"id": case.id, "error": f"{type(e).__name__}: {e}"}
            results.append(rec)
            print(f"  오류 (케이스 건너뜀): {rec['error'][:120]}\n")
            continue
        results.append(rec)
        d = rec.get("decision", {})
        print(f"  경로: {rec.get('path')}")
        for c in rec.get("candidates", []):
            m = c["mutation"]
            print(f"  {c['id']} {c['strategy']}: 테스트 "
                  f"{'통과' if c['verification']['tests_passed'] else '실패'}, "
                  f"뮤테이션 {m['mutants_killed']}/{m['mutants_total']} kill "
                  f"(사이트 {m['sites_total']}, 점수 {m['score']})")
            for s in m["survived"]:
                print(f"      생존: {s}")
        print(f"  판정: {'자동 병합 ✅' if d.get('accepted') else '에스컬레이션 🔶'} — "
              f"{d.get('reason')}")
        gt = rec.get("ground_truth")
        if gt is not None:
            print(f"  사람 해법과 문자열 일치: {gt['string_equal']}")
        print()

    if args.out:
        Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=1))
        print(f"결과 저장: {args.out}")


if __name__ == "__main__":
    main()
