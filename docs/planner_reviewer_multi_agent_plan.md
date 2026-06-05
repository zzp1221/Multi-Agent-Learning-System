# Planner + Reviewer 多智能体核心闭环实施计划

> 2026-06-05 归档说明：对话 Planner 运行时已作为遗留死代码删除；当前生产链路保留 Critic/Reviewer 复核，不再包含 `conversation_planner.py`、`conversation_plan.py` 或 `PlanningLLMClientFactory`。

日期：2026-06-03
分支记录：`plan`（历史实施分支）
当前代码状态：对话 Planner 已归档并从运行时代码删除；Python Supervisor 当前仅保留既有路由执行和 Critic/Reviewer 质量复核能力，未新增公开 API、SSE event 类型或 Docker 服务；`PERSONALIZED_LEARNING` 已纳入允许服务类型和质量审查范围。

## 实施状态

| 阶段 | 状态 | 完成人 | 审查 agent id/nickname | 验证命令 | 日期 |
|---|---|---|---|---|---|
| P0 硬约束与无兜底 | 已完成 | Codex | `019e78ea-c3f0-7b31-b107-ea92718bc66d` / Mendel | `py_compile`；`pytest tests\test_conversation_planner.py tests\test_review_agents.py tests\test_supervisor.py tests\test_resource_bundle_workflow.py tests\test_routing_agents.py tests\test_provider_config.py -q`（75 passed） | 2026-05-30 |
| P1 后端 Planner | 已完成 | Codex | `019e78ea-c3f0-7b31-b107-ea92718bc66d` / Mendel | 同 P0 后端验证 | 2026-05-30 |
| P2 Supervisor 自动执行 | 已完成 | Codex | `019e78ea-c3f0-7b31-b107-ea92718bc66d` / Mendel | 同 P0 后端验证 | 2026-05-30 |
| P3 Reviewer 接入 | 已完成 | Codex | `019e78ea-c3f0-7b31-b107-ea92718bc66d` / Mendel | 同 P0 后端验证；源码无 `RuleBasedPlanningLLM` / `RuleBasedReviewLLM` / `_fallback_review` 残留 | 2026-05-30 |
| P4 前端展示 | 已完成 | Codex | `019e78e6-4a56-7dd2-96be-2a6f4b979468` / Cicero | `cd frontend && npx.cmd tsc --noEmit`；`cd frontend && npx.cmd vite build` | 2026-05-30 |
| P5 热更新与验收 | 已完成 | Codex | `019e78ea-c3f0-7b31-b107-ea92718bc66d` / Mendel | `git diff --check`；`docker compose ps`；`curl http://localhost:8081/api/health`；`curl http://localhost:8000/health`；`docker cp` 热更新 Python/Frontend；容器内 `python -m py_compile`；`nginx -s reload`；Python `kill -HUP 1` | 2026-05-30 |
| P6 文档对齐 | 已完成 | Codex | 本轮文档复核 | README、架构文档、技术报告、部署指南同步到当前代码路径 | 2026-05-31 |

后续 agent 开工前必须先读本表：已完成阶段只允许修缺陷，不重复实现。

## 硬约束

- 当前联调/演示环境只允许通过 `docker cp` 热更新，禁止 `docker compose build`、`docker compose up --build`、`--force-recreate` 和重建容器。
- Reviewer、Critic 和可发布生成内容不得使用模板、规则或启发式兜底冒充 LLM；LLM provider 不可用、JSON 不合法、缺 provider/model 时必须失败。
- 不修改 SSE wire format，不新增强依赖 SSE event 类型；只复用现有 `progress` / `done` payload 字段。
- 不新增公开 API、DTO、环境变量或 Docker 服务拓扑。
- Java 仍是唯一外部入口；当前 Python Supervisor 内仅保留路由执行与 Reviewer/Critic 复核，不再包含对话 Planner。

## 当前接入点

| 模块 | 文件 | 说明 |
|---|---|---|
| Reviewer / Critic | `python-agent/src/ai_modules/agents/common_agents.py` | 质量复核、事实支撑和安全检查 |
| Supervisor | `python-agent/src/ai_modules/supervisor.py` | 执行当前服务路由，并在关键任务后接 Critic |
| 前端展示 | `frontend/src/pages/LearningStudioDemoPage.*` | 展示质量复核与任务结果 |

## 已实现范围

### P0 硬约束与无兜底

- `PlanningLLMClientFactory` 和 `ReviewLLMClientFactory` 在 provider 不可用时直接抛错。
- 已移除 Planner/Reviewer 专属规则兜底类和导出。
- `CriticAgent` / `SafetyAgent` 的 LLM review 失败直接抛错，不返回启发式结果。
- 源码检索无 `_fallback_review` 残留。

### P1 后端 Planner（历史归档）

- 历史实现曾新增独立对话计划模型 `ConversationPlan` / `ConversationPlanStep` 和对话 Planner 运行时。
- 2026-06-05 已确认该对话 Planner 运行时不可达，并删除 `conversation_plan.py`、`conversation_planner.py`、`planning_llm.py` 与孤立测试。
- 当前运行链路不再生成或执行对话 Plan，也不再使用 `plannerNested` 防递归标记。

### P2 Supervisor 自动执行（历史归档）

- 历史实现曾在 `PythonAgentSupervisor` 中注册 Planner 工厂，并按 Plan 顺序执行 Step。
- 当前 Supervisor 已删除 Planner 工厂、Plan Step 自动执行和 `plannerNested` 相关分支。
- 所有状态继续通过现有 `progress` payload 表达，没有新增 SSE event 类型。

### P3 Reviewer 接入

- 对关键结果接入 Critic：`PERSONALIZED_LEARNING`、`RESOURCE_GENERATION`、`VIDEO_GENERATION`、`PATH_PLANNING`、`EVALUATION`、`LEARNING_EVALUATION`、深度/检索增强/图片类 `TUTORING`。
- Critic 失败会输出 `error` 和 `done(status=FAILED)`。
- `criticReview` 写入 `params`，最终 `done.payload` 合并 `learningPlan`、`criticReview`、`summary`。
- `ResourceBundleWorkflow` 中 Critic/Safety/provenance/禁用 fallback 质量门失败会升级为整任务失败；普通资源失败仍保留 `PARTIAL_FAILED`。
- 本轮不做自动重试，避免无限链路和现场不稳定。

### P4 前端展示

- LearningStudio 类型、任务快照和历史结果保留 `criticReview` 展示能力；历史任务如含 `learningPlan` 可按已有数据展示。
- 前端只解析后端 payload 和 `task.responseSummary`，不本地生成默认 Plan 或默认 Critic 文案。
- 结果面板展示质量复核：verdict、issues、suggestions、summaryText。
- 选中历史结果时只展示该历史记录自己的 Critic/历史 Plan 数据；缺字段显示空态，不回退当前任务。

## 运行边界

- Reviewer/Critic 不是新的微服务，也不需要新端口。
- Reviewer/Critic 不直接写数据库；通过现有 Supervisor params 和 SSE payload 流动。
- SmartEngine 长任务仍由 Redis Streams Worker 执行；Reviewer/Critic 只是 Worker 调用 Supervisor 时的内部质量复核逻辑。
- 对话直连流式仍走 Java `ConversationService -> Python /internal/smart-engine/stream`。

## P5 热更新与验收记录

1. `git diff --check` 通过，仅有 Windows 行尾提示。
2. `docker compose ps` 显示 Java、前端、Python、Postgres、Mongo、Redis 均 Up/healthy。
3. `curl -s http://localhost:8081/api/health` 返回 `{"status":"UP"}`。
4. `curl -s http://localhost:8000/health` 返回 Python Agent ok。
5. Python 仅使用 `docker cp` 同步改动文件到 `zhixue-python-agent:/app/<same path>`，容器内 `python -m py_compile ...` 通过，再执行 `docker exec zhixue-python-agent kill -HUP 1`。
6. 前端仅使用 `docker cp frontend/dist/. zhixue-frontend:/usr/share/nginx/html/`，再执行 `docker exec zhixue-frontend nginx -s reload`。
7. 热更新后前端首页 `http://localhost/` 返回 200。

## 验证记录

历史验证：

- 历史后端语法检查通过：`server.py`、Planner/Reviewer/Supervisor/ResourceBundle 相关文件全部 `py_compile` 通过。
- 后端测试通过：`75 passed, 266 warnings`。
- 前端类型检查通过：`npx.cmd tsc --noEmit`。
- 前端生产构建通过：`npx.cmd vite build`，仅保留既有大 chunk 警告。

持续验证建议：

```bash
cd python-agent
pytest tests/test_review_agents.py tests/test_supervisor.py -q

cd frontend
npx tsc --noEmit
npx vite build
```

## Assumptions

- 本轮范围是核心闭环，不做 Critic 后自动重试。
- 高成本 Step 自动执行和 `plannerNested=true` 防递归逻辑属于历史 Planner 实现，当前运行时代码已删除。
- 存储容灾类 fallback 可保留，但不得生成或伪造对外内容。
- 现有未提交改动视为用户或其他任务改动，不回滚无关变更。
