"""담당: 김민재

다국어 지원의 단일 진실 공급원(언어 레지스트리).

언어 하나를 추가하려면 이 파일의 `_LANGUAGES`에 `LanguageSpec` 항목 하나를
추가하면 된다. 파이프라인의 언어 의존 지점들이 전부 이 레지스트리를 통해
분기한다:

  - classify/mergiraf.py  → 임시 파일 확장자 (mergiraf가 확장자로 문법 선택)
  - verify/mutation.py    → Python은 ast 엔진, 그 외는 tree-sitter 엔진으로 라우팅
  - verify/mutation_ts.py → tree-sitter 문법 이름 + 테스트 실행 명령
  - evaluation/multilang.py → 언어별 E2E 하네스

주의: 팀원 파트(verify/sandbox.py의 pytest 실행, verify/impact.py의 coverage
선별)는 아직 Python 전용이다. 비Python 언어의 검증·테스트 선별은 당분간
evaluation/multilang.py의 자체 러너(test_command 전체 실행)로 대신한다.
"""

from __future__ import annotations

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


def language_suffix(file_path: str, default: str = ".py") -> str:
    """mergiraf 임시 파일에 붙일 확장자.

    mergiraf는 확장자로 문법을 고르므로 원본 확장자를 그대로 보존한다 —
    이 레지스트리에 없는 언어라도 mergiraf가 아는 30+개 문법이면 분류가 되고,
    모르는 확장자면 mergiraf가 exit != 0을 내서 fail-safe(진짜 충돌)로
    떨어지므로 여기서 미리 거를 필요가 없다. 확장자가 아예 없을 때만 default."""
    suffix = Path(file_path).suffix.lower()
    return suffix if suffix else default
