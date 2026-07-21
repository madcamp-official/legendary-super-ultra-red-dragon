"""담당: 김민재

다국어 지원의 단일 진실 공급원(언어 레지스트리).

언어 하나를 추가하려면 이 파일의 `_LANGUAGES`에 `LanguageSpec` 항목 하나를
추가하면 된다. 파이프라인의 언어 의존 지점들이 전부 이 레지스트리를 통해
분기한다:

  - classify/mergiraf.py  → 임시 파일 확장자 (mergiraf가 확장자로 문법 선택)
  - verify/mutation.py    → Python은 ast 엔진, 그 외는 tree-sitter 엔진으로 라우팅
  - verify/mutation_ts.py → tree-sitter 문법 이름 + 테스트 실행 명령
  - evaluation/multilang.py → 언어별 E2E 하네스

주의: 팀원 파트 중 verify/sandbox.py의 테스트 "실행"은 아직 Python
전용이다(비Python은 test_command 전체 스위트만 돎, 개별 테스트 실행 불가).
반면 verify/impact.py의 테스트 "선별"은 python(coverage 엔진) +
그 외 ts_language 있는 언어(verify/callgraph.py의 tree-sitter call graph +
RTA 엔진)로 전부 커버한다 — 선별 결과가 아직 sandbox.py 실행에는 안 쓰일
뿐, 선별 자체는 다국어다. 비Python 언어의 E2E 검증은 당분간
evaluation/multilang.py의 자체 러너(test_command 전체 실행)로 대신한다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LanguageSpec:
    name: str
    extensions: tuple[str, ...]
    """이 언어로 취급할 파일 확장자 (점 포함, 예: ".js")."""
    ts_language: str | None
    """tree-sitter-language-pack에서의 문법 이름. None이면 뮤테이션 미지원."""
    test_command: tuple[str, ...] | None
    """저장소 루트에서 전체 테스트를 실행하는 명령. None이면 실행 검증 불가
    (뮤테이션 엔진이 신호 없음으로 폴백한다)."""
    build_command: tuple[str, ...] | None = None
    """컴파일 언어용 빌드 명령 (테스트 실행과 분리). 있으면 뮤테이션 엔진이
    뮤턴트마다 먼저 빌드하고, 빌드 실패는 '무효 뮤턴트'로 집계에서 제외한다
    (kill로 세면 점수가 부풀려짐 — C++ 템플릿 `<` 반전처럼 컴파일 자체가
    깨지는 변형의 안전망). 빌드/테스트 실패를 구분해야 하므로 test_command에
    빌드를 섞지 말 것.

    주의(make 사용 시): macOS 기본 GNU make 3.81은 mtime을 초 단위로만
    비교해서, 뮤테이션처럼 1초 안에 파일을 바꾸면 재빌드를 건너뛰고 낡은
    바이너리를 실행한다(가짜 생존). 반드시 -B(무조건 재빌드)를 포함할 것."""


_LANGUAGES: tuple[LanguageSpec, ...] = (
    # Python은 verify/mutation.py의 기존 ast 엔진 + pytest 경로가 그대로 담당.
    LanguageSpec(
        name="python",
        extensions=(".py",),
        ts_language="python",
        test_command=None,  # pytest는 impact/sandbox/mutation(ast) 쪽에서 처리
    ),
    LanguageSpec(
        name="javascript",
        extensions=(".js", ".mjs", ".cjs", ".jsx"),
        ts_language="javascript",
        # node 내장 러너 — 별도 프레임워크 설치 없이 *.test.js를 실행한다.
        test_command=("node", "--test"),
    ),
    LanguageSpec(
        name="typescript",
        extensions=(".ts", ".mts", ".cts"),
        ts_language="typescript",
        # Node 23+는 타입 스트리핑이 기본이라 플래그 없이 .ts를 직접 실행한다
        # (node 26에서 실측 확인).
        test_command=("node", "--test"),
    ),
    # C/C++ 규약: 저장소 루트 Makefile의 기본 타깃이 테스트 바이너리를 빌드하고,
    # `test` 타깃이 그것을 실행한다. -B는 build_command docstring의 mtime 함정 참고.
    LanguageSpec(
        name="c",
        extensions=(".c",),
        ts_language="c",
        test_command=("make", "-s", "test"),
        build_command=("make", "-s", "-B"),
    ),
    LanguageSpec(
        name="cpp",
        extensions=(".cpp", ".cc", ".cxx", ".hpp", ".hh", ".h"),
        ts_language="cpp",
        test_command=("make", "-s", "test"),
        build_command=("make", "-s", "-B"),
    ),
    # 아래 언어들은 mergiraf 분류 + tree-sitter 뮤테이션 사이트 수집까지는
    # 되지만, 이 머신에 런타임이 없어 테스트 실행(kill 판정)은 불가 —
    # test_command가 채워지면 그대로 활성화된다.
    LanguageSpec(name="go", extensions=(".go",), ts_language="go", test_command=None),
    LanguageSpec(name="rust", extensions=(".rs",), ts_language="rust", test_command=None),
    LanguageSpec(name="java", extensions=(".java",), ts_language="java", test_command=None),
)


def detect_language(file_path: str) -> LanguageSpec | None:
    """파일 경로의 확장자로 언어를 찾는다. 모르는 확장자면 None."""
    suffix = Path(file_path).suffix.lower()
    if not suffix:
        return None
    for spec in _LANGUAGES:
        if suffix in spec.extensions:
            return spec
    return None


# JS/TS에서 개별 파일을 지정 실행할 때 러너별 명령 형태.
# 값은 (실행 프리픽스 튜플). 파일 인자는 이 뒤에 그대로 이어붙인다.
_JS_RUNNER_TARGETED: dict[str, tuple[str, ...]] = {
    "vitest": ("npx", "vitest", "run"),
    "jest": ("npx", "jest"),
    "mocha": ("npx", "mocha"),
}


def _detect_js_runner(repo_root: Path) -> str | None:
    """저장소의 JS 테스트 러너 이름(vitest/jest/mocha). package.json의 test
    스크립트와 의존성 목록에서 스니핑한다. 못 찾으면 None."""
    pkg = repo_root / "package.json"
    if not pkg.is_file():
        return None
    try:
        data = json.loads(pkg.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    haystack = " ".join([
        str((data.get("scripts") or {}).get("test", "")),
        " ".join(data.get("devDependencies") or {}),
        " ".join(data.get("dependencies") or {}),
    ]).lower()
    for runner in _JS_RUNNER_TARGETED:
        if runner in haystack:
            return runner
    return None


def _test_files(selected_tests: list[str]) -> list[str]:
    """TestId 목록에서 파일 경로만 뽑아 순서 보존 중복 제거.

    선별기가 "path/to/foo.test.js::caseName"처럼 노드 ID를 줄 수도, 파일 경로만
    줄 수도 있어 "::" 앞부분만 취한다. vitest/jest는 파일 단위로 필터링하므로
    파일 경로면 충분하다."""
    files: list[str] = []
    for t in selected_tests:
        path = t.split("::", 1)[0]
        if path and path not in files:
            files.append(path)
    return files


def effective_test_command(
    spec: LanguageSpec,
    repo_root: str | Path,
    selected_tests: list[str] | None = None,
) -> tuple[str, ...] | None:
    """실제 실행할 테스트 명령.

    selected_tests(관련 테스트 파일/노드ID 목록)가 주어지면 그 파일만 도는
    **targeted 명령**을 만든다 — 실제 저장소는 전체 스위트가 느리거나(뮤턴트
    마다 반복) 무관한 브라우저/e2e 테스트로 baseline이 깨지므로, 관련 테스트만
    좁혀 돌려야 실용적이다. impact 선별 결과를 이 인자로 흘려주면 된다.

    selected_tests가 없으면(선별 불가/미구현) 저장소가 자기 러너를 선언한
    경우 `npm test`로 위임하고(vitest/jest 자동 호출), 그것도 없으면 정적
    기본값(데모 fixture의 `node --test`)을 쓴다.

    C/C++처럼 이미 make(저장소 빌드시스템)에 위임하는 언어는 개별 테스트
    타깃팅을 일반화하기 어려워 test_command를 그대로 반환한다.
    """
    repo_root = Path(repo_root)
    if spec.name not in ("javascript", "typescript"):
        return spec.test_command

    files = _test_files(selected_tests) if selected_tests else []
    if files:
        runner = _detect_js_runner(repo_root)
        if runner is not None:
            return (*_JS_RUNNER_TARGETED[runner], *files)
        # 러너를 못 알아냈지만 파일은 있음 → node --test로 그 파일만 지정.
        return ("node", "--test", *files)

    # 선별 없음(또는 빈 선별) — 전체 위임. 저장소가 test 스크립트를 선언했으면
    # npm test, 아니면 정적 기본값.
    pkg = repo_root / "package.json"
    if pkg.is_file():
        try:
            scripts = json.loads(pkg.read_text(encoding="utf-8")).get("scripts", {})
        except (ValueError, OSError):
            scripts = {}
        if isinstance(scripts, dict) and scripts.get("test"):
            return ("npm", "test")
    return spec.test_command


def language_suffix(file_path: str, default: str = ".py") -> str:
    """mergiraf 임시 파일에 붙일 확장자.

    mergiraf는 확장자로 문법을 고르므로 원본 확장자를 그대로 보존한다 —
    이 레지스트리에 없는 언어라도 mergiraf가 아는 30+개 문법이면 분류가 되고,
    모르는 확장자면 mergiraf가 exit != 0을 내서 fail-safe(진짜 충돌)로
    떨어지므로 여기서 미리 거를 필요가 없다. 확장자가 아예 없을 때만 default."""
    suffix = Path(file_path).suffix.lower()
    return suffix if suffix else default
