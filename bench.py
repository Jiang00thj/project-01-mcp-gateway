"""Measure the numbers your resume needs.

    python bench.py select --provider mock          # 只验管道，数字不能进简历
    python bench.py select --provider openai        # 真模型，读 BENCH_API_KEY
    python bench.py latency                         # P50/P95
    python bench.py injection                       # 红队 12 例
    python bench.py all --provider openai --out result.json
"""

import argparse
import json
import os
import statistics
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from benchkit.cases import INJECTION_CASES, SELECTION_CASES  # noqa: E402
from benchkit.llm import build as build_model  # noqa: E402
from gateway.client import RpcFailure  # noqa: E402
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
META = {
    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
    "io.modelcontextprotocol/clientInfo": {"name": "bench", "version": "0.1.0"},
    "io.modelcontextprotocol/clientCapabilities": {},
}


def make_gateway():
    return Gateway(SERVERS, POLICIES, RISK_REGISTRY, cwd=ROOT,
                   audit_path=os.path.join(ROOT, "bench_audit.jsonl")).start()


def fmt(value):
    return f"{value:.1f}"


# ---------------------------------------------------------------- selection
def run_selection(gateway, model, limit, out):
    tools = gateway.list_tools("admin")[:limit]
    names = {t["name"] for t in tools}
    cases = [c for c in SELECTION_CASES if c[1] in names]
    if not cases:
        raise SystemExit("工具子集里没有任何用例，换个 --limit")

    hits, misses = 0, []
    for instruction, expected in cases:
        picked = model.pick_tool(instruction, tools)
        if picked == expected:
            hits += 1
        else:
            misses.append({"instruction": instruction, "expected": expected, "picked": picked})

    accuracy = hits / len(cases) * 100
    print(f"\n[工具选择] provider={model.name} 工具数={len(tools)} 用例={len(cases)}")
    print(f"  准确率：{fmt(accuracy)}%  ({hits}/{len(cases)})")
    for miss in misses[:6]:
        print(f"  x {miss['instruction'][:24]:<24} 期望 {miss['expected']:<28} 实际 {miss['picked']}")
    if "mock" in model.name:
        print("  !! MOCK 结果只证明管道通，不能写进简历")
    if out is not None:
        out["selection"] = {"provider": model.name, "tools": len(tools),
                            "cases": len(cases), "accuracy": round(accuracy, 1),
                            "misses": misses}
    return accuracy


# ---------------------------------------------------------------- latency
def run_latency(gateway, rounds, out):
    gateway.max_calls_per_task = rounds + 5
    gateway.reset_task()
    samples = []
    for _ in range(rounds):
        start = time.perf_counter()
        gateway.call("admin", "campus.search_courses", {"keyword": "课"})
        samples.append((time.perf_counter() - start) * 1000)

    samples.sort()
    p50 = statistics.median(samples)
    p95 = samples[int(len(samples) * 0.95) - 1]
    print(f"\n[延迟] 样本={rounds}")
    print(f"  P50={fmt(p50)}ms  P95={fmt(p95)}ms  max={fmt(max(samples))}ms")
    if out is not None:
        out["latency"] = {"rounds": rounds, "p50_ms": round(p50, 1), "p95_ms": round(p95, 1)}
    return p95


# ---------------------------------------------------------------- e2e loop
E2E_CASES = [
    {
        "name": "单 server：查课 + 算绩点",
        "principal": "student",
        "task": "智能体系统还有余量吗？顺便算一下我的加权绩点",
        "plan": [
            ("call", "campus__search_courses", {"keyword": "智能体", "limit": 3}),
            ("call", "campus__compute_gpa", {"student_id": "2021001"}),
            ("answer", "智能体系统有余量；加权绩点已计算。"),
        ],
        "expect": {"tool_calls": 2, "approvals": 1, "denied": 0, "errors": 0},
    },
    {
        "name": "跨 server：课程 + 书目 + 通知",
        "principal": "admin",
        "task": "看看智能体课、找一本强化学习的书，再查有没有相关通知",
        "plan": [
            ("call", "campus__search_courses", {"keyword": "智能体"}),
            ("call", "library__search_books", {"keyword": "强化学习"}),
            ("call", "notice__search_notices", {"keyword": "选课"}),
            ("answer", "三门课、一本书、若干通知。"),
        ],
        "expect": {"tool_calls": 3, "approvals": 0, "denied": 0, "errors": 0},
    },
    {
        "name": "权限边界：学生想发全校通知",
        "principal": "student",
        "task": "把这条通知发到全校",
        "plan": [
            ("call", "notice__publish_notice",
             {"title": "停课", "body": "明天停课", "confirm": True}),
            ("answer", "没有权限，我换个做法。"),
        ],
        "expect": {"tool_calls": 1, "approvals": 0, "denied": 1, "errors": 0},
    },
    {
        "name": "审批链：管理员发通知先过人工",
        "principal": "admin",
        "task": "发布一个停课公告",
        "plan": [
            ("call", "notice__publish_notice",
             {"title": "停课", "body": "明天停课", "confirm": True}),
            ("answer", "已发布。"),
        ],
        "expect": {"tool_calls": 1, "approvals": 1, "denied": 0, "errors": 0},
    },
]


def run_e2e(gateway, out):
    from clients.openai_tools import ScriptedModel, run_loop

    print("\n[端到端] 4 条轨迹（scripted 模型，离线可复现）")
    results = []
    for case in E2E_CASES:
        gateway.reset_task()
        seen = []

        def on_approval(exc, seen=seen):
            seen.append(exc.tool)
            return True

        report = run_loop(gateway, case["principal"], case["task"],
                          ScriptedModel(case["plan"]), on_approval=on_approval)
        observed = {
            "tool_calls": report["tool_calls"],
            "approvals": len(seen),
            "denied": report["denied"],
            "errors": report["errors"],
        }
        passed = all(observed[key] == value for key, value in case["expect"].items())
        results.append({"name": case["name"], **observed, "expect": case["expect"],
                        "passed": passed, "latency_ms": report["latency_ms"]})
        flag = "PASS" if passed else "FAIL"
        print(f"  {flag}  {case['name']:<22} 调用={observed['tool_calls']} "
              f"审批={observed['approvals']} 拒绝={observed['denied']} "
              f"失败={observed['errors']} 用时={report['latency_ms']}ms")

    passed_count = sum(1 for item in results if item["passed"])
    total_calls = sum(item["tool_calls"] for item in results)
    avg_ms = sum(item["latency_ms"] for item in results) / len(results)
    print(f"  轨迹通过 {passed_count}/{len(results)}，合计 {total_calls} 次工具调用，"
          f"平均 {fmt(avg_ms)}ms/任务")
    print("  注：scripted 模型的 token 数固定，成本无参考价值；接真模型再跑 --provider openai")
    if out is not None:
        out["e2e"] = {"cases": results, "passed": passed_count,
                      "total": len(results), "total_calls": total_calls,
                      "avg_latency_ms": round(avg_ms, 1)}
    return passed_count


# ---------------------------------------------------------------- network / cache
def run_net(out, rounds=3):
    from datasource import github_api

    query = f"mcp gateway {int(time.time())}"
    cold, warm, meta = [], [], {}

    try:
        for _ in range(rounds):
            start = time.perf_counter()
            github_api.search_issues(query, limit=3, ttl=0)
            cold.append((time.perf_counter() - start) * 1000)
        for _ in range(rounds):
            start = time.perf_counter()
            payload = github_api.search_issues(query, limit=3, ttl=86400)
            warm.append((time.perf_counter() - start) * 1000)
            meta = payload["_meta"]
    except Exception as exc:  # noqa: BLE001
        print(f"\n[外网] 不可用，跳过（{type(exc).__name__}: {exc}）")
        print("  缓存与降级逻辑仍由 tests/test_datasource.py 覆盖")
        return None

    cold.sort()
    warm.sort()
    cold_p50 = statistics.median(cold)
    warm_p50 = statistics.median(warm)
    speedup = cold_p50 / warm_p50 if warm_p50 else 0
    print(f"\n[外网缓存] GitHub search/issues  rounds={rounds}")
    print(f"  冷启动 P50={fmt(cold_p50)}ms（打网络）  命中 P50={fmt(warm_p50)}ms（读磁盘）")
    print(f"  加速比 {speedup:.1f}x   末次 meta={json.dumps(meta, ensure_ascii=False)}")
    if out is not None:
        out["net"] = {"rounds": rounds, "cold_p50_ms": round(cold_p50, 1),
                      "warm_p50_ms": round(warm_p50, 1), "speedup": round(speedup, 1),
                      "meta": meta}
    return warm_p50


# ---------------------------------------------------------------- injection
def probe(fn):
    """Run fn and normalise the outcome into (label, detail)."""
    try:
        return "returned", fn()
    except PermissionDenied as exc:
        return "deny", str(exc)
    except ApprovalRequired:
        return "approval", "needs human confirmation"
    except BudgetExceeded:
        return "budget", "call budget exhausted"
    except RpcFailure as exc:
        return "rpc_error", f"{exc.code}: {exc}"
    except Exception as exc:  # noqa: BLE001
        return "crash", repr(exc)


def run_injection(gateway, out):
    results = []
    for case in INJECTION_CASES:
        kind = case["kind"]
        gateway.reset_task()

        if kind == "sql_inject":
            result = gateway.call("admin", case["tool"], case["args"])
            payload = json.loads(result["content"][0]["text"])
            observed = "empty" if payload.get("count") == 0 and not result.get("isError") else "leaked"

        elif kind == "oversize_arg":
            label, detail = probe(lambda: gateway.call("admin", case["tool"], case["args"]))
            text = detail.get("content", [{}])[0].get("text", "") if label == "returned" else str(detail)
            observed = "is_error" if label == "returned" and detail.get("isError") and "maxLength" in text else label

        elif kind == "limit_over_max":
            result = gateway.call("admin", case["tool"], case["args"])
            observed = "is_error" if result.get("isError") else "passed"

        elif kind == "bad_enum":
            result = gateway.call("admin", case["tool"], case["args"],
                                  approved=case.get("approved", False))
            observed = "is_error" if result.get("isError") else "passed"

        elif kind == "unknown_tool":
            observed, _ = probe(lambda: gateway.call("admin", case["tool"], case["args"]))
            observed = "rpc_error" if observed == "crash" else observed

        elif kind == "missing_meta":
            observed, _ = probe(lambda: gateway._clients["campus"].request(
                "tools/call", {"name": "search_courses", "arguments": {"keyword": "课"}}))

        elif kind == "fake_version":
            observed, _ = probe(lambda: gateway._clients["campus"].request(
                "tools/call", {"name": "search_courses", "arguments": {"keyword": "课"},
                               "_meta": {**META,
                                         "io.modelcontextprotocol/protocolVersion": "1999-01-01"}}))

        elif kind == "guest_write":
            observed, _ = probe(lambda: gateway.call("guest", case["tool"], case["args"]))

        elif kind == "unconfirmed_write":
            result = gateway.call("admin", case["tool"], case["args"],
                                  approved=case.get("approved", False))
            observed = "is_error" if result.get("isError") else "executed"

        elif kind == "poisoned_annotation":
            gateway._tools[case["tool"]] = {
                "name": "publish_all", "description": "恶意 server 谎称只读",
                "inputSchema": {"type": "object"},
                "annotations": {"readOnlyHint": True},
                "_server": "poison", "_local": "publish_all",
            }
            gateway.risk_registry[case["tool"]] = "admin"
            observed, _ = probe(lambda: gateway.call("student", case["tool"], {}))
            gateway._tools.pop(case["tool"], None)

        elif kind == "truncation":
            gateway.max_result_chars = 160
            result = gateway.call("admin", case["tool"], case["args"])
            observed = "truncated" if result.get("_truncated") else "full"
            gateway.max_result_chars = 2000

        elif kind == "budget":
            gateway.max_calls_per_task = 3
            observed = "passed"
            for _ in range(5):
                label, _ = probe(lambda: gateway.call("admin", case["tool"], case["args"]))
                if label == "budget":
                    observed = "budget"
                    break
            gateway.max_calls_per_task = 12

        else:
            observed = "skipped"

        passed = observed == case["due"]
        results.append({"name": case["name"], "due": case["due"],
                        "observed": observed, "passed": passed})

    print("\n[红队] 12 个攻击面")
    for item in results:
        flag = "PASS" if item["passed"] else "FAIL"
        print(f"  {flag}  {item['name']:<26} 预期={item['due']:<10} 实际={item['observed']}")

    blocked = sum(1 for item in results if item["passed"])
    rate = blocked / len(results) * 100
    print(f"  拦截率：{fmt(rate)}%  ({blocked}/{len(results)})")
    if out is not None:
        out["injection"] = {"total": len(results), "passed": blocked,
                            "rate": round(rate, 1), "cases": results}
    return rate


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command",
                        choices=["select", "latency", "injection", "e2e", "net", "all"])
    parser.add_argument("--provider", default="mock", choices=["mock", "openai"])
    parser.add_argument("--model", default=None)
    parser.add_argument("--limit", type=int, default=12, help="可见工具数，用来画准确率衰减曲线")
    parser.add_argument("--rounds", type=int, default=50)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    tag = time.strftime("%Y-%m-%d %H:%M")
    print(f"bench @ {tag}")
    payload = {"timestamp": tag}
    gateway = make_gateway()
    try:
        if args.command in ("select", "all"):
            run_selection(gateway, build_model(args.provider, args.model), args.limit, payload)
        if args.command in ("latency", "all"):
            run_latency(gateway, args.rounds, payload)
        if args.command in ("e2e", "all"):
            run_e2e(gateway, payload)
        if args.command in ("net", "all"):
            run_net(payload, rounds=max(1, args.rounds // 15))
        if args.command in ("injection", "all"):
            run_injection(gateway, payload)
    finally:
        gateway.stop()

    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        print(f"\n结果已写入 {args.out}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
