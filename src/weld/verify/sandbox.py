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
import signal
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from weld.langs import LanguageSpec, detect_language
from weld.types import MergeCandidate, TestId, VerificationResult

_WORKTREE_TIMEOUT_S = 30
_COMPILE_TIMEOUT_S = 15
_TEST_TIMEOUT_S = 120
_LANG_BUILD_TIMEOUT_S = 60

_TEST_RESULT_RE = re.compile(r"^(\S+::\S+)\s+(PASSED|FAILED|ERROR|SKIPPED)\b")

# 최상단 import/export 존재 여부로 ESM인지 스니핑한다.
_ESM_HINT_RE = re.compile(r"(?m)^\s*(?:import\b|export\b)")


def _node_check_path(target: Path, content: str) -> Path:
    """node --check가 실제로 검사할 경로를 정한다.

    확장자가 애매한 .js/.ts에 package.json `type: module` 선언이 없으면,
    node의 기본 활성 휴리스틱(detect-module)이 import/export를 보고
    ESM으로 재파싱을 시도하는데 — 그 재파싱이 실패해도 --check가 에러를
    삼키고 exit 0을 내는 버그가 있다(Node 22 실측 확인: 깨진 문법의 .js가
    통과 처리됨, 같은 내용을 .mjs로 저장하면 정상적으로 잡힘). import/export
    존재를 정적으로 스니핑해서, 이미 .mjs/.mts가 아니면 그 확장자로 강제한
    사본을 만들어 실제 module 파싱 경로로 검사한다.
    """
    if target.suffix in (".mjs", ".mts", ".cjs", ".cts"):
        return target
    if not _ESM_HINT_RE.search(content):
        return target
    forced_suffix = ".mts" if target.suffix in (".ts", ".tsx") else ".mjs"
    forced = target.with_suffix(forced_suffix)
    forced.write_text(content, encoding="utf-8")
    return forced


def _kill_tree(proc: subprocess.Popen) -> None:
    """proc가 낳은 자식까지 통째로 죽인다. proc.kill()은 직계만 죽여서
    pytest가 낳은 손자 프로세스(coverage subprocess mode, xdist worker 등)가
    타임아웃 뒤에도 고아로 남아 영원히 도는 사고를 막는다."""
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            timeout=10,
        )
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
    proc.kill()


def _run(
    cmd: list[str],
    *,
    cwd: Path | str | None = None,
    timeout: float,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """subprocess.run과 동등하지만 타임아웃 시 프로세스 트리를 통째로 죽인다.

    별도 프로세스 그룹(POSIX: start_new_session, Windows: 새 프로세스 그룹)으로
    띄워야 트리 전체를 한 번에 죽일 대상을 잡을 수 있다.
    """
    kwargs: dict = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True

    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        **kwargs,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        proc.communicate()
        raise
    return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)


def _add_worktree(repo_path: str, worktree: Path) -> str | None:
    """detached worktree를 만든다. 실패하면 에러 메시지를, 성공하면 None을 반환한다."""
    try:
        result = _run(
            ["git", "-C", repo_path, "worktree", "add", "--detach", str(worktree), "HEAD"],
            timeout=_WORKTREE_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return "git worktree add 타임아웃"
    if result.returncode != 0:
        return f"git worktree add 실패(exit {result.returncode}): {result.stderr.strip()}"
    return None


def _remove_worktree(repo_path: str, worktree: Path) -> None:
    try:
        _run(
            ["git", "-C", repo_path, "worktree", "remove", "--force", str(worktree)],
            timeout=_WORKTREE_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        pass


def _check_compiles(worktree: Path, file_path: str) -> tuple[bool, str | None]:
    """candidate.file_path가 있으면 그 파일만 컴파일 체크한다 (언어 스코프 Python 고정, MVP)."""
    if not file_path:
        return True, None
    target = worktree / file_path
    try:
        result = _run(
            [sys.executable, "-m", "py_compile", str(target)],
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


def _run_tests_once(
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
        result = _run(
            cmd,
            cwd=worktree,
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


def _run_tests(
    worktree: Path, tests: list[TestId] | None
) -> tuple[list[TestId], list[TestId], bool, str | None]:
    """tests로 좁혀 돌리되, (미지의 환경/pytest 버전에서) 노드ID 수집이 통째로
    실패하면(tests_run==0) 전체 스위트로 한 번 더 돌려 안전 신호를 확보한다 —
    선별 실패를 검증 실패로 오판해 불필요하게 에스컬레이션시키지 않기 위한
    방어선. 근본 원인(worktree 미정규화 경로)은 run_in_sandbox에서 고쳤지만,
    미지의 수집 실패 케이스에 대비해 남겨둔다."""
    tests_run, tests_failed, tests_passed, error = _run_tests_once(worktree, tests)
    if tests and not tests_run:
        tests_run, tests_failed, tests_passed, error = _run_tests_once(worktree, None)
    return tests_run, tests_failed, tests_passed, error


def _check_compiles_lang(worktree: Path, spec: LanguageSpec, file_path: str) -> tuple[bool, str | None]:
    """비Python 언어 컴파일 게이트. build_command 있으면 빌드, 없으면(JS/TS) 문법 검사만."""
    if spec.build_command is not None:
        try:
            result = _run(list(spec.build_command), cwd=worktree, timeout=_LANG_BUILD_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            return False, "빌드 타임아웃"
        if result.returncode != 0:
            return False, (result.stderr or result.stdout).strip()
        return True, None

    if spec.name in ("javascript", "typescript"):
        target = worktree / file_path
        check_target = _node_check_path(target, target.read_text(encoding="utf-8"))
        try:
            result = _run(["node", "--check", str(check_target)], timeout=_COMPILE_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            return False, "문법 검사 타임아웃"
        if result.returncode != 0:
            return False, result.stderr.strip()
        return True, None

    return True, None


def _run_tests_lang(
    worktree: Path, spec: LanguageSpec
) -> tuple[list[TestId], list[TestId], bool, str | None]:
    """비Python 언어 테스트 게이트. 개별 테스트 ID 파싱 불가라 exit code + 스위트
    표식 하나로 대신한다 (evaluation/multilang.py의 임시 로직과 동일 계약)."""
    if spec.test_command is None:
        return [], [], False, f"테스트 실행 방법을 모르는 언어: {spec.name}"

    try:
        result = _run(list(spec.test_command), cwd=worktree, timeout=_TEST_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return [], [], False, "테스트 실행 타임아웃"

    marker = f"{spec.name}-full-suite"
    passed = result.returncode == 0
    if passed:
        return [marker], [], True, None
    return [marker], [marker], False, (result.stdout + result.stderr).strip()[-4000:]


def run_in_sandbox(
    candidate: MergeCandidate, repo_path: str, tests: list[TestId] | None = None
) -> VerificationResult:
    """후보 하나를 샌드박스에서 실행한다. tests(pytest 노드 ID)가 주어지면 그 테스트만 돈다."""
    start = time.monotonic()

    with tempfile.TemporaryDirectory(prefix="weld-sandbox-") as tmp:
        # .resolve() 필수 — macOS는 tempdir이 /var/folders/...(실제로는
        # /private/var 심링크)라, 미정규화 경로를 --rootdir/--confcutdir로
        # 넘기면 pytest가 노드ID를 상대경로로 재계산하다 깨진다(exit 4 또는
        # '../../../worktree::test_x' 뭉개짐).
        worktree = Path(tmp).resolve() / "worktree"

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

            spec = detect_language(candidate.file_path) if candidate.file_path else None
            is_python = spec is None or spec.name == "python"

            if is_python:
                compiled, compile_error = _check_compiles(worktree, candidate.file_path)
            else:
                compiled, compile_error = _check_compiles_lang(worktree, spec, candidate.file_path)
            if not compiled:
                return VerificationResult(
                    candidate_id=candidate.id,
                    compiled=False,
                    tests_passed=False,
                    duration_s=time.monotonic() - start,
                    error=compile_error,
                )

            if is_python:
                tests_run, tests_failed, tests_passed, test_error = _run_tests(worktree, tests)
            else:
                # 비Python은 pytest 노드 ID 개념이 없다 — tests 인자 무시하고 전체 스위트로 폴백.
                tests_run, tests_failed, tests_passed, test_error = _run_tests_lang(worktree, spec)
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
