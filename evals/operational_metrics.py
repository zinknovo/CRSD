"""Operational quality metrics from observability traces.

Reads traces from contract-reviewer-workspace/observability/traces.jsonl
and computes 6 KPIs (5 original + token cost):

1. 可调用成功率 (invocation_success_rate) > 95%
2. 平均响应时间 (avg_response_time): non-IO < 1s, IO < 5s
3. 错误恢复率 (error_recovery_rate) > 80%
4. LLM选用准确率 (llm_selection_accuracy) > 90%
5. 参数传递准确率 (param_accuracy) > 95%
6. Token 成本 (token_cost): avg cost per invocation, p50/p95/p99

Usage:
    uv run python evals/operational_metrics.py --report     # All KPIs
    uv run python evals/operational_metrics.py --trace ID   # Show single trace
    uv run python evals/operational_metrics.py --seed       # Seed sample traces
    uv run python evals/operational_metrics.py --all        # Report + recent traces
"""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OBS_DIR = ROOT / "contract-reviewer-workspace" / "observability"
TRACES_FILE = OBS_DIR / "traces.jsonl"
SPANS_FILE = OBS_DIR / "spans.jsonl"
TRIGGER_EVAL = ROOT / "contract-reviewer-workspace" / "trigger-eval.json"
RUN_LOG = ROOT / "contract-reviewer-workspace" / "run_log.jsonl"  # legacy

# ── KPI thresholds ────────────────────────────────────────────────────────
THRESHOLDS = {
    "invocation_success_rate": 0.95,
    "avg_response_time_non_io": 1.0,
    "avg_response_time_io": 5.0,
    "error_recovery_rate": 0.80,
    "llm_selection_accuracy": 0.90,
    "param_accuracy": 0.95,
}


# ═══════════════════════════════════════════════════════════════════════════
# 1. Trace I/O
# ═══════════════════════════════════════════════════════════════════════════

def load_traces() -> list[dict]:
    """Load all completed traces."""
    if not TRACES_FILE.exists():
        return []
    traces = []
    with open(TRACES_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    traces.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return traces


def load_spans() -> list[dict]:
    """Load all flattened spans."""
    if not SPANS_FILE.exists():
        return []
    spans = []
    with open(SPANS_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    spans.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return spans


# ═══════════════════════════════════════════════════════════════════════════
# 2. KPI computation from traces
# ═══════════════════════════════════════════════════════════════════════════

def compute_invocation_success_rate(traces: list[dict]) -> dict:
    """KPI 1: 可调用成功率 — triggered traces that completed."""
    triggered = [t for t in traces if t.get("summary", {}).get("triggered")]
    if not triggered:
        return {"value": None, "count": 0, "threshold": THRESHOLDS["invocation_success_rate"]}

    completed = [t for t in triggered if t.get("summary", {}).get("completed")]
    rate = len(completed) / len(triggered)
    return {
        "value": round(rate, 4),
        "count": len(triggered),
        "completed": len(completed),
        "failed": len(triggered) - len(completed),
        "threshold": THRESHOLDS["invocation_success_rate"],
        "pass": rate >= THRESHOLDS["invocation_success_rate"],
    }


def compute_avg_response_time(traces: list[dict]) -> dict:
    """KPI 2: 平均响应时间 — from trace summary."""
    completed = [t for t in traces if t.get("summary", {}).get("completed")]
    if not completed:
        return {
            "non_io_ms": None, "non_io_s": None,
            "io_ms": None, "io_s": None,
            "non_io_pass": None, "io_pass": None,
            "threshold_non_io_s": THRESHOLDS["avg_response_time_non_io"],
            "threshold_io_s": THRESHOLDS["avg_response_time_io"],
            "count": 0,
        }

    io_runs = [t for t in completed if t.get("summary", {}).get("had_io")]
    non_io_runs = [t for t in completed if not t.get("summary", {}).get("had_io")]

    def avg_ms(runs):
        if not runs:
            return None
        return round(sum(t["summary"]["duration_ms"] for t in runs) / len(runs), 1)

    non_io_avg = avg_ms(non_io_runs)
    io_avg = avg_ms(io_runs)

    return {
        "non_io_ms": non_io_avg,
        "non_io_s": round(non_io_avg / 1000, 2) if non_io_avg else None,
        "io_ms": io_avg,
        "io_s": round(io_avg / 1000, 2) if io_avg else None,
        "non_io_pass": (non_io_avg / 1000 < THRESHOLDS["avg_response_time_non_io"]) if non_io_avg else None,
        "io_pass": (io_avg / 1000 < THRESHOLDS["avg_response_time_io"]) if io_avg else None,
        "threshold_non_io_s": THRESHOLDS["avg_response_time_non_io"],
        "threshold_io_s": THRESHOLDS["avg_response_time_io"],
        "count": len(completed),
    }


def compute_error_recovery_rate(traces: list[dict]) -> dict:
    """KPI 3: 错误恢复率 — traces with errors that recovered."""
    with_errors = [t for t in traces if t.get("summary", {}).get("error_span_count", 0) > 0]
    if not with_errors:
        return {"value": None, "error_count": 0, "threshold": THRESHOLDS["error_recovery_rate"]}

    recovered = [t for t in with_errors if t.get("summary", {}).get("recovered")]
    rate = len(recovered) / len(with_errors)
    return {
        "value": round(rate, 4),
        "error_count": len(with_errors),
        "recovered": len(recovered),
        "unrecovered": len(with_errors) - len(recovered),
        "threshold": THRESHOLDS["error_recovery_rate"],
        "pass": rate >= THRESHOLDS["error_recovery_rate"],
    }


def compute_llm_selection_accuracy(traces: list[dict]) -> dict:
    """KPI 4: LLM选用准确率 — from trigger detection vs confirmation."""
    # Traces where heuristic detected intent
    detected = [t for t in traces if t.get("summary", {}).get("trigger.detected") or
                any(s.get("attributes", {}).get("trigger.detected") for s in t.get("spans", []))]
    if not detected:
        return {"value": None, "count": 0, "note": "No trigger-detection traces"}

    # Confirmed = trace status is TRIGGERED/COMPLETED/ERROR (not NOT_TRIGGERED)
    correct_positive = [t for t in detected if t.get("summary", {}).get("triggered")]
    false_negative = [t for t in detected if not t.get("summary", {}).get("triggered")]

    # Also check trigger-eval negatives (queries that should NOT trigger)
    # These come from the legacy run_log or trigger-eval results
    neg_entries = _load_trigger_negatives()
    correct_negative = neg_entries.get("tn", 0)
    false_positive = neg_entries.get("fp", 0)

    total = len(detected) + correct_negative + false_positive
    correct = len(correct_positive) + correct_negative
    if total == 0:
        return {"value": None, "count": 0, "note": "No data"}

    rate = correct / total
    tp = len(correct_positive)
    fn = len(false_negative)
    fp = false_positive
    tn = correct_negative

    return {
        "value": round(rate, 4),
        "total": total,
        "correct": correct,
        "tp": tp, "fn": fn, "fp": fp, "tn": tn,
        "trigger_recall": round(tp / (tp + fn), 4) if (tp + fn) else None,
        "trigger_precision": round(tp / (tp + fp), 4) if (tp + fp) else None,
        "threshold": THRESHOLDS["llm_selection_accuracy"],
        "pass": rate >= THRESHOLDS["llm_selection_accuracy"],
    }


def _load_trigger_negatives() -> dict:
    """Load negative trigger results from legacy run_log."""
    result = {"tn": 0, "fp": 0}
    if not RUN_LOG.exists():
        return result
    with open(RUN_LOG, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "expected_trigger" not in entry:
                continue
            if not entry["expected_trigger"] and not entry.get("triggered"):
                result["tn"] += 1
            elif not entry["expected_trigger"] and entry.get("triggered"):
                result["fp"] += 1
    return result


def compute_param_accuracy(traces: list[dict]) -> dict:
    """KPI 5: 参数传递准确率."""
    with_params = [t for t in traces if t.get("params") and t.get("summary", {}).get("triggered")]
    if not with_params:
        return {"value": None, "count": 0, "threshold": THRESHOLDS["param_accuracy"]}

    total_fields = 0
    correct_fields = 0
    field_results = {}

    for trace in with_params:
        params = trace.get("params", {})
        expected = trace.get("expected_params", {})
        if not expected:
            continue

        for key in expected:
            total_fields += 1
            got = params.get(key)
            exp = expected[key]

            if key == "focus_areas":
                got_set = set(got) if isinstance(got, list) else {got} if got else set()
                exp_set = set(exp) if isinstance(exp, list) else {exp} if exp else set()
                match = bool(got_set & exp_set) if exp_set else True
            else:
                match = got == exp

            if match:
                correct_fields += 1

            field_results.setdefault(key, {"correct": 0, "total": 0})
            field_results[key]["correct"] += int(match)
            field_results[key]["total"] += 1

    if total_fields == 0:
        return {"value": None, "count": len(with_params), "threshold": THRESHOLDS["param_accuracy"]}

    rate = correct_fields / total_fields
    return {
        "value": round(rate, 4),
        "total_fields": total_fields,
        "correct_fields": correct_fields,
        "per_field": field_results,
        "threshold": THRESHOLDS["param_accuracy"],
        "pass": rate >= THRESHOLDS["param_accuracy"],
    }


def compute_token_cost(traces: list[dict]) -> dict:
    """KPI 6: Token 成本统计."""
    triggered = [t for t in traces if t.get("summary", {}).get("triggered")]
    if not triggered:
        return {"count": 0, "note": "No triggered traces"}

    costs = [t.get("summary", {}).get("token_cost_usd", 0) for t in triggered]
    input_tokens = [t.get("summary", {}).get("input_tokens", 0) for t in triggered]
    output_tokens = [t.get("summary", {}).get("output_tokens", 0) for t in triggered]
    context_pcts = [t.get("summary", {}).get("context_used_pct", 0) for t in triggered
                    if t.get("summary", {}).get("context_used_pct", 0) > 0]

    def percentile(vals, p):
        if not vals:
            return 0
        s = sorted(vals)
        idx = int(len(s) * p / 100)
        return s[min(idx, len(s) - 1)]

    return {
        "count": len(triggered),
        "total_cost_usd": round(sum(costs), 4),
        "avg_cost_usd": round(sum(costs) / len(costs), 4),
        "p50_cost_usd": round(percentile(costs, 50), 4),
        "p95_cost_usd": round(percentile(costs, 95), 4),
        "total_input_tokens": sum(input_tokens),
        "total_output_tokens": sum(output_tokens),
        "avg_input_tokens": round(sum(input_tokens) / len(input_tokens)),
        "avg_output_tokens": round(sum(output_tokens) / len(output_tokens)),
        "avg_context_used_pct": round(sum(context_pcts) / len(context_pcts), 1) if context_pcts else None,
        "p95_context_used_pct": round(percentile(context_pcts, 95), 1) if context_pcts else None,
    }


# ═══════════════════════════════════════════════════════════════════════════
# 3. Report generation
# ═══════════════════════════════════════════════════════════════════════════

def generate_report() -> dict:
    traces = load_traces()

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_traces": len(traces),
        "triggered_traces": len([t for t in traces if t.get("summary", {}).get("triggered")]),
        "metrics": {
            "invocation_success_rate": compute_invocation_success_rate(traces),
            "avg_response_time": compute_avg_response_time(traces),
            "error_recovery_rate": compute_error_recovery_rate(traces),
            "llm_selection_accuracy": compute_llm_selection_accuracy(traces),
            "param_accuracy": compute_param_accuracy(traces),
            "token_cost": compute_token_cost(traces),
        },
    }

    passable = [
        report["metrics"]["invocation_success_rate"].get("pass"),
        report["metrics"]["error_recovery_rate"].get("pass"),
        report["metrics"]["llm_selection_accuracy"].get("pass"),
        report["metrics"]["param_accuracy"].get("pass"),
        report["metrics"]["avg_response_time"].get("non_io_pass"),
        report["metrics"]["avg_response_time"].get("io_pass"),
    ]
    evaluated = [p for p in passable if p is not None]
    report["overall_pass"] = all(evaluated) if evaluated else None

    return report


def print_report(report: dict) -> None:
    m = report["metrics"]

    print(f"\n{'=' * 65}")
    print(f"  合同审查 Skill 运营质量报告")
    print(f"  {report['timestamp'][:19]} UTC")
    print(f"{'=' * 65}")
    print(f"  总 Trace 数: {report['total_traces']}  |  已触发: {report['triggered_traces']}")
    print()

    # KPI 1
    k1 = m["invocation_success_rate"]
    print(f"  1. 可调用成功率       {_badge(k1.get('pass'), k1['value'])}")
    if k1["value"] is not None:
        print(f"     {k1['value']:.1%}  ({k1['completed']}/{k1['count']} 成功)  阈值 >{k1['threshold']:.0%}")
    else:
        print(f"     无数据  阈值 >{k1['threshold']:.0%}")

    # KPI 2
    k2 = m["avg_response_time"]
    print(f"\n  2. 平均响应时间")
    if k2["count"]:
        s_non = _badge(k2.get("non_io_pass")) + f" {k2['non_io_s']}s" if k2["non_io_s"] else "  N/A"
        s_io = _badge(k2.get("io_pass")) + f" {k2['io_s']}s" if k2["io_s"] else "  N/A"
        print(f"     非IO: {s_non}  阈值 <{k2['threshold_non_io_s']}s")
        print(f"     IO:   {s_io}  阈值 <{k2['threshold_io_s']}s")
    else:
        print(f"     无数据  阈值: 非IO <{k2['threshold_non_io_s']}s, IO <{k2['threshold_io_s']}s")

    # KPI 3
    k3 = m["error_recovery_rate"]
    print(f"\n  3. 错误恢复率         {_badge(k3.get('pass'), k3['value'])}")
    if k3["value"] is not None:
        print(f"     {k3['value']:.1%}  ({k3['recovered']}/{k3['error_count']} 恢复)  阈值 >{k3['threshold']:.0%}")
    else:
        print(f"     无数据  阈值 >{k3['threshold']:.0%}")

    # KPI 4
    k4 = m["llm_selection_accuracy"]
    print(f"\n  4. LLM选用准确率      {_badge(k4.get('pass'), k4.get('value'))}")
    if k4.get("value") is not None:
        print(f"     {k4['value']:.1%}  ({k4.get('correct','?')}/{k4.get('total','?')} 正确)  阈值 >{k4['threshold']:.0%}")
        if k4.get("trigger_recall") is not None:
            print(f"     触发召回: {k4['trigger_recall']:.1%}  触发精确: {k4['trigger_precision']:.1%}")
            print(f"     TP={k4['tp']} FN={k4['fn']} FP={k4['fp']} TN={k4['tn']}")
    else:
        print(f"     无数据  阈值 >{k4['threshold']:.0%}")

    # KPI 5
    k5 = m["param_accuracy"]
    print(f"\n  5. 参数传递准确率     {_badge(k5.get('pass'), k5['value'])}")
    if k5["value"] is not None:
        print(f"     {k5['value']:.1%}  ({k5['correct_fields']}/{k5['total_fields']} 字段)  阈值 >{k5['threshold']:.0%}")
        for field, stats in k5.get("per_field", {}).items():
            print(f"     - {field}: {stats['correct']}/{stats['total']}")
    else:
        print(f"     无数据  阈值 >{k5['threshold']:.0%}")

    # KPI 6
    k6 = m["token_cost"]
    print(f"\n  6. Token 成本")
    if k6.get("count"):
        print(f"     总成本: ${k6['total_cost_usd']:.4f}  |  均次: ${k6['avg_cost_usd']:.4f}")
        print(f"     P50: ${k6['p50_cost_usd']:.4f}  |  P95: ${k6['p95_cost_usd']:.4f}")
        print(f"     总输入: {k6['total_input_tokens']:,}  |  总输出: {k6['total_output_tokens']:,}")
        print(f"     均次输入: {k6['avg_input_tokens']:,}  |  均次输出: {k6['avg_output_tokens']:,}")
        if k6.get("avg_context_used_pct") is not None:
            print(f"     上下文使用率: 均{k6['avg_context_used_pct']}%  P95={k6['p95_context_used_pct']}%")
    else:
        print(f"     无数据")

    # Overall
    overall = report.get("overall_pass")
    if overall is not None:
        print(f"\n  {'✅ 全部通过' if overall else '❌ 未达标'}")
    else:
        print(f"\n  ⏳ 数据不足")

    print(f"{'=' * 65}\n")


def _badge(passed: bool | None, value=None) -> str:
    if passed is None:
        return "⏳"
    return "✅" if passed else "❌"


# ═══════════════════════════════════════════════════════════════════════════
# 4. Single trace viewer
# ═══════════════════════════════════════════════════════════════════════════

def print_trace(trace: dict) -> None:
    """Pretty-print a single trace with span DAG."""
    summary = trace.get("summary", {})
    print(f"\n{'─' * 55}")
    print(f"  Trace: {trace['trace_id']}")
    print(f"  Session: {trace.get('session_id', '?')}")
    print(f"  Status: {trace.get('status', '?')}")
    print(f"  Duration: {summary.get('duration_ms', 0):.0f}ms")
    print(f"  IO: {'Yes' if summary.get('had_io') else 'No'}  |  "
          f"Spans: {summary.get('span_count', 0)}  |  "
          f"Errors: {summary.get('error_span_count', 0)}")
    print(f"  Token cost: ${summary.get('token_cost_usd', 0):.4f}  "
          f"(in={summary.get('input_tokens', 0):,} out={summary.get('output_tokens', 0):,})")
    print(f"  Params: {json.dumps(trace.get('params', {}), ensure_ascii=False)}")

    # Span tree
    print(f"\n  Span DAG:")
    spans = trace.get("spans", [])
    root_id = trace.get("root_span_id")
    child_map = {}
    for s in spans:
        pid = s.get("parent_span_id", "")
        child_map.setdefault(pid, []).append(s)

    def print_tree(span, indent=2):
        dur_ms = span.get("duration_ns", 0) / 1e6
        status_icon = "✓" if span["status"] == "OK" else "✗"
        tool = span.get("attributes", {}).get("tool_name", "")
        op = span["operation"]
        label = f"{tool} " if tool else ""
        print(f"{' ' * indent}├─ {status_icon} [{span['span_id']}] {label}{op}  {dur_ms:.0f}ms")
        if span["status"] == "ERROR":
            for evt in span.get("events", []):
                print(f"{' ' * (indent + 4)}⚠ {evt.get('message', '')[:80]}")
        for child in child_map.get(span["span_id"], []):
            print_tree(child, indent + 4)

    root = next((s for s in spans if s["span_id"] == root_id), None)
    if root:
        print_tree(root)
    print(f"{'─' * 55}\n")


# ═══════════════════════════════════════════════════════════════════════════
# 5. Seed data
# ═══════════════════════════════════════════════════════════════════════════

def seed_sample_traces() -> None:
    """Create realistic sample traces for testing."""
    OBS_DIR.mkdir(parents=True, exist_ok=True)
    now = int(time.time() * 1e9)

    samples = [
        # Successful IO trace
        {
            "trace_id": "a1b2c3d4e5f6a1b2",
            "session_id": "seed-io-001",
            "start_time": now,
            "end_time": now + 4_200_000_000,  # 4.2s
            "status": "TRIGGERED",
            "root_span_id": "r001",
            "spans": [
                {"span_id": "r001", "trace_id": "a1b2c3d4e5f6a1b2", "parent_span_id": None,
                 "operation": "skill.invocation", "start_time": now, "end_time": now + 4_200_000_000,
                 "duration_ns": 4_200_000_000, "status": "OK",
                 "attributes": {"session_id": "seed-io-001", "user_query": "审查这份采购合同，我是买方",
                                "trigger.detected": True, "trigger.confirmed": True, "skill.args": "采购合同审查"},
                 "events": [], "token_cost": {}},
                {"span_id": "s001", "trace_id": "a1b2c3d4e5f6a1b2", "parent_span_id": "r001",
                 "operation": "skill.load", "start_time": now + 100_000_000, "end_time": now + 200_000_000,
                 "duration_ns": 100_000_000, "status": "OK",
                 "attributes": {"tool_name": "Skill", "tool_input_summary": "contract-reviewer"},
                 "events": [], "token_cost": {"input_tokens": 5200, "output_tokens": 800}},
                {"span_id": "s002", "trace_id": "a1b2c3d4e5f6a1b2", "parent_span_id": "r001",
                 "operation": "io.read", "start_time": now + 300_000_000, "end_time": now + 800_000_000,
                 "duration_ns": 500_000_000, "status": "OK",
                 "attributes": {"tool_name": "Read", "tool_input_summary": "contract-4.txt"},
                 "events": [], "token_cost": {"input_tokens": 28000, "output_tokens": 0}},
                {"span_id": "s003", "trace_id": "a1b2c3d4e5f6a1b2", "parent_span_id": "r001",
                 "operation": "llm.inference", "start_time": now + 900_000_000, "end_time": now + 4_100_000_000,
                 "duration_ns": 3_200_000_000, "status": "OK",
                 "attributes": {"tool_name": "", "phase": "review_analysis"},
                 "events": [], "token_cost": {"input_tokens": 35000, "output_tokens": 12000,
                                              "total_cost_usd": 0.0342, "context_used_pct": 31.5}},
            ],
            "token_cost": {"total_cost_usd": 0.0342, "input_tokens": 68200, "output_tokens": 12800,
                           "context_used_pct": 31.5},
            "params": {"role": "买方", "contract_type": "采购合同", "focus_areas": ["违约责任", "付款条款"]},
            "summary": {"duration_ms": 4200.0, "had_io": True, "span_count": 4, "io_span_count": 1,
                        "error_span_count": 0, "triggered": True, "completed": True, "recovered": False,
                        "token_cost_usd": 0.0342, "input_tokens": 68200, "output_tokens": 12800,
                        "context_used_pct": 31.5},
        },
        # Trace with error that recovered
        {
            "trace_id": "f1e2d3c4b5a6f1e2",
            "session_id": "seed-recover-001",
            "start_time": now - 10_000_000_000,
            "end_time": now - 5_000_000_000,
            "status": "TRIGGERED",
            "root_span_id": "r002",
            "spans": [
                {"span_id": "r002", "trace_id": "f1e2d3c4b5a6f1e2", "parent_span_id": None,
                 "operation": "skill.invocation", "start_time": now - 10_000_000_000,
                 "end_time": now - 5_000_000_000, "duration_ns": 5_000_000_000, "status": "OK",
                 "attributes": {"trigger.detected": True, "trigger.confirmed": True},
                 "events": [], "token_cost": {}},
                {"span_id": "s010", "trace_id": "f1e2d3c4b5a6f1e2", "parent_span_id": "r002",
                 "operation": "io.read", "start_time": now - 9_800_000_000, "end_time": now - 9_500_000_000,
                 "duration_ns": 300_000_000, "status": "ERROR",
                 "attributes": {"tool_name": "Read"},
                 "events": [{"time": now - 9_500_000_000, "name": "error", "message": "文件路径错误"}],
                 "token_cost": {}},
                {"span_id": "s011", "trace_id": "f1e2d3c4b5a6f1e2", "parent_span_id": "r002",
                 "operation": "io.read", "start_time": now - 9_400_000_000, "end_time": now - 9_000_000_000,
                 "duration_ns": 400_000_000, "status": "OK",
                 "attributes": {"tool_name": "Read"},
                 "events": [], "token_cost": {"input_tokens": 25000}},
            ],
            "token_cost": {"total_cost_usd": 0.0280, "input_tokens": 25000, "output_tokens": 11000},
            "params": {"role": "需方", "contract_type": "购销协议"},
            "summary": {"duration_ms": 5000.0, "had_io": True, "span_count": 3, "io_span_count": 2,
                        "error_span_count": 1, "triggered": True, "completed": True, "recovered": True,
                        "token_cost_usd": 0.0280, "input_tokens": 25000, "output_tokens": 11000,
                        "context_used_pct": 18.0},
        },
        # NOT_TRIGGERED trace (false negative)
        {
            "trace_id": "n1o2t3t4r5i6g7g",
            "session_id": "seed-fn-001",
            "start_time": now - 20_000_000_000,
            "end_time": now - 20_000_000_000 + 100_000_000,
            "status": "NOT_TRIGGERED",
            "root_span_id": "r003",
            "spans": [
                {"span_id": "r003", "trace_id": "n1o2t3t4r5i6g7g", "parent_span_id": None,
                 "operation": "skill.invocation", "start_time": now - 20_000_000_000,
                 "end_time": now - 20_000_000_000 + 100_000_000, "duration_ns": 100_000_000, "status": "OK",
                 "attributes": {"trigger.detected": True, "trigger.confirmed": False,
                                "user_query": "审查这份合同，我是采购方"},
                 "events": [], "token_cost": {}},
            ],
            "token_cost": {},
            "params": {"role": "买方", "contract_type": "采购合同"},
            "summary": {"duration_ms": 100.0, "had_io": False, "span_count": 1, "io_span_count": 0,
                        "error_span_count": 0, "triggered": False, "completed": False, "recovered": False,
                        "token_cost_usd": 0, "input_tokens": 0, "output_tokens": 0, "context_used_pct": 0},
        },
    ]

    with open(TRACES_FILE, "a", encoding="utf-8") as f:
        for t in samples:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")

    with open(SPANS_FILE, "a", encoding="utf-8") as f:
        for t in samples:
            for s in t["spans"]:
                f.write(json.dumps(s, ensure_ascii=False) + "\n")

    print(f"Seeded {len(samples)} traces to {TRACES_FILE}")


# ═══════════════════════════════════════════════════════════════════════════
# 6. CLI
# ═══════════════════════════════════════════════════════════════════════════

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Operational metrics from traces")
    parser.add_argument("--report", action="store_true", help="Compute all 6 KPIs")
    parser.add_argument("--trace", type=str, help="Show a specific trace by ID")
    parser.add_argument("--traces", action="store_true", help="List recent traces")
    parser.add_argument("--seed", action="store_true", help="Seed sample traces")
    parser.add_argument("--all", action="store_true", help="Report + recent traces")
    args = parser.parse_args()

    if args.seed:
        # Reset
        if TRACES_FILE.exists():
            TRACES_FILE.unlink()
        if SPANS_FILE.exists():
            SPANS_FILE.unlink()
        seed_sample_traces()

    if args.report or args.all:
        report = generate_report()
        print_report(report)

        report_path = OBS_DIR / "operational_report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Report saved: {report_path}")

    if args.trace:
        traces = load_traces()
        found = [t for t in traces if t["trace_id"] == args.trace]
        if found:
            print_trace(found[0])
        else:
            print(f"Trace {args.trace} not found")

    if args.traces or args.all:
        traces = load_traces()
        if not traces:
            print("No traces yet. Run --seed for sample data.")
        else:
            print(f"\nRecent traces ({len(traces)} total):\n")
            print(f"  {'Trace ID':<18} {'Status':<14} {'Duration':<10} {'IO':<4} {'Cost':<8} {'Session'}")
            print(f"  {'─' * 18} {'─' * 14} {'─' * 10} {'─' * 4} {'─' * 8} {'─' * 20}")
            for t in traces[-20:]:
                s = t.get("summary", {})
                print(f"  {t['trace_id']:<18} {t.get('status','?'):<14} "
                      f"{s.get('duration_ms',0):>7.0f}ms  "
                      f"{'Y' if s.get('had_io') else 'N':<4} "
                      f"${s.get('token_cost_usd',0):.4f}  "
                      f"{t.get('session_id','?')[:20]}")


if __name__ == "__main__":
    main()
