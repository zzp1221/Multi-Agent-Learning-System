# Planner + Reviewer 多智能体核心闭环实施计划

日期：2026-05-30
分支：`plan`

## 实施状态

| 阶段 | 状态 | 完成人 | 审查 agent id/nickname | 验证命令 | 日期 |
|---|---|---|---|---|---|
| P0 硬约束与无兜底 | ✅ 已完成 | Codex | `019e78ea-c3f0-7b31-b107-ea92718bc66d` / Mendel | `py_compile`；`pytest tests\test_conversation_planner.py tests\test_review_agents.py tests\test_supervisor.py tests\test_resource_bundle_workflow.py tests\test_routing_agents.py tests\test_provider_config.py -q`（75 passed） | 2026-05-30 |
| P1 后端 Planner | ✅ 已完成 | Codex | `019e78ea-c3f0-7b31-b107-ea92718bc66d` / Mendel | 同 P0 后端验证 | 2026-05-30 |
| P2 Supervisor 自动执行 | ✅ 已完成 | Codex | `019e78ea-c3f0-7b31-b107-ea92718bc66d` / Mendel | 同 P0 后端验证 | 2026-05-30 |
| P3 Reviewer 接入 | ✅ 已完成 | Codex | `019e78ea-c3f0-7b31-b107-ea92718bc66d` / Mendel | 同 P0 后端验证；源码无 `RuleBasedPlanningLLM` / `RuleBasedReviewLLM` / `_fallback_review` 残留 | 2026-05-30 |
| P4 前端展示 | ✅ 已完成 | Codex | `019e78e6-4a56-7dd2-96be-2a6f4b979468` / Cicero | `cd frontend && npx.cmd tsc --noEmit`；`cd frontend && npx.cmd vite build` | 2026-05-30 |
| P5 热更新与验收 | ✅ 已完成 | Codex | `019e78ea-c3f0-7b31-b107-ea92718bc66d` / Mendel | `git diff --check`；`docker compose ps`；`curl http://localhost:8081/api/health`；`curl http://localhost:8000/health`；`docker cp` 热更新 Python/Frontend；容器内 `python -m py_compile`；`nginx -s reload`；Python `kill -HUP 1` | 2026-05-30 |

后续 agent 开工前必须先读本表：已完成阶段只允许修缺陷，不重复实现。

## 硬约束

- 当前联调/演示环境只允许通过 `docker cp` 热更新，禁止 `docker compose build`、`docker compose up --build`、`--force-recreate` 和重建容器。
- Planner、Reviewer、可发布生成内容不得使用模板、规则或启发式兜底冒充 LLM；LLM provider 不可用、JSON 不合法、Step 非白名单、缺 provider/model 时必须失败。
- 每个阶段实现后必须由子 agent 只读审查，通过后才允许在上表标记完成。
- 不修改 SSE wire 格式，不新增强依赖 SSE event 类型；只复用现有 `progress` / `done` payload 字段。
- 不新增公开 API、DTO、环境变量或 Docker 服务拓扑。

## 已实现范围

### P0 硬约束与无兜底

- 已确认当前分支为 `plan`。
- `PlanningLLMClientFactory` 和 `ReviewLLMClientFactory` 在 provider 不可用时直接抛错。
- 已移除 Planner/Reviewer 专属规则兜底类和导出：`RuleBasedPlanningLLM`、`RuleBasedReviewLLM`。
- `CriticAgent` / `SafetyAgent` 的 LLM review 失败直接抛错，不返回启发式结果。
- 源码检索无 `_fallback_review` 残留。

### P1 后端 Planner

- 新增独立对话计划模型 `ConversationPlan` / `ConversationPlanStep`，避免复用路径规划业务模型。
- 新增 LLM Planner 运行时，要求真实 LLM 生成 JSON 并经 Pydantic 校验。
- Step 白名单固定为：`TUTORING`、`RESOURCE_GENERATION`、`RESOURCE_PUSH`、`PRACTICE_JUDGE`、`PATH_PLANNING`、`EVALUATION`、`PROFILE_BUILD`，以及已注册内部 Agent。
- Planner 输出必须包含 provider/model，非法 Step 或缺元数据时失败。

### P2 Supervisor 自动执行

- `PythonAgentSupervisor` 注册 Planner 工厂和 `critic` Agent。
- 对符合条件的对话任务先发送 `planning` progress，再按 Plan 顺序执行 Step。
- 服务型 Step 复用现有服务能力；嵌套执行设置 `plannerNested=true`，嵌套任务禁止再次启动 Planner。
- 所有状态继续通过现有 `progress` payload 表达，没有新增 SSE event 类型。

### P3 Reviewer 接入

- 对关键结果接入 Critic：`RESOURCE_GENERATION`、`VIDEO_GENERATION`、`PATH_PLANNING`、`EVALUATION`、`LEARNING_EVALUATION`、深度/检索增强/图片类 `TUTORING`。
- Critic 失败会输出 `error` 和 `done(status=FAILED)`。
- `criticReview` 写入 `params`，最终 `done.payload` 合并 `learningPlan`、`criticReview`、`summary`。
- `ResourceBundleWorkflow` 中 Critic/Safety/provenance/禁用 fallback 质量门失败会升级为整任务失败；普通资源失败仍保留 `PARTIAL_FAILED`。
- 本轮不做自动重试，避免无限链路和现场不稳定。

### P4 前端展示

- LearningStudio 类型、任务快照和历史结果新增 `LearningPlanView`、`LearningPlanStepView`、`CriticReviewView`。
- 前端只解析后端 `payload.learningPlan`、`payload.criticReview` 和 `task.responseSummary`，不本地生成默认 Plan 或默认 Critic 文案。
- 结果面板新增“协作计划 / 质量复核”区域，展示 Step 标题、Agent/服务名、状态、Critic verdict、issues、suggestions、summaryText。
- 选中历史结果时只展示该历史记录自己的 Plan/Critic；缺字段显示空态，不回退当前任务。

## P5 热更新与验收记录

1. `git diff --check` 通过，仅有 Windows 行尾提示。
2. `docker compose ps` 显示 Java、前端、Python、Postgres、Mongo、Redis 均 Up/healthy。
3. `curl -s http://localhost:8081/api/health` 返回 `{"status":"UP"}`。
4. `curl -s http://localhost:8000/health` 返回 Python Agent ok。
5. Python 仅使用 `docker cp` 同步改动文件到 `zhixue-python-agent:/app/<same path>`，容器内 `python -m py_compile ...` 通过，再执行 `docker exec zhixue-python-agent kill -HUP 1`。
6. 前端仅使用 `docker cp frontend/dist/. zhixue-frontend:/usr/share/nginx/html/`，再执行 `docker exec zhixue-frontend nginx -s reload`。
7. 热更新后前端首页 `http://localhost/` 返回 200。

## 验证记录

- 后端语法检查通过：`server.py`、Planner/Reviewer/Supervisor/ResourceBundle 相关文件全部 `py_compile` 通过。
- 后端测试通过：`75 passed, 266 warnings`。
- 前端类型检查通过：`npx.cmd tsc --noEmit`。
- 前端生产构建通过：`npx.cmd vite build`，仅保留既有大 chunk 警告。

## Assumptions

- 本轮范围是核心闭环，不做 Critic 后自动重试。
- 高成本 Step 自动执行仅限白名单，并通过 `plannerNested=true` 防止递归 Planner。
- 存储容灾类 fallback 可保留，但不得生成或伪造对外内容。
- 现有未提交改动视为用户或其他任务改动，不回滚无关变更。
