"""
OpenAI-compatible API 可观测中间件
用于国产模型（GLM/Qwen/DeepSeek/Kimi/MiniMax）的 trace + metrics + logs 自动采集

用法：
    from openai_observer import observe_contract_review

    # 包装 OpenAI 兼容客户端
    result = observe_contract_review(
        client=openai.OpenAI(base_url="https://open.bigmodel.cn/api/paas/v4", api_key="..."),
        model="glm-4",
        contract_text="合同全文...",
        user_role="买方",
    )
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── 路径 ──────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent.parent.parent  # skills/contract-reviewer/scripts/ → 项目根
OBS_DIR = ROOT / "contract-reviewer-workspace" / "observability"
TRACES_FILE = OBS_DIR / "traces.jsonl"
SPANS_FILE = OBS_DIR / "spans.jsonl"


def _ensure_dir() -> None:
    OBS_DIR.mkdir(parents=True, exist_ok=True)


def _write_jsonl(path: Path, record: dict) -> None:
    _ensure_dir()
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Token 成本估算（国产模型价格参考 2026-06） ──────
MODEL_PRICING = {
    # model_prefix: (input_per_1m, output_per_1m) in CNY
    "glm-4": (100, 100),
    "glm-4-flash": (1, 1),
    "qwen-max": (20, 60),
    "qwen-plus": (4, 8),
    "qwen-turbo": (2, 6),
    "deepseek-chat": (1, 2),
    "deepseek-reasoner": (4, 16),
    "moonshot-v1": (12, 12),
    "abab6.5s-chat": (1, 1),
}


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """估算 Token 成本（CNY），长前缀优先匹配避免子串误匹配"""
    for prefix, (inp, out) in sorted(MODEL_PRICING.items(), key=lambda x: -len(x[0])):
        if model.startswith(prefix):
            return (input_tokens * inp + output_tokens * out) / 1_000_000
    # 未知模型用保守估算
    return (input_tokens * 50 + output_tokens * 50) / 1_000_000


# ── 核心函数 ───────────────────────────────────────
def observe_contract_review(
    client: Any,
    model: str,
    contract_text: str,
    user_role: str = "未明确",
    contract_type: str = "买卖合同",
    focus_areas: list[str] | None = None,
    extra_system_prompt: str = "",
) -> dict:
    """
    执行合同审查并自动采集 trace/metrics/logs。

    返回: {
        "trace_id": "...",
        "result": "审查报告文本",
        "metrics": {
            "response_time_ms": ...,
            "input_tokens": ...,
            "output_tokens": ...,
            "cost_cny": ...,
        }
    }
    """
    trace_id = f"tr_{uuid.uuid4().hex[:16]}"
    span_id = f"sp_{uuid.uuid4().hex[:16]}"
    start_time = time.time()
    start_iso = _now_iso()

    # 读取 system prompt
    system_prompt_path = Path(__file__).resolve().parent.parent / "adapters" / "system-prompt.md"
    system_prompt = system_prompt_path.read_text(encoding="utf-8") if system_prompt_path.exists() else ""
    if extra_system_prompt:
        system_prompt = f"{system_prompt}\n\n{extra_system_prompt}"

    # 构造用户消息
    user_msg = f"请审查以下合同：\n\n{contract_text}"
    if user_role != "未明确":
        user_msg = f"我方角色：{user_role}\n\n{user_msg}"
    if focus_areas:
        user_msg = f"重点关注：{'、'.join(focus_areas)}\n\n{user_msg}"

    # 工具定义（可选，支持 function calling 的模型）
    try:
        tools_path = Path(__file__).resolve().parent.parent / "adapters" / "openai-function.json"
        tools = [json.loads(tools_path.read_text(encoding="utf-8"))] if tools_path.exists() else None
    except Exception:
        tools = None

    # 调用 LLM
    error_events = []
    result_text = ""
    recovered = False
    usage = {}

    try:
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            "temperature": 0.1,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        response = client.chat.completions.create(**kwargs)

        choice = response.choices[0]
        result_text = choice.message.content or ""

        # 提取 token 用量
        if hasattr(response, "usage") and response.usage:
            usage = {
                "input_tokens": response.usage.prompt_tokens or 0,
                "output_tokens": response.usage.completion_tokens or 0,
                "total_tokens": response.usage.total_tokens or 0,
            }

    except Exception as e:
        error_events.append({
            "event": "error",
            "timestamp": _now_iso(),
            "error_type": type(e).__name__,
            "error_message": str(e),
        })
        # 尝试恢复：不带 tools 重试
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_msg},
                ],
                temperature=0.1,
            )
            result_text = response.choices[0].message.content or ""
            recovered = True
            if hasattr(response, "usage") and response.usage:
                usage = {
                    "input_tokens": response.usage.prompt_tokens or 0,
                    "output_tokens": response.usage.completion_tokens or 0,
                    "total_tokens": response.usage.total_tokens or 0,
                }
        except Exception as e2:
            error_events.append({
                "event": "recovery_failed",
                "timestamp": _now_iso(),
                "error_type": type(e2).__name__,
                "error_message": str(e2),
            })

    end_time = time.time()
    end_iso = _now_iso()
    duration_ms = round((end_time - start_time) * 1000)

    # 计算 token 成本
    cost_cny = _estimate_cost(model, usage.get("input_tokens", 0), usage.get("output_tokens", 0))

    # 写入 trace
    trace_record = {
        "trace_id": trace_id,
        "root_span_id": span_id,
        "start_time": start_iso,
        "end_time": end_iso,
        "duration_ms": duration_ms,
        "status": "ERROR" if error_events and not recovered else "OK",
        "model": model,
        "triggered": True,
        "completed": bool(result_text),
        "had_io": True,
        "errors": error_events,
        "recovered": recovered,
        "params": {
            "role": user_role,
            "contract_type": contract_type,
            "focus_areas": focus_areas or [],
        },
        "token_usage": usage,
        "token_cost_cny": round(cost_cny, 6),
    }
    _write_jsonl(TRACES_FILE, trace_record)

    # 写入 span
    span_record = {
        "span_id": span_id,
        "trace_id": trace_id,
        "parent_id": None,
        "operation": "contract_review",
        "start_time": start_iso,
        "end_time": end_iso,
        "duration_ms": duration_ms,
        "status": trace_record["status"],
        "model": model,
        "events": error_events or [{"event": "completed", "timestamp": end_iso}],
        "token_usage": usage,
    }
    _write_jsonl(SPANS_FILE, span_record)

    return {
        "trace_id": trace_id,
        "result": result_text,
        "metrics": {
            "response_time_ms": duration_ms,
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
            "cost_cny": round(cost_cny, 6),
            "recovered": recovered,
            "errors": len(error_events),
        },
    }


# ── CLI 快速测试 ───────────────────────────────────
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("用法: python openai-observer.py <合同文件路径> [--model MODEL] [--role ROLE]")
        print("示例: python openai-observer.py contract.txt --model glm-4-flash --role 买方")
        sys.exit(1)

    # 解析参数
    args = sys.argv[1:]
    contract_file = args[0]
    model = "glm-4-flash"
    role = "未明确"

    i = 1
    while i < len(args):
        if args[i] == "--model" and i + 1 < len(args):
            model = args[i + 1]
            i += 2
        elif args[i] == "--role" and i + 1 < len(args):
            role = args[i + 1]
            i += 2
        else:
            i += 1

    # 读取合同文件
    try:
        contract_text = Path(contract_file).read_text(encoding="utf-8")
    except Exception as e:
        print(f"无法读取合同文件: {e}")
        sys.exit(1)

    # 需要 openai 库
    try:
        import os
        import openai
    except ImportError:
        print("需要安装 openai 库: pip install openai")
        sys.exit(1)

    # 从环境变量获取 API 配置
    base_url = os.environ.get("OPENAI_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
    api_key = os.environ.get("OPENAI_API_KEY", "")

    if not api_key:
        print("请设置 OPENAI_API_KEY 环境变量")
        sys.exit(1)

    client = openai.OpenAI(base_url=base_url, api_key=api_key)

    print(f"正在审查合同 ({len(contract_text)} 字)...")
    print(f"模型: {model}, 角色: {role}")

    result = observe_contract_review(
        client=client,
        model=model,
        contract_text=contract_text,
        user_role=role,
    )

    print(f"\n{'='*60}")
    print(f"Trace ID: {result['trace_id']}")
    print(f"耗时: {result['metrics']['response_time_ms']}ms")
    print(f"Token: {result['metrics']['input_tokens']}in + {result['metrics']['output_tokens']}out")
    print(f"成本: ¥{result['metrics']['cost_cny']}")
    print(f"{'='*60}\n")
    print(result["result"])
