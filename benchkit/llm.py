"""LLM adapter for the tool-selection benchmark.

Two providers:
  mock    - keyword scorer, no network. Only proves the plumbing works.
            NEVER put mock numbers on a resume.
  openai  - any OpenAI-compatible endpoint (DeepSeek / Qwen / GLM / local vLLM).

The openai provider calls the **native function-calling interface**, not a prompt
that asks the model to "reply with a tool name". There are three reasons:

  1. Production agents use tool_calls, so measuring a prompt hack measures
     nothing that ships.
  2. Tool names must be mangled: MCP allows `campus.search_courses`, OpenAI
     requires `^[a-zA-Z0-9_-]{1,64}$`. Doing the mapping here keeps the rest of
     the codebase on MCP names.
  3. usage is returned per call, so the cost column is measured, not guessed.

Reasoning models spend completion tokens on reasoning_content before emitting
anything user-visible. With max_tokens=32 the tool name gets cut in half
(`notice.publish_not`), so the budget is generous and the mangle/unmangle step
tolerates truncated names.
"""

import json
import os
import re
import urllib.error
import urllib.request

KEYWORDS = {
    "campus.search_courses": ["课", "老师", "学分", "名额", "余量", "开课"],
    "campus.get_grades": ["成绩", "分数", "考了", "成绩单"],
    "campus.compute_gpa": ["绩点", "gpa", "GPA", "加权", "保研"],
    "campus.room_availability": ["空教室", "空闲", "自习", "教室", "研讨间"],
    "campus.submit_course_change": ["退课", "退掉", "选课", "我要选", "报名", "提交申请"],
    "library.search_books": ["书", "馆藏", "索书号"],
    "library.borrow_book": ["借书", "借出", "借阅", "借一下", "帮我借"],
    "notice.search_notices": ["通知", "公告", "有没有说"],
    "notice.publish_notice": ["发布", "发到全校", "全校"],
}


def to_openai_name(qualified):
    """campus.search_courses -> campus__search_courses"""
    return qualified.replace(".", "__")


def from_openai_name(name):
    """campus__search_courses -> campus.search_courses (first separator only)."""
    return name.replace("__", ".", 1)


class MockModel:
    name = "mock-keyword-scorer"
    usage = {"prompt": 0, "completion": 0, "reasoning": 0, "calls": 0}

    def pick_tool(self, instruction, tools):
        self.usage["calls"] += 1
        best, best_score = None, 0
        for tool in tools:
            score = sum(1 for word in KEYWORDS.get(tool["name"], []) if word in instruction)
            if score > best_score:
                best, best_score = tool["name"], score
        return best or tools[0]["name"]


class OpenAICompatModel:
    def __init__(self, model=None, base_url=None, api_key=None,
                 thinking=None, max_tokens=512):
        self.model = model or os.environ.get("BENCH_MODEL", "deepseek-flash")
        self.base_url = (base_url or os.environ.get("BENCH_BASE_URL")
                         or "https://api.deepseek.com/v1").rstrip("/")
        self.api_key = api_key or os.environ.get("BENCH_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")
        if not self.api_key:
            raise SystemExit("缺少 API key：设置 BENCH_API_KEY 或 DEEPSEEK_API_KEY，或改用 --provider mock")
        self.thinking = thinking      # None = leave it to the server; False = disable
        self.max_tokens = max_tokens
        self.tool_choice = "required"
        self.degraded = False
        self.last_raw = None
        self.usage = {"prompt": 0, "completion": 0, "reasoning": 0, "calls": 0}
        mode = "thinking:on" if thinking is not False else "thinking:off"
        self.name = f"openai:{self.model}/{mode}"

    # ------------------------------------------------------------------ wire
    def _schema(self, tool):
        params = tool.get("inputSchema") or {"type": "object", "properties": {}}
        clean = {k: v for k, v in params.items() if k != "$schema"}
        if clean.get("type") != "object":
            clean = {"type": "object", "properties": {}}
        return {"type": "function",
                "function": {"name": to_openai_name(tool["name"]),
                             "description": tool.get("description", "")[:1000],
                             "parameters": clean}}

    def _post(self, payload, allow_tool_choice_fallback=False):
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.api_key}"},
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            # Reasoning models reject tool_choice=required with HTTP 400
            # ("Thinking mode does not support this tool_choice"). Falling back
            # to auto keeps the run alive, but it is a *changed condition*, so
            # it is surfaced in the report instead of hidden.
            if (allow_tool_choice_fallback and exc.code == 400
                    and "tool_choice" in detail):
                payload["tool_choice"] = "auto"
                self.tool_choice = "auto"      # must stay a legal value, it is reused
                self.degraded = True
                return self._post(payload)
            raise SystemExit(f"API 报错 {exc.code}: {detail[:400]!r}") from exc

    # ------------------------------------------------------------------ call
    def pick_tool(self, instruction, tools):
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system",
                 "content": "你是工具路由器。必须调用且只调用一个最合适的工具。"},
                {"role": "user", "content": instruction},
            ],
            "tools": [self._schema(t) for t in tools],
            "tool_choice": self.tool_choice,
            "temperature": 0,
            "max_tokens": self.max_tokens,
        }
        if self.thinking is False:
            payload["thinking"] = {"type": "disabled"}

        data = self._post(payload, allow_tool_choice_fallback=True)
        usage = data.get("usage", {}) or {}
        self.usage["prompt"] += usage.get("prompt_tokens", 0)
        self.usage["completion"] += usage.get("completion_tokens", 0)
        self.usage["reasoning"] += (usage.get("completion_tokens_details") or {}).get("reasoning_tokens", 0)
        self.usage["calls"] += 1

        message = data["choices"][0]["message"]
        calls = message.get("tool_calls") or []
        valid = {t["name"] for t in tools}
        if calls:
            raw_name = calls[0]["function"]["name"]
            qualified = from_openai_name(raw_name)
            # tool_choice=required forces a choice, so a model that cannot decide
            # will invent a function name instead of admitting it. Observed once
            # in 63 cases: `name_placeholder`. Anything not in the advertised
            # list must be rejected here, not passed to the gateway.
            if qualified in valid:
                self.last_raw = f"tool_calls:{raw_name}"
                return qualified
            self.last_raw = f"hallucinated_tool:{raw_name}"
            return None

        # Fallback: some endpoints ignore tool_choice and answer in prose.
        text = (message.get("content") or "").strip()
        self.last_raw = f"content:{text[:80]}"
        for tool in tools:
            for candidate in (tool["name"], to_openai_name(tool["name"])):
                if candidate in text:
                    return tool["name"]
        match = re.search(r"[a-z_]+\.[a-z_]+", text)
        return match.group(0) if match else None


def build(provider, model=None, thinking=None):
    if provider == "mock":
        return MockModel()
    return OpenAICompatModel(model=model, thinking=thinking)
