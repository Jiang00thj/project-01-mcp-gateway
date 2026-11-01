"""Bridge between the gateway and a chat model.

Two things here that are easy to get wrong and worth explaining in an interview:

1. **Name mapping.** MCP allows dots in tool names (`campus.search_courses`);
   OpenAI-compatible endpoints only allow `[A-Za-z0-9_-]` and reject the whole
   request otherwise. So the export layer rewrites names and keeps a reverse map.
2. **Approval inside the loop.** A gateway that raises `ApprovalRequired` is
   useless if the agent loop treats it as a hard failure. The loop has to pause,
   ask a human, and resume with the same call — without losing the trajectory.

`ScriptedModel` exists so the whole loop can be tested and benchmarked offline;
`OpenAIModel` is the real thing and needs BENCH_API_KEY.
"""

import json
import os
import re
import time
import urllib.request

from gateway.gateway import ApprovalRequired, BudgetExceeded, PermissionDenied

SAFE_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# USD per 1M tokens (input, output). Only used for cost accounting; edit freely.
PRICES = {
    "gpt-4o-mini": (0.15, 0.60),
    "deepseek-chat": (0.27, 1.10),
    "qwen-plus": (0.40, 1.20),
}

SYSTEM_PROMPT = (
    "你是一个通过 MCP 工具网关工作的助手。工具名格式为 server__tool。"
    "只调用与任务直接相关的工具，参数必须符合 schema。"
    "如果工具返回错误，先读错误信息再决定是否重试，最多重试一次。"
    "信息足够时直接给出答案，不要重复调用同一个工具。"
)


def export_tools(tools):
    """gateway.list_tools() -> (openai_tool_schemas, reverse_map)."""
    schemas, reverse = [], {}
    for tool in tools:
        safe = tool["name"].replace(".", "__")
        if not SAFE_NAME.match(safe):
            raise ValueError(f"tool name {tool['name']!r} cannot be expressed as a function name")
        reverse[safe] = tool["name"]
        schemas.append({
            "type": "function",
            "function": {
                "name": safe,
                "description": (tool.get("description") or "")[:512],
                "parameters": tool.get("inputSchema") or {"type": "object", "properties": {}},
            },
        })
    return schemas, reverse


class ScriptedModel:
    """Deterministic stand-in: replays a fixed plan instead of calling a model.

    Used by `demo.py` and `bench.py e2e` so the loop can be verified with no API
    key and no network. Never mix its numbers with real-model numbers.
    """

    name = "scripted"

    def __init__(self, plan):
        self.plan = list(plan)
        self.cursor = 0

    def chat(self, messages, tools):
        if self.cursor >= len(self.plan):
            return {"content": "（脚本已用尽）", "tool_calls": [], "usage": {}}
        step = self.plan[self.cursor]
        self.cursor += 1
        if step[0] == "call":
            _, name, args = step
            return {
                "content": None,
                "tool_calls": [{"id": f"call_{self.cursor}", "name": name,
                                "arguments": json.dumps(args, ensure_ascii=False)}],
                "usage": {"prompt_tokens": 320, "completion_tokens": 48},
            }
        return {"content": step[1], "tool_calls": [],
                "usage": {"prompt_tokens": 380, "completion_tokens": 96}}


class OpenAIModel:
    """Any OpenAI-compatible endpoint (OpenAI, DeepSeek, Qwen, vLLM...)."""

    def __init__(self, model=None, api_key=None, base_url=None, temperature=0.0):
        self.model = model or os.environ.get("BENCH_MODEL", "gpt-4o-mini")
        self.api_key = api_key or os.environ.get("BENCH_API_KEY")
        self.base_url = (base_url or os.environ.get("BENCH_BASE_URL")
                         or "https://api.openai.com/v1").rstrip("/")
        self.temperature = temperature
        if not self.api_key:
            raise RuntimeError("缺少 BENCH_API_KEY，无法调用真实模型")
        self.name = f"openai:{self.model}"

    def chat(self, messages, tools):
        payload = {"model": self.model, "messages": messages, "temperature": self.temperature}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.api_key}"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            body = json.loads(response.read())
        message = body["choices"][0]["message"]
        calls = [
            {"id": call["id"], "name": call["function"]["name"],
             "arguments": call["function"].get("arguments") or "{}"}
            for call in message.get("tool_calls") or []
        ]
        return {"content": message.get("content"), "tool_calls": calls,
                "usage": body.get("usage") or {}}


def run_loop(gateway, principal, task, model, max_steps=8, on_approval=None, system=SYSTEM_PROMPT):
    """Drive one task end to end. Returns an auditable trace, not just an answer."""
    tools = gateway.list_tools(principal)
    schemas, reverse = export_tools(tools)
    messages = [{"role": "system", "content": system}, {"role": "user", "content": task}]

    trace = []
    usage = {"prompt_tokens": 0, "completion_tokens": 0}
    started = time.perf_counter()
    answer = None

    for step in range(1, max_steps + 1):
        reply = model.chat(messages, schemas)
        for key in ("prompt_tokens", "completion_tokens"):
            usage[key] += int((reply.get("usage") or {}).get(key, 0))
        calls = reply.get("tool_calls") or []

        if not calls:
            answer = reply.get("content") or ""
            trace.append({"step": step, "type": "answer", "text": answer[:200]})
            break

        messages.append({
            "role": "assistant",
            "content": reply.get("content"),
            "tool_calls": [{"id": call["id"], "type": "function",
                            "function": {"name": call["name"], "arguments": call["arguments"]}}
                           for call in calls],
        })

        for call in calls:
            # A tool the principal cannot see is not in `reverse`. Fall back to
            # rebuilding the qualified name so the call still goes through
            # authorization and comes back as a clean denial. Skipping the check
            # would let a hallucinated name bypass the policy layer entirely.
            qualified = reverse.get(call["name"]) or call["name"].replace("__", ".", 1)
            try:
                arguments = json.loads(call["arguments"] or "{}")
            except json.JSONDecodeError:
                arguments = {}

            outcome, payload = _execute(gateway, principal, qualified, arguments, on_approval)
            trace.append({"step": step, "type": "tool", "tool": qualified,
                          "arguments": arguments, "outcome": outcome,
                          "preview": json.dumps(payload, ensure_ascii=False)[:160]})
            messages.append({"role": "tool", "tool_call_id": call["id"],
                             "content": json.dumps(payload, ensure_ascii=False)[:4000]})

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    return {
        "answer": answer,
        "steps": len([item for item in trace if item["type"] != "answer"]) or len(trace),
        "tool_calls": sum(1 for item in trace if item["type"] == "tool"),
        "approvals": sum(1 for item in trace if item.get("outcome") == "approved"),
        "denied": sum(1 for item in trace if item.get("outcome") in ("denied", "approval_denied")),
        "errors": sum(1 for item in trace if item.get("outcome") == "error"),
        "latency_ms": elapsed_ms,
        "usage": usage,
        "cost_usd": estimate_cost(model.name, usage),
        "trace": trace,
    }


def _execute(gateway, principal, qualified, arguments, on_approval):
    """One tool call, with the approval gate treated as a pause rather than a failure."""
    try:
        result = gateway.call(principal, qualified, arguments)
        return "ok", result.get("content", [{}])[0].get("text", "")
    except ApprovalRequired as exc:
        if on_approval and on_approval(exc):
            result = gateway.call(principal, qualified, arguments, approved=True)
            return "approved", result.get("content", [{}])[0].get("text", "")
        return "approval_denied", {"error": "用户拒绝授权，换一种不需要该权限的做法"}
    except PermissionDenied as exc:
        return "denied", {"error": str(exc)}
    except BudgetExceeded as exc:
        return "error", {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return "error", {"error": str(exc)}


def estimate_cost(model_name, usage):
    for key, (in_price, out_price) in PRICES.items():
        if key in model_name:
            return round(usage["prompt_tokens"] / 1e6 * in_price
                         + usage["completion_tokens"] / 1e6 * out_price, 6)
    return None
