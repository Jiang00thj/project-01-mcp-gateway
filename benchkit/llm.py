"""LLM adapter for the benchmark.

Two providers:
  mock  - keyword scorer, no network. Only proves the plumbing works.
          NEVER put mock numbers on a resume.
  openai-  any OpenAI-compatible endpoint (DeepSeek / Qwen / GLM / local vLLM).

Only urllib is used, so the repo stays dependency-free.
"""

import json
import os
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


class MockModel:
    name = "mock-keyword-scorer"

    def pick_tool(self, instruction, tools):
        best, best_score = None, 0
        for tool in tools:
            score = sum(1 for word in KEYWORDS.get(tool["name"], []) if word in instruction)
            if score > best_score:
                best, best_score = tool["name"], score
        return best or tools[0]["name"]


class OpenAICompatModel:
    def __init__(self, model=None, base_url=None, api_key=None):
        self.model = model or os.environ.get("BENCH_MODEL", "deepseek-chat")
        self.base_url = (base_url or os.environ.get("BENCH_BASE_URL")
                         or "https://api.deepseek.com/v1").rstrip("/")
        self.api_key = api_key or os.environ.get("BENCH_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")
        if not self.api_key:
            raise SystemExit("缺少 API key：设置 BENCH_API_KEY 或 DEEPSEEK_API_KEY，或改用 --provider mock")
        self.name = f"openai-compatible:{self.model}"

    def pick_tool(self, instruction, tools):
        catalogue = "\n".join(f"- {t['name']}: {t['description']}" for t in tools)
        prompt = (
            "你是工具路由器。根据用户指令选择一个工具。\n\n"
            f"可用工具：\n{catalogue}\n\n"
            f"用户指令：{instruction}\n\n"
            "只输出工具名，不要解释，不要加引号。"
        )
        body = json.dumps({
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": 32,
        }).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions", data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.api_key}"},
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise SystemExit(f"API 报错 {exc.code}: {exc.read()[:300]!r}") from exc
        text = payload["choices"][0]["message"]["content"].strip()
        for token in text.replace("`", " ").split():
            cleaned = token.strip("\"'.,，。")
            if "." in cleaned:
                return cleaned
        return text


def build(provider, model=None):
    if provider == "mock":
        return MockModel()
    return OpenAICompatModel(model=model)
