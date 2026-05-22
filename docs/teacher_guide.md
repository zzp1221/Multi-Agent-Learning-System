# 智学引擎 (ZhiXue Engine) — 项目技术报告

> 作者: 张志鹏 | 最后更新: 2026-05-20

---

## 参考资料与设计来源

本项目在设计与实现过程中参考了以下开源项目和技术文章，各模块的参考关系如下：

| 参考来源 | 链接 | 本项目对应模块 | 参考内容 |
|----------|------|----------------|----------|
| **Karpathy LLM Wiki + 三通道混合检索实践** | [博客园: 用 Karpathy LLM Wiki 方法论，为 AI Agent 系统构建结构化知识层](https://www.cnblogs.com/jtuki/p/19861920) | **RAG 检索引擎** (2.3 节)、**离线知识导入** | 三通道混合检索架构 (Grep + 向量 + 图扩展)、RRF 融合权重设计、FMM 分词策略、中文术语检索中 grep 优于向量的发现、IDF+Coverage 评分思路 |
| **DH_live (浏览器端数字人)** | [github.com/kleinlee/DH_live](https://github.com/kleinlee/DH_live) | **浏览器端视频合成** (2.5 节视频生成) | DH Live WASM SDK 集成、浏览器端数字人渲染、postMessage 通信协议、音频驱动口型同步 |
| **Claude Code Agent 运行时架构** | Claude Code 官方文档及源码分析 | **Agent 运行时内核** (3.4 节) | AgentCoreLoop (工具调用循环)、ToolRegistry (工具注册)、HookChain (钩子拦截)、PermissionPolicy (权限分级)、RecoveryEngine (故障恢复)、ConversationCompactor (上下文压缩) |

### 各参考来源的详细说明

**1. 三通道混合检索 (参考 Karpathy LLM Wiki 方法论)**

博客园文章《用 Karpathy LLM Wiki 方法论，为 AI Agent 系统构建结构化知识层》报告了一种面向企业 AI Agent 的结构化知识层方案。其核心发现——在中文技术术语检索中，字面匹配 (grep) 优于语义相似度 (向量)——直接影响了本项目 RAG 检索引擎的设计。本项目实现了相同的三通道架构 (Grep + 向量 + 图扩展)，采用加权 RRF 融合 (grep_weight=3.0, vector_weight=5.0, graph_weight=0.5)，并使用 FMM 分词器处理中文术语。与原文不同的是，本项目额外增加了第四通道 (Tavily 联网搜索)，并将知识领域限定为计算机科学课程。


**2. 浏览器端数字人视频 (参考 DH_live)**

DH_live 项目实现了浏览器端的数字人视频渲染，核心是通过 WebAssembly 在客户端完成音频驱动口型同步、视频录制和封装，无需服务端 GPU。本项目将 DH Live SDK 集成为 `frontend/public/dh_live/` 目录下的静态资源，通过 `browserVideoRenderer.ts` 以隐藏 iframe + postMessage 的方式与主应用通信。视频生成流程为：Python Agent 生成脚本 → TTS 语音合成 → 浏览器端 DH Live 渲染 → 输出 MP4 Blob。这一方案将视频渲染的计算成本从服务端转移到客户端，使系统无需 GPU 服务器即可支持视频生成功能。

**3. Agent 运行时内核 (参考 Claude Code)**

Claude Code 的 Agent 执行机制——LLM 推理 → 工具调用 → 结果注入 → 再推理的循环——是本项目 Agent 运行时内核的设计蓝本。具体映射关系：

| 本项目模块 | Claude Code 对应 | 设计思路 |
|-----------|-----------------|----------|
| `AgentCoreLoop` | Agent tool-use loop | LLM 推理 → 工具调用 → 结果注入 → 再推理，max_iterations 限制 |
| `ToolRegistry` | Tool 注册与发现 | 按 name 注册工具，生成 OpenAI function-calling schema |
| `HookChain` | Hooks 系统 | 工具执行前后的拦截链，可修改参数、拒绝执行、校验输出 |
| `PermissionPolicy` | Permission 系统 | allow/deny 规则 + 数值级别检查 (READ_ONLY → FULL_ACCESS) |
| `RecoveryEngine` | 错误处理与重试 | 按 failure_type 分类重试 (Timeout 1次, RateLimit 2次) |
| `ConversationCompactor` | 上下文压缩 | token 预算内的对话摘要，保留关键信息 |
| `KnowledgeGuardHook` | (本项目独创) | 知识库守卫钩子，无证据则拒绝生成，防止幻觉 |

---

## 一、项目背景与目标

### 1.1 赛题背景

本项目参加 2026 年中国软件杯竞赛，目标是构建一个 **AI 驱动的个性化学习系统**，面向计算机科学专业学生，解决以下教学痛点：

- 学生课后提问无人解答，学习卡壳无人引导
- 学习资源千人一面，不因人而异
- 做完练习缺乏反馈，不知道错在哪里、为什么错
- 错题做完就忘，没有科学的复习安排
- 学生对自己的知识掌握情况缺乏全局认知

### 1.2 设计目标

| 目标 | 对应实现 |
|------|----------|
| 像真人老师一样因材施教 | AI 对话辅导 + 学习画像驱动的个性化策略 |
| 精准定位知识薄弱点 | 多维度画像：对话分析、练习结果、评测信号 |
| 自动生成个性化学习资源 | 6 种格式：文档、思维导图、幻灯片、代码案例、练习题、视频 |
| 智能批改并给出反馈 | 客观题精确匹配 + 主观题 LLM 语义评判 |
| 科学管理复习 | SM-2 间隔重复算法自动调度错题复习 |

### 1.3 教育理论依据

本系统的设计基于以下教育学理论，不是简单包装 LLM API，而是有理论支撑的教学系统：

**Bloom 掌握学习 (Mastery Learning)**: 每个知识点必须达到掌握标准才能进入下一个。系统通过练习正确率和画像置信度判断是否掌握，不跳过薄弱环节。

**Bloom 2-Sigma 问题**: 一对一辅导的学生比课堂教学的学生表现高 2 个标准差。本系统用 AI Agent 模拟一对一辅导体验——每个学生有独立的画像、对话历史、错题本和学习路径。

**苏格拉底式提问**: 不直接告诉答案，而是通过提问引导学生自己推理出结论。TutorAgent 实现了三种教学策略：掌握式苏格拉底（默认）、检索增强脚手架（有知识缺口时）、诊断式脚手架（有误解时）。

**间隔重复 (Spaced Repetition)**: 根据遗忘曲线安排复习时间。错题本实现了 SM-2 算法，在学生即将遗忘时安排复习，效果远优于随机复习。

---

## 二、系统功能详述

### 2.1 智能对话辅导

**入口**: 首页 Q&A 聊天界面

**功能**:
- 支持文字 + 图片输入（可拍照上传题目截图，系统自动分析图片内容）
- 支持联网搜索（可选开启，通过 Tavily API 补充最新知识）
- 支持深度推理模式（对复杂问题执行四步推理流水线）
- 流式输出——逐字显示回复，无需等待完整生成
- 多轮对话——自动记忆上下文，支持追问和深入讨论
- Markdown 渲染——公式、代码块、表格、思维导图均正确显示

**教学策略选择逻辑** (TutorAgent):

系统根据学生当前状态自动选择教学策略：

```
if 有知识缺口 且 有检索文档:
    → 检索增强脚手架 (retrieval_grounded_scaffold)
    → 用知识库中的材料辅助讲解
elif 有知识缺口 或 有已知误解:
    → 诊断式脚手架 (diagnostic_scaffold)
    → 通过反例帮助学生发现错误理解
else:
    → 掌握式苏格拉底 (mastery_socratic)
    → 先了解学生当前水平，再针对性讲解
```

**输入模式分类**: 系统自动识别 4 种输入模式（寒暄、回答上一问题、明确提问、模糊话题），对寒暄等场景直接短路回复，不消耗 LLM 调用。

### 2.2 深度推理

**入口**: 聊天界面中点击"深度推理"按钮开启

**四步流水线**:

| 步骤 | 进度 | 功能 |
|------|------|------|
| 1. 分析 (analysis) | 35% | 识别核心问题、约束条件、缺失概念 |
| 2. 推理 (reasoning) | 55% | 基于分析、检索证据、学生画像进行逐步推理 |
| 3. 自审 (critique) | 75% | 检查是否有跳步、证据误用、遗漏意图 |
| 4. 最终回答 (final) | 92% | 生成面向用户的最终回答，隐藏内部推理链 |

每步独立运行 AgentCoreLoop（最多 4 次工具调用迭代），有独立的 system prompt。如果工具循环超限，降级到直接 LLM 补全。

### 2.3 RAG 知识检索

**知识库规模**: 覆盖 20 门计算机科学课程，986+ 知识块，1024 维向量，hits@3 = 100%

**三通道混合检索**:

| 通道 | 方法 | 权重 | 优势 |
|------|------|------|------|
| Grep 关键词 | FMM 分词 + 关键词匹配 | 3.0 | 精确匹配专业术语 |
| 向量语义 | DashScope Embedding → pgvector 余弦距离 | 5.0 | 理解语义，召回率高 |
| 知识图谱 | 从 top 结果扩展知识图谱邻居 | 0.5 | 发现关联知识 |
| 联网搜索 | Tavily API (可选) | 1.5 | 获取最新信息 |

四通道结果通过 **加权 RRF (Reciprocal Rank Fusion)** 融合：

```
score = Σ (weight × priority_boost / (k + rank + 1))
k = 60, 词组匹配额外 1.5x boost
```

**离线知识导入**: 两阶段流程——结构化导入 (wiki JSON → PostgreSQL) → 向量化 (Embedding API → pgvector)。向量数据通过 `vector_data.dump` (11.5MB) 随项目分发，首次部署自动恢复。

### 2.4 学习画像系统

**入口**: 侧边栏 → 学习画像

**画像维度**:

| 维度 | 数据来源 | 推断方法 |
|------|----------|----------|
| 知识水平 (BASIC/INTERMEDIATE/ADVANCED) | 对话内容 | 关键词规则 ("不太懂"→BASIC, "熟悉"→INTERMEDIATE) + LLM 分析 |
| 技能掌握度 | 练习结果、对话分析 | per-topic mastery 分数 |
| 学习习惯 | 行为数据 | 频率、偏好时间、笔记习惯、自测习惯 |
| 错误模式 | 练习批改 | 概念混淆、条件遗漏、知识不稳 |
| 当前目标 | 对话内容 | 关键词检测 (想/希望/需要/目标/复习/掌握) |
| 置信度 | 综合 | 公式: 0.42 + accuracy × 0.38 |

**画像更新时机**: 每次对话结束后，ProfileAgent 异步分析对话内容并更新画像。练习批改、评测结果也会触发画像更新。

**可视化**: 雷达图（6 维度得分）+ 维度进度条 + 薄弱点排名 + 行为趋势柱状图（14 天）。

### 2.5 资源生成

**入口**: 引擎服务 → 资源生成

**资源生成入口支持 6 种资源类型，可多选**:

| 类型 | 说明 | 输出 |
|------|------|------|
| 文档 (Document) | 知识点详细解释 | Markdown |
| 思维导图 (MindMap) | 知识结构可视化 | Mermaid 图 |
| 幻灯片 (Slides) | 课件演示文稿 | PPTX 文件 |
| 代码案例 (Code) | 带注释的代码示例 | 多语言代码块 |
| 练习题 (Quiz) | 围绕知识点的练习批次 | 页面内作答 |
| 视频 (Video) | AI 数字人讲解视频 | MP4 文件 |

**生成流水线** (LangGraph ResourceBundleWorkflow 编排):

1. **查询改写** — QueryRewriteAgent 优先用 LLM 改写检索查询，LLM 临时失败时退回本地 direct rewrite
2. **知识检索** — RetrievalAgent 执行四通道混合检索 (grep + vector + graph + web)，真实知识库/Web 证据
3. **资源包 Fan-out** — `ResourceBundleWorkflow` 用 LangGraph 按用户选择的 `resourceTypes[]` 并发调用对应资源 Agent；未选择时默认生成讲解文档
4. **Provenance Gate** — 每个可展示/下载的产物必须带 LLM provenance (来源追溯)；无 provenance 标记为 UNVERIFIED
5. **Quality Gate** — SafetyAgent 审查产物内容安全；部分资源失败时展示成功资源并返回 `PARTIAL_FAILED`
6. **产物发布** — 生成签名下载 Token + SSE `resource_file` 事件推送到前端

**视频生成特殊流程**:
- LLM 生成讲解脚本 → TTS 语音合成 (MiMo-v2.5-tts) → 数字人渲染
- 浏览器端 DH Live WASM SDK 渲染（无需服务端 GPU 服务器）
- 支持 talking_head / animation / hybrid 三种风格

### 2.6 练习与自动批改

**入口**: 引擎服务 → 学习评测 / 对话中触发

**流程**:
1. PracticeAgent 根据当前学习主题生成 5-10 道练习题（选择题 + 简答题）
2. 学生在前端作答
3. JudgeAgent 自动批改：
   - 客观题：文本标准化后精确匹配（strip + uppercase）
   - 主观题：LLM 语义评判（或可选的本地 Qwen3-0.6B GGUF 模型评判）
4. 生成详细反馈：每题对错 + 知识点标注 + 改进建议
5. 错题通过数据库触发器自动进入错题本

**批改结果结构**:
- 每题得分 + 正误判定
- 薄弱知识点标签 (knowledge_tags)
- 总分 / 正确率
- 4 维度专项分析：学习主动性、复盘闭环、知识基础、综合
- `profileDelta`：正确 → confidenceLevel MEDIUM；错误 → LOW + weakPoints

### 2.7 错题本 (SM-2 间隔重复)

**入口**: 侧边栏 → 错题本

**核心机制**:

错题通过 PostgreSQL 触发器自动收录——当 `practice_submission` 表插入或更新且 `is_correct = FALSE` 时，自动 upsert 到 `mistake_record` 表。

**SM-2 算法实现**:

```
质量评分 q: 0-5 (0=完全不会, 5=完全掌握)

简易因子更新:
  EF' = max(1.3, EF + (0.1 - (5-q) × (0.08 + (5-q) × 0.02)))

间隔计算:
  q < 3 → interval = 1 (重置，明天复习)
  首次成功 → interval = 1
  第二次成功 → interval = 6
  后续 → interval = interval × EF

掌握判定:
  mastered = (q ≥ 4) 且 (interval ≥ 21 天)
```

**前端交互**:
- 按状态筛选：due (待复习) / active (未掌握) / mastered (已掌握) / all
- 按难度、知识点标签筛选
- 复习会话：选择错题 → 逐题评分 (0-5) → 提交 → 系统自动更新复习间隔
- 错误类型标注：概念性 / 程序性 / 粗心

### 2.8 学习路径规划

**入口**: 引擎服务 → 路径规划

PathPlanningAgent 分析学生画像，生成个性化学习计划，包括推荐学习顺序、每个阶段的学习目标、推荐资源、预计完成时间。

**触发方式**: 手动刷新 / 练习结果 / 评测结果 / 画像更新 / 初始生成。

### 2.9 学习评测

**入口**: 引擎服务 → 学习评测

EvaluationAgent 聚合学生行为信号，运行 LLM 评估，生成 4 个维度的评测报告：
- 案例迁移能力
- 练习掌握程度
- 学习主动性
- 复盘闭环质量

每个维度生成 2-3 道评测题（单选 + 简答），学生作答后系统综合评分。

---

## 三、技术架构

### 3.1 整体架构

系统采用 **前后端分离 + 三层服务** 架构，Docker Compose 一键部署 6 个容器：

```
┌─────────────────────────────────────────────────────┐
│                    用户浏览器                         │
│         React 18 + TypeScript + Tailwind CSS         │
└───────────────────────┬─────────────────────────────┘
                        │ HTTP / SSE (流式)
                        ▼
┌─────────────────────────────────────────────────────┐
│              Nginx (反向代理 + 静态资源)              │
└───────────────────────┬─────────────────────────────┘
                        │
          ┌─────────────┼─────────────┐
          ▼             ▼             ▼
┌──────────────┐ ┌────────────┐ ┌──────────────┐
│  Java BFF    │ │ Python     │ │   数据层      │
│  Spring Boot │ │ AI Agent   │ │              │
│  认证/编排    │ │ 17 个Agent │ │ PostgreSQL   │
│  任务管理     │ │ RAG 检索   │ │ MongoDB      │
│  文件管理     │ │ LLM 调用   │ │ Redis        │
└──────────────┘ └────────────┘ └──────────────┘
```

| 层 | 技术 | 端口 | 代码量 |
|----|------|------|--------|
| 前端 | React 18 + Vite 5 + Tailwind CSS 4 | 80 | ~8,000 行 (32 文件) |
| 后端 BFF | Spring Boot 3.3 + Java 21 (Virtual Threads) | 8081 | ~5,000 行 (45 文件) |
| AI Agent | FastAPI + Python 3.11 | 8000 | ~15,000 行 (50+ 文件) |
| 数据库 | PostgreSQL 16 (pgvector) + MongoDB 7 + Redis 7 | 5432/27017/6379 | ~1,500 行 SQL |

**总代码量**: ~35,000 行，180+ 文件，40+ 测试文件。

### 3.2 前端架构

**技术选型理由**:
- React 18 — 成熟的 UI 框架，生态丰富
- TypeScript — 类型安全，减少运行时错误
- Vite 5 — 快速构建，支持代码分割
- Tailwind CSS 4 — 原子化 CSS，开发效率高
- Framer Motion — 流畅的动画效果

**页面结构**:

| 路由 | 功能 |
|------|------|
| `/` | Q&A 聊天界面（对话辅导、深度推理、图片上传） |
| `/engine` | 引擎服务（资源生成、路径规划、学习评测） |
| `/mistakes` | 错题本（筛选、复习、SM-2 评分） |
| `/profile` | 学习画像（雷达图、行为趋势、薄弱点分析） |

**关键实现**:

- **SSE 流式渲染**: 自定义 `fetch + ReadableStream` 实现（非浏览器原生 EventSource），支持 429/5xx 自动重试和指数退避
- **浏览器端视频合成**: 通过隐藏 iframe 加载 DH Live WASM SDK，postMessage 通信，生成 blob URL。避免了服务端 GPU 依赖
- **会话管理**: sessionStorage 持久化会话快照，per-conversation 消息缓存，草稿保存，切换对话不丢失状态
- **懒加载**: 所有页面组件 React.lazy + Suspense，首屏加载优化

### 3.3 Java 后端架构

**架构风格: 六边形架构 (Hexagonal Architecture)**

```
src/main/java/com/project/
├── api/              ← 入站适配器 (Controllers)
├── application/      ← 应用服务层
├── domain/           ← 领域实体
├── infrastructure/   ← 出站适配器 (Python Agent Client, Redis, etc.)
├── security/         ← 认证授权 (JWT, 限流)
└── config/           ← 配置类
```

**核心职责**:
- 用户认证 (JWT, BCrypt)
- 任务编排 (提交 → 幂等检查 → 虚拟线程池执行 → SSE 代理)
- 对话管理 (创建/列表/消息流式)
- 错题本服务 (SM-2 算法, 跨表 JSONB 查询)
- 文件管理 (签名下载链接, 2h TTL)
- 速率限制 (IP 级 + 用户级滑动窗口)
- 审计日志

**设计决策**:
- **Java 21 Virtual Threads**: I/O 密集型的 Python Agent 调用，虚拟线程避免平台线程阻塞
- **Port/Adapter 模式**: PythonAgentClient, IdempotencyService, RateLimiter 均为接口，Redis/内存实现可自动切换
- **悲观锁**: SmartEngineTask 使用 PESSIMISTIC_WRITE 保证并发事件写入一致性
- **原生 JDBC (错题本)**: 复杂跨表 JSONB 查询，JPA 过于笨重，使用 NamedParameterJdbcTemplate

### 3.4 Python AI Agent 架构

这是系统的核心——17 个注册 Agent 协同工作。

**Supervisor 路由编排**: 9 种服务类型通过 `supervisor_routes.json` 映射到不同的 Agent 链：

| 服务类型 | Agent 链 |
|----------|----------|
| 对话辅导 | query_rewrite → retrieval → tutor；闲聊/承接上一轮可直达 tutor；图片问题先 image_analysis |
| 深度推理 | query_rewrite → retrieval → image_analysis → deep_reasoning |
| 练习批改 | practice → judge → profile |
| 资源生成 | query_rewrite → retrieval → resource_bundle |
| 视频生成 | query_rewrite → retrieval → video_generator |
| 路径规划 | path_planning |
| 学习评测 | evaluation |
| 画像构建 | tutor → profile |
| 资源推送 | resource_push |

资源生成不再使用 `{generator}` 单槽位替换，而是固定进入 `resource_bundle`。资源包统一处理多资源选择、多下载产物、任务结果历史、LLM provenance gate 和 `PARTIAL_FAILED` 语义。

**Agent 运行时内核** (借鉴 Claude Code 的 Agent 架构):

```
AgentCoreLoop (通用工具调用循环, 最多 4 轮迭代)
├── ToolRegistry (工具注册, agent_level 可见性过滤)
├── HookChain (前置/后置钩子)
│   └── KnowledgeGuardHook (幻觉防护——生成前强制检索知识依据，无证据则拒绝)
├── PermissionPolicy (READ_ONLY → FULL_ACCESS 四级权限)
├── RecoveryEngine (失败重试 + 降级)
│   ├── LLM timeout → 重试 1 次
│   ├── rate limit → 重试 2 次
│   └── retrieval → 重试 1 次
├── ConversationCompactor (对话压缩, 1200 token 预算)
└── ContextSnapshot (上下文快照构建)
```

**ResourceBundleWorkflow — 多资源生成 Graph 编排**:

资源生成不再使用简单的 Agent 串行调用，而是通过 LangGraph StateGraph 构建的有向无环图 (DAG) 进行编排：

```
ResourceBundleWorkflow (LangGraph StateGraph)
│
├── node: query_rewrite     # 查询改写
├── node: retrieval          # 四通道知识检索
│
├── node: resource_selector  # 解析 resourceTypes[]
│
├── fan-out 并发资源节点
│   ├── document_agent  → DocumentGeneratorAgent
│   ├── mindmap_agent   → MindMapGeneratorAgent
│   ├── slides_agent    → SlideGeneratorAgent (PPTX 直出)
│   ├── code_case_agent → CodeGeneratorAgent
│   ├── practice_agent  → PracticeAgent
│   └── video_agent     → VideoGenerationAgent (脚本→TTS→渲染)
│
├── custom stream bridge     # 实时透传资源节点 SSE
│
└── node: bundle_synthesizer # 汇总已通过 provenance gate 的产物
    └── SUCCESS / PARTIAL_FAILED / FAILED
```

**核心容错原则**: 每一个 LLM 调用都有确定性或启发式兜底方案，系统永远不会因单点故障而完全不可用。

**Skill 系统**: 每个核心 Agent 通过 `SKILL.md` 文件加载 prompt，支持 YAML frontmatter 元数据和 `{{snapshot_context}}` 运行时模板注入。将 prompt 工程化管理，而非硬编码在 Python 代码中。

**LLM 多 Provider 适配**:

| Provider | 厂商 | 主模型 |
|----------|------|--------|
| DashScope (默认) | 阿里云百炼 | qwen3.6-plus |
| MiMo | 小米 | mimo-v2-omni |
| Spark | 讯飞 | 4.0Ultra |

支持 14 个组件级 LLM 覆盖槽位，每个功能模块可独立配置使用哪个 Provider 和模型。

### 3.5 数据层

**PostgreSQL** (pgvector): 3 个 Schema，25+ 张表，1024 维向量索引 (IVFFlat)，24 张表启用行级安全 (RLS)。

**MongoDB**: 3 个 Collection (会话线程、消息、SSE 事件)，TTL 索引自动清理过期事件。

**Redis**: 速率限制 (Lua 脚本原子操作)、幂等性 (SETNX + TTL)、LLM 结果缓存 (300s)、检索结果缓存 (60s)。Redis 不可用时自动降级到内存实现。

### 3.6 安全设计

| 层 | 措施 |
|----|------|
| 传输 | Nginx 安全头 (X-Content-Type-Options, X-Frame-Options, CSP) |
| 认证 | JWT (HMAC-SHA), Access Token 2h / Refresh Token 7d |
| 限流 | IP 级 (100 req/min) + 用户级 (60 req/min) 双层滑动窗口 |
| 幂等 | 任务提交支持 Idempotency-Key，防止重复提交 |
| 内容安全 | SafetyAgent 过滤敏感内容和学术诚信问题 |
| 幻觉防护 | KnowledgeGuardHook 约束 LLM 在知识库范围内回答 |
| 数据隔离 | PostgreSQL 24 张表启用 RLS，多租户隔离 |
| 文件安全 | 路径穿越检查、2h TTL 自动清理、类型白名单 |
| 内部通信 | X-Zhixue-Internal-Token (timing-safe 比较) |

---

## 四、SSE 事件协议

前后端之间、Java 与 Python 之间通过 SSE (Server-Sent Events) 实现实时流式通信。定义了 14 种标准化事件类型，有 JSON Schema 约束。

| 事件类型 | 用途 |
|----------|------|
| `message` | 文本消息 |
| `progress` | 进度更新 (stage + percent) |
| `result_chunk` | 结果片段 (流式文本) |
| `resource_file` | 生成资源文件 (含下载链接) |
| `question_batch` | 练习题目 |
| `judge_result` | 批改结果 |
| `done` / `error` | 终态 |
| `video_gen:*` (5种) | 视频生成各阶段进度 |

**可靠性设计**:
- Smart Engine 事件持久化到 PostgreSQL，断线重连可回放
- 对话事件不持久化（fire-and-forget），但有重试机制
- 任务取消通过 SHA256 标记文件实现跨 Worker 通知

---

## 五、部署方式

### 5.1 Docker Compose 一键部署

```bash
cp .env.example .env    # 配置 API Key 等
docker compose up -d --build   # 构建并启动 6 个容器
```

所有数据库端口仅绑定 127.0.0.1，不对外暴露。服务间通过 Docker DNS 解析通信。

当前联调/演示环境禁止重建容器；代码或静态资源更新只允许 `docker cp` 覆盖到运行容器，并按需重启进程或服务。

### 5.2 可选的本地评判模式

系统支持使用本地 Qwen3-0.6B GGUF 模型进行主观题评判，无需额外 API 调用：

```bash
docker compose -f docker-compose.yml -f docker-compose.local-judge.yml up -d --build
```

模型仅 610MB，CPU 推理约 3 秒/题，适合离线环境或 API 额度有限的场景。

已有演示容器不能用上述命令热切本地 Judge；需要维护窗口或重新部署环境。

---

## 六、测试覆盖

| 层 | 框架 | 测试内容 |
|----|------|----------|
| 前端 | TypeScript strict + Vite build | 类型检查 + 构建验证 |
| Java 后端 | JUnit 5 + Spring Boot Test + H2 | 认证、限流、幂等、SSE 事件契约、SM-2 算法、画像分析 |
| Python Agent | pytest + pytest-asyncio | 31 个测试文件：Agent 行为、RAG 质量 (50 题)、LLM 集成、SSE 序列化、对话压缩、容错降级 |
| E2E | Playwright | 17/19 通过 |

Python 测试中的 **golden eval** 测试确保 Agent 行为契约——例如 TutorAgent 的回复必须包含引导性问题，ProfileAgent 的输出必须包含特定画像字段。

---

## 七、创新点

### 7.1 教育创新

1. **AI 苏格拉底式教学** — 不是问答机器人，而是理解学生水平后引导式教学，三种策略自动切换
2. **多维度学习画像** — 从对话、练习、评测多信号源构建画像，而非单一维度
3. **SM-2 间隔重复错题本** — 科学的复习调度算法，数据库触发器自动收录错题
4. **深度推理模式** — 四步推理流水线，模拟人类深度思考过程
5. **多格式资源生成** — 根据学习主题自动生成文档、思维导图、幻灯片、代码案例、练习题和视频

### 7.2 技术创新

1. **17 Agent 协作架构** — 借鉴 Claude Code 运行时内核设计，每个 Agent 专业化分工
2. **三通道 RAG + 加权 RRF 融合** — Grep + 向量 + 知识图谱三通道检索，比单一向量检索效果好
3. **浏览器端视频合成** — DH Live WASM SDK 在浏览器端渲染数字人视频，无需 GPU 服务器
4. **本地 GGUF 评判模型** — 可选的 Qwen3-0.6B 本地模型，支持离线部署
5. **全链路容错** — 每个 LLM 调用都有确定性降级方案
6. **组件级 LLM 覆盖** — 14 个槽位，每个功能模块可独立配置模型

### 7.3 工程创新

1. **六边形架构 (Java)** — Port/Adapter 模式，基础设施可切换
2. **Skill 系统 (Python)** — Agent prompt 通过 SKILL.md 文件管理，工程化而非硬编码
3. **SSE 事件协议** — 14 种事件类型的标准化协议，JSON Schema 约束
4. **自主优化循环** — CLAUDE.md 定义的"读→改→验→判→记→循环"自主优化流程，50+ 次迭代

---

## 八、项目规模

| 指标 | 数量 |
|------|------|
| 总代码行数 | ~35,000 |
| 源文件数 | 180+ |
| 测试文件数 | 40+ |
| AI Agent 数量 | 17 |
| 数据库表数 | 25+ |
| API 端点数 | 20+ |
| SSE 事件类型 | 14 |
| 知识库课程数 | 20 |
| 知识块数 | 986+ |
| 实验日志条目 | 82 |
| Docker 服务数 | 6 |
