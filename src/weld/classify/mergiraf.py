"""담당: 김민재

Mergiraf(tree-sitter 기반 구조적 병합 도구)를 체이닝해서 가짜 충돌(구조적으로
안 겹치는 변경)을 자동으로 걸러낸다. 새로 병합 알고리즘을 짜는 게 아니라
기존 Mergiraf 바이너리를 호출하고 결과를 해석하는 어댑터.

안전 원칙: Mergiraf가 "구조적으로 확실히 병합했다"고 확인해준 경우에만
가짜 충돌로 분류한다. 바이너리가 없거나, 실행에 실패하거나, 타임아웃이 나거나,
Mergiraf 스스로 못 풀었다고 하면(exit code != 0) — 전부 진짜 충돌로 간주해
LLM 후보 생성 + 뮤테이션 검증 파이프라인으로 넘긴다. 애매하면 무조건 더
안전한 쪽(사람이 보게 될 수도 있는 뮤테이션 검증 경로)으로 fail-safe한다.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from weld.types import ClassificationResult

MERGIRAF_BIN = os.environ.get("WELD_MERGIRAF_BIN", "mergiraf")
_TIMEOUT_S = 30
_CONFLICT_MARKERS = ("<<<<<<<", "=======", ">>>>>>>")


def _has_conflict_markers(text: str) -> bool:
    return any(marker in text for marker in _CONFLICT_MARKERS)


def classify_conflict(base: str, ours: str, theirs: str) -> ClassificationResult:
    """3-way 충돌을 Mergiraf에 넘겨 가짜/진짜를 분류한다.

    가짜 충돌이면 resolved_content에 Mergiraf가 만든 최종 병합 결과를 채운다.
    """
    with tempfile.TemporaryDirectory(prefix="weld-mergiraf-") as tmpdir:
        tmp = Path(tmpdir)
        # 언어 스코프가 Python 단일 언어로 고정돼 있어(MVP) .py로 고정한다.
        # 스트레치로 다른 언어를 붙일 때는 실제 파일 확장자를 인자로 받아야 함.
        base_path = tmp / "base.py"
        ours_path = tmp / "ours.py"
        theirs_path = tmp / "theirs.py"
        output_path = tmp / "output.py"
        base_path.write_text(base)
        ours_path.write_text(ours)
        theirs_path.write_text(theirs)

        try:
            result = subprocess.run(
                [
                    MERGIRAF_BIN,
                    "merge",
                    str(base_path),
                    str(ours_path),
                    str(theirs_path),
                    "-o",
                    str(output_path),
                ],
                capture_output=True,
                text=True,
                timeout=_TIMEOUT_S,
            )
        except FileNotFoundError:
            return ClassificationResult(
                is_spurious=False,
                reason=f"'{MERGIRAF_BIN}' 실행 파일을 찾을 수 없음 — 진짜 충돌로 간주(fail-safe)",
            )
        except subprocess.TimeoutExpired:
            return ClassificationResult(
                is_spurious=False,
                reason="mergiraf 실행 타임아웃 — 진짜 충돌로 간주(fail-safe)",
            )

        if result.returncode != 0:
            return ClassificationResult(
                is_spurious=False,
                reason=(
                    f"mergiraf가 구조적으로 못 풀었음(exit {result.returncode}): "
                    f"{result.stderr.strip()}"
                ),
            )

        resolved_content = output_path.read_text() if output_path.exists() else ""
        if _has_conflict_markers(resolved_content):
            # mergiraf 계약상 exit 0이면 충돌 마커가 없어야 하지만, 혹시 몰라 다시 확인한다.
            return ClassificationResult(
                is_spurious=False,
                reason="mergiraf가 exit 0을 반환했지만 충돌 마커가 남아있음 — 안전하게 진짜 충돌로 간주",
            )

        return ClassificationResult(
            is_spurious=True,
            resolved_content=resolved_content,
            reason="mergiraf가 구조적으로 겹치지 않음을 확인하고 자동 병합함",
        )
