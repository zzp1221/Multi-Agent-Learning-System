# 智学引擎部署指南

最后更新：2026-06-03

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

如果启用悬浮智能语音助手，还需要配置百炼语音服务密钥。两个变量二选一即可，推荐统一使用 `VOICE_API_KEY`：

```env
VOICE_API_KEY=replace-with-bailian-api-key
# 或兼容旧部署：
BAILIAN_API_KEY=replace-with-bailian-api-key
VOICE_ASR_MODEL=qwen3-asr-flash-realtime
VOICE_TTS_MODEL=qwen3-tts-flash-realtime
VOICE_TTS_VOICE=Cherry
VOICE_SAMPLE_RATE=16000
VOICE_MAX_AUDIO_SECONDS=60
VOICE_MAX_AUDIO_BYTES=10485760
VOICE_ASR_VAD_SILENCE_DURATION_MS=1200
VOICE_ASR_VAD_THRESHOLD=0.5
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
- `VOICE_API_KEY`/`BAILIAN_API_KEY` 只注入 Java 控制平面，用于 `/api/voice/**` 调用百炼实时 ASR/TTS；前端不得持有或直连第三方语音服务。
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

语音助手专项验收需要先通过 `/api/auth/login` 获取 JWT，然后检查：

```bash
curl -s -X POST http://localhost:8081/api/voice/sessions \
  -H "Authorization: Bearer <jwt>"

curl -s -N -X POST http://localhost:8081/api/voice/tts/stream \
  -H "Authorization: Bearer <jwt>" \
  -H "Content-Type: application/json" \
  --data '{"text":"hello","voice":"Cherry"}'
```

期望：

- `/api/voice/sessions` 返回 `provider=bailian`、`asrModel`、`ttsModel` 和 `sampleRate=16000`。
- `/api/voice/tts/stream` 返回 `event: audio`，最后返回 `event: done`。
- `/api/voice/transcribe` 上传 16k mono PCM 时返回 `text/durationMs/provider/model`。
- `/api/voice/ws` 建连后返回 `ready` 和 `turnId`，上传 16k mono PCM chunk 时应返回 `asr_partial`；provider 在说话中途可能返回分段 `asr_final`，前端只更新识别文本，不应停止录音；用户点停止后发送 `commit`，收到最终 `asr_final` 后进入可发送状态；发送 `cancel` 后返回新的 `turnId`，前端不应继续展示旧 turn 的字幕或音频。
- `/api/voice/ws` 的浏览器 WebSocket 鉴权使用 query token：Spring Security 放行升级入口，`VoiceRealtimeWebSocketHandler` 校验 JWT 和 voice session 归属。不要把它改成仅依赖 `Authorization` 头，否则浏览器 WebSocket 会在握手阶段被 401 拦截。
- `/api/voice/commands/parse` 应能解析 `停止朗读`、`暂停朗读`、`继续朗读`、`打开错题本`、`开始今日复习`、`打开个人画像`、`回到问答`、`生成学习计划`；其中页面动作由前端消费本地 intent，不新增后端写接口。
- 前端悬浮面板应展示最近 5 条语音文本历史，可点击重发；只保存识别文本和回答摘要到浏览器 `localStorage`，不保存原始音频。
- 前端 Console 无 CORS、401、`ERR_CONNECTION_REFUSED`。

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

当前 `master` 分支热更新标准步骤：

```bash
# 前端：本地构建后覆盖 nginx 静态目录
cd frontend
npx tsc --noEmit
npx vite build
docker cp dist/. zhixue-frontend:/usr/share/nginx/html/
docker exec zhixue-frontend nginx -s reload

# Java：如确需更新后端，先本地打包，再覆盖 jar 并重启 app 容器
cd project
mvn.cmd -q -DskipTests package
docker cp target/zhixue-control-plane-0.0.1-SNAPSHOT.jar zhixue-app:/app/app.jar
docker restart zhixue-app

# Python：如涉及 Agent 代码，复制对应源码/skill 到 /app 后校验并重启 Python Agent
docker cp python-agent/server.py zhixue-python-agent:/app/server.py
docker cp python-agent/src/. zhixue-python-agent:/app/src/
docker cp python-agent/skills/. zhixue-python-agent:/app/skills/
docker exec zhixue-python-agent python -m py_compile /app/server.py /app/src/ai_modules/supervisor.py
docker restart zhixue-python-agent
```

只修改前端展示时，不需要更新 Java 或 Python；只修改 Java 时，不需要覆盖前端静态目录；只修改 Python Agent 时，不需要重启 `zhixue-app`。无论哪种场景，都不要执行 build/recreate 类命令。

当前热更新环境如需临时注入语音助手密钥，不要重建容器，也不要把 key 写入仓库。可通过 Spring Boot 外置配置覆盖容器内 `/app/config/application.yml`：

```yaml
app:
  upload:
    image-token-ttl-seconds: 1800
    image-storage-dir: /data/sandbox-temp/chat-images
  voice:
    api-key: "<bailian-api-key>"
```

建议先备份容器内现有配置，再用 `docker cp` 覆盖并重启 Java 容器：

```bash
docker exec zhixue-app cp /app/config/application.yml /app/config/application.yml.before-voice-key
docker cp application.yml zhixue-app:/app/config/application.yml
docker restart zhixue-app
```

该方式只适合联调/演示热更新环境。正式环境应把 `VOICE_API_KEY` 放入 `.env`、密钥管理系统或部署平台 Secret，并在维护窗口重建或滚动发布注入。

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

语音助手返回 `VOICE_API_KEY_MISSING`：

- Java 容器没有读取到 `VOICE_API_KEY` 或 `BAILIAN_API_KEY`。
- 热更新环境检查 `/app/config/application.yml` 是否包含 `app.voice.api-key`。
- 修改容器外置配置后必须 `docker restart zhixue-app`，同一容器重启可以读取外置配置；新增环境变量则通常需要重建容器。

语音助手 TTS SSE 返回 `event:error` 或 ASR 返回 `VOICE_PROVIDER_UNAVAILABLE`：

- 检查 Java 容器到 `https://dashscope.aliyuncs.com` 的网络连通性。
- 检查模型名是否为 `qwen3-asr-flash-realtime` / `qwen3-tts-flash-realtime`。
- 检查 API key 是否有效、是否已开通对应百炼实时语音模型。
- 查看 `docker logs --tail 200 zhixue-app` 中的 `BailianRealtimeVoiceClient` 警告。

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
- 不提交真实 `VOICE_API_KEY`、`BAILIAN_API_KEY` 或容器外置配置中的语音 key。
- 不保留示例占位值或弱密码。
- `APP_JWT_SECRET`、`PYTHON_AGENT_INTERNAL_TOKEN` 使用不同随机值。
- 生产环境只开放前端入口，Java/Python 端口按需限制来源。
- 数据库端口默认目标是 `127.0.0.1`；服务器部署仍建议用防火墙限制。
- 生成资源下载 token 和图片 token 保持短 TTL。
