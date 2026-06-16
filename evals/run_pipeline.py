"""Contract-reviewer eval pipeline — one command to run the whole loop.

The full optimization loop has 6 stages. This orchestrator runs them in order
and produces a single consolidated report:

    [LLM] 0. 跑 skill 于测试合同        → workspace/iteration-N/eval-*/with_skill/outputs/review_output.md
    [LLM] 1. 语义匹配 vs ground-truth   → workspace/iteration-N/eval-*/semantic_match.json
    [auto] 2. 格式门禁  (validate_output)   每份输出结构是否合规
    [auto] 3. 质量指标  (run_eval)          召回 / 定级 / 标高标低 / 漏报
    [auto] 4. 回归对比                      本轮 vs 上一轮 benchmark.json
    [auto] 5. 运营 KPI  (operational_metrics)  6 个 KPI(来自 trace)

Stages 2-5 are pure-python and always run on existing artifacts.
Stage 1 (the manual bottleneck) is automated with --with-llm when a GLM/OpenAI
key is present; otherwise it is skipped and existing semantic_match.json is used.
Stage 0 stays manual by design (run the skill in Claude Code for real fidelity,
or via scripts/openai-observer.py for the cross-platform endpoint).

Usage:
    uv run python evals/run_pipeline.py                 # latest iteration, existing artifacts
    uv run python evals/run_pipeline.py --workspace contract-reviewer-workspace/iteration-3
    uv run python evals/run_pipeline.py --with-llm      # also auto-run semantic matching (needs OPENAI_API_KEY)
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_eval import load_semantic_matches, compute_overall_metrics  # noqa: E402
from validate_output import validate  # noqa: E402
from validate_grounding import validate_grounding  # noqa: E402
import operational_metrics  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
WORKSPACE = ROOT / "contract-reviewer-workspace"
GROUND_TRUTH_DIR = ROOT / "tests" / "ground-truth"
LEGAL_BASIS = ROOT / "skills" / "contract-reviewer" / "references" / "legal-basis.md"
# eval-dir token → source contract (iter-3/4 have no eval_metadata; 2 test contracts)
CONTRACT_ALIASES = {
    "huandiangui": "tests/contracts/contract-4-换电柜采购.txt",
    "huagongpin": "tests/contracts/contract-5-化工品采购.txt",
}


def resolve_contract(eval_name: str) -> Path | None:
    for token, rel in CONTRACT_ALIASES.items():
        if token in eval_name:
            p = ROOT / rel
            return p if p.exists() else None
    return None


# ───────────────────────────────────────────────────────────────────────────
# Stage selection
# ───────────────────────────────────────────────────────────────────────────

def pick_iteration(explicit: str | None) -> Path:
    if explicit:
        p = Path(explicit)
        return p if p.is_absolute() else ROOT / p
    iters = sorted(WORKSPACE.glob("iteration-*"), key=lambda p: int(p.name.split("-")[-1]))
    if not iters:
        print("No iteration-* workspace found.")
        sys.exit(1)
    return iters[-1]


def previous_iteration(current: Path) -> Path | None:
    """Most recent earlier iteration that has a benchmark.json."""
    try:
        cur_n = int(current.name.split("-")[-1])
    except ValueError:
        return None
    candidates = []
    for p in WORKSPACE.glob("iteration-*"):
        try:
            n = int(p.name.split("-")[-1])
        except ValueError:
            continue
        if n < cur_n and (p / "benchmark.json").exists():
            candidates.append((n, p))
    return max(candidates)[1] if candidates else None


def find_output_md(eval_dir: Path) -> Path | None:
    for pattern in ("with_skill/outputs/*.md", "with_skill/*.md"):
        hits = sorted(eval_dir.glob(pattern))
        if hits:
            return hits[0]
    return None


# ───────────────────────────────────────────────────────────────────────────
# Stage 1 (optional): LLM semantic matching
# ───────────────────────────────────────────────────────────────────────────

def llm_semantic_match(iter_dir: Path) -> int:
    """Auto-generate semantic_match.json for eval dirs missing one, via GLM/OpenAI.

    Returns count of files written. No-ops gracefully without a key.
    """
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        print("  [stage 1] OPENAI_API_KEY 未设置 → 跳过自动语义匹配，使用已有 semantic_match.json")
        return 0
    try:
        from openai import OpenAI
    except ImportError:
        print("  [stage 1] openai 包未安装 → 跳过")
        return 0

    base_url = os.environ.get("OPENAI_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
    model = os.environ.get("OPENAI_MODEL", "glm-4-plus")
    client = OpenAI(api_key=api_key, base_url=base_url)

    written = 0
    for eval_dir in sorted(iter_dir.glob("eval-*")):
        if (eval_dir / "semantic_match.json").exists():
            continue
        out_md = find_output_md(eval_dir)
        meta = eval_dir / "eval_metadata.json"
        gt_path = None
        if meta.exists():
            gt_rel = json.loads(meta.read_text(encoding="utf-8")).get("ground_truth")
            if gt_rel:
                gt_path = ROOT / gt_rel
        if not out_md or not gt_path or not gt_path.exists():
            print(f"  [stage 1] {eval_dir.name}: 缺 output 或 ground-truth → 跳过")
            continue

        prompt = _semantic_match_prompt(gt_path.read_text(encoding="utf-8"),
                                        out_md.read_text(encoding="utf-8"))
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )
        raw = resp.choices[0].message.content or "{}"
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        (eval_dir / "semantic_match.json").write_text(raw, encoding="utf-8")
        written += 1
        print(f"  [stage 1] {eval_dir.name}: 已生成 semantic_match.json")
    return written


def _semantic_match_prompt(ground_truth: str, output: str) -> str:
    return f"""你是合同审查评估员。对照 ground-truth 的每个风险点，判断下面的审查输出是否命中。

输出严格的 JSON：{{"results": [{{"gt_id": "...", "gt_risk_level": "严重|重要|一般|提示", "gt_dimension": "...", "gt_description": "...", "found": true/false, "output_risk_level": "...", "level_match": true/false}}]}}

## Ground Truth
{ground_truth[:8000]}

## 审查输出
{output[:12000]}
"""


# ───────────────────────────────────────────────────────────────────────────
# Stage 2: format gate
# ───────────────────────────────────────────────────────────────────────────

def stage_gate(iter_dir: Path) -> dict:
    """格式门禁(硬) + 护栏 grounding(复核软标记)."""
    results = {}
    legal = LEGAL_BASIS.read_text(encoding="utf-8") if LEGAL_BASIS.exists() else ""
    for eval_dir in sorted(iter_dir.glob("eval-*")):
        out_md = find_output_md(eval_dir)
        if not out_md:
            continue
        content = out_md.read_text(encoding="utf-8")
        fmt = validate(content)
        entry = {
            "output": str(out_md.relative_to(ROOT)),
            "format_pass": all(p for _, p, _ in fmt),
            "format": [{"name": n, "pass": p, "detail": d} for n, p, d in fmt],
            "grounding_clean": None,
            "grounding": [],
        }
        contract = resolve_contract(eval_dir.name)
        if contract:
            gr = validate_grounding(content, contract.read_text(encoding="utf-8"), legal)
            entry["grounding_clean"] = all(p for _, p, _ in gr)
            entry["grounding"] = [{"name": n, "flag": not p, "detail": d} for n, p, d in gr]
        results[eval_dir.name] = entry
    return results


# ───────────────────────────────────────────────────────────────────────────
# Stage 4: regression
# ───────────────────────────────────────────────────────────────────────────

def stage_regression(current_overall: dict, prev_iter: Path | None) -> dict:
    if not prev_iter:
        return {"baseline": None, "note": "无可对比的上一轮"}
    prev = json.loads((prev_iter / "benchmark.json").read_text(encoding="utf-8")).get("overall", {})

    def delta(key):
        a, b = current_overall.get(key), prev.get(key)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            return round(a - b, 3)
        return None

    # newly-missed: items found in prev but missed now (by gt_id).
    # Ignore anonymous ids ('?'/empty) — they can't be tracked across runs.
    def real_ids(items):
        return {str(m.get("gt_id")) for m in items
                if m.get("gt_id") not in (None, "", "?")}
    prev_missed_ids = real_ids(prev.get("missed_items", []))
    cur_missed_ids = real_ids(current_overall.get("missed_items", []))
    newly_missed = sorted(cur_missed_ids - prev_missed_ids)
    newly_fixed = sorted(prev_missed_ids - cur_missed_ids)
    recall_delta = delta("recall")

    return {
        "baseline": prev_iter.name,
        "recall_delta": recall_delta,
        "level_accuracy_delta": delta("level_accuracy"),
        "inflation_delta": delta("severity_inflation_count"),
        "deflation_delta": delta("severity_deflation_count"),
        "newly_missed_ids": newly_missed,
        "newly_fixed_ids": newly_fixed,
        # True regression = a tracked GT item that used to be found is now missed.
        # A small recall dip without newly-missed tracked items is flagged softly.
        "regressed": bool(newly_missed),
        "recall_dipped": (recall_delta or 0) < 0,
    }


# ───────────────────────────────────────────────────────────────────────────
# Orchestration
# ───────────────────────────────────────────────────────────────────────────

def run(iter_dir: Path, with_llm: bool) -> dict:
    print(f"\n{'#' * 65}\n  EVAL PIPELINE — {iter_dir.name}\n{'#' * 65}")

    # Stage 1
    print(f"\n[1/5] 语义匹配")
    if with_llm:
        n = llm_semantic_match(iter_dir)
        print(f"      生成 {n} 个新匹配文件")
    else:
        print(f"      (默认模式：使用已有 semantic_match.json，--with-llm 可自动生成)")

    # Stage 2
    print(f"\n[2/5] 格式门禁(硬) + 护栏 grounding(复核)")
    gate = stage_gate(iter_dir)
    for name, r in gate.items():
        print(f"      {'✅' if r['format_pass'] else '❌'} 格式 · {name}")
        for c in r["format"]:
            if not c["pass"]:
                print(f"          ✗ {c['name']}: {c['detail'][:70]}")
        if r["grounding_clean"] is None:
            print(f"      —  护栏 · {name}: 未匹配到合同，跳过")
        else:
            print(f"      {'✅' if r['grounding_clean'] else '⚠️ '} 护栏 · {name}")
            for c in r["grounding"]:
                if c["flag"]:
                    print(f"          ⚠️ {c['name']}: {c['detail'][:76]}")
    if not gate:
        print("      (本轮无 with_skill 输出可校验)")

    # Stage 3
    print(f"\n[3/5] 质量指标")
    matches = load_semantic_matches(iter_dir)
    overall = compute_overall_metrics(matches) if matches else {}
    if overall:
        print(f"      召回 {overall['recall']:.1%}  定级 {overall['level_accuracy']:.1%}  "
              f"严重 {overall['severe_recall']}  重要 {overall['important_recall']}  一般 {overall['general_recall']}")
        print(f"      标高 {overall['severity_inflation_count']} / 标低 {overall['severity_deflation_count']}  "
              f"漏报 {overall['missed_count']}")
    else:
        print("      (无 semantic_match.json → 跳过)")

    # Stage 4
    print(f"\n[4/5] 回归对比")
    prev = previous_iteration(iter_dir)
    reg = stage_regression(overall, prev) if overall else {"baseline": None}
    if reg.get("baseline"):
        print(f"      vs {reg['baseline']}:  召回Δ {reg['recall_delta']:+}  定级Δ {reg['level_accuracy_delta']:+}  "
              f"标高Δ {reg['inflation_delta']:+}")
        if reg["newly_missed_ids"]:
            print(f"      ⚠️ 新增漏报(真回归): {reg['newly_missed_ids']}")
        if reg["newly_fixed_ids"]:
            print(f"      ✅ 修复漏报: {reg['newly_fixed_ids']}")
        if reg["regressed"]:
            print(f"      ❌ 出现回归(已找到的项变漏报)")
        elif reg.get("recall_dipped"):
            print(f"      ⚠️ 召回轻微下探({reg['recall_delta']:+})，无已追踪项回归——多为定级↔召回权衡")
        else:
            print(f"      ✅ 无回归")
    else:
        print(f"      {reg.get('note', '无基线')}")

    # Stage 5
    print(f"\n[5/5] 运营 KPI")
    op_report = operational_metrics.generate_report()
    print(f"      Trace {op_report['total_traces']} 条 / 触发 {op_report['triggered_traces']} 条  "
          f"→ 总体 {'✅' if op_report.get('overall_pass') else ('❌' if op_report.get('overall_pass') is False else '⏳')}")

    # Consolidate
    _grounded = [r["grounding_clean"] for r in gate.values() if r["grounding_clean"] is not None]
    grounding_verdict = all(_grounded) if _grounded else None
    pipeline_report = {
        "timestamp": datetime.now().isoformat(),
        "iteration": iter_dir.name,
        "gate": gate,
        "quality": overall,
        "regression": reg,
        "operational": op_report,
        "verdict": {
            "format_pass": all(r["format_pass"] for r in gate.values()) if gate else None,
            "grounding_clean": grounding_verdict,
            "quality_recall": overall.get("recall"),
            "regressed": reg.get("regressed"),
            "operational_pass": op_report.get("overall_pass"),
        },
    }
    out_path = iter_dir / "pipeline_report.json"
    out_path.write_text(json.dumps(pipeline_report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{'#' * 65}\n  报告已保存: {out_path.relative_to(ROOT)}\n{'#' * 65}\n")
    return pipeline_report


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Contract-reviewer eval pipeline (one command)")
    parser.add_argument("--workspace", type=str, help="特定 iteration 目录(默认最新一轮)")
    parser.add_argument("--with-llm", action="store_true", help="自动跑语义匹配(需 OPENAI_API_KEY)")
    args = parser.parse_args()

    iter_dir = pick_iteration(args.workspace)
    run(iter_dir, with_llm=args.with_llm)


if __name__ == "__main__":
    main()
