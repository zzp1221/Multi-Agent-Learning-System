# 个性化学习路径规划与资源推送多智能体改造方案

日期：2026-06-03

## 0. 前置要求

本改造必须在新的 Git 分支下进行，禁止直接在当前业务开发分支或演示热更新分支上改动。

建议分支名：

```bash
git switch -c feature/personalized-learning-multi-agent
```

开工前必须确认：

```bash
git status --short --branch
docker compose ps
curl -s http://localhost:8081/api/health
```

注意事项：

- 当前联调/演示环境只允许 `docker cp` 热更新，禁止 `docker compose build`、`docker compose up --build`、`--force-recreate` 和重建容器。
- 不修改 Docker 服务拓扑、数据库 schema、SSE `event:` / `data:` 协议格式、Java 唯一入口契约。
- 每次只改一个问题，验证通过后再进入下一步。
- 若改动导致测试或核心指标变差，必须回滚该步改动。
- 禁止使用mock数据兜底，保证都是由llm生成。

## 1. 改造目标

赛题要求：

> 个性化学习路径规划和资源推送：依托多智能体协同工作机制，整合系统生成的个性化资源，结合大模型对学生专业、学习进度、知识掌握情况及学习偏好的深度分析，为学生规划科学、动态的个性化学习路径，明确学习步骤和顺序；同时基于画像实现学习资源的精准推送，涵盖文档、视频、题库、实操案例等多类型内容。

当前代码中 `PATH_PLANNING` 和 `RESOURCE_PUSH` 分别是单 Agent 路由：

- `PATH_PLANNING -> path_planning`
- `RESOURCE_PUSH -> resource_push`

这更像 Supervisor 编排下的单 Agent workflow。改造目标是把它升级为可展示、可追踪、可答辩的多智能体协同闭环：

```text
画像分析 -> 掌握度诊断 -> 知识检索 -> 路径规划 -> 资源匹配 -> 质量审查 -> 动态更新
```

## 2. 目标架构

新增复合服务类型：

```text
PERSONALIZED_LEARNING
```

建议多智能体链路：

```text
profile -> evaluation -> query_rewrite -> retrieval -> path_planning -> resource_push -> critic
```

不同触发场景可采用不同路由：

```text
练习后更新：
judge -> profile -> retrieval -> path_planning -> resource_push -> critic

评测后更新：
evaluation -> profile -> retrieval -> path_planning -> resource_push -> critic

手动生成方案：
profile -> query_rewrite -> retrieval -> path_planning -> resource_push -> critic
```

各 Agent 职责：

| Agent | 职责 | 输出到 params |
|---|---|---|
| `ProfileAgent` | 分析学生专业、学习偏好、薄弱点、资源偏好 | `profileAnalysis` |
| `EvaluationAgent` / `JudgeAgent` | 诊断知识掌握情况、错因、下一阶段重点 | `masteryDiagnosis` / `judgeResult` |
| `QueryRewriteAgent` | 将学习目标改写为检索查询 | `rewrittenQuery` |
| `RetrievalAgent` | 检索知识点证据、前置依赖、候选资源 | `retrievalEvidence` |
| `PathPlanningAgent` | 生成有顺序的学习路径 | `learningPath` |
| `ResourcePushAgent` | 按路径节点匹配文档、视频、题库、实操案例等资源 | `resourcePushPlan` / `pushedResources` |
| `CriticAgent` | 审查路径顺序、资源覆盖率、偏好匹配度和事实支撑 | `criticReview` |

## 3. 后端实施步骤

### Step 1：新增服务类型

修改 Java 与 Python 的服务类型枚举/白名单，新增：

```text
PERSONALIZED_LEARNING
```

涉及位置：

- Java `ServiceType`
- Python `supervisor_routes.json`
- Python Planner 白名单 `ALLOWED_PLAN_SERVICE_TYPES`
- 前端 service type 类型定义

目标路由：

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

验证：

```bash
cd python-agent
pytest tests/test_supervisor.py -q
```

### Step 2：规范共享状态字段

统一多 Agent 之间通过 `params` 传递的结构，避免每个 Agent 各读各的字段。

建议字段：

```json
{
  "profileAnalysis": {},
  "masteryDiagnosis": {},
  "retrievalEvidence": [],
  "learningPath": {},
  "resourcePushPlan": {},
  "pushedResources": [],
  "criticReview": {}
}
```

要求：

- `PathPlanningAgent` 不再只从原始 `profile/evaluationResult/snapshot` 临时拼上下文。
- 它必须优先消费 `profileAnalysis`、`masteryDiagnosis`、`retrievalEvidence`。
- `ResourcePushAgent` 必须优先消费 `learningPath.steps`，按路径步骤推荐资源。

验证：

```bash
cd python-agent
pytest tests/test_supervisor.py tests/test_routing_agents.py -q
```

### Step 3：改造 PathPlanningAgent

当前 `PathPlanningAgent` 内部完成画像分析、路径生成、落库和图谱同步。改造后保留生成和落库职责，但输入来源要体现多智能体协作。

调整原则：

- 输入优先级：`profileAnalysis` > `profile` > `snapshot`
- 掌握度来源：`masteryDiagnosis` / `judgeResult` / `evaluationResult`
- 证据来源：`retrievalEvidence`
- 输出必须包含步骤顺序、每步目标、前置依赖、推荐资源类型、预计时长、检查点。

建议 `learningPath.steps[]` 结构：

```json
{
  "stepId": "step-1",
  "title": "补齐前置知识",
  "order": 1,
  "targetKnowledgePoints": ["TCP 慢启动", "拥塞避免"],
  "reason": "该学生在拥塞控制相关题目中错误率较高",
  "preferredResourceTypes": ["DOCUMENT", "VIDEO", "QUIZ"],
  "estimatedMinutes": 45,
  "checkpoint": "完成 5 道拥塞控制专项题"
}
```

验证：

```bash
cd python-agent
pytest tests/test_path_planning_agent.py -q
```

若当前没有专用测试文件，应新增覆盖以下场景：

- 有 `profileAnalysis` 时优先使用它。
- 有 `masteryDiagnosis` 时路径目标覆盖薄弱知识点。
- 有 `retrievalEvidence` 时路径生成上下文包含证据。
- LLM 失败时任务失败，不生成伪路径。

### Step 4：改造 ResourcePushAgent

资源推送不再作为独立功能孤立输出列表，而是服务于学习路径。

改造后逻辑：

```text
读取 learningPath.steps
为每个 step 解析知识点和推荐资源类型
优先匹配系统已生成资源 generatedAssets
其次匹配 RAG / learning_resource / resource_chunk
必要时调用 RESOURCE_GENERATION 生成缺失类型资源
最后才使用 Tavily 外部搜索兜底
输出按 step 绑定的资源包
```

建议 `resourcePushPlan` 结构：

```json
{
  "stepResources": [
    {
      "stepId": "step-1",
      "resources": [
        {
          "title": "TCP 拥塞控制原理讲解",
          "resourceType": "DOCUMENT",
          "source": "generated",
          "matchReason": "覆盖 step-1 的核心薄弱点",
          "downloadUrl": null
        },
        {
          "title": "慢启动与拥塞避免视频",
          "resourceType": "VIDEO",
          "source": "resource_library",
          "matchReason": "符合学生视频偏好"
        }
      ]
    }
  ]
}
```

资源类型至少覆盖：

- 文档：`DOCUMENT`
- 视频：`VIDEO`
- 题库：`QUIZ`
- 实操案例：`CODE` 或 `PRACTICAL_CASE`
- 可选：`SLIDES`、`MINDMAP`、`READING`

验证：

```bash
cd python-agent
pytest tests/test_resource_push_agent.py -q
```

测试重点：

- 有 `learningPath.steps` 时按 step 分组推荐。
- 优先使用 `generatedAssets`。
- 不同学习偏好会影响资源类型排序。
- Tavily 未配置时不伪造外部资源。

### Step 5：接入 CriticAgent 质量审查

`CriticAgent` 不只审查资源生成，还要审查个性化学习方案。

审查维度：

- 路径顺序是否符合前置依赖。
- 是否覆盖画像中的薄弱知识点。
- 是否匹配学习偏好和当前进度。
- 每个路径步骤是否至少绑定一种资源。
- 文档、视频、题库、实操案例等类型是否覆盖赛题要求。
- 推荐理由是否有画像、评测或检索证据支撑。

`criticReview` 建议包含：

```json
{
  "verdict": "PASS",
  "coverageScore": 0.92,
  "pathOrderScore": 0.88,
  "resourceMatchScore": 0.9,
  "issues": [],
  "suggestions": ["第 3 步可增加实操案例"]
}
```

验证：

```bash
cd python-agent
pytest tests/test_review_agents.py tests/test_supervisor.py -q
```

### Step 6：Supervisor 串联与 done payload 汇总

`PythonAgentSupervisor` 需要在 `PERSONALIZED_LEARNING` 完成后，把关键结果写入最终 `done.payload`：

```json
{
  "learningPath": {},
  "resourcePushPlan": {},
  "pushedResources": [],
  "criticReview": {},
  "agentTrace": []
}
```

同时建议记录多智能体执行轨迹：

```json
{
  "agentTrace": [
    {"agentName": "profile", "status": "DONE"},
    {"agentName": "evaluation", "status": "DONE"},
    {"agentName": "retrieval", "status": "DONE"},
    {"agentName": "path_planning", "status": "DONE"},
    {"agentName": "resource_push", "status": "DONE"},
    {"agentName": "critic", "status": "DONE"}
  ]
}
```

注意：不新增 SSE event 类型，继续复用 `progress`、`result_chunk`、`resource_file`、`done`。

验证：

```bash
cd python-agent
pytest tests/test_supervisor.py -q
```

## 4. 前端实施步骤

### Step 1：调整入口

不要在“学习服务”中突兀展示两个割裂按钮：

```text
学习路径规划
资源推送
```

改为统一入口：

```text
生成个性化学习方案
```

或：

```text
智能规划我的学习路径
```

入口触发 `PERSONALIZED_LEARNING`，而不是分别触发 `PATH_PLANNING` 和 `RESOURCE_PUSH`。

### Step 2：展示多智能体协同过程

前端进度区域展示：

```text
画像分析智能体：正在分析学生专业、进度和偏好
掌握度诊断智能体：正在诊断薄弱知识点
知识检索智能体：正在检索相关证据和资源
路径规划智能体：正在生成学习步骤和顺序
资源推荐智能体：正在匹配文档、视频、题库和实操案例
质量审查智能体：正在检查路径合理性和资源覆盖率
```

展示依据来自后端 `progress.payload.agentName/stage/message`，前端不得本地伪造完成状态。

### Step 3：结果页结构

推荐页面结构：

```text
个性化学习方案
├── 当前画像摘要
├── 多智能体协同轨迹
├── 个性化学习路径
│   ├── Step 1 + 推荐资源
│   ├── Step 2 + 推荐资源
│   └── Step 3 + 推荐资源
├── 资源覆盖情况
└── 质量审查结果
```

路径节点展示：

```text
Step 1：补齐 TCP 拥塞控制前置知识
原因：该学生在拥塞控制题目中错误率较高
推荐资源：
- 文档：TCP 拥塞控制原理讲解
- 视频：慢启动与拥塞避免
- 题库：TCP 专项练习
- 实操案例：抓包分析拥塞窗口变化
检查点：完成 5 道专项题，正确率达到 80%
```

### Step 4：保留旧能力但弱化入口

`PATH_PLANNING` 和 `RESOURCE_PUSH` 可作为内部服务或高级调试入口保留，但面向评委演示的主入口必须是 `PERSONALIZED_LEARNING`。

前端文案重点：

- 不是“单次生成路径”。
- 不是“单次推送资源”。
- 而是“多智能体协同生成个性化学习方案”。

验证：

```bash
cd frontend
npx tsc --noEmit
npx vite build
```

## 5. 验收标准

### 功能验收

- 用户点击一个统一入口即可生成完整个性化学习方案。
- 前端能看到多智能体阶段进度。
- 结果包含学习路径步骤和顺序。
- 每个路径步骤至少绑定一种学习资源。
- 推荐资源覆盖文档、视频、题库、实操案例等类型。
- 质量审查结果可见。
- 历史任务可回看同一份学习路径和资源推荐。

### 技术验收

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

### 热更新验收

只允许：

```bash
docker cp <changed-file> <container>:/app/<same-path>
docker exec <container> <reload-or-compile-command>
```

禁止：

```bash
docker compose build
docker compose up --build
docker compose up --force-recreate
```

## 6. 答辩表达建议

可使用如下表述：

> 本系统采用 Supervisor 监管的多智能体协同机制。针对个性化学习路径规划和资源推送，系统由画像分析智能体、掌握度诊断智能体、知识检索智能体、路径规划智能体、资源推荐智能体和质量审查智能体共同完成任务。各智能体通过共享任务状态传递中间结果，路径规划智能体基于画像、评测和检索证据生成有序学习步骤，资源推荐智能体再按每个步骤匹配文档、视频、题库和实操案例，最后由质量审查智能体检查路径合理性、资源覆盖率和个性化匹配度。

避免表述：

```text
我们有一个学习路径规划按钮和一个资源推送按钮。
```

推荐表述：

```text
我们提供一个个性化学习方案入口，背后由多智能体协同完成画像诊断、掌握度分析、知识检索、路径规划、资源匹配和质量审查。
```

## 7. 分阶段提交建议

每完成一个阶段单独提交，方便回滚：

```text
commit 1: add personalized learning service route
commit 2: normalize multi-agent shared params
commit 3: make path planner consume profile and diagnosis outputs
commit 4: bind resource push results to learning path steps
commit 5: add critic review for personalized learning plan
commit 6: update frontend personalized learning workspace
commit 7: add tests and documentation
```

每次提交前运行相关测试，并在 `docs/experiment_log.md` 追加一行结果。

## 8. 当前实现状态（2026-06-03）

- 已在 `feature/personalized-learning-multi-agent` 完成实现，并合入 `master`。
- 当前 `master` 的 `PERSONALIZED_LEARNING` 路由为 `profile -> evaluation -> query_rewrite -> retrieval -> path_planning -> resource_push -> critic`。
- Java 提交 `PERSONALIZED_LEARNING` 前会通过 `PersonalizedLearningContextService` 自动聚合画像、学习进度、知识掌握、练习测试、错题复习和资源反馈。
- 前端主入口已收敛为“个性化学习方案”，结果页面向学生展示学习路径、资源推荐、任务状态和真实产物；内部协同轨迹、评估诊断和质量审查保留在任务状态/日志数据中。
- `PATH_PLANNING` 和 `RESOURCE_PUSH` 保留为兼容或高级调试入口，不作为评委演示主入口。
- 已通过并记录的关键验证包括：`npx tsc --noEmit`、`npx vite build`、相关 Python Agent pytest、相关 Java 测试、RAG 测试、Java/Python health check 和 Docker `docker cp` 热更新。
- 仍需单独实测：浏览器完整全链路巡检和 >5min 长任务不断连。
