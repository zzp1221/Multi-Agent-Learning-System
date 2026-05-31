# 智学引擎部署指南

最后更新：2026-05-31

本文面向从代码仓库全新部署的同学。默认部署使用 Docker Compose，包含前端、Java 控制平面、Python Agent、PostgreSQL、MongoDB、Redis 六个服务。

> 当前联调/演示环境是热更新环境：只允许 `docker cp` 同步文件，禁止 `docker compose build`、`docker compose up --build`、`--force-recreate` 和重建容器。本文中的 build/recreate 命令只适用于全新部署、空环境初始化或明确维护窗口。

## 1. 环境要求

- Docker 24+
- Docker Compose v2.20+
- Git
- 至少一个 OpenAI-compatible LLM API Key
- Embedding API Key，用于 RAG 向量检索
- 可选：Tavily API Key，用于联网检索
- 可选：本地 GGUF Judge 模型

Java 后端镜像使用 Docker 多阶段构建，不要求宿主机提前安装 Maven。

## 2. 拉取代码

```bash
git clone <repo-url> zhixue-engine
cd zhixue-engine
```

## 3. 配置环境变量

```bash
cp .env.example .env
```

Windows PowerShell：

```powershell
Copy-Item .env.example .env
```

至少修改以下变量，不能保留占位值：

```env
POSTGRES_PASSWORD=replace-with-a-strong-postgres-password
APP_JWT_SECRET=replace-with-a-random-secret-at-least-32-bytes
PYTHON_AGENT_INTERNAL_TOKEN=replace-with-a-random-shared-internal-token
AI_OPENAI_COMPATIBLE_API_KEY=replace-with-openai-compatible-api-key
EMBEDDING_API_KEY=replace-with-embedding-api-key
```

随机值生成示例：

```bash
openssl rand -base64 32
```

PowerShell：

```powershell
[Convert]::ToBase64String((1..32 | ForEach-Object { Get-Random -Maximum 256 }))
```

关键说明：

- `APP_JWT_SECRET` 用于签发 JWT，至少 32 字节。
- `PYTHON_AGENT_INTERNAL_TOKEN` 是 Java 与 Python 内部接口共享密钥。
- `AI_OPENAI_COMPATIBLE_BASE_URL` 默认来自 `.env.example`，可按厂商替换。
- `EMBEDDING_API_KEY` 与 `DASHSCOPE_API_KEY` 互为兼容入口；RAG 质量验证依赖 embedding。
- `POSTGRES_PASSWORD` 首次启动后写入 `./data/postgres`，后续修改需要同步数据库密码或清空数据目录重建。
- Compose 文件中数据服务目标绑定是 `127.0.0.1`；如果旧容器仍显示 `0.0.0.0`，说明容器尚未按新 Compose 配置重建。

## 4. 启动标准部署

全新空环境：

```bash
docker compose up -d --build
```

查看状态：

```bash
docker compose ps
```

预期服务：

- `zhixue-frontend`
- `zhixue-app`
- `zhixue-python-agent`
- `zhixue-postgres`
- `zhixue-mongo`
- `zhixue-redis`

首次启动会自动执行：

- `init.sql`：创建 PostgreSQL `app`、`rag`、`storage` schema、表、索引和触发器。
- `restore_vector_data.sh`：从 `vector_data.dump` 恢复预置向量数据。
- `mongo-init.js`：初始化 MongoDB collection 和索引。

## 5. 端口与入口

| 入口 | 地址 | 说明 |
|---|---|---|
| 前端 | `http://localhost/` | 用户主入口 |
| Java API | `http://localhost:8081/api/health` | 外部业务 API |
| Python health | `http://localhost:8000/health` | 健康检查 |
| PostgreSQL | `127.0.0.1:5432` | 本机调试 |
| MongoDB | `127.0.0.1:27017` | 本机调试 |
| Redis | `127.0.0.1:6379` | 本机调试 |

浏览器业务请求只走前端 `/api/*` 反代到 Java。Python `/internal/*` 接口只供 Java 调用。

## 6. 验证部署

```bash
curl -s http://localhost:8081/api/health
curl -s http://localhost:8000/health
curl -I http://localhost/
```

预期：

- Java `/api/health` 返回 `{"status":"UP"}`。
- Python `/health` 返回 `status: ok`，并带 provider/model 解析信息。
- 前端首页 HTTP 200。

基础全链路检查：

```bash
docker compose ps
cd frontend && npx tsc --noEmit && npx vite build
cd python-agent && pytest tests/ -k rag -v
```

如需检查 SmartEngine 队列，确认 Redis 中存在 consumer group 和 stream key：

```bash
docker exec zhixue-redis redis-cli XINFO GROUPS zhixue:smart-engine:tasks
```

如果尚未提交过任务，该命令可能返回空或 no such key；提交一次 `/engine` 任务后再看。

## 7. 可选：本地 Judge 模型

本地主观题 Judge 使用 GGUF 模型，默认关闭，不影响标准部署。

准备模型：

```bash
mkdir -p models
# 将 judge_model.gguf 放到 ./models/judge_model.gguf
```

全新部署或维护窗口启动 overlay：

```bash
docker compose -f docker-compose.yml -f docker-compose.local-judge.yml up -d --build
```

overlay 会设置：

```env
ENABLE_LOCAL_JUDGE=true
LOCAL_JUDGE_MODEL_PATH=/app/models/judge_model.gguf
UVICORN_WORKERS=1
```

单 worker 是为了避免多个 Uvicorn worker 重复加载 GGUF 模型。

## 8. 常用运维命令

查看日志：

```bash
docker compose logs -f
docker compose logs -f app
docker compose logs -f python-agent
docker compose logs -f frontend
```

当前热更新环境不要执行 `docker compose down`、`docker compose up --build` 或 `docker compose up --force-recreate`。

允许的热更新示例：

```bash
# 前端：本地构建后覆盖 nginx 静态目录
cd frontend
npx tsc --noEmit
npx vite build
docker cp dist/. zhixue-frontend:/usr/share/nginx/html/
docker exec zhixue-frontend nginx -s reload

# Python：只复制改动的 .py/skill 文件到 /app 对应路径
docker cp python-agent/src/ai_modules/runtime/resource_bundle_workflow.py zhixue-python-agent:/app/src/ai_modules/runtime/resource_bundle_workflow.py
docker exec zhixue-python-agent python -m py_compile /app/src/ai_modules/runtime/resource_bundle_workflow.py
docker exec zhixue-python-agent kill -HUP 1

# Java：如确需更新后端，先本地打包，再覆盖 jar 并重启 app 容器
cd project
mvn.cmd -q -DskipTests package
docker cp target/zhixue-control-plane-0.0.1-SNAPSHOT.jar zhixue-app:/app/app.jar
docker restart zhixue-app
```

维护窗口中，保留数据并重新应用端口绑定/环境变量：

```bash
docker compose up -d --force-recreate
```

清空全部容器和数据前请先备份，仅全新初始化使用：

```bash
docker compose down -v
```

本项目数据主要落在：

```text
./data/postgres
./data/mongo
./data/redis
./data/logs
./data/sandbox-temp
```

## 9. 本地开发

只启动依赖服务：

```bash
docker compose up -d postgres mongo redis
```

前端：

```bash
cd frontend
pnpm install
pnpm dev
```

Java：

```bash
cd project
mvn spring-boot:run
```

Python Agent：

```bash
cd python-agent
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.template .env
uvicorn server:app --reload --port 8000
```

Windows PowerShell：

```powershell
.\.venv\Scripts\Activate.ps1
```

## 10. 故障排查

`POSTGRES_PASSWORD must be configured`：

- `.env` 不存在或未配置 `POSTGRES_PASSWORD`。
- 复制 `.env.example` 后填写真实强密码。

`APP_JWT_SECRET must be configured`：

- `.env` 未配置 JWT secret，或长度不足。
- 使用随机 32 字节以上字符串。

`PYTHON_AGENT_INTERNAL_TOKEN must be configured`：

- `.env` 缺少 Java/Python internal token。
- 标准部署只需要在根目录 `.env` 配置一次，Compose 会注入两个服务。

Python Agent 调 LLM 失败：

- 检查 `AI_OPENAI_COMPATIBLE_API_KEY` 和 `AI_OPENAI_COMPATIBLE_BASE_URL`。
- 检查 `EMBEDDING_API_KEY` 或 `DASHSCOPE_API_KEY`。
- `curl http://localhost:8000/health` 可看到 runtimeProvider 和 resolved model。

RAG 测试因 SSL/EOF 中断：

- 通常是外部 embedding API 网络波动。
- 先确认 Postgres/pgvector 和 vector dump 已恢复，再重跑 `pytest python-agent/tests/ -k rag -v`。

SmartEngine 提交后一直 `PENDING`：

- 检查 Redis 是否可用。
- 检查 `zhixue-python-agent` 日志里 `SmartEngine Redis Streams worker started`。
- 检查 `CONTROL_PLANE_BASE_URL=http://app:8081`。
- 检查 internal token 在 Java/Python 两侧一致。

端口仍显示 `0.0.0.0:5432`：

- 说明容器是旧端口配置创建的。
- 当前热更新环境不要重建数据服务容器；记录风险并等待维护窗口。
- 维护窗口中可执行 `docker compose up -d --force-recreate postgres mongo redis`。

## 11. 安全检查清单

- 不提交真实 `.env`。
- 不保留示例占位值或弱密码。
- `APP_JWT_SECRET`、`PYTHON_AGENT_INTERNAL_TOKEN` 使用不同随机值。
- 生产环境只开放前端入口，Java/Python 端口按需限制来源。
- 数据库端口默认目标是 `127.0.0.1`；服务器部署仍建议用防火墙限制。
- 生成资源下载 token 和图片 token 保持短 TTL。
