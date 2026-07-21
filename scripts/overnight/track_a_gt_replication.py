"""Track A — LLM 필수 병합 203케이스에서 qwen이 사람 정답을 재현하는가.

datasets/llm_required_merges.jsonl의 각 케이스에 대해
  classify → generate_candidates(qwen) → 후보 vs ground_truth 비교
를 돌리고, 케이스별 결과를 JSONL로 증분 저장한다.

- 재개 가능: 이미 결과가 있는 case id는 건너뜀 (+ LLM 디스크 캐시)
- VPN 끊김 대응: 케이스 시작 전 TCP 프로브, 실패 시 60초 간격 재시도(최대 4시간)
- 서버 배려: 직렬 실행 (친구 단일 노드)

지표(케이스당):
  exact      — 후보 == 정답 (strip)
  normalized — 공백 정규화 후 일치
  best_ratio — difflib 최고 유사도 (정답이 전체파일이라 무관 동시편집 노이즈 포함
               → exact는 하한선, ratio가 실제 품질에 가까움)
"""
from __future__ import annotations

import difflib
import json
import os
import re
import socket
import sys
import time
from pathlib import Path

WELD = "/Users/kimminjae/Documents/몰입캠프/3주차/weld"
sys.path.insert(0, f"{WELD}/src")
os.chdir(WELD)  # .env / .weld_cache가 여기 기준

from weld.candidates.generate import generate_candidates  # noqa: E402
from weld.classify.mergiraf import classify_conflict  # noqa: E402

DATASET = os.environ.get(
    "TRACK_A_DATASET", f"{WELD}/datasets/llm_required_merges.jsonl"
)
OUT = os.environ.get(
    "TRACK_A_OUT", f"{WELD}/results/overnight-0721/track_a.jsonl"
)
LLM_HOST, LLM_PORT = "172.10.7.246", 443


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip())


def _wait_for_llm(max_hours: float = 4.0) -> bool:
    """LLM 서버 TCP 프로브. 끊겨 있으면 60초 간격 재시도."""
    deadline = time.time() + max_hours * 3600
    while time.time() < deadline:
        try:
            socket.create_connection((LLM_HOST, LLM_PORT), timeout=3).close()
            return True
        except OSError:
            print(f"[probe] LLM 서버 연결 불가(VPN?), 60초 후 재시도", flush=True)
            time.sleep(60)
    return False


def main() -> None:
    rows = [json.loads(l) for l in open(DATASET)]
    limit = int(os.environ.get("TRACK_A_LIMIT", "0"))
    if limit:
        rows = rows[:limit]
    Path(OUT).parent.mkdir(parents=True, exist_ok=True)
    done: set[str] = set()
    if os.path.exists(OUT):
        for l in open(OUT):
            try:
                done.add(json.loads(l)["id"])
            except Exception:  # noqa: BLE001
                pass
    print(f"총 {len(rows)}케이스, 완료 {len(done)}, 남음 {len(rows) - len(done)}", flush=True)

    out_f = open(OUT, "a")
    consec_err = 0
    for i, r in enumerate(rows):
        if r["id"] in done:
            continue
        if not _wait_for_llm():
            print("[abort] LLM 서버 4시간 연속 다운 — 중단", flush=True)
            break

        rec: dict = {"id": r["id"], "language": r["language"],
                     "resolution_kind": r["resolution_kind"],
                     "source_repo": r["source_repo"],
                     "approx_tokens": r["approx_tokens"]}
        t0 = time.time()
        try:
            cls = classify_conflict(r["base"], r["ours"], r["theirs"],
                                    file_path=r["file_path"])
            rec["classified_spurious"] = bool(cls.is_spurious)
            cands = generate_candidates(r["base"], r["ours"], r["theirs"],
                                        file_path=r["file_path"])
            gt = r["ground_truth"]
            gt_s, gt_n = gt.strip(), _norm(gt)
            best_ratio, best_id = 0.0, None
            exact = normalized = False
            for c in cands:
                cs = c.content.strip()
                if cs == gt_s:
                    exact = True
                if _norm(c.content) == gt_n:
                    normalized = True
                ratio = difflib.SequenceMatcher(None, cs, gt_s).ratio()
                if ratio > best_ratio:
                    best_ratio, best_id = ratio, c.id
            llm_cands = [c for c in cands if c.strategy.startswith("llm")]
            rec.update({
                "n_candidates": len(cands),
                "n_llm_candidates": len(llm_cands),
                "strategies": [c.strategy for c in cands],
                "exact": exact,
                "normalized": normalized,
                "best_ratio": round(best_ratio, 4),
                "best_candidate": best_id,
                "gen_seconds": round(time.time() - t0, 2),
                "error": None,
            })
            consec_err = 0
        except Exception as e:  # noqa: BLE001
            rec.update({"error": f"{type(e).__name__}: {str(e)[:200]}",
                        "gen_seconds": round(time.time() - t0, 2)})
            consec_err += 1
            if consec_err >= 15:
                print("[abort] 15연속 에러 — 구조적 문제로 판단, 중단", flush=True)
                out_f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                out_f.flush()
                break

        out_f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        out_f.flush()
        n_done = len(done) + sum(1 for _ in [1])  # 진행 표기용
        print(f"[{i + 1}/{len(rows)}] {r['id']} lang={r['language']} "
              f"exact={rec.get('exact')} ratio={rec.get('best_ratio')} "
              f"{rec.get('gen_seconds')}s err={rec.get('error') is not None}",
              flush=True)

    out_f.close()
    # 최종 요약
    res = [json.loads(l) for l in open(OUT)]
    ok = [x for x in res if not x.get("error")]
    print("\n===== Track A 요약 =====", flush=True)
    print(f"완료 {len(res)} (성공 {len(ok)}, 에러 {len(res) - len(ok)})")
    from collections import defaultdict
    by_lang: dict[str, list] = defaultdict(list)
    for x in ok:
        by_lang[x["language"]].append(x)
    for lang, xs in sorted(by_lang.items()):
        ex = sum(1 for x in xs if x["exact"])
        no = sum(1 for x in xs if x["normalized"])
        avg = sum(x["best_ratio"] for x in xs) / len(xs)
        print(f"  {lang}: n={len(xs)} exact={ex} norm={no} avg_ratio={avg:.3f}")


if __name__ == "__main__":
    main()
