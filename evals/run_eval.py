"""Contract review skill evaluation pipeline.

Complete workflow:
    uv run python evals/run_eval.py --report          # Compute metrics from semantic matches
    uv run python evals/run_eval.py --analyze          # Analyze failures + suggest fixes
    uv run python evals/run_eval.py --all              # Report + analyze

The pipeline assumes you have already:
    1. Run the skill on test contracts (output in workspace/iteration-N/)
    2. Run semantic matching agents (output as semantic_match.json in each eval dir)
"""

import json
import re
import sys
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
GROUND_TRUTH_DIR = ROOT / "tests" / "ground-truth"
SKILL_DIR = ROOT / "skills" / "contract-reviewer"

RISK_ORDER = {"严重": 0, "重要": 1, "一般": 2, "提示": 3}


# ---------------------------------------------------------------------------
# Metrics computation
# ---------------------------------------------------------------------------

def load_semantic_matches(workspace_dir: Path) -> dict[str, dict]:
    """Load all semantic_match.json files from an iteration workspace.

    Returns {eval_name: {results: [...], metrics: {...}}}.
    """
    matches = {}
    for eval_dir in sorted(workspace_dir.glob("eval-*")):
        match_file = eval_dir / "semantic_match.json"
        if not match_file.exists():
            continue
        data = json.loads(match_file.read_text(encoding="utf-8"))

        # Normalize: some files use "results" key, others use "items"
        results = data.get("results") or data.get("items") or []
        metrics = data.get("metrics") or data.get("aggregate") or {}

        matches[eval_dir.name] = {"results": results, "metrics": metrics}
    return matches


def compute_overall_metrics(all_matches: dict) -> dict:
    """Aggregate metrics across all eval cases."""
    all_results = []
    for name, data in all_matches.items():
        all_results.extend(data["results"])

    total_gt = len(all_results)
    found = [r for r in all_results if r.get("found")]
    missed = [r for r in all_results if not r.get("found")]
    level_correct = [r for r in found if r.get("level_match")]

    # By severity
    severe_gt = [r for r in all_results if r.get("gt_risk_level") == "严重"]
    severe_found = [r for r in severe_gt if r.get("found")]
    important_gt = [r for r in all_results if r.get("gt_risk_level") == "重要"]
    important_found = [r for r in important_gt if r.get("found")]
    general_gt = [r for r in all_results if r.get("gt_risk_level") == "一般"]
    general_found = [r for r in general_gt if r.get("found")]

    # Level mismatch analysis
    inflated = []   # output > GT
    deflated = []   # output < GT
    for r in found:
        out_lvl = r.get("output_risk_level", "")
        gt_lvl = r.get("gt_risk_level", "")
        if out_lvl and gt_lvl and out_lvl != gt_lvl:
            out_idx = RISK_ORDER.get(out_lvl, 99)
            gt_idx = RISK_ORDER.get(gt_lvl, 99)
            if out_idx < gt_idx:
                inflated.append(r)
            elif out_idx > gt_idx:
                deflated.append(r)

    # Missed items detail
    missed_detail = []
    for r in missed:
        missed_detail.append({
            "gt_id": r.get("gt_id", "?"),
            "risk_level": r.get("gt_risk_level", ""),
            "gt_dimension": r.get("gt_dimension", ""),
            "description": r.get("gt_description", "")[:100],
        })

    return {
        "total_gt": total_gt,
        "found_count": len(found),
        "missed_count": len(missed),
        "recall": round(len(found) / total_gt, 3) if total_gt else 0,
        "level_match_count": len(level_correct),
        "level_accuracy": round(len(level_correct) / len(found), 3) if found else 0,
        "severe_recall": f"{len(severe_found)}/{len(severe_gt)}",
        "important_recall": f"{len(important_found)}/{len(important_gt)}",
        "general_recall": f"{len(general_found)}/{len(general_gt)}",
        "severity_inflation_count": len(inflated),
        "severity_deflation_count": len(deflated),
        "inflated_examples": [
            {"gt_id": r.get("gt_id"), "gt": r.get("gt_risk_level"), "out": r.get("output_risk_level"),
             "desc": (r.get("gt_description") or r.get("gt", {}).get("issue", ""))[:80]}
            for r in inflated[:5]
        ],
        "deflated_examples": [
            {"gt_id": r.get("gt_id"), "gt": r.get("gt_risk_level"), "out": r.get("output_risk_level"),
             "desc": (r.get("gt_description") or r.get("gt", {}).get("issue", ""))[:80]}
            for r in deflated[:5]
        ],
        "missed_items": missed_detail,
    }


def print_metrics(metrics: dict, title: str = "Evaluation Results"):
    """Pretty-print metrics."""
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")
    print(f"  总 Ground Truth 项:    {metrics['total_gt']}")
    print(f"  语义召回率:            {metrics['recall']:.1%}  ({metrics['found_count']}/{metrics['total_gt']})")
    print(f"  定级准确率:            {metrics['level_accuracy']:.1%}  ({metrics['level_match_count']}/{metrics['found_count']})")
    print(f"  严重风险召回:          {metrics['severe_recall']}")
    print(f"  重要风险召回:          {metrics['important_recall']}")
    print(f"  一般风险召回:          {metrics['general_recall']}")
    print(f"  标高（保守倾向）:      {metrics['severity_inflation_count']} 次")
    print(f"  标低（危险倾向）:      {metrics['severity_deflation_count']} 次")

    if metrics["missed_items"]:
        print(f"\n  漏报项 ({metrics['missed_count']}):")
        for item in metrics["missed_items"]:
            print(f"    - #{item['gt_id']} [{item['risk_level']}] {item['description']}")

    if metrics["inflated_examples"]:
        print(f"\n  标高示例:")
        for ex in metrics["inflated_examples"]:
            print(f"    - #{ex['gt_id']} GT={ex['gt']} → Skill={ex['out']}: {ex['desc']}")

    if metrics["deflated_examples"]:
        print(f"\n  标低示例:")
        for ex in metrics["deflated_examples"]:
            print(f"    - #{ex['gt_id']} GT={ex['gt']} → Skill={ex['out']}: {ex['desc']}")


# ---------------------------------------------------------------------------
# Failure analysis → fix suggestions
# ---------------------------------------------------------------------------

def build_analysis_prompt(metrics: dict, all_matches: dict) -> str:
    """Build a prompt for an LLM to analyze failures and propose skill edits."""

    # Summarize all missed items
    missed_summary = []
    for name, data in all_matches.items():
        for r in data["results"]:
            if not r.get("found"):
                desc = r.get("gt_description") or r.get("gt", {}).get("issue", "")
                missed_summary.append(
                    f"- [{r.get('gt_risk_level', '?')}] {desc[:120]}"
                )

    # Summarize level mismatches
    level_issues = []
    for name, data in all_matches.items():
        for r in data["results"]:
            if r.get("found") and not r.get("level_match"):
                out_lvl = r.get("output_risk_level", "")
                gt_lvl = r.get("gt_risk_level", "")
                desc = r.get("gt_description") or r.get("gt", {}).get("issue", "")
                level_issues.append(
                    f"- GT={gt_lvl} → Skill={out_lvl}: {desc[:120]}"
                )

    return f"""## Eval 结果摘要

- 语义召回率: {metrics['recall']:.1%} ({metrics['found_count']}/{metrics['total_gt']})
- 定级准确率: {metrics['level_accuracy']:.1%}
- 标高（保守）：{metrics['severity_inflation_count']} 次
- 标低（危险）：{metrics['severity_deflation_count']} 次

## 漏报项

{chr(10).join(missed_summary) if missed_summary else '无'}

## 定级偏差

{chr(10).join(level_issues[:15]) if level_issues else '无'}

---

请阅读以下 skill 文件，分析上述失败项，并提出具体的修改建议：

1. /Users/Z1nk/Desktop/ai/CRSD/skills/contract-reviewer/SKILL.md
2. /Users/Z1nk/Desktop/ai/CRSD/skills/contract-reviewer/references/risk-level-guide.md
3. /Users/Z1nk/Desktop/ai/CRSD/skills/contract-reviewer/references/purchase-contract.md

对于每个问题：
- 说明是 skill 指令问题还是 reference 规则问题
- 给出具体的修改方案（在哪个文件、改什么内容）
- 按优先级排序（先修标低/漏报，再修标高）"""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_report(workspace_dir: Path):
    """Compute and print metrics from semantic_match.json files."""
    all_matches = load_semantic_matches(workspace_dir)
    if not all_matches:
        print("No semantic_match.json files found. Run semantic matching first.")
        sys.exit(1)

    metrics = compute_overall_metrics(all_matches)
    print_metrics(metrics, f"Benchmark: {workspace_dir.name}")

    # Save
    report = {
        "timestamp": datetime.now().isoformat(),
        "workspace": str(workspace_dir),
        "per_eval": {name: data["metrics"] for name, data in all_matches.items()},
        "overall": metrics,
    }
    report_path = workspace_dir / "benchmark.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  Benchmark saved: {report_path}")


def cmd_analyze(workspace_dir: Path):
    """Generate analysis prompt for LLM-based fix suggestions."""
    all_matches = load_semantic_matches(workspace_dir)
    if not all_matches:
        print("No semantic_match.json files found.")
        sys.exit(1)

    metrics = compute_overall_metrics(all_matches)
    print_metrics(metrics, f"Analysis: {workspace_dir.name}")

    prompt = build_analysis_prompt(metrics, all_matches)
    prompt_path = workspace_dir / "analysis_prompt.md"
    prompt_path.write_text(prompt, encoding="utf-8")
    print(f"\n  Analysis prompt saved: {prompt_path}")
    print(f"  Feed this to an Agent to get fix suggestions.")


def cmd_all(workspace_dir: Path):
    """Run report + analyze."""
    cmd_report(workspace_dir)
    print("\n")
    cmd_analyze(workspace_dir)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Contract review skill eval pipeline")
    parser.add_argument("--report", action="store_true", help="Compute metrics from semantic matches")
    parser.add_argument("--analyze", action="store_true", help="Generate analysis prompt for fix suggestions")
    parser.add_argument("--all", action="store_true", help="Report + analyze")
    parser.add_argument("--workspace", type=str, help="Path to iteration workspace")
    args = parser.parse_args()

    if args.workspace:
        workspace_dir = Path(args.workspace)
    else:
        workspace = ROOT / "contract-reviewer-workspace"
        iters = sorted(workspace.glob("iteration-*"))
        if not iters:
            print("No iteration workspace found.")
            sys.exit(1)
        workspace_dir = iters[-1]

    print(f"Workspace: {workspace_dir}")

    if args.all:
        cmd_all(workspace_dir)
    elif args.analyze:
        cmd_analyze(workspace_dir)
    elif args.report:
        cmd_report(workspace_dir)
    else:
        # Default: report
        cmd_report(workspace_dir)


if __name__ == "__main__":
    main()
