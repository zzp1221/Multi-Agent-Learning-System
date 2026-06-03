# 比赛提交材料指南

最后更新：2026-06-03

本文用于准备“智学引擎”比赛提交包，重点是交付可复现源码、配置、预置向量数据和文档，排除运行时数据、依赖目录、构建产物和大模型权重。

## 必须提交

| 目录/文件 | 说明 |
|---|---|
| `README.md` | 项目总览、快速开始、架构入口 |
| `AGENTS.md` / `CLAUDE.md` | Agent 运行约束和联调规则 |
| `.env.example` | 环境变量模板，不含真实密钥 |
| `docker-compose.yml` | 标准六服务编排 |
| `docker-compose.local-judge.yml` | 可选本地 Judge overlay |
| `init.sql` | PostgreSQL 初始化 |
| `mongo-init.js` | MongoDB 初始化 |
| `restore_vector_data.sh` | 向量数据恢复脚本 |
| `vector_data.dump` | PostgreSQL 向量数据 dump，约 11.5MB |
| `contracts/` | SSE 事件契约 |
| `docs/` | 架构、部署、技术报告、专题设计和实验日志 |
| `frontend/` | 前端源码，不含 `node_modules/`、`dist/` |
| `project/` | Java 后端源码，不含 `target/` |
| `python-agent/` | Python Agent 源码，不含 `.venv/`、`__pycache__/`、运行时 sandbox |
| `tests/` | 端到端与系统测试代码 |
| `migrations/` | 数据库迁移脚本 |
| `wiki/` | RAG 原始知识文档；如提交平台限大小，可改为提交重建脚本和数据源 |

## 必须排除

| 目录/文件 | 原因 |
|---|---|
| `frontend/node_modules/` | `pnpm install` 可重建 |
| `frontend/dist/` | `npx vite build` 可重建 |
| `project/target/` | `mvn package` 可重建 |
| `python-agent/.venv/` | `pip install -r requirements.txt` 可重建 |
| `python-agent/**/__pycache__/` | Python 字节码 |
| `python-agent/sandbox-temp/` | 运行时临时文件 |
| `data/` | Docker 运行时数据 |
| `.env` | 真实密钥和本地密码 |
| `*.log` | 运行日志 |
| `models/` | 本地 Judge 大模型权重，提交脚本和文档即可 |

## 训练产物

提交脚本和说明，不提交模型权重或训练输出目录。

| 文件/目录 | 处理方式 |
|---|---|
| `python-agent/scripts/sft_train.py` | 提交 |
| `python-agent/scripts/grpo_train.py` | 提交 |
| `python-agent/scripts/generate_judge_train_data.py` | 提交 |
| `python-agent/judge_train_data.json` | 可提交小规模训练数据；如平台限制大小则由脚本重建 |
| `python-agent/sft_output/` | 不提交 |
| `python-agent/grpo_output/` | 不提交 |
| `*.gguf` | 不提交 |

## 数据文件

| 文件/目录 | 处理方式 |
|---|---|
| `vector_data.dump` | 提交，标准部署初始化必需 |
| `wiki/` | 推荐提交，保证 RAG 原始材料可审阅 |
| `python-agent/knowledge/wiki_topics.json` | 提交，作为 wiki 数据源 |
| `python-agent/reports/*.json` | 提交关键报告，用于说明 RAG 指标 |

当前报告口径：

- `python-agent/reports/rag_100_after.json`：基础 100 题 hit@3 98%。
- `python-agent/reports/graph_rag_100_current.json`：图谱型 100 题 hit@3 94%。

## 答辩功能口径

- 个性化学习主入口是 `PERSONALIZED_LEARNING`，不是割裂的 `PATH_PLANNING` 或 `RESOURCE_PUSH` 两个按钮。
- 多智能体链路为 `profile -> evaluation -> query_rewrite -> retrieval -> path_planning -> resource_push -> critic`，覆盖画像分析、掌握度诊断、检索证据、路径规划、资源推荐和质量审查。
- 学习效果评估在主链路中体现为 `masteryDiagnosis`，依据学习行为、练习测试、错题复习、资源使用反馈和画像弱项动态调整学习路径与资源推送。
- 资源推送按学习步骤绑定文档、视频、题库和实操案例等类型，缺口会如实暴露，不用假资源兜底。
- 学习画像页的知识掌握图谱已按主题归并重复项，顶部展示“下一步优先关注”，其余知识点用紧凑状态概览展示，便于学生提取重点。

## 一次性或可重建文件

以下脚本如果仍在仓库中可以提交；若需要压缩提交包，可优先保留数据源和主流程脚本：

| 文件 | 说明 |
|---|---|
| `python-agent/knowledge/expand_lang_wiki*.py` | 语言类 wiki 扩展脚本 |
| `python-agent/knowledge/expand_wiki.py` | wiki 扩展脚本 |
| `python-agent/knowledge/import_wiki_to_db.py` | 结构化导入脚本 |
| `python-agent/knowledge/vectorize_wiki.py` | 向量化脚本 |
| `python-agent/knowledge/benchmark_*.py` | RAG 基准脚本 |

不要提交本机外部路径，例如 `E:/models/`。

## .gitignore 检查清单

```gitignore
frontend/node_modules/
frontend/dist/
project/target/
python-agent/.venv/
python-agent/**/__pycache__/
python-agent/sandbox-temp/
data/
.env
*.log
models/
*.gguf
*.dump
!vector_data.dump
```

注意：`docs/` 是必须提交的项目文档目录，不应整体忽略。

## 提交前验证

```bash
docker compose config --quiet
docker compose -f docker-compose.yml -f docker-compose.local-judge.yml config --quiet
git diff --check

cd frontend && npx tsc --noEmit && npx vite build
cd python-agent && pytest tests/ -k rag -v
cd project && mvn test
```

当前联调/演示环境不要为了提交验证而重建容器。

## 预计提交大小

| 部分 | 估计 |
|---|---|
| 前端源码 | 2-5MB |
| Java 后端源码 | 1-3MB |
| Python Agent 源码 | 3-8MB |
| `vector_data.dump` | 11.5MB |
| wiki 与报告 | 视提交平台限制 |
| 测试代码 | 2-5MB |
| 文档和配置 | <1MB |

如平台限制提交包大小，优先保留可运行源码、`vector_data.dump`、`wiki_topics.json`、关键报告和文档。
