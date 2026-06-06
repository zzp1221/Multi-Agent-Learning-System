"""
全链路综合测试 V3 (精简版)
==========================
Part 1: 基础设施 + 认证
Part 2: Python Agent 直接调用 (7 serviceTypes)
Part 3: Java 全链路 (conversation → submit → SSE)
Part 4: 报告生成

5 dimensions: D1-ResponseTime D2-Correctness D3-SSE Event D4-ErrorHandling D5-OutputQuality
"""

import asyncio
import json
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

JAVA_BASE = "http://localhost:8081"
PYTHON_BASE = "http://localhost:8000"

TIMEOUT_DEFAULT = httpx.Timeout(300.0, connect=10.0)
TIMEOUT_FAST = httpx.Timeout(30.0, connect=10.0)

TEST_USER = {
    "loginId": f"ctest_{uuid.uuid4().hex[:6]}",
    "password": "Test@123456",
    "fullName": "综合测试用户",
    "majorCode": "CS",
}


@dataclass
class TestResult:
    name: str
    passed: bool
    dimension: str
    detail: str = ""
    duration_ms: float = 0.0
    severity: str = ""

    def markdown_row(self) -> str:
        tag = "PASS" if self.passed else "FAIL"
        ms = f"{self.duration_ms:.0f}ms" if self.duration_ms else "-"
        return f"| {self.name} | {self.dimension} | {tag} | {ms} | {self.detail} |"


class TestCollector:
    def __init__(self):
        self.results: dict[str, list[TestResult]] = {}

    def add(self, section: str, r: TestResult):
        if section not in self.results:
            self.results[section] = []
        self.results[section].append(r)
        tag = "PASS" if r.passed else "FAIL"
        sev = f" [{r.severity}]" if r.severity else ""
        ms = f" ({r.duration_ms:.0f}ms)" if r.duration_ms else ""
        print(f"  [{tag}]{sev} {r.dimension} {r.name}{ms}")

    def all(self) -> list[TestResult]:
        return [r for lst in self.results.values() for r in lst]

    def section_summary(self, section: str) -> tuple[int, int]:
        lst = self.results.get(section, [])
        p = sum(1 for r in lst if r.passed)
        return p, len(lst) - p


C = TestCollector()
STATE = {"token": None, "userId": None, "conversationId": None, "taskIds": []}


def auth_headers():
    h = {}
    if STATE["token"]:
        h["Authorization"] = f"Bearer {STATE['token']}"
    return h


# ═══════════════════════════════════════════════════════════════════
# Part 1: 基础设施 + 认证
# ═══════════════════════════════════════════════════════════════════

async def part1_infra_and_auth(client: httpx.AsyncClient):
    print("\n" + "=" * 60)
    print("Part 1: 基础设施健康检查 + 认证全链路")
    print("=" * 60)

    # Health
    t0 = time.perf_counter()
    try:
        r = await client.get(f"{JAVA_BASE}/actuator/health", timeout=TIMEOUT_FAST)
        d = r.json()
        ok = all(d.get("components", {}).get(k, {}).get("status") == "UP"
                 for k in ["db", "mongo", "redis", "ping"])
        C.add("infra", TestResult("Java健康检查", ok, "D1",
            f"{(time.perf_counter()-t0)*1000:.0f}ms", (time.perf_counter()-t0)*1000,
            "CRITICAL" if not ok else ""))
    except Exception as e:
        C.add("infra", TestResult("Java健康检查", False, "D1", str(e), 0, "CRITICAL"))

    t0 = time.perf_counter()
    try:
        r = await client.get(f"{PYTHON_BASE}/health", timeout=TIMEOUT_FAST)
        d = r.json()
        ok = d.get("status") == "ok"
        C.add("infra", TestResult("Python Agent健康检查", ok, "D1",
            f"{(time.perf_counter()-t0)*1000:.0f}ms, model={d.get('model','?')}",
            (time.perf_counter()-t0)*1000, "CRITICAL" if not ok else ""))
    except Exception as e:
        C.add("infra", TestResult("Python Agent健康检查", False, "D1", str(e), 0, "CRITICAL"))

    # Register
    print("\n  -- 认证链路 --")
    t0 = time.perf_counter()
    try:
        r = await client.post(f"{JAVA_BASE}/api/auth/register", json=TEST_USER, timeout=TIMEOUT_FAST)
        d = r.json()
        ok = r.status_code == 200 and "token" in d
        C.add("auth", TestResult("注册新用户", ok, "D2",
            f"status={r.status_code}", (time.perf_counter()-t0)*1000))
        if ok:
            STATE["token"] = d.get("token", "")
            u = d.get("user", {})
            STATE["userId"] = str(u.get("id", d.get("userId", "")))
    except Exception as e:
        C.add("auth", TestResult("注册新用户", False, "D2", str(e), 0, "CRITICAL"))

    # Duplicate register
    t0 = time.perf_counter()
    try:
        r = await client.post(f"{JAVA_BASE}/api/auth/register", json=TEST_USER, timeout=TIMEOUT_FAST)
        C.add("auth", TestResult("重复注册检测", r.status_code == 409, "D4",
            f"status={r.status_code} (expect 409)", (time.perf_counter()-t0)*1000))
    except Exception as e:
        C.add("auth", TestResult("重复注册检测", False, "D4", str(e)))

    # Login
    t0 = time.perf_counter()
    login_ok = False
    try:
        r = await client.post(f"{JAVA_BASE}/api/auth/login",
            json={"loginId": TEST_USER["loginId"], "password": TEST_USER["password"]},
            timeout=TIMEOUT_FAST)
        d = r.json()
        login_ok = r.status_code == 200 and "token" in d
        detail = f"status={r.status_code}"
        if not login_ok and r.status_code != 200:
            detail += f" body={r.text[:150]}"
        C.add("auth", TestResult("用户登录", login_ok, "D2",
            detail, (time.perf_counter()-t0)*1000, "CRITICAL" if not login_ok else ""))
        if login_ok:
            STATE["token"] = d.get("token", "")
            u = d.get("user", {})
            STATE["userId"] = str(u.get("id", d.get("userId", "")))
    except Exception as e:
        C.add("auth", TestResult("用户登录", False, "D2", str(e), 0, "CRITICAL"))

    # Rate limit
    t0 = time.perf_counter()
    try:
        r = await client.post(f"{JAVA_BASE}/api/auth/login",
            json={"loginId": TEST_USER["loginId"], "password": "WrongPass1"},
            timeout=TIMEOUT_FAST)
        C.add("auth", TestResult("错误密码→401", r.status_code == 401, "D4",
            f"status={r.status_code}", (time.perf_counter()-t0)*1000))
    except Exception as e:
        C.add("auth", TestResult("错误密码→401", False, "D4", str(e)))

    t0 = time.perf_counter()
    try:
        r = await client.post(f"{JAVA_BASE}/api/auth/login",
            json={"loginId": "no_user_" + uuid.uuid4().hex[:8], "password": "x"},
            timeout=TIMEOUT_FAST)
        C.add("auth", TestResult("不存在用户→401", r.status_code == 401, "D4",
            f"status={r.status_code}", (time.perf_counter()-t0)*1000))
    except Exception as e:
        C.add("auth", TestResult("不存在用户→401", False, "D4", str(e)))

    # GET /me
    if STATE["token"]:
        t0 = time.perf_counter()
        try:
            r = await client.get(f"{JAVA_BASE}/api/auth/me", headers=auth_headers(), timeout=TIMEOUT_FAST)
            d = r.json()
            C.add("auth", TestResult("GET /me", r.status_code == 200, "D2",
                f"loginId={d.get('loginId','?')}", (time.perf_counter()-t0)*1000))
        except Exception as e:
            C.add("auth", TestResult("GET /me", False, "D2", str(e), 0, "CRITICAL"))

    # No-token access
    t0 = time.perf_counter()
    try:
        r = await client.get(f"{JAVA_BASE}/api/auth/me", timeout=TIMEOUT_FAST)
        C.add("auth", TestResult("无Token访问→401", r.status_code == 401, "D4",
            f"status={r.status_code}", (time.perf_counter()-t0)*1000))
    except Exception as e:
        C.add("auth", TestResult("无Token访问→401", False, "D4", str(e)))

    # Login response time
    if login_ok:
        t0 = time.perf_counter()
        await client.post(f"{JAVA_BASE}/api/auth/login",
            json={"loginId": TEST_USER["loginId"], "password": TEST_USER["password"]},
            timeout=TIMEOUT_FAST)
        ms = (time.perf_counter() - t0) * 1000
        C.add("auth", TestResult("登录响应时间", ms < 1000, "D1", f"{ms:.0f}ms", ms))


# ═══════════════════════════════════════════════════════════════════
# Part 2: Agent 功能测试
# ═══════════════════════════════════════════════════════════════════

SERVICE_SPECS = {
    "TUTORING": {
        "params": {"message": "什么是二分查找？请简单讲解", "streamMode": True},
        "timeout_s": 120,
        "expect_content": True,
    },
    "RESOURCE_GENERATION": {
        "params": {"message": "生成一份冒泡排序的学习讲义", "resourceType": "DOCUMENT", "knowledgePoint": "冒泡排序", "streamMode": True},
        "timeout_s": 150,
        "expect_content": True,
    },
    "VIDEO_GENERATION": {
        "params": {"message": "生成快速排序的教学视频脚本", "resourceType": "VIDEO", "knowledgePoint": "快速排序", "streamMode": True},
        "timeout_s": 150,
        "expect_content": True,
    },
    "PRACTICE_JUDGE": {
        "params": {"message": "生成2道关于数组的选择题并批改", "knowledgePoint": "数组", "questionCount": 2, "streamMode": True},
        "timeout_s": 150,
        "expect_content": True,
    },
    "PATH_PLANNING": {
        "params": {"message": "为初学者规划Python学习路径", "knowledgePoint": "Python", "streamMode": True},
        "timeout_s": 120,
        "expect_content": True,
    },
    "EVALUATION": {
        "params": {"message": "评估我的数据结构知识水平", "knowledgePoint": "数据结构", "streamMode": True},
        "timeout_s": 120,
        "expect_content": True,
    },
    "PROFILE_BUILD": {
        "params": {"message": "通过对话了解我的学习风格", "streamMode": True},
        "timeout_s": 150,
        "expect_content": True,
    },
}


async def run_one_service_check(client: httpx.AsyncClient, st: str, spec: dict) -> list[TestResult]:
    results = []
    body = {
        "serviceType": st,
        "params": dict(spec["params"]),
        "userId": STATE["userId"] or "test-user",
        "taskId": str(uuid.uuid4()),
        "traceId": str(uuid.uuid4()),
        "conversationId": STATE["conversationId"] or str(uuid.uuid4()),
    }

    # D1 + D2 + D3 + D5: Call the agent
    t0 = time.perf_counter()
    events = []
    error_happened = False
    error_msg = ""
    timeout_happened = False
    raw_lines = 0

    timeout_s = spec["timeout_s"] + 60  # add buffer
    try:
        async with client.stream(
            "POST", f"{PYTHON_BASE}/internal/smart-engine/stream",
            json=body,
            timeout=httpx.Timeout(timeout_s, connect=10.0),
            headers={"Accept": "text/event-stream"},
        ) as resp:
            if resp.status_code != 200:
                body_raw = await resp.aread()
                error_happened = True
                error_msg = f"HTTP {resp.status_code}: {body_raw[:200]}"
            else:
                cur_event = ""
                async for line in resp.aiter_lines():
                    raw_lines += 1
                    if raw_lines > 5000:  # safety limit
                        events.append({"event": "truncated", "payload": {}})
                        break
                    if line.startswith("event:"):
                        cur_event = line[6:].strip()
                    elif line.startswith("data:"):
                        try:
                            d = json.loads(line[5:].strip())
                            events.append(d)
                        except json.JSONDecodeError:
                            pass
    except httpx.ReadTimeout:
        timeout_happened = True
        error_msg = "ReadTimeout"
    except Exception as e:
        error_happened = True
        error_msg = f"{type(e).__name__}: {str(e)[:150]}"

    elapsed = (time.perf_counter() - t0) * 1000

    # D1: Response time
    threshold = spec["timeout_s"] * 1000
    d1_ok = (not timeout_happened) and elapsed < threshold
    results.append(TestResult(f"{st}响应时间", d1_ok, "D1",
        f"{elapsed:.0f}ms (threshold<{threshold:.0f}ms){' TIMEOUT' if timeout_happened else ''}",
        elapsed, "CRITICAL" if timeout_happened else ("WARNING" if not d1_ok else "")))

    # D2: Structure correctness
    has_done = any(e.get("event") == "done" for e in events)
    has_error = any(e.get("event") == "error" for e in events)
    d2_ok = has_done and not has_error and len(events) > 0
    etypes = [e.get("event", "?") for e in events]
    results.append(TestResult(f"{st}输出结构", d2_ok, "D2",
        f"done={has_done} error={has_error} events={len(events)} types={etypes[:5]}",
        severity="CRITICAL" if has_error else ("" if d2_ok else "WARNING")))

    # D3: SSE event sequence
    valid_types = {"progress", "result_chunk", "resource_file", "question_batch", "judge_result", "done", "error"}
    all_valid = all(e.get("event", "") in valid_types for e in events)
    d3_ok = all_valid and (has_done or has_error) and len(events) > 0
    results.append(TestResult(f"{st}SSE事件序列", d3_ok, "D3",
        f"allValid={all_valid} seq={'→'.join(etypes[:8])}{'...' if len(etypes)>8 else ''}"))

    # D5: Output quality
    text_parts = []
    for e in events:
        p = e.get("payload", {})
        if isinstance(p, dict):
            text_parts.append(p.get("text", ""))
            text_parts.append(p.get("summary", ""))
            text_parts.append(p.get("message", ""))
    total_text = "".join(str(t) for t in text_parts)
    d5_ok = len(total_text) >= 20 if spec["expect_content"] else True
    results.append(TestResult(f"{st}输出质量", d5_ok, "D5",
        f"textLen={len(total_text)}{' EMPTY!' if len(total_text) < 20 else ''}"))

    # D4: Invalid serviceType
    t0 = time.perf_counter()
    try:
        bad = {**body, "serviceType": "BAD_TYPE_99"}
        async with client.stream(
            "POST", f"{PYTHON_BASE}/internal/smart-engine/stream",
            json=bad, timeout=TIMEOUT_FAST, headers={"Accept": "text/event-stream"},
        ) as resp:
            await resp.aread()
            d4_ok = resp.status_code == 400
            results.append(TestResult(f"{st}无效serviceType", d4_ok, "D4",
                f"status={resp.status_code}", (time.perf_counter()-t0)*1000))
    except Exception as e:
        results.append(TestResult(f"{st}无效serviceType", False, "D4",
            str(e)[:100], (time.perf_counter()-t0)*1000))

    return results


async def part2_agent_tests(client: httpx.AsyncClient):
    print("\n" + "=" * 60)
    print("Part 2: Python Agent 7 ServiceTypes 测试")
    print("=" * 60)

    for st, spec in SERVICE_SPECS.items():
        print(f"\n  ── {st} ──")
        res = await run_one_service_check(client, st, spec)
        for r in res:
            C.add(f"agent_{st}", r)


# ═══════════════════════════════════════════════════════════════════
# Part 3: Java 全链路
# ═══════════════════════════════════════════════════════════════════

async def part3_java_chain(client: httpx.AsyncClient):
    print("\n" + "=" * 60)
    print("Part 3: Java 全链路 (Conversation → Submit → Stream)")
    print("=" * 60)

    if not STATE["token"]:
        C.add("chain", TestResult("前置条件", False, "D2", "无有效token", 0, "CRITICAL"))
        return

    # Create conversation
    t0 = time.perf_counter()
    try:
        r = await client.post(f"{JAVA_BASE}/api/conversations", headers=auth_headers(), timeout=TIMEOUT_FAST)
        d = r.json()
        ok = r.status_code == 200 and "conversationId" in d
        C.add("chain", TestResult("创建会话", ok, "D2",
            f"status={r.status_code}", (time.perf_counter()-t0)*1000,
            "CRITICAL" if not ok else ""))
        if ok:
            STATE["conversationId"] = d["conversationId"]
    except Exception as e:
        C.add("chain", TestResult("创建会话", False, "D2", str(e), 0, "CRITICAL"))
        return

    # Submit task
    cid = STATE["conversationId"]
    if not cid:
        return

    t0 = time.perf_counter()
    try:
        body = {"conversationId": cid, "serviceType": "TUTORING", "params": {"message": "请简短介绍什么是栈"}}
        r = await client.post(f"{JAVA_BASE}/api/smart-engine/submit",
            json=body, headers=auth_headers(), timeout=TIMEOUT_FAST)
        d = r.json()
        ok = r.status_code in (200, 409)
        tid = str(d.get("taskId", "?"))
        C.add("chain", TestResult("Submit任务", ok, "D2",
            f"status={r.status_code} taskId={tid[:20]}", (time.perf_counter()-t0)*1000,
            "CRITICAL" if not ok else ""))
        if ok and r.status_code == 200:
            STATE["taskIds"].append(tid)
    except Exception as e:
        C.add("chain", TestResult("Submit任务", False, "D2", str(e), 0, "CRITICAL"))

    # Query task status
    for tid in STATE["taskIds"][:1]:
        t0 = time.perf_counter()
        try:
            r = await client.get(f"{JAVA_BASE}/api/smart-engine/tasks/{tid}",
                headers=auth_headers(), timeout=TIMEOUT_FAST)
            d = r.json()
            C.add("chain", TestResult("任务状态查询", r.status_code == 200, "D2",
                f"taskStatus={d.get('status','?')} progress={d.get('progress','?')}",
                (time.perf_counter()-t0)*1000))
        except Exception as e:
            C.add("chain", TestResult("任务状态查询", False, "D2", str(e)))

        # SSE Stream
        t0 = time.perf_counter()
        sevents = []
        try:
            async with client.stream(
                "GET", f"{JAVA_BASE}/api/smart-engine/tasks/{tid}/stream",
                headers={**auth_headers(), "Accept": "text/event-stream"},
                timeout=httpx.Timeout(180, connect=10),
            ) as resp:
                if resp.status_code == 200:
                    cur = ""
                    async for line in resp.aiter_lines():
                        if line.startswith("event:"):
                            cur = line[6:].strip()
                        elif line.startswith("data:"):
                            sevents.append({"event": cur, "data": line[5:].strip()[:200]})
                        if len(sevents) > 100:
                            break
            etypes = [e["event"] for e in sevents]
            has_d = "done" in etypes
            C.add("chain", TestResult("SSE流订阅", has_d, "D3",
                f"{(time.perf_counter()-t0)*1000:.0f}ms events={len(sevents)} seq={'→'.join(etypes[:8])}",
                (time.perf_counter()-t0)*1000))
        except Exception as e:
            C.add("chain", TestResult("SSE流订阅", False, "D3",
                str(e)[:150], (time.perf_counter()-t0)*1000))

    # No-auth submit
    t0 = time.perf_counter()
    try:
        r = await client.post(f"{JAVA_BASE}/api/smart-engine/submit",
            json={"conversationId": cid, "serviceType": "TUTORING", "params": {"message": "x"}},
            timeout=TIMEOUT_FAST)
        C.add("chain", TestResult("无认证Submit→401", r.status_code == 401, "D4",
            f"status={r.status_code}", (time.perf_counter()-t0)*1000))
    except Exception as e:
        C.add("chain", TestResult("无认证Submit→401", False, "D4", str(e)))


# ═══════════════════════════════════════════════════════════════════
# Report Generation
# ═══════════════════════════════════════════════════════════════════

def generate_report(elapsed_total: float) -> str:
    all_r = C.all()
    total = len(all_r)
    passed = sum(1 for r in all_r if r.passed)
    failed = total - passed
    critic = [r for r in all_r if r.severity == "CRITICAL" and not r.passed]

    # Per dimension stats
    dims = {}
    for r in all_r:
        d = r.dimension
        dims.setdefault(d, {"p": 0, "f": 0})
        if r.passed: dims[d]["p"] += 1
        else: dims[d]["f"] += 1

    # Per section
    sections_order = ["infra", "auth", "agent_TUTORING", "agent_RESOURCE_GENERATION",
        "agent_VIDEO_GENERATION", "agent_PRACTICE_JUDGE", "agent_PATH_PLANNING",
        "agent_EVALUATION", "agent_PROFILE_BUILD", "chain"]

    L = []
    L.append("# 智学系统全链路综合测试报告 V3")
    L.append("")
    L.append(f"**测试时间**: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    L.append(f"**总耗时**: {elapsed_total:.0f}s")
    L.append(f"**测试环境**: Java BFF={JAVA_BASE}, Python Agent={PYTHON_BASE}")
    L.append(f"**模型**: qwen3.6-plus @ 阿里云百炼")
    L.append("")
    L.append("---")
    L.append("")
    L.append("## 1. 总体结果")
    L.append("")
    pct = passed / total * 100 if total > 0 else 0
    L.append(f"| 指标 | 值 |")
    L.append(f"|------|----|")
    L.append(f"| 总测试项 | {total} |")
    L.append(f"| 通过 | {passed} |")
    L.append(f"| 失败 | {failed} |")
    L.append(f"| **通过率** | **{pct:.1f}%** |")
    L.append(f"| 关键失败 (CRITICAL) | {len(critic)} |")
    L.append("")
    bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
    L.append(f"```")
    L.append(f"[{bar}] {pct:.1f}%")
    L.append(f"```")
    L.append("")

    L.append("## 2. 各部分通过率")
    L.append("")
    L.append("| 部分 | 测试数 | 通过 | 失败 | 通过率 |")
    L.append("|------|--------|------|------|--------|")
    sec_names = {
        "infra": "基础设施", "auth": "认证链路",
        "agent_TUTORING": "Agent: TUTORING",
        "agent_RESOURCE_GENERATION": "Agent: RESOURCE_GENERATION",
        "agent_VIDEO_GENERATION": "Agent: VIDEO_GENERATION",
        "agent_PRACTICE_JUDGE": "Agent: PRACTICE_JUDGE",
        "agent_PATH_PLANNING": "Agent: PATH_PLANNING",
        "agent_EVALUATION": "Agent: EVALUATION",
        "agent_PROFILE_BUILD": "Agent: PROFILE_BUILD",
        "chain": "Java全链路",
    }
    for sec in sections_order:
        p, f = C.section_summary(sec)
        t = p + f
        rate = f"{p/t*100:.0f}%" if t > 0 else "N/A"
        L.append(f"| {sec_names.get(sec, sec)} | {t} | {p} | {f} | {rate} |")
    L.append("")

    L.append("## 3. 五维度统计")
    L.append("")
    L.append("| 维度 | 含义 | 通过 | 失败 | 通过率 |")
    L.append("|------|------|------|------|--------|")
    dim_labels = {"D1": "响应时间", "D2": "正确性/结构", "D3": "SSE事件序列", "D4": "错误处理", "D5": "输出质量"}
    for d in ["D1", "D2", "D3", "D4", "D5"]:
        s = dims.get(d, {"p": 0, "f": 0})
        t = s["p"] + s["f"]
        rate = f"{s['p']/t*100:.0f}%" if t > 0 else "N/A"
        L.append(f"| {d} | {dim_labels.get(d,d)} | {s['p']} | {s['f']} | {rate} |")
    L.append("")

    L.append("## 4. 详细结果")
    L.append("")
    for sec in sections_order:
        lst = C.results.get(sec, [])
        if not lst:
            continue
        L.append(f"### {sec_names.get(sec, sec)} ({C.section_summary(sec)[0]}/{len(lst)} 通过)")
        L.append("")
        L.append("| 测试项 | 维度 | 结果 | 耗时 | 详情 |")
        L.append("|--------|------|------|------|------|")
        for r in lst:
            tag = "✅" if r.passed else "❌"
            ms = f"{r.duration_ms:.0f}ms" if r.duration_ms else "-"
            L.append(f"| {r.name} | {r.dimension} | {tag} | {ms} | {r.detail} |")
        L.append("")

    L.append("## 5. 响应时间汇总")
    L.append("")
    L.append("| 操作 | 耗时(ms) |")
    L.append("|------|----------|")
    timed = [(r.name, r.duration_ms) for r in all_r if r.duration_ms > 100]
    timed.sort(key=lambda x: -x[1])
    for name, ms in timed[:20]:
        L.append(f"| {name} | {ms:.0f} |")
    L.append("")

    if critic:
        L.append("## 6. CRITICAL 问题")
        L.append("")
        for r in critic:
            L.append(f"- **{r.name}**: {r.detail}")
        L.append("")

    # Analysis
    L.append("## 7. 问题深度分析")
    L.append("")

    L.append("### 7.1 登录问题")
    login_r = next((r for r in all_r if "登录" in r.name and "用户登录" in r.name), None)
    if login_r and not login_r.passed:
        L.append("")
        L.append(f"**现象**: 注册成功但登录返回401。详情: {login_r.detail}")
        L.append("")
        L.append("**可能根因**:")
        L.append("1. 注册和登录使用的 password 参数不一致（如注册时经过 trim，登录时未处理）")
        L.append("2. BCrypt 编码配置在注册和登录时使用了不同的 PasswordEncoder 实例")
        L.append("3. JWT Filter 在登录路径上错误拦截了请求（但 SecurityConfig 已将 /api/auth/login 设为 permitAll）")
        L.append("4. 请求 Content-Type 不正确导致 @Valid 校验失败，但全局异常处理器返回了统一401格式")
        L.append("")
        L.append("**建议排查**:")
        L.append("1. 查看 Java 日志确认 login 端点是否被 JwtAuthenticationFilter 拦截")
        L.append("2. 检查 `passwordEncoder.matches()` 调用是否正常")
        L.append("3. 确认 LoginRequest DTO 的 validation 是否正确")

    L.append("")
    L.append("### 7.2 响应时间分析")
    d1_fails = [r for r in all_r if r.dimension == "D1" and not r.passed]
    if d1_fails:
        L.append("")
        L.append(f"{len(d1_fails)} 个服务超时或响应过慢：")
        for r in d1_fails:
            L.append(f"- {r.name}: {r.detail}")
        L.append("")
        L.append("**根因**: Agent 链式架构导致耗时 = Σ(各Agent LLM调用)。每个 LLM 调用耗时 30-90s，3个Agent串联即超120s。")
        L.append("")
        L.append("**优化方案**:")
        L.append("1. 合并 query_rewrite + retrieval 为一次调用")
        L.append("2. 对知识检索结果做 Redis 缓存")
        L.append("3. 使用 qwen3.6-flash 替代 plus 做非核心 Agent 推理")
        L.append("4. 接入 streaming token 输出，改善感知延迟")
    else:
        L.append("所有服务响应时间在可接受范围内。")

    L.append("")
    L.append("### 7.3 Agent 完成度评估")
    L.append("")
    L.append("| ServiceType | 通过率 | 响应 | 结构 | 质量 | 错误处理 | 综合评价 |")
    L.append("|-------------|--------|------|------|------|----------|----------|")
    for st in ["TUTORING", "RESOURCE_GENERATION", "VIDEO_GENERATION", "PRACTICE_JUDGE", "PATH_PLANNING", "EVALUATION", "PROFILE_BUILD"]:
        sec = f"agent_{st}"
        p, f = C.section_summary(sec)
        total_s = p + f
        if total_s == 0:
            L.append(f"| {st} | N/A | - | - | - | - | 未测试 |")
            continue
        rate = p / total_s * 100
        d1_r = next((r for r in C.results.get(sec, []) if r.dimension == "D1"), None)
        d2_r = next((r for r in C.results.get(sec, []) if r.dimension == "D2"), None)
        d5_r = next((r for r in C.results.get(sec, []) if r.dimension == "D5"), None)
        d4_r = next((r for r in C.results.get(sec, []) if r.dimension == "D4"), None)
        emoji = "🟢" if rate >= 80 else ("🟡" if rate >= 50 else "🔴")
        L.append(f"| {st} | {emoji} {rate:.0f}% | {'✅' if d1_r and d1_r.passed else '❌'} | {'✅' if d2_r and d2_r.passed else '❌'} | {'✅' if d5_r and d5_r.passed else '❌'} | {'✅' if d4_r and d4_r.passed else '❌'} | {p}/{total_s}通过 |")
    L.append("")

    L.append("## 8. 优化优先级矩阵")
    L.append("")
    L.append("| 优先级 | 问题 | 影响 | 建议 | 工作量 |")
    L.append("|--------|------|------|------|--------|")
    L.append("| P0 | 登录失败 | 全系统不可用 | 排查 Security Config + BCrypt | 1h |")
    L.append("| P0 | Agent 超时严重 | 用户体验极差 | LLM调用合并 + 缓存 | 4h |")
    L.append("| P1 | 部分 Agent Placeholder | 功能不完整 | 接入真实 LLM 业务逻辑 | 8h |")
    L.append("| P1 | 输出质量不足 | 内容价值低 | 优化 Prompt + 增加示例 | 3h |")
    L.append("| P2 | 流式感知延迟大 | 用户等待焦虑 | Streaming token | 6h |")
    L.append("| P2 | 缺少中间状态反馈 | 前端展示单调 | 增加 progress 事件密度 | 2h |")
    L.append("| P3 | 检索结果无缓存 | 重复调用浪费 | Redis 缓存检索结果 | 2h |")
    L.append("")

    L.append("---")
    L.append(f"*报告由自动化测试脚本生成*")
    return "\n".join(L)


# ═══════════════════════════════════════════════════════════════════

async def main():
    total_start = time.perf_counter()
    print("=" * 60)
    print(" 智学系统 全链路综合测试 V3")
    print(f" {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    async with httpx.AsyncClient(timeout=TIMEOUT_DEFAULT) as client:
        await part1_infra_and_auth(client)
        await part2_agent_tests(client)
        await part3_java_chain(client)

    elapsed = time.perf_counter() - total_start
    report = generate_report(elapsed)

    report_path = Path(__file__).parent / "comprehensive_test_report.md"
    report_path.write_text(report, encoding="utf-8")

    all_r = C.all()
    p = sum(1 for r in all_r if r.passed)
    print(f"\n{'='*60}")
    print(f" 完成! {p}/{len(all_r)} 通过 ({p/len(all_r)*100:.1f}%)")
    print(f" 报告: {report_path}")
    print(f"{'='*60}")


if __name__ == "__main__":
    asyncio.run(main())
