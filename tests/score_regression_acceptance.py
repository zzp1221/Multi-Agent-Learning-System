"""评分扣分点回归验收脚本。

默认连接当前热更新环境，覆盖评委容易实际点击到的链路：
登录、问答 SSE、资源推送、练习生成/判题、学习评估、画像、错题本。
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, asdict
from typing import Any
from urllib import error, request


JAVA_BASE = "http://localhost:8081"
REPORT_PATH = "tmp/score-regression-acceptance.json"


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str


def pass_check(name: str, detail: str = "") -> CheckResult:
    return CheckResult(name, True, detail)


def fail_check(name: str, detail: str) -> CheckResult:
    return CheckResult(name, False, detail)


def http_json(method: str, path: str, payload: dict[str, Any] | None = None, token: str | None = None, timeout: int = 30) -> Any:
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = request.Request(f"{JAVA_BASE}{path}", data=body, headers=headers, method=method)
    with request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def stream_events(path: str, payload: dict[str, Any], token: str, timeout: int = 180) -> list[dict[str, Any]]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Accept": "text/event-stream",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }
    req = request.Request(f"{JAVA_BASE}{path}", data=body, headers=headers, method="POST")
    events: list[dict[str, Any]] = []
    current_event = "message"
    current_data: list[str] = []
    with request.urlopen(req, timeout=timeout) as resp:
        for raw_line in resp:
            line = raw_line.decode("utf-8").rstrip("\r\n")
            if not line:
                if current_data:
                    events.append(parse_sse_event(current_event, "\n".join(current_data)))
                current_event = "message"
                current_data = []
                continue
            if line.startswith("event:"):
                current_event = line[6:].strip()
            elif line.startswith("data:"):
                current_data.append(line[5:].strip())
    if current_data:
        events.append(parse_sse_event(current_event, "\n".join(current_data)))
    return events


def parse_sse_event(event_name: str, data: str) -> dict[str, Any]:
    try:
        payload = json.loads(data)
    except json.JSONDecodeError:
        payload = {"raw": data}
    return {"event": event_name, "data": payload}


def register_and_login() -> tuple[str, str]:
    login_id = f"score_reg_{uuid.uuid4().hex[:10]}"
    password = "Test@123456"
    payload = {
        "loginId": login_id,
        "password": password,
        "fullName": "评分回归验收用户",
        "majorCode": "CS",
    }
    http_json("POST", "/api/auth/register", payload)
    auth = http_json("POST", "/api/auth/login", {"loginId": login_id, "password": password})
    return auth["token"], auth["user"]["userId"]


def create_conversation(token: str) -> str:
    response = http_json("POST", "/api/conversations", {}, token=token)
    return response["conversationId"]


def submit_task(token: str, conversation_id: str, service_type: str, params: dict[str, Any]) -> str:
    response = http_json(
        "POST",
        "/api/smart-engine/submit",
        {"conversationId": conversation_id, "serviceType": service_type, "params": params},
        token=token,
    )
    return response["taskId"]


def wait_task(token: str, task_id: str, timeout_seconds: int = 240) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds
    last: dict[str, Any] = {}
    while time.time() < deadline:
        last = http_json("GET", f"/api/smart-engine/tasks/{task_id}", token=token)
        if last.get("status") in {"COMPLETED", "FAILED", "CANCELLED"}:
            return last
        time.sleep(2)
    raise TimeoutError(f"任务超时: {task_id}, last={last}")


def get_response_summary(task: dict[str, Any]) -> dict[str, Any]:
    summary = task.get("responseSummary")
    return summary if isinstance(summary, dict) else {}


def assert_completed_task(task: dict[str, Any], name: str) -> CheckResult:
    status = task.get("status")
    detail = f"status={status}, error={task.get('errorCode') or ''}:{task.get('errorMessage') or ''}"
    return CheckResult(name, status == "COMPLETED", detail)


def assert_resource_relevance(summary: dict[str, Any]) -> tuple[bool, str]:
    response_summary = get_response_summary(summary)
    resources = response_summary.get("pushedResources") or response_summary.get("resources") or []
    if not resources:
        task_summary = str(response_summary.get("summary") or "")
        positive = any(term in task_summary.lower() for term in ["红黑树", "red-black", "red black", "rbtree", "旋转", "染色"])
        negative_terms = ["dify", "openai", "javascript", "python 自动化", "广播"]
        negative = any(term in task_summary.lower() for term in negative_terms)
        empty_ok = "未命中" in task_summary or "暂无" in task_summary or "无可推送" in task_summary or "未找到" in task_summary
        return (positive and not negative) or empty_ok, task_summary or "无资源且缺少明确空结果说明"
    joined = json.dumps(resources, ensure_ascii=False).lower()
    positive = any(term in joined for term in ["红黑树", "red-black", "red black", "rbtree", "旋转", "染色"])
    negative_terms = ["dify", "openai", "javascript", "python 自动化", "广播"]
    negative = any(term in joined for term in negative_terms)
    return positive and not negative, f"resources={joined[:300]}"


def assert_qna_stream(token: str, conversation_id: str) -> list[CheckResult]:
    events = stream_events(
        f"/api/conversations/{conversation_id}/messages/stream",
        {
            "message": "请在80字内总结红黑树的核心思想",
            "serviceType": "TUTORING",
            "webSearchEnabled": False,
            "reasoningMode": "NORMAL",
        },
        token,
    )
    chunks = []
    untagged_internal = False
    for event in events:
        payload = event.get("data", {}).get("payload", {})
        if event.get("event") == "result_chunk":
            text = str(payload.get("text") or "")
            stage = str(payload.get("stage") or "")
            if stage and stage != "tutoring":
                continue
            if not stage and any(marker in text for marker in ["原始查询", "检索查询", "来源摘要", "Critic OK", "复核"]):
                untagged_internal = True
            chunks.append(text)
        if event.get("event") == "progress" and str(payload.get("message") or "") in chunks:
            mixed_internal = True
    answer = "".join(chunks).strip()
    return [
        CheckResult("问答 SSE 正常完成", bool(answer) and not any(item.get("event") == "error" for item in events), answer[:120]),
        CheckResult("问答长度遵循用户要求", len(answer) <= 80, f"length={len(answer)}, answer={answer[:120]}"),
        CheckResult("问答不混入内部进度/Critic", not untagged_internal and "[处理中]" not in answer and "Critic OK" not in answer and "原始查询" not in answer and "检索查询" not in answer, answer[:120]),
    ]


def find_question_batch(task: dict[str, Any]) -> dict[str, Any] | None:
    summary = get_response_summary(task)
    if isinstance(summary.get("practiceQuestionBatch"), dict):
        return summary["practiceQuestionBatch"]
    if isinstance(summary.get("questions"), list):
        return summary
    if summary.get("assetType") == "QUIZ" and isinstance(summary.get("questions"), list):
        return summary
    assets = summary.get("generatedAssets")
    if isinstance(assets, list):
        for asset in assets:
            if isinstance(asset, dict) and asset.get("assetType") == "QUIZ" and isinstance(asset.get("questions"), list):
                return asset
    return None


def build_answers_for_batch(batch: dict[str, Any]) -> dict[str, str]:
    answers: dict[str, str] = {}
    wrong_objective_used = False
    for question in batch.get("questions", []):
        if not isinstance(question, dict):
            continue
        question_id = str(question.get("questionId") or "")
        if not question_id:
            continue
        if str(question.get("questionType") or "").upper() == "SHORT_ANSWER":
            answers[question_id] = str(question.get("answer") or "我会先说明核心性质，再结合旋转和染色解释更新后的修复过程。")
        else:
            if not wrong_objective_used:
                answers[question_id] = "__验收故意错答__"
                wrong_objective_used = True
            else:
                answers[question_id] = str(question.get("answer") or "")
    return answers


def objective_question_ids(batch: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for question in batch.get("questions", []):
        if not isinstance(question, dict):
            continue
        if str(question.get("questionType") or "").upper() != "SHORT_ANSWER":
            question_id = str(question.get("questionId") or "")
            if question_id:
                ids.add(question_id)
    return ids


def assert_judge_result(task: dict[str, Any], batch: dict[str, Any]) -> CheckResult:
    summary = get_response_summary(task)
    items = summary.get("items") if isinstance(summary.get("items"), list) else []
    if not items:
        judge_result = summary.get("judgeResult")
        if isinstance(judge_result, dict):
            items = judge_result.get("items") if isinstance(judge_result.get("items"), list) else []
    expected_count = len(batch.get("questions", []))
    objective_ids = objective_question_ids(batch)
    wrong_objective_recorded = any(
        isinstance(item, dict)
        and str(item.get("questionId") or "") in objective_ids
        and str(item.get("learnerAnswer") or "") == "__验收故意错答__"
        and item.get("isCorrect") is False
        for item in items
    )
    return CheckResult("练习判题结果完整", len(items) == expected_count and wrong_objective_recorded, json.dumps(items, ensure_ascii=False)[:300])


def wait_profile_non_empty(token: str, user_id: str, timeout_seconds: int = 120) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds
    last: dict[str, Any] = {}
    while time.time() < deadline:
        last = http_json("GET", f"/api/users/{user_id}/profile/current", token=token)
        profile_json = last.get("profile")
        if isinstance(profile_json, dict) and bool(profile_json):
            return last
        time.sleep(3)
    return last


def run() -> list[CheckResult]:
    results: list[CheckResult] = []
    token, user_id = register_and_login()
    conversation_id = create_conversation(token)
    results.append(pass_check("登录与会话创建", f"userId={user_id}, conversationId={conversation_id}"))
    results.extend(assert_qna_stream(token, conversation_id))

    task_id = submit_task(
        token,
        conversation_id,
        "RESOURCE_PUSH",
        {
            "resourceType": "READING",
            "learningContext": {"course": "数据结构", "chapter": "红黑树"},
            "profile": {
                "studentLevel": "BASIC",
                "weakPoints": ["红黑树旋转与染色"],
                "currentGoal": {"shortTerm": "掌握红黑树插入修复"},
            },
            "query": "红黑树旋转与染色 拓展阅读",
        },
    )
    resource_status = wait_task(token, task_id)
    relevance_ok, relevance_detail = assert_resource_relevance(resource_status)
    results.append(assert_completed_task(resource_status, "资源推送任务完成"))
    results.append(CheckResult("资源推送主题相关", relevance_ok, relevance_detail))

    task_id = submit_task(
        token,
        conversation_id,
        "RESOURCE_GENERATION",
        {
            "resourceType": "QUIZ",
            "resourceTypes": ["QUIZ"],
            "course": "数据结构",
            "difficulty": "basic",
            "keyPoints": "红黑树旋转与染色",
            "topic": "红黑树旋转与染色",
            "query": "数据结构 红黑树旋转与染色 基础 练习题",
            "learningContext": {"course": "数据结构", "chapter": "红黑树"},
        },
    )
    practice_status = wait_task(token, task_id, timeout_seconds=360)
    results.append(assert_completed_task(practice_status, "练习题生成任务完成"))
    practice_batch = find_question_batch(practice_status)
    results.append(CheckResult("练习题批含真实来源", isinstance(practice_batch, dict) and bool(practice_batch.get("provider")) and bool(practice_batch.get("model")), json.dumps(practice_batch or {}, ensure_ascii=False)[:240]))

    task_id = submit_task(
        token,
        conversation_id,
        "PRACTICE_JUDGE",
        {
            "topic": "红黑树旋转与染色",
            "query": "红黑树练习题判题",
            "practiceQuestionBatch": practice_batch or {},
            "practiceQuestions": (practice_batch or {}).get("questions", []),
            "answers": build_answers_for_batch(practice_batch or {}),
            "learningContext": {"course": "数据结构", "chapter": "红黑树"},
        },
    )
    judge_status = wait_task(token, task_id, timeout_seconds=360)
    results.append(assert_completed_task(judge_status, "练习判题任务完成"))
    results.append(assert_judge_result(judge_status, practice_batch or {}))

    mistakes = http_json("GET", "/api/mistakes?page=0&size=5", token=token)
    mistake_items = mistakes.get("items") if isinstance(mistakes, dict) else None
    results.append(CheckResult("错题本错题入库", isinstance(mistake_items, list) and len(mistake_items) >= 1, json.dumps(mistakes, ensure_ascii=False)[:300]))

    task_id = submit_task(
        token,
        conversation_id,
        "LEARNING_EVALUATION",
        {
            "range": "知识基础",
            "dimensions": ["知识基础"],
            "assessmentDimension": "知识基础",
            "learningContext": {"course": "数据结构", "chapter": "红黑树"},
            "messages": [{"role": "user", "content": "我刚做完红黑树练习，想评估自己对旋转和染色的掌握。"}],
        },
    )
    evaluation_status = wait_task(token, task_id, timeout_seconds=300)
    results.append(assert_completed_task(evaluation_status, "学习评估任务完成"))

    profile = wait_profile_non_empty(token, user_id)
    profile_json = profile.get("profile") or {}
    results.append(CheckResult("画像沉淀非空", isinstance(profile_json, dict) and bool(profile_json), f"profileKeys={list(profile_json)[:8]}"))
    return results


if __name__ == "__main__":
    checks: list[CheckResult] = []
    try:
        checks = run()
    except (error.HTTPError, error.URLError, TimeoutError, KeyError, ValueError) as exc:
        checks.append(fail_check("验收脚本执行", f"{type(exc).__name__}: {exc}"))
    report = {"passed": all(item.passed for item in checks), "checks": [asdict(item) for item in checks]}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as file:
        json.dump(report, file, ensure_ascii=False, indent=2)
    raise SystemExit(0 if report["passed"] else 1)
