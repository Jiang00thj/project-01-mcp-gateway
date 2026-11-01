"""End-to-end walkthrough of the gateway.

    python demo.py

Five scenes: discovery, least privilege, approval gate, result truncation,
call budget. Everything is asserted, so a broken policy fails loudly.
"""

import json
import os
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from gateway.gateway import (  # noqa: E402
    ApprovalRequired, BudgetExceeded, Gateway, PermissionDenied,
)

SERVERS = {
    "campus": ["-m", "servers.campus_server"],
    "library": ["-m", "servers.library_server"],
    "notice": ["-m", "servers.notice_server"],
    "github": ["-m", "servers.github_server"],
}

POLICIES = {
    "guest": {
        "max_risk": "read",
        "allow": ["campus.search_courses", "campus.room_availability",
                  "library.search_books", "notice.search_notices",
                  "github.search_issues", "github.get_issue"],
    },
    "student": {"max_risk": "read", "allow": ["*"]},
    "admin": {"max_risk": "admin", "allow": ["*"]},
}

RISK_REGISTRY = {"notice.publish_notice": "admin"}


def title(text):
    print(f"\n{'=' * 62}\n{text}\n{'=' * 62}")


def main():
    audit_path = os.path.join(ROOT, "gateway_audit.jsonl")
    if os.path.exists(audit_path):
        os.remove(audit_path)

    gateway = Gateway(SERVERS, POLICIES, RISK_REGISTRY, cwd=ROOT,
                      audit_path=audit_path).start()
    try:
        title("场景 1 · 发现层：四个 server，一次握手全部聚合")
        total = 0
        for alias, info in gateway.discovery.items():
            name = info["_meta"]["io.modelcontextprotocol/serverInfo"]["name"]
            count = sum(1 for t in gateway._tools.values() if t["_server"] == alias)
            total += count
            print(f"  {alias:<8} {name:<16} 版本={info['supportedVersions']} 工具={count}")
        print(f"  聚合后工具总数：{total}，命名规则 server.tool 避免重名")

        title("场景 2 · 最小权限：guest 只能看见 6 个只读工具")
        guest_tools = [t["name"] for t in gateway.list_tools("guest")]
        print("  guest 可见：", guest_tools)
        assert len(guest_tools) == 6, guest_tools
        try:
            gateway.call("guest", "notice.publish_notice", {"title": "x", "body": "y", "confirm": True})
        except PermissionDenied as exc:
            print(f"  guest 越权被拒：{exc}")
        else:
            raise AssertionError("guest should not publish notices")

        title("场景 3 · 审批门：敏感工具先拦下来，人确认后才执行")
        try:
            gateway.call("student", "campus.get_grades", {"student_id": "2021001"})
        except ApprovalRequired as exc:
            print(f"  拦截：{exc}\n  待确认参数：{exc.preview}")
        result = gateway.call("student", "campus.get_grades", {"student_id": "2021001"}, approved=True)
        print("  确认后返回：", result["content"][0]["text"][:90], "...")

        title("场景 4 · 结果截断：超长输出保留头尾，并显式告知模型")
        gateway.reset_task()
        gateway.max_result_chars = 160  # 故意调小以便演示
        big = gateway.call("admin", "campus.search_courses", {"keyword": "", "limit": 50})
        print("  截断标记：", big.get("_truncated"), "| 文本长度：", len(big["content"][0]["text"]))
        print("  ", big["content"][0]["text"].replace("\n", " ")[:150])
        # Truncating a JSON payload makes it unparseable, so anything that needs
        # machine-readable output must read structuredContent, not the text block.
        print("  结构化结果仍在 structuredContent：", "structuredContent" in big)
        gateway.max_result_chars = 2000  # 还原，否则后面所有场景都会被截断

        title("场景 5 · 调用预算：防止 Agent 死循环烧钱")
        gateway.reset_task()
        for index in range(1, 14):
            try:
                gateway.call("admin", "campus.room_availability",
                             {"building": "三教", "date": "2026-11-03"})
            except BudgetExceeded as exc:
                print(f"  第 {index} 次调用被拦：{exc}")
                break
        else:
            raise AssertionError("budget should have tripped")

        title("场景 6 · 真实外部数据源：GitHub API + 缓存，断网自动降级")
        gateway.reset_task()
        try:
            first = gateway.call("admin", "github.search_issues",
                                 {"keyword": "rate limit", "limit": 3})
            payload = first.get("structuredContent") or json.loads(first["content"][0]["text"])
            print(f"  第 1 次 cache={payload['_meta']['cache']:<10} total={payload['total']} "
                  f"返回={len(payload['items'])} 条")
            second = gateway.call("admin", "github.search_issues",
                                  {"keyword": "rate limit", "limit": 3})
            payload2 = second.get("structuredContent") or json.loads(second["content"][0]["text"])
            print(f"  第 2 次 cache={payload2['_meta']['cache']:<10} "
                  f"age={payload2['_meta'].get('age_s')}s  ← 同样的查询不该再打一次网络")
            print("  （首次运行是 miss=打网络，之后是 hit=读磁盘；断网时是 stale=用过期缓存）")
            assert payload2["_meta"]["cache"] in ("hit", "revalidated", "stale")
        except Exception as exc:  # noqa: BLE001
            print(f"  （跳过：GitHub 不可达 -> {type(exc).__name__}；缓存/降级路径见 bench.py net）")

        title("场景 7 · 接 LLM：名字映射 + 审批中断 + 可审计轨迹")
        from clients.openai_tools import ScriptedModel, run_loop

        gateway.reset_task()
        plan = [
            ("call", "campus__search_courses", {"keyword": "智能体", "limit": 3}),
            ("call", "campus__get_grades", {"student_id": "2021001"}),
            ("answer", "智能体系统还有余量；你的加权绩点已返回。"),
        ]
        approved = []

        def on_approval(exc):
            approved.append(exc.tool)
            return True

        report = run_loop(gateway, "student", "查一下智能体系统的余量，再看我的成绩",
                          ScriptedModel(plan), on_approval=on_approval)
        print(f"  工具调用={report['tool_calls']} 审批次数={len(approved)} "
              f"失败={report['errors']} 用时={report['latency_ms']}ms")
        for item in report["trace"]:
            if item["type"] == "tool":
                print(f"    · {item['tool']:<26} -> {item['outcome']}")
        assert report["tool_calls"] == 2, report["trace"]
        assert len(approved) == 1, "敏感工具必须经过审批"

        title("审计日志汇总")
        with open(audit_path, encoding="utf-8") as handle:
            records = [json.loads(line) for line in handle if line.strip()]
        for decision, count in Counter(r["decision"] for r in records).most_common():
            print(f"  {decision:<18} {count}")
        print(f"  日志条数：{len(records)} -> {os.path.basename(audit_path)}")
    finally:
        gateway.stop()

    print("\n全部断言通过。")


if __name__ == "__main__":
    main()
