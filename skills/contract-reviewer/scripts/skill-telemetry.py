#!/usr/bin/env python3
from __future__ import annotations
"""Production-grade observability for contract-reviewer skill.

Three pillars: Trace + Metrics + Logs
- AOP via Claude Code hooks — zero intrusion into SKILL.md
- OpenTelemetry-inspired trace/span model
- Token cost tracking from hook cost/context_window payloads

Data flow:
  Hook events → Trace Collector (this script) → traces.jsonl
                                                    ↓
                                              Metrics Aggregator
                                              (operational_metrics.py)

Storage:
  traces.jsonl    — one JSON object per trace (with nested spans)
  spans.jsonl     — flat span log for fast querying
  state.json      — mutable in-flight state (hook correlation)

Trace lifecycle:
  1. UserPromptSubmit  → detect trigger intent → start trace
  2. PreToolUse/Skill  → create skill_load span
  3. PreToolUse/Read   → create io_read span
  4. PostToolUse/Read  → end io_read span, capture tokens
  5. Stop              → end trace, flush to disk
  6. StopFailure       → end trace as ERROR, flush
"""

import hashlib
import json
import os
import sys
import time
import uuid
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parent.parent.parent.parent  # skills/contract-reviewer/scripts/ → project root
OBS_DIR = ROOT / "contract-reviewer-workspace" / "observability"
STATE_FILE = OBS_DIR / "state.json"
TRACES_FILE = OBS_DIR / "traces.jsonl"
SPANS_FILE = OBS_DIR / "spans.jsonl"

SKILL_NAME = "contract-reviewer"
IO_TOOLS = {"Read", "Grep", "Glob"}

# Span operation names
OP_TRACE_ROOT = "skill.invocation"
OP_SKILL_LOAD = "skill.load"
OP_IO_READ = "io.read"
OP_IO_SEARCH = "io.search"
OP_IO_GLOB = "io.glob"
OP_LLM_CALL = "llm.inference"
OP_UNKNOWN_TOOL = "tool.unknown"


# ── ID generation ──────────────────────────────────────────────────────────

def gen_trace_id() -> str:
    """16-char hex trace ID."""
    return uuid.uuid4().hex[:16]

def gen_span_id() -> str:
    """8-char hex span ID."""
    return uuid.uuid4().hex[:8]


# ── State management ──────────────────────────────────────────────────────

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError):
            pass
    return {}

def save_state(state: dict) -> None:
    OBS_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


# ── Span helpers ──────────────────────────────────────────────────────────

def tool_to_operation(tool_name: str) -> str:
    """Map tool name to span operation name."""
    return {
        "Skill": OP_SKILL_LOAD,
        "Read": OP_IO_READ,
        "Grep": OP_IO_SEARCH,
        "Glob": OP_IO_GLOB,
    }.get(tool_name, OP_UNKNOWN_TOOL)


def make_span(
    span_id: str,
    trace_id: str,
    operation: str,
    parent_span_id: str | None = None,
    start_time_ns: int = 0,
    end_time_ns: int = 0,
    status: str = "OK",
    attributes: dict | None = None,
    events: list | None = None,
    token_cost: dict | None = None,
) -> dict:
    """Create a span following OpenTelemetry convention."""
    return {
        "span_id": span_id,
        "trace_id": trace_id,
        "parent_span_id": parent_span_id,
        "operation": operation,
        "start_time": start_time_ns,
        "end_time": end_time_ns,
        "duration_ns": end_time_ns - start_time_ns if start_time_ns and end_time_ns else 0,
        "status": status,
        "attributes": attributes or {},
        "events": events or [],
        "token_cost": token_cost or {},
    }


# ── Token cost extraction ─────────────────────────────────────────────────

def extract_token_cost(data: dict) -> dict:
    """Extract token usage and cost from hook payload."""
    cost = data.get("cost", {})
    cw = data.get("context_window", {})

    result = {}
    if cost:
        result["total_cost_usd"] = cost.get("total_cost_usd", 0)
        result["total_duration_ms"] = cost.get("total_duration_ms", 0)
    if cw:
        result["input_tokens"] = cw.get("total_input_tokens", 0)
        result["output_tokens"] = cw.get("total_output_tokens", 0)
        result["context_window_size"] = cw.get("context_window_size", 0)
        result["context_used_pct"] = round(cw.get("used_percentage", 0), 2)
    return result


# ── Flush trace to disk ───────────────────────────────────────────────────

def flush_trace(trace: dict) -> None:
    """Write completed trace + flattened spans to disk."""
    OBS_DIR.mkdir(parents=True, exist_ok=True)

    # Write full trace
    with open(TRACES_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(trace, ensure_ascii=False) + "\n")

    # Write flattened spans
    with open(SPANS_FILE, "a", encoding="utf-8") as f:
        for span in trace.get("spans", []):
            f.write(json.dumps(span, ensure_ascii=False) + "\n")


# ── Param extraction ──────────────────────────────────────────────────────

def extract_params(text: str) -> dict:
    """Best-effort extraction of role, contract_type, focus_areas."""
    params = {}
    if not text:
        return params

    role_map = {
        "买方": "买方", "采购方": "买方", "需方": "买方", "甲方": "甲方",
        "卖方": "卖方", "供方": "卖方", "乙方": "乙方",
    }
    for pattern, role in role_map.items():
        if pattern in text:
            params["role"] = role
            break

    type_map = {
        "采购合同": "采购合同", "买卖合同": "买卖合同",
        "购销协议": "购销协议", "购销合同": "购销协议",
    }
    for pattern, ctype in type_map.items():
        if pattern in text:
            params["contract_type"] = ctype
            break

    focus_kw = [
        "违约责任", "付款条款", "验收", "质保", "交付", "免责条款",
        "赔偿上限", "争议解决", "知识产权", "保密", "变更解除",
        "标的物", "价款", "异议期", "风险转移",
    ]
    found = [kw for kw in focus_kw if kw in text]
    if found:
        params["focus_areas"] = found

    return params


# ── Trigger pattern detection ─────────────────────────────────────────────

TRIGGER_KEYWORDS = ["审查", "审一下", "检查", "找坑", "看风险", "条款审查", "风险审查", "review"]
CONTRACT_KEYWORDS = ["采购合同", "买卖合同", "购销协议", "购销合同", "合同"]
EXCLUDE_KEYWORDS = ["写", "起草", "翻译", "租赁", "劳动", "股权", "保密协议", "NDA", "加州", "美国"]


def should_trigger(message: str) -> bool:
    """Heuristic: does this prompt intend to trigger contract-reviewer?"""
    has_trigger = any(kw in message for kw in TRIGGER_KEYWORDS)
    has_contract = any(kw in message for kw in CONTRACT_KEYWORDS)
    has_exclude = any(kw in message for kw in EXCLUDE_KEYWORDS)
    return has_trigger and has_contract and not has_exclude


# ═══════════════════════════════════════════════════════════════════════════
# Hook handlers — AOP entry points
# ═══════════════════════════════════════════════════════════════════════════

def handle_user_prompt(data: dict) -> None:
    """UserPromptSubmit → detect trigger intent, start trace if matched."""
    message = data.get("prompt", "") or data.get("message", "")
    if not message or not should_trigger(message):
        return

    session_id = data.get("session_id", "") or os.environ.get("CLAUDE_IDE_SESSION_ID", "") or data.get("agent_id", "") or f"sid-{int(time.time())}"
    now_ns = int(time.time() * 1e9)
    trace_id = gen_trace_id()
    root_span_id = gen_span_id()

    # Create root span
    root_span = make_span(
        span_id=root_span_id,
        trace_id=trace_id,
        operation=OP_TRACE_ROOT,
        start_time_ns=now_ns,
        attributes={
            "session_id": session_id,
            "user_query": message[:300],
            "trigger.detected": True,
            "trigger.source": "heuristic",
        },
        token_cost=extract_token_cost(data),
    )

    # Create in-flight trace
    trace = {
        "trace_id": trace_id,
        "session_id": session_id,
        "start_time": now_ns,
        "end_time": None,
        "status": "IN_PROGRESS",
        "root_span_id": root_span_id,
        "spans": [root_span],
        "token_cost": extract_token_cost(data),
        "params": extract_params(message),
    }

    state = load_state()
    state["active_trace"] = trace
    state["span_index"] = {root_span_id: 0}  # span_id → index in trace.spans
    save_state(state)


def handle_pre_tool_use(data: dict) -> None:
    """PreToolUse → start a child span for the tool call."""
    state = load_state()
    trace = state.get("active_trace")
    if not trace:
        return

    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {}) or {}
    now_ns = int(time.time() * 1e9)

    # For Skill(contract-reviewer): mark trigger confirmed
    if tool_name == "Skill" and SKILL_NAME in tool_input.get("skill", ""):
        trace["status"] = "TRIGGERED"
        # Update root span attributes
        root_idx = state["span_index"].get(trace["root_span_id"])
        if root_idx is not None:
            trace["spans"][root_idx]["attributes"]["trigger.confirmed"] = True
            trace["spans"][root_idx]["attributes"]["skill.args"] = tool_input.get("args", "")[:300]

    # Create child span
    span_id = gen_span_id()
    parent_id = trace["root_span_id"]
    span = make_span(
        span_id=span_id,
        trace_id=trace["trace_id"],
        operation=tool_to_operation(tool_name),
        parent_span_id=parent_id,
        start_time_ns=now_ns,
        attributes={
            "tool_name": tool_name,
            "tool_input_summary": json.dumps(tool_input, ensure_ascii=False)[:500] if tool_input else "",
        },
        token_cost=extract_token_cost(data),
    )

    state["span_index"][span_id] = len(trace["spans"])
    trace["spans"].append(span)
    trace["token_cost"] = _merge_token_cost(trace.get("token_cost", {}), extract_token_cost(data))
    save_state(state)


def handle_post_tool_use(data: dict) -> None:
    """PostToolUse → end the matching span, record result + token delta."""
    state = load_state()
    trace = state.get("active_trace")
    if not trace:
        return

    tool_name = data.get("tool_name", "")
    now_ns = int(time.time() * 1e9)
    token_delta = extract_token_cost(data)

    # Find the most recent unmatched span for this tool
    span_idx = None
    for idx in range(len(trace["spans"]) - 1, -1, -1):
        s = trace["spans"][idx]
        if s["attributes"].get("tool_name") == tool_name and s["end_time"] == 0:
            span_idx = idx
            break

    if span_idx is not None:
        span = trace["spans"][span_idx]
        span["end_time"] = now_ns
        span["duration_ns"] = now_ns - span["start_time"]

        # Record result
        error = data.get("error", "")
        if error:
            span["status"] = "ERROR"
            span["events"].append({"time": now_ns, "name": "error", "message": error[:300]})
        else:
            span["status"] = "OK"

        # Record token delta for this span
        span["token_cost"] = token_delta

    trace["token_cost"] = _merge_token_cost(trace.get("token_cost", {}), token_delta)
    save_state(state)


def handle_stop(data: dict) -> None:
    """Stop → finalize trace as OK."""
    state = load_state()
    trace = state.get("active_trace")
    if not trace:
        return

    now_ns = int(time.time() * 1e9)
    trace["end_time"] = now_ns
    trace["status"] = trace.get("status", "COMPLETED")

    # If never confirmed as TRIGGERED, mark as NOT_TRIGGERED (false positive heuristic)
    if trace["status"] == "IN_PROGRESS":
        trace["status"] = "NOT_TRIGGERED"

    # End root span
    root_idx = state.get("span_index", {}).get(trace["root_span_id"])
    if root_idx is not None:
        root = trace["spans"][root_idx]
        root["end_time"] = now_ns
        root["duration_ns"] = now_ns - root["start_time"]

    # Final token cost
    trace["token_cost"] = _merge_token_cost(trace.get("token_cost", {}), extract_token_cost(data))

    # Compute summary metrics on the trace itself
    trace["summary"] = _compute_trace_summary(trace)

    flush_trace(trace)

    # Clean state
    del state["active_trace"]
    del state["span_index"]
    save_state(state)


def handle_stop_failure(data: dict) -> None:
    """StopFailure → finalize trace as ERROR."""
    state = load_state()
    trace = state.get("active_trace")
    if not trace:
        return

    now_ns = int(time.time() * 1e9)
    trace["end_time"] = now_ns
    trace["status"] = "ERROR"

    # End all open spans
    for span in trace["spans"]:
        if span["end_time"] == 0:
            span["end_time"] = now_ns
            span["duration_ns"] = now_ns - span["start_time"]
            span["status"] = "ERROR"
        span["events"].append({"time": now_ns, "name": "stop_failure", "message": data.get("error", "")[:300]})

    trace["token_cost"] = _merge_token_cost(trace.get("token_cost", {}), extract_token_cost(data))
    trace["summary"] = _compute_trace_summary(trace)

    flush_trace(trace)

    del state["active_trace"]
    del state["span_index"]
    save_state(state)


# ── Helpers ───────────────────────────────────────────────────────────────

def _merge_token_cost(base: dict, delta: dict) -> dict:
    """Merge token cost delta into base. Keeps the latest snapshot values."""
    merged = dict(base)
    for k, v in delta.items():
        if k in ("total_cost_usd", "total_duration_ms", "input_tokens", "output_tokens"):
            # These are cumulative — keep the larger value
            merged[k] = max(merged.get(k, 0), v)
        else:
            merged[k] = v
    return merged


def _compute_trace_summary(trace: dict) -> dict:
    """Derive summary metrics from a completed trace."""
    spans = trace.get("spans", [])
    io_spans = [s for s in spans if s["operation"].startswith("io.")]
    error_spans = [s for s in spans if s["status"] == "ERROR"]
    root = next((s for s in spans if s["span_id"] == trace.get("root_span_id")), None)

    duration_ms = (root["duration_ns"] / 1e6) if root and root.get("duration_ns") else 0
    had_io = len(io_spans) > 0
    token_cost = trace.get("token_cost", {})

    return {
        "duration_ms": round(duration_ms, 1),
        "had_io": had_io,
        "span_count": len(spans),
        "io_span_count": len(io_spans),
        "error_span_count": len(error_spans),
        "triggered": trace.get("status") in ("TRIGGERED", "COMPLETED", "ERROR"),
        "completed": trace.get("status") in ("TRIGGERED", "COMPLETED"),
        "recovered": len(error_spans) > 0 and trace.get("status") != "ERROR",
        "token_cost_usd": token_cost.get("total_cost_usd", 0),
        "input_tokens": token_cost.get("input_tokens", 0),
        "output_tokens": token_cost.get("output_tokens", 0),
        "context_used_pct": token_cost.get("context_used_pct", 0),
    }


# ═══════════════════════════════════════════════════════════════════════════
# Main dispatch
# ═══════════════════════════════════════════════════════════════════════════

HANDLERS = {
    "PreToolUse": handle_pre_tool_use,
    "PostToolUse": handle_post_tool_use,
    "Stop": handle_stop,
    "StopFailure": handle_stop_failure,
    "UserPromptSubmit": handle_user_prompt,
}


def main():
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    hook_event = sys.argv[1] if len(sys.argv) > 1 else ""
    if not hook_event:
        if "tool_name" in data:
            hook_event = "PostToolUse"
        elif "message" in data:
            hook_event = "UserPromptSubmit"

    handler = HANDLERS.get(hook_event)
    if handler:
        handler(data)


if __name__ == "__main__":
    main()
