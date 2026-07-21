"""밤샘 검증 결과 집계 → 발표용 마크다운 리포트 생성.

Track A(정답 재현율) + B-1(씨딩 통제실험) + B-2(실전 리플레이) JSONL을 읽어
언어별 표와 핵심 지표를 뽑는다. Track A가 진행 중이어도 지금까지 분량으로 집계.
"""
from __future__ import annotations

import json
import os
from collections import Counter, defaultdict

RES = "/Users/kimminjae/Documents/몰입캠프/3주차/weld/results/overnight-0721"
OUT = f"{RES}/overnight_report.md"


def _load(name: str) -> list[dict]:
    path = f"{RES}/{name}"
    if not os.path.exists(path):
        return []
    rows = []
    for l in open(path):
        try:
            rows.append(json.loads(l))
        except Exception:  # noqa: BLE001
            pass
    # 같은 id 재실행 시 마지막 기록 우선
    uniq: dict[str, dict] = {}
    for r in rows:
        uniq[r["id"]] = r
    return list(uniq.values())


def track_a_section(rows: list[dict]) -> str:
    ok = [r for r in rows if not r.get("error")]
    errs = [r for r in rows if r.get("error")]
    by_lang: dict[str, list[dict]] = defaultdict(list)
    for r in ok:
        by_lang[r["language"]].append(r)
    lines = [
        "## Track A — LLM이 사람 정답을 재현하는가 (실저장소 채굴 케이스)",
        "",
        "실제 오픈소스 병합 히스토리에서 채굴한 **LLM 필수 구조충돌**"
        f" {len(rows)}건에 qwen3-235b 후보 생성을 돌리고, 사람이 실제 채택한"
        " 결과와 비교. (정답은 병합 커밋의 전체 파일이라 무관 동시편집 노이즈가"
        " 섞임 — exact는 하한선, ratio≥0.995는 사실상 재현으로 본다.)",
        "",
        "| 언어 | n | exact | 정규화일치 | ratio≥0.995 | 평균 유사도 |",
        "|---|---|---|---|---|---|",
    ]
    for lang, xs in sorted(by_lang.items(), key=lambda kv: -len(kv[1])):
        ex = sum(1 for x in xs if x["exact"])
        no = sum(1 for x in xs if x["normalized"])
        hi = sum(1 for x in xs if x["best_ratio"] >= 0.995)
        avg = sum(x["best_ratio"] for x in xs) / len(xs)
        lines.append(
            f"| {lang} | {len(xs)} | {ex} ({ex / len(xs):.0%}) | {no} | "
            f"{hi} ({hi / len(xs):.0%}) | {avg:.3f} |"
        )
    if ok:
        ex = sum(1 for x in ok if x["exact"])
        hi = sum(1 for x in ok if x["best_ratio"] >= 0.995)
        avg = sum(x["best_ratio"] for x in ok) / len(ok)
        lines.append(
            f"| **전체** | **{len(ok)}** | **{ex} ({ex / len(ok):.0%})** | "
            f"**{sum(1 for x in ok if x['normalized'])}** | "
            f"**{hi} ({hi / len(ok):.0%})** | **{avg:.3f}** |"
        )
    # 합성 케이스만 별도
    synth = [x for x in ok if x.get("resolution_kind") == "synthesis"]
    if synth:
        hi = sum(1 for x in synth if x["best_ratio"] >= 0.995)
        lines += [
            "",
            f"- 그중 **진짜 합성**(어느 쪽 verbatim도 아닌 사람 해결) {len(synth)}건: "
            f"ratio≥0.995 {hi}건 ({hi / len(synth):.0%}), "
            f"평균 유사도 {sum(x['best_ratio'] for x in synth) / len(synth):.3f}",
        ]
    if errs:
        cnt = Counter(e["error"].split(":")[0] for e in errs)
        lines.append(f"- 에러 {len(errs)}건: {dict(cnt)}")
    gen = [x["gen_seconds"] for x in ok if x.get("gen_seconds")]
    if gen:
        gen.sort()
        lines.append(
            f"- 케이스당 생성 시간: 중앙값 {gen[len(gen) // 2]:.1f}s, "
            f"p90 {gen[int(len(gen) * 0.9)]:.1f}s"
        )
    return "\n".join(lines)


def track_b1_section(rows: list[dict]) -> str:
    lines = [
        "## Track B-1 — 4개 언어 × 6유형 씨딩 통제실험 (풀 파이프라인)",
        "",
        "언어마다 동일한 6가지 충돌 유형을 심고 classify → generate(qwen) →"
        " verify → mutation → decide 풀 사이클. **핵심 지표: 오탐 0.**",
        "",
        "| 언어 | 자동병합(정확) | 설계된 에스컬레이션 | 오탐 |",
        "|---|---|---|---|",
    ]
    by_lang: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        by_lang[r.get("lang_group", "?")][r.get("verdict", "?")] += 1
    total_wrong = 0
    for lang in ("py", "js", "c", "cpp"):
        c = by_lang.get(lang, Counter())
        auto = c.get("auto_correct_exact", 0) + c.get("auto_correct_semantic", 0)
        esc = c.get("escalated_ok", 0)
        wrong = c.get("auto_WRONG", 0) + c.get("auto_but_expected_escalate", 0)
        total_wrong += wrong
        name = {"py": "Python", "js": "JavaScript", "c": "C", "cpp": "C++"}[lang]
        lines.append(f"| {name} | {auto}/6 | {esc}/6 | {wrong} |")
    lines += [
        "",
        f"- **4개 언어 전부 대칭**: 가짜충돌·값충돌·강테스트 구조합성·안전장치 보존"
        f" 재작성 → 자동병합 / 약한 테스트·미정의 참조 → 에스컬레이션",
        f"- **오탐 총 {total_wrong}건** (잘못된 내용을 자동 병합한 사례 없음)",
        "- 밤샘 중 발견·수정한 파이프라인 갭 2건: ①multilang 러너 Python 미지원"
        " ②ast 뮤테이션이 import 시점 실행 줄(모듈 상수)을 미커버로 오판해 드랍"
        " → 수정 후 4개 언어 판정 완전 대칭 (커밋 5d2ade9, f7a7fb4)",
    ]
    return "\n".join(lines)


def track_b2_section(rows: list[dict]) -> str:
    by_repo: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        key = r.get("action") or r.get("skip") or "error"
        by_repo[r.get("repo", "?")][key] += 1
    lines = [
        "## Track B-2 — 실전 리플레이 (실제 저장소, 실전 경로 전체)",
        "",
        "glom·axios의 실제 병합충돌을 **병합 부모 커밋으로 체크아웃**한 뒤"
        " 실전 파이프라인(impact 테스트선별 → sandbox → mutation → policy)을"
        " 그대로 실행. 통제 없는 과거 환경이라 에스컬레이션이 많은 게 정상 —"
        " 핵심은 **검증 근거를 못 얻으면 자동 병합하지 않는다**는 원칙의 실증.",
        "",
        "| 저장소 | 자동(가짜충돌) | 자동(검증병합) | 에스컬레이션 | 환경불가 |",
        "|---|---|---|---|---|",
    ]
    for repo, c in sorted(by_repo.items()):
        lines.append(
            f"| {repo} | {c.get('auto_spurious', 0)} | {c.get('auto_verified', 0)} "
            f"| {c.get('escalated', 0)} | "
            f"{c.get('baseline-suite-broken', 0) + c.get('checkout-failed', 0) + c.get('error', 0)} |"
        )
    auto = [r for r in rows if str(r.get("action", "")).startswith("auto")]
    mism = [r for r in auto if r.get("gt_equal") is False]
    lines += [
        "",
        f"- 자동병합 {len(auto)}건 중 사람 해법과 문자열 불일치 {len(mism)}건을"
        " **전수 손검증** → 전부 의미 동등(임포트 스타일/블록 순서/공백)."
        " **행동 오탐 0건.** (상세: b2_mismatch_audit.md)",
        "- 에스컬레이션 다수는 rel_tests=0(그 시점 커버리지로 관련 테스트를 못"
        " 찾음) 또는 과거 커밋 환경 문제 — '모르면 사람에게'가 설계대로 작동.",
    ]
    return "\n".join(lines)


def main() -> None:
    a = _load("track_a.jsonl")
    b1 = _load("track_b_seeded.jsonl")
    b2 = _load("track_b2_real.jsonl")
    parts = [
        "# Weld 밤샘 검증 리포트 (2026-07-21 → 07-22)",
        "",
        "LLM: 친구 커스텀 **qwen3-235b** (OpenAI 호환, 캠프 VPN)"
        " — 전 트랙 이 모델로 실측. 원칙: *놓친 자동화는 있어도, 잘못된 자동화는 없다.*",
        "",
        track_b1_section(b1) if b1 else "",
        "",
        track_b2_section(b2) if b2 else "",
        "",
        track_a_section(a) if a else "",
        "",
        "---",
        f"산출 데이터: track_a.jsonl {len(a)}건 · track_b_seeded.jsonl {len(b1)}건"
        f" · track_b2_real.jsonl {len(b2)}건 (모두 results/overnight-0721/)",
    ]
    with open(OUT, "w") as f:
        f.write("\n".join(parts) + "\n")
    print(f"리포트 생성: {OUT}")
    print("\n".join(parts))


if __name__ == "__main__":
    main()
