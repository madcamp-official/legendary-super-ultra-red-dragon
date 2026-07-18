"""담당: 이재준

후보 하나를 격리된 Docker 컨테이너에서 실행해 컴파일+테스트 통과 여부를 확인한다.
후보가 여러 개면 병렬로 돌린다 (일정상 최대 리스크 지점).

격리 방식은 git worktree — repo_path의 HEAD를 임시 디렉터리에 detached
worktree로 떼어내고, candidate.content를 그 안의 candidate.file_path 위치에
써넣은 뒤 컴파일 체크 + pytest를 그 worktree 안에서만 돌린다. 원본 저장소는
전혀 건드리지 않고, 후보마다 별도 worktree라 병렬 실행끼리도 서로 안 밟는다.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from weld.types import MergeCandidate, TestId, VerificationResult

_WORKTREE_TIMEOUT_S = 30
_COMPILE_TIMEOUT_S = 15
_TEST_TIMEOUT_S = 120

_TEST_RESULT_RE = re.compile(r"^(\S+::\S+)\s+(PASSED|FAILED|ERROR|SKIPPED)\b")


def _add_worktree(repo_path: str, worktree: Path) -> str | None:
    """detached worktree를 만든다. 실패하면 에러 메시지를, 성공하면 None을 반환한다."""
    try:
        result = subprocess.run(
            ["git", "-C", repo_path, "worktree", "add", "--detach", str(worktree), "HEAD"],
            capture_output=True,
            text=True,
            timeout=_WORKTREE_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return "git worktree add 타임아웃"
    if result.returncode != 0:
        return f"git worktree add 실패(exit {result.returncode}): {result.stderr.strip()}"
    return None


def _remove_worktree(repo_path: str, worktree: Path) -> None:
    subprocess.run(
        ["git", "-C", repo_path, "worktree", "remove", "--force", str(worktree)],
        capture_output=True,
        text=True,
        timeout=_WORKTREE_TIMEOUT_S,
    )


def _check_compiles(worktree: Path, file_path: str) -> tuple[bool, str | None]:
    """candidate.file_path가 있으면 그 파일만 컴파일 체크한다 (언어 스코프 Python 고정, MVP)."""
    if not file_path:
        return True, None
    target = worktree / file_path
    try:
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(target)],
            capture_output=True,
            text=True,
            timeout=_COMPILE_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return False, "컴파일 체크 타임아웃"
    if result.returncode != 0:
        return False, result.stderr.strip()
    return True, None


def _sandbox_env(worktree: Path) -> dict[str, str]:
    """worktree 안 패키지(예: src 레이아웃의 weld)가 site-packages보다 먼저 잡히게 한다.

    worktree 루트와 worktree/src를 PYTHONPATH 맨 앞에 둔다 — site.py가
    PYTHONPATH를 site-packages/.pth 처리보다 먼저 sys.path에 반영하므로,
    대상 저장소 패키지가 (개발 모드든 일반 설치든) site-packages에 이미
    깔려 있어도 worktree 쪽 사본이 우선 임포트된다(그래야 후보 content가
    실제로 테스트에 반영된다 — 안 그러면 site-packages의 원본 코드를 그대로
    테스트해서 후보 변경이 무시된 채로 통과 판정이 나는 사고가 난다).
    PYTHONNOUSERSITE로 사용자 site-packages(별도 editable 설치가 있을 수
    있는 경로)도 아예 배제한다.

    한계: setuptools strict-mode editable install처럼 sys.meta_path에
    파인더를 꽂는 방식은 sys.path 순서와 무관하게 우선할 수 있어 이 방어로도
    못 막는다 — MVP 범위 밖.
    """
    env = os.environ.copy()
    candidates = [str(worktree), str(worktree / "src")]
    existing = env.get("PYTHONPATH")
    if existing:
        candidates.append(existing)
    env["PYTHONPATH"] = os.pathsep.join(candidates)
    env["PYTHONNOUSERSITE"] = "1"
    return env


def _run_tests(
    worktree: Path, tests: list[TestId] | None
) -> tuple[list[TestId], list[TestId], bool, str | None]:
    """pytest -v 출력에서 노드ID별 결과를 파싱한다. tests가 없으면 전체 스위트를 돈다."""
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-v",
        "--tb=short",
        "--no-header",
        f"--rootdir={worktree}",
        f"--confcutdir={worktree}",
    ]
    if tests:
        cmd += list(tests)

    try:
        result = subprocess.run(
            cmd,
            cwd=worktree,
            capture_output=True,
            text=True,
            timeout=_TEST_TIMEOUT_S,
            env=_sandbox_env(worktree),
        )
    except subprocess.TimeoutExpired:
        requested = list(tests or [])
        return requested, requested, False, "테스트 실행 타임아웃"

    tests_run: list[TestId] = []
    tests_failed: list[TestId] = []
    for line in result.stdout.splitlines():
        match = _TEST_RESULT_RE.match(line)
        if not match:
            continue
        node_id, outcome = match.group(1), match.group(2)
        tests_run.append(node_id)
        if outcome in ("FAILED", "ERROR"):
            tests_failed.append(node_id)

    tests_passed = result.returncode == 0
    error = None
    if not tests_passed and not tests_run:
        # 결과 라인 파싱이 하나도 안 됐다 — 수집 실패 등, exit code만으론 원인 불명.
        error = (result.stdout + result.stderr).strip()[-4000:]

    return tests_run, tests_failed, tests_passed, error


def run_in_sandbox(
    candidate: MergeCandidate, repo_path: str, tests: list[TestId] | None = None
) -> VerificationResult:
    """후보 하나를 샌드박스에서 실행한다. tests(pytest 노드 ID)가 주어지면 그 테스트만 돈다."""
    start = time.monotonic()

    with tempfile.TemporaryDirectory(prefix="weld-sandbox-") as tmp:
        worktree = Path(tmp) / "worktree"

        add_error = _add_worktree(repo_path, worktree)
        if add_error is not None:
            return VerificationResult(
                candidate_id=candidate.id,
                compiled=False,
                tests_passed=False,
                duration_s=time.monotonic() - start,
                error=add_error,
            )

        try:
            if candidate.file_path:
                target = worktree / candidate.file_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(candidate.content)

            compiled, compile_error = _check_compiles(worktree, candidate.file_path)
            if not compiled:
                return VerificationResult(
                    candidate_id=candidate.id,
                    compiled=False,
                    tests_passed=False,
                    duration_s=time.monotonic() - start,
                    error=compile_error,
                )

            tests_run, tests_failed, tests_passed, test_error = _run_tests(worktree, tests)
            return VerificationResult(
                candidate_id=candidate.id,
                compiled=True,
                tests_passed=tests_passed,
                tests_run=tests_run,
                tests_failed=tests_failed,
                duration_s=time.monotonic() - start,
                error=test_error,
            )
        finally:
            _remove_worktree(repo_path, worktree)


def run_candidates_parallel(
    candidates: list[MergeCandidate], repo_path: str, tests: list[TestId] | None = None
) -> list[VerificationResult]:
    """후보 여러 개를 병렬 샌드박스에서 동시에 검증한다."""
    if not candidates:
        return []

    with ThreadPoolExecutor(max_workers=len(candidates)) as pool:
        futures = [pool.submit(run_in_sandbox, c, repo_path, tests) for c in candidates]
        return [f.result() for f in futures]
