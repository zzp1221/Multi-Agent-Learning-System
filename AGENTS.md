# AGENTS.md

AI agent 自主优化"智学引擎"全栈项目的运行指令。人类写这份 program，agent 执行循环：**读代码 → 改代码 → 验证指标 → 保留或回滚 → 下一轮**。

***

## 1. 成功指标（val_bpb 等价物）

每次改动后必须验证。指标变差 = 回滚。

| 指标 | 命令/方式 | 通过标准 |
|---|---|---|
| 全链路功能 | 当前热更新环境：`docker compose ps && pytest tests/ -v`；全新部署才可 `docker compose up -d --build` | 全部通过 |
| RAG 检索质量 | `pytest python-agent/tests/ -k rag -v` | hits@3 >= 90% |
| 前端构建 | `cd frontend && npx tsc --noEmit && npx vite build` | 无错误 |
| SSE 流式联通 | 前端 Console 无 CORS/401/ERR | 流式对话逐字渲染 |
| 长任务不断连 | 发起 >5min 任务，观察是否完成 | 不被 nginx/SSE 截断 |
| API 响应 | `curl -s http://localhost:8081/api/health` | 200 |

## 2. 自主循环

```
1. 读：理解当前状态（代码 + docker compose ps + 最近 test 结果）
2. 改：手术级修改，只动必须动的文件
3. 验：跑相关验证命令
4. 判：指标改善 → 保留 commit；指标不变/变差 → git checkout 回滚该文件
5. 记：在 docs/ 下追加一行结论（日期、改动、指标变化）
6. 循环，直到全部指标通过
```

## 3. 可修改 / 不可修改

| 可修改（agent 的 train.py） | 不可修改（agent 的 prepare.py） |
|---|---|
| 前端组件、页面、hooks | docker-compose 服务拓扑 |
| Java API、DTO、Service | 向量维度 1024 |
| Python Agent 工具、检索逻辑 | SSE 协议格式 `event:`/`data:` 前缀 |
| nginx 配置（超时、缓冲） | Java 是唯一入口的契约 |
| 数据库查询、索引 | 环境变量命名规范 |
| 测试用例 | 数据库 schema 结构 |

## 4. 改动约束

- **手术级**：只改问题相关的行，不顺手重构相邻代码
- **单次一改**：一次只修一个问题，验证通过再下一个
- **最少代码**：3 个重复才提取，不预设未来需求
- **禁止硬编码**：类型→枚举，密码→env，模型名→`LLMComponentOverride`，中文→i18n
- **资源清理**：文件写入带 finally 删除；SSE emitter 三回调都 remove；cache 必须 TTL
- **禁止** `except: pass`
- **热更新边界**：当前联调/演示环境只允许 `docker cp` 热更新，禁止 `docker compose build`、`docker compose up --build`、`--force-recreate` 和重建容器；全新部署文档中的 build 命令只用于空环境。
- **LLM Key 边界**：正式用户/演示任务必须使用用户在设置页保存的 API Key 和模型，不得 fallback 到系统/环境 LLM Key；自动化测试、创建测试账号、测试联调可继续使用系统/环境 Key 和默认模型，方便测试。

## 5. 全链路检查清单

每次联调前，通过前端入口逐项过：

```
□ 登录 → JWT → localStorage 持久化
□ 流式对话 → SSE text/event-stream → 逐字渲染
□ 任务 SSE → progress/done 事件推动状态流转
□ resource_file downloadUrl 可下载
□ >5min 任务不被截断
□ Console 无 CORS/401/ERR_CONNECTION_REFUSED
```

常见联调 bug 来源：nginx 超时截断、跨层 SSE 字段不匹配、容器 sandbox 路径不一致、docker-compose 遗漏环境变量。

## 6. 技术栈速查

| 层 | 语言 | 框架 | 端口 |
|---|---|---|---|
| 前端 | TypeScript | React + Vite + Tailwind | 80 |
| 后端 | Java | Spring Boot | 8081 |
| AI Agent | Python | FastAPI + SSE | 8000 |
| 数据库 | - | PostgreSQL(pgvector) + MongoDB | 5432 / 27017 |
| 缓存/队列 | - | Redis Streams + TTL 缓存 | 6379 |

## 7. 实验日志

每次改动在 `docs/experiment_log.md` 追加一行：

```
2026-05-06 | <改动描述> | <验证结果> | <指标变化> | <保留/回滚>
```

**目标：loop 直到全部指标绿灯，无 P0/P1 bug。**
