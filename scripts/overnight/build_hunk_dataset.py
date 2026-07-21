"""훙크 단위 v2 데이터셋 — too_big로 버려진 대형 파일 충돌을 복구한다.

전체 파일이 토큰 한도를 넘는 케이스도, 실제 충돌은 파일 일부다.
diff3 충돌 블록마다 앞뒤 K줄 컨텍스트 창을 잘라
  base/ours/theirs 창  +  사람 정답에서 같은 앵커로 잘라낸 정답 창
을 만들면 대부분 복구되고, 전체파일 정답의 무관 동시편집 노이즈도 준다.

정답 창 추출: 충돌 블록 앞/뒤의 안정 컨텍스트 줄을 앵커로 사람 정답에서
같은 구간을 찾는다. 앵커를 못 찾으면(사람이 컨텍스트까지 고침) 그 블록은
버린다 — 라벨이 불확실한 데이터는 안 만드는 게 원칙.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

WELD = "/Users/kimminjae/Documents/몰입캠프/3주차/weld"
sys.path.insert(0, f"{WELD}/src")

from weld.candidates.generate import is_value_conflict  # noqa: E402
from weld.classify.mergiraf import classify_conflict  # noqa: E402
from weld.evaluation.mining import mine_conflicts  # noqa: E402

OLD = "/private/tmp/claude-501/-Users-kimminjae-Documents------1--/3402dea4-1e99-4fc0-b9e7-eff4db43282c/scratchpad"
OUT = f"{WELD}/datasets/llm_required_merges_hunks.jsonl"
K = 8            # 컨텍스트 창 (줄)
MIN_FULL_TOKENS = 15000 // 4 * 4  # v1이 버린 크기 기준과 동일선상

_EXT_LANG = {
    ".py": "python", ".js": "javascript", ".mjs": "javascript", ".ts": "typescript",
    ".jsx": "javascript", ".tsx": "typescript", ".c": "c", ".h": "c",
    ".cpp": "cpp", ".cc": "cpp", ".hpp": "cpp",
}
_NOISE_PATH = (
    "playground", "sandbox", "scratch", "/example", "examples/", "/demo", "demo/",
    "fixture", "benchmark", "__snapshots__", "/docs/", "changelog", "CHANGELOG",
)


def _lang(path: str) -> str:
    for ext, lang in _EXT_LANG.items():
        if path.endswith(ext):
            return lang
    return "unknown"


def _diff3(base: str, ours: str, theirs: str) -> str | None:
    with tempfile.TemporaryDirectory() as d:
        bp, op, tp = Path(d) / "b", Path(d) / "o", Path(d) / "t"
        bp.write_text(base)
        op.write_text(ours)
        tp.write_text(theirs)
        r = subprocess.run(
            ["git", "merge-file", "-p", "--diff3",
             "-L", "ours", "-L", "base", "-L", "theirs",
             str(op), str(bp), str(tp)],
            capture_output=True, text=True,
        )
        return r.stdout if "<<<<<<<" in r.stdout else None


def _parse_blocks(merged: str) -> list[dict]:
    """diff3 출력에서 (앞컨텍스트, ours, base, theirs, 뒤컨텍스트 시작idx) 추출."""
    lines = merged.splitlines()
    blocks = []
    i = 0
    ctx_start = 0
    while i < len(lines):
        if lines[i].startswith("<<<<<<<"):
            pre = lines[max(ctx_start, i - K):i]
            j = i + 1
            ours_part: list[str] = []
            base_part: list[str] = []
            theirs_part: list[str] = []
            cur = ours_part
            while j < len(lines) and not lines[j].startswith(">>>>>>>"):
                if lines[j].startswith("|||||||"):
                    cur = base_part
                elif lines[j].startswith("======="):
                    cur = theirs_part
                else:
                    cur.append(lines[j])
                j += 1
            post = lines[j + 1:j + 1 + K]
            blocks.append({
                "pre": pre, "ours": ours_part, "base": base_part,
                "theirs": theirs_part, "post": post,
            })
            ctx_start = j + 1
            i = j + 1
        else:
            i += 1
    return blocks


def _find_seq(hay: list[str], needle: list[str], start: int) -> int:
    """hay[start:]에서 needle 연속 구간의 시작 idx (-1=없음)."""
    if not needle:
        return -1
    n = len(needle)
    for i in range(start, len(hay) - n + 1):
        if hay[i:i + n] == needle:
            return i
    return -1


def _gt_window(gt_lines: list[str], pre: list[str], post: list[str],
               cursor: int) -> tuple[str, int] | None:
    """정답에서 pre...post 사이 구간을 앵커로 잘라낸다. (창, 새 커서)"""
    if pre:
        p = _find_seq(gt_lines, pre, cursor)
        if p < 0:
            return None
        mid_start = p + len(pre)
    else:
        mid_start = 0
    if post:
        q = _find_seq(gt_lines, post, mid_start)
        if q < 0:
            return None
        mid_end = q + len(post)
    else:
        mid_end = len(gt_lines)
    window = gt_lines[(mid_start - len(pre)):mid_end]
    return "\n".join(window) + "\n", mid_end


def main() -> None:
    PY = (".py",)
    JS = (".js", ".mjs", ".ts", ".jsx", ".tsx")
    C = (".c", ".h")
    CPP = (".cpp", ".cc", ".hpp", ".hh")
    REPOS = [
        ("glom", f"{OLD}/eval_bug/glom", PY),
        ("axios", f"{OLD}/eval_js/axios", JS),
        ("flask", f"{OLD}/ds_repos/flask", PY),
        ("click", f"{OLD}/ds_repos/click", PY),
        ("requests", f"{OLD}/ds_repos/requests", PY),
        ("rich", f"{OLD}/ds_repos/rich", PY),
        ("httpx", f"{OLD}/ds_repos/httpx", PY),
        ("express", f"{OLD}/ds_repos/express", JS),
        ("zod", f"{OLD}/ds_repos/zod", JS),
        ("redis", f"{OLD}/ds_repos/redis", C),
        ("json-cpp", f"{OLD}/ds_repos/json-cpp", CPP),
        ("fmt", f"{OLD}/ds_repos/fmt", CPP),
    ]
    stats = {"files": 0, "noise": 0, "no_gt": 0, "blocks": 0, "anchor_fail": 0,
             "spurious_w": 0, "value_w": 0, "dup": 0, "kept": 0}
    rows: list[dict] = []
    seen: set[str] = set()
    for name, repo, exts in REPOS:
        if not os.path.isdir(repo):
            continue
        try:
            cases = mine_conflicts(repo, extensions=exts)
        except Exception as e:  # noqa: BLE001
            print(f"[{name}] 채굴 실패: {e}", flush=True)
            continue
        n_before = stats["kept"]
        for c in cases:
            stats["files"] += 1
            if any(nz in c.file_path for nz in _NOISE_PATH):
                stats["noise"] += 1
                continue
            gt = c.ground_truth_resolution
            if not gt or "<<<<<<<" in gt:
                stats["no_gt"] += 1
                continue
            merged = _diff3(c.base, c.ours, c.theirs)
            if merged is None:
                continue
            gt_lines = gt.splitlines()
            cursor = 0
            for bi, blk in enumerate(_parse_blocks(merged)):
                stats["blocks"] += 1
                got = _gt_window(gt_lines, blk["pre"], blk["post"], cursor)
                if got is None:
                    stats["anchor_fail"] += 1
                    continue
                gt_w, cursor = got
                base_w = "\n".join(blk["pre"] + blk["base"] + blk["post"]) + "\n"
                ours_w = "\n".join(blk["pre"] + blk["ours"] + blk["post"]) + "\n"
                theirs_w = "\n".join(blk["pre"] + blk["theirs"] + blk["post"]) + "\n"
                # 창 단위 재분류: 가짜충돌/값충돌은 LLM 불필요 → 제외
                try:
                    cls = classify_conflict(base_w, ours_w, theirs_w,
                                            file_path=c.file_path)
                    if cls.is_spurious:
                        stats["spurious_w"] += 1
                        continue
                except Exception:  # noqa: BLE001
                    pass
                if is_value_conflict(base_w, ours_w, theirs_w):
                    stats["value_w"] += 1
                    continue
                key = hashlib.sha256(
                    f"{base_w}|{ours_w}|{theirs_w}|{gt_w}".encode()
                ).hexdigest()
                if key in seen:
                    stats["dup"] += 1
                    continue
                seen.add(key)
                o, t, g = ours_w.strip(), theirs_w.strip(), gt_w.strip()
                kind = ("chose_ours" if g == o else
                        "chose_theirs" if g == t else "synthesis")
                rows.append({
                    "id": f"{name}-h-{key[:12]}",
                    "language": _lang(c.file_path),
                    "file_path": c.file_path,
                    "source_repo": name,
                    "source_commit": c.source_commit,
                    "base": base_w, "ours": ours_w, "theirs": theirs_w,
                    "ground_truth": gt_w,
                    "resolution_kind": kind,
                    "hunk_index": bi,
                    "approx_tokens": (len(base_w) + len(ours_w) + len(theirs_w)
                                      + len(gt_w)) // 4,
                })
                stats["kept"] += 1
        print(f"[{name}] 훙크 채택 {stats['kept'] - n_before}건", flush=True)

    Path(OUT).parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    from collections import Counter
    print("\n===== v2 훙크 요약 =====")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    print(f"  언어별: {dict(Counter(r['language'] for r in rows))}")
    print(f"  해결유형: {dict(Counter(r['resolution_kind'] for r in rows))}")
    print(f"  → {OUT}")


if __name__ == "__main__":
    main()
