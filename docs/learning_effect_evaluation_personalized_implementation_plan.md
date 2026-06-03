# 学习效果评估与前端收敛实现计划

日期：2026-06-03
目标分支：`feature/personalized-learning-multi-agent`

本文档补充 `docs/personalized_learning_multi_agent_refactor_plan.md`，专门约束学习效果评估和前端展示如何并入 `PERSONALIZED_LEARNING` 多智能体闭环，避免与路径规划、资源推送重构方案冲突。

## 0. 分支与协作约束

本改造必须在 feature 分支上进行：

```bash
git switch -c feature/personalized-learning-multi-agent
```

如果该分支已经存在并且当前已经在该分支上，则继续使用当前分支，不要重复创建同名分支。

开工前必须确认：

```bash
git status --short --branch
docker compose ps
curl -s http://localhost:8081/api/health
```

协作约束：

- 不修改 Docker 服务拓扑、数据库 schema、SSE `event:` / `data:` 协议格式、Java 唯一入口契约。
- 当前联调/演示环境只允许 `docker cp` 热更新，禁止 `docker compose build`、`docker compose up --build`、`--force-recreate` 和重建容器。
- 如果另一个会话的 agent 也在本分支工作，改动前先读 `docs/experiment_log.md` 和本计划；涉及同一文件时先通过文档协调，避免覆盖对方未提交改动。
- 每次只完成一个可验证问题，验证通过后再进入下一步。

## 1. 改造目标

赛题中的学习效果评估要求是：

> 通过实时跟踪学生的学习行为、练习测试情况、资源使用反馈等数据，依托大模型的数据分析能力实现对学生学习效果的多维度、精准评估；并根据评估结果及时动态调整学习资源推送策略和学习计划，实现学习方案的持续优化。

结合已有个性化学习方案计划，学习效果评估不再作为孤立的报告生成能力，而应成为 `PERSONALIZED_LEARNING` 链路中的“掌握度诊断层”：

```text
profile -> evaluation -> query_rewrite -> retrieval -> path_planning -> resource_push -> critic
```

评估模块职责收敛为：

- 汇总画像、对话、练习、错题、资源使用和已有评测结果。
- 生成结构化 `masteryDiagnosis`。
- 为路径规划提供薄弱知识点、优先级、证据和检查点建议。
- 为资源推送提供每个薄弱点的推荐资源类型和策略提示。
- 为前端提供简洁的诊断摘要，而不是暴露大量内部评估参数。

## 2. 明确不做的事

为避免职责冲突，学习效果评估不得承担以下职责：

- 不直接生成完整学习计划。
- 不直接推送资源列表。
- 不绕过 `PathPlanningAgent` 修改 `learningPath`。
- 不绕过 `ResourcePushAgent` 修改 `resourcePushPlan`。
- 不在前端作为与“学习路径规划”“资源推送”平级的主入口展示。

保留 `LEARNING_EVALUATION` / `EVALUATION` 作为专项评估或调试入口，但评委演示和主业务入口应使用 `PERSONALIZED_LEARNING`。

## 3. 共享状态契约

在多智能体链路中统一使用以下共享字段：

```json
{
  "profileAnalysis": {},
  "masteryDiagnosis": {},
  "retrievalEvidence": [],
  "learningPath": {},
  "resourcePushPlan": {},
  "pushedResources": [],
  "criticReview": {},
  "agentTrace": []
}
```

兼容字段：

- `evaluationResult`：保留给历史前端和专项评估结果卡使用。
- `judgeResult`：练习判题结果仍保留，评估层可转成 `masteryDiagnosis` 的证据。
- `profile`：旧画像输入保留，但主流程应优先消费 `profileAnalysis`。

优先级规则：

- `PathPlanningAgent` 输入优先级：`profileAnalysis` > `profile` > `snapshot`。
- 掌握度输入优先级：`masteryDiagnosis` > `judgeResult` > `evaluationResult`。
- 证据输入优先级：`retrievalEvidence` > `retrievalSummary` > `snapshot`。
- `ResourcePushAgent` 必须优先消费 `learningPath.steps`，按步骤绑定资源。

## 4. masteryDiagnosis 结构设计

建议新增或扩展 Pydantic 模型，结构如下：

```json
{
  "diagnosisSource": "evaluation",
  "overallLevel": "BASIC",
  "overallMasteryScore": 0.58,
  "confidence": 0.72,
  "targetScope": {
    "course": "计算机网络",
    "chapter": "TCP 拥塞控制",
    "knowledgePoints": ["慢启动", "拥塞避免", "快重传"]
  },
  "knowledgeDiagnoses": [
    {
      "knowledgePoint": "慢启动",
      "masteryScore": 0.42,
      "status": "weak",
      "priority": 1,
      "evidence": [
        "练习题正确率低于 60%",
        "最近对话多次询问拥塞窗口变化"
      ],
      "errorPatterns": ["概念混淆", "过程顺序不清"],
      "nextFocus": "先补齐慢启动窗口增长规则",
      "recommendedResourceTypes": ["DOCUMENT", "VIDEO", "QUIZ"]
    }
  ],
  "behaviorSignals": {
    "practiceAccuracy": 0.6,
    "recentQuestionCount": 8,
    "reviewCount": 2,
    "resourceDownloads": 3
  },
  "planAdjustmentHints": {
    "shouldRefreshPlan": true,
    "refreshReason": "核心薄弱点发生变化",
    "strategy": "先补概念，再做专项题"
  },
  "summaryText": "学生在 TCP 拥塞控制主题上已具备基础概念，但慢启动与拥塞避免的过程边界仍不稳定。"
}
```

字段要求：

- `knowledgeDiagnoses[]` 必须是路径规划和资源推送的主消费对象。
- `evidence[]` 必须来自真实输入信号，不得编造。
- `recommendedResourceTypes[]` 只能使用系统支持的资源类型枚举。
- `planAdjustmentHints.shouldRefreshPlan` 只表达建议，不直接触发修改计划。

## 5. 后端实施步骤

### Step 1：确认服务类型和路由

目标：

- Java `ServiceType` 增加 `PERSONALIZED_LEARNING`。
- Python `supervisor_routes.json` 增加：

```json
"PERSONALIZED_LEARNING": [
  "profile",
  "evaluation",
  "query_rewrite",
  "retrieval",
  "path_planning",
  "resource_push",
  "critic"
]
```

- Python Planner 白名单允许 `PERSONALIZED_LEARNING`。
- `DonePayload` 兼容 `resourcePushPlan`、`agentTrace`。

验证：

```bash
cd python-agent
pytest tests/test_supervisor.py -q
```

### Step 2：让 EvaluationAgent 产出 masteryDiagnosis

目标：

- `EvaluationAgent` 保留 `params["evaluationResult"]`。
- 同步生成 `params["masteryDiagnosis"]`。
- `masteryDiagnosis` 中必须包含知识点级诊断、证据、优先级、推荐资源类型和计划调整建议。
- 专项评估继续可以输出报告和互动题，但主流程消费 `masteryDiagnosis`。

建议修改位置：

- `python-agent/src/ai_modules/models/planning.py`
- `python-agent/src/ai_modules/agents/evaluation_agent.py`
- `python-agent/src/ai_modules/llms/agent_models.py`
- `python-agent/skills/evaluation/SKILL.md`

测试重点：

- 有 `judgeResult` 时，弱项和正确率进入 `masteryDiagnosis`。
- 有画像弱点时，诊断优先级能覆盖画像中的薄弱知识点。
- 有资源使用反馈时，`behaviorSignals` 有真实聚合值。
- LLM 失败时任务失败或明确暴露错误，不产出伪诊断。

验证：

```bash
cd python-agent
pytest tests/test_routing_agents.py -q -k evaluation
pytest tests/test_supervisor.py -q -k evaluation
```

### Step 3：让 ProfileAgent 输出 profileAnalysis

目标：

- `ProfileAgent` 继续持久化画像。
- 同步在 `params["profileAnalysis"]` 中提供主流程可消费的画像摘要。
- 内容聚焦专业背景、当前目标、学习偏好、资源偏好、薄弱点和置信度。

测试重点：

- `PERSONALIZED_LEARNING` 链路中 `evaluation` 可以消费 `profileAnalysis`。
- `PathPlanningAgent` 优先读取 `profileAnalysis`。

验证：

```bash
cd python-agent
pytest tests/test_profile_agent.py -q
pytest tests/test_supervisor.py -q -k personalized
```

### Step 4：让 RetrievalAgent 输出 retrievalEvidence

目标：

- `QueryRewriteAgent` 根据 `profileAnalysis` 和 `masteryDiagnosis` 改写检索查询。
- `RetrievalAgent` 将检索结果整理为 `params["retrievalEvidence"]`。
- 证据应覆盖知识点解释、前置依赖、候选资源和来源。

测试重点：

- 检索 query 包含高优先级薄弱知识点。
- `retrievalEvidence` 结构可被 `PathPlanningAgent` 和 `ResourcePushAgent` 消费。
- 不降低 RAG hits@3 指标。

验证：

```bash
cd python-agent
pytest tests/test_retrieval_services.py tests/test_routing_agents.py -q
pytest tests/ -k rag -v
```

### Step 5：改造 PathPlanningAgent

目标：

- `PathPlanningAgent` 优先消费 `profileAnalysis`、`masteryDiagnosis`、`retrievalEvidence`。
- `learningPath.steps[]` 必须包含顺序、目标知识点、原因、推荐资源类型、预计时长和检查点。
- 保存学习计划时，若由 `masteryDiagnosis` 触发，版本快照的触发源应能表达 `EVALUATION` 或 `PRACTICE_RESULT`。

`learningPath.steps[]` 建议结构：

```json
{
  "stepId": "step-1",
  "title": "补齐 TCP 拥塞控制前置知识",
  "order": 1,
  "targetKnowledgePoints": ["慢启动", "拥塞避免"],
  "reason": "masteryDiagnosis 显示慢启动掌握度 0.42，优先级最高",
  "preferredResourceTypes": ["DOCUMENT", "VIDEO", "QUIZ"],
  "estimatedMinutes": 45,
  "checkpoint": "完成 5 道专项题，正确率达到 80%"
}
```

验证：

```bash
cd python-agent
pytest tests/test_routing_agents.py -q -k path_planning
```

### Step 6：改造 ResourcePushAgent

目标：

- `ResourcePushAgent` 不再以孤立资源列表为主，而是围绕 `learningPath.steps` 生成 `resourcePushPlan.stepResources[]`。
- 每个 step 至少绑定一种资源。
- 资源类型优先级来自 step 的 `preferredResourceTypes` 和画像偏好。
- 资源来源优先级：`generatedAssets` > `learning_resource/resource_chunk` > RAG 候选资源 > Tavily 外部搜索。
- Tavily 未配置时不得伪造外部资源。

验证：

```bash
cd python-agent
pytest tests/test_resource_push_agent.py -q
```

### Step 7：CriticAgent 审查完整方案

目标：

- `CriticAgent` 审查 `learningPath`、`resourcePushPlan` 和 `masteryDiagnosis` 的一致性。
- 输出 `coverageScore`、`pathOrderScore`、`resourceMatchScore`、`issues` 和 `suggestions`。

审查重点：

- 路径是否覆盖高优先级薄弱点。
- 路径顺序是否符合前置依赖。
- 每个 step 是否有资源绑定。
- 资源类型是否覆盖文档、视频、题库、实操案例等赛题要求。
- 推荐理由是否有画像、评测或检索证据支撑。

验证：

```bash
cd python-agent
pytest tests/test_review_agents.py tests/test_supervisor.py -q
```

### Step 8：Supervisor 聚合 done payload

目标：

`PERSONALIZED_LEARNING` 完成后，`done.payload` 至少包含：

```json
{
  "masteryDiagnosis": {},
  "learningPath": {},
  "resourcePushPlan": {},
  "pushedResources": [],
  "criticReview": {},
  "agentTrace": []
}
```

注意：

- 不新增 SSE event 类型。
- 继续复用 `progress`、`result_chunk`、`resource_file`、`question_batch`、`done`。
- `agentTrace` 只能来自真实执行状态，前端不得伪造。

验证：

```bash
cd python-agent
pytest tests/test_supervisor.py -q -k personalized
```

## 6. 前端实施步骤

### Step 1：收敛服务入口

主入口从多个分裂按钮收敛为一个：

```text
生成个性化学习方案
```

触发服务：

```text
PERSONALIZED_LEARNING
```

建议保留：

- `RESOURCE_GENERATION`：作为独立“生成资料”能力。

建议弱化：

- `PATH_PLANNING`
- `RESOURCE_PUSH`
- `LEARNING_EVALUATION`

这些可保留为内部服务或高级调试入口，但不作为评委演示主入口。

修改位置：

- `frontend/src/api/smartEngine.ts`
- `frontend/src/pages/LearningStudioDemoPage.types.ts`
- `frontend/src/pages/useLearningStudioEngine.ts`
- `frontend/src/pages/LearningStudioDemoPage.components.tsx`
- `frontend/src/pages/LearningStudioDemoPage.utils.ts`

### Step 2：简化表单参数

主表单只收集学生能理解的输入：

- 课程/章节
- 当前进度
- 学习目标
- 目标周期
- 每周可投入时间
- 资源偏好，可选

不要让主入口暴露这些内部评估维度：

- 知识基础
- 案例迁移
- 练习掌握
- 学习主动性
- 复盘闭环

专项评估入口可以保留为二级动作：

```text
补充一次专项诊断
```

或：

```text
做 3 道检查题校准方案
```

### Step 3：重构结果页

结果页按用户理解组织，不按 Agent 内部实现组织。

推荐结构：

```text
个性化学习方案
├── 学习诊断摘要
├── 多智能体协同轨迹
├── 个性化学习路径
│   ├── Step 1 + 推荐资源
│   ├── Step 2 + 推荐资源
│   └── Step 3 + 推荐资源
├── 资源覆盖情况
└── 质量审查结果
```

展示原则：

- `masteryDiagnosis` 只展示摘要、薄弱点、证据和下一步重点。
- `learningPath.steps` 是主视觉骨架。
- `resourcePushPlan.stepResources` 嵌入对应 step，不再单独展示孤立资源列表。
- `criticReview` 简洁展示通过状态和分数。
- `agentTrace` 或 `progress` 展示真实协作过程。

### Step 4：前端状态与历史兼容

目标：

- 历史任务可回看完整 `learningPath` 和 `resourcePushPlan`。
- 已有 `PATH_PLANNING`、`RESOURCE_PUSH`、`LEARNING_EVALUATION` 历史结果不崩溃。
- 新任务优先读取 `PERSONALIZED_LEARNING` done payload。

解析兼容顺序：

- `payload.masteryDiagnosis`
- `payload.learningPath`
- `payload.resourcePushPlan`
- `payload.pushedResources`
- `payload.criticReview`
- `payload.agentTrace`
- 旧字段 `payload.learningPlan`、`payload.generatedAssets` 继续兼容。

验证：

```bash
cd frontend
npx tsc --noEmit
npx vite build
```

## 7. 数据闭环与触发策略

学习效果评估闭环分为三类触发。

### 评测后更新

```text
evaluation -> profile -> retrieval -> path_planning -> resource_push -> critic
```

适用场景：

- 用户完成学习效果评估。
- 系统已有足够评估证据。
- 需要刷新个性化学习方案。

### 练习后更新

```text
judge -> profile -> retrieval -> path_planning -> resource_push -> critic
```

适用场景：

- 用户提交练习答案。
- `judgeResult` 显示正确率明显变化。
- 错题暴露新的高优先级薄弱点。

### 资源使用后更新

```text
behavior aggregation -> masteryDiagnosis -> path_planning/resource_push refresh decision
```

适用场景：

- 用户生成或下载资源。
- 用户完成错题复习。
- 用户持续没有使用某类资源，画像偏好需要调整。

本阶段可以先实现“手动生成方案时读取真实行为聚合”，后续再增加自动触发刷新，避免一次性引入过多复杂度。

## 8. 验收标准

功能验收：

- 用户点击一个统一入口即可生成完整个性化学习方案。
- 前端能看到真实多智能体阶段进度。
- 结果包含学习诊断、路径步骤、资源绑定和质量审查。
- 每个路径步骤至少绑定一种资源。
- 推荐资源覆盖文档、视频、题库、实操案例等类型。
- 历史任务可回看同一份学习路径和资源推荐。

技术验收：

```bash
docker compose ps
pytest tests/ -v

cd python-agent
pytest tests/ -v
pytest tests/ -k rag -v

cd ../frontend
npx tsc --noEmit
npx vite build

curl -s http://localhost:8081/api/health
```

如果完整验证受外部网络、依赖或环境限制阻塞，必须记录阻塞原因，并至少运行受影响改动的最小相关测试。

## 9. 分阶段提交建议

建议按以下顺序提交，便于回滚：

```text
commit 1: add personalized learning route and service type
commit 2: add mastery diagnosis shared model and evaluation output
commit 3: expose profile analysis and retrieval evidence
commit 4: make path planner consume diagnosis and evidence
commit 5: bind resource push results to learning path steps
commit 6: add critic review and done payload aggregation
commit 7: collapse frontend entry into personalized learning workspace
commit 8: add tests and update documentation
```

每个 commit 前必须追加 `docs/experiment_log.md`，说明改动、验证结果、指标变化和保留/回滚判断。

