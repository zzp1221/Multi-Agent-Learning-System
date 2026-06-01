# 智学引擎悬浮智能语音助手设计文档

## 1. 背景与目标

智学引擎当前已经具备前端交互、Java 统一入口、Python Agent、RAG 检索和 SSE 流式对话能力。悬浮智能语音助手的目标是在不破坏现有架构的前提下，为用户提供低延迟、随时可用的语音学习入口。

本设计采用“语音助手放 Java，其他能力不变”的方案：

- 前端负责悬浮窗、麦克风采集、播放控制和交互状态。
- Java 负责语音助手入口、鉴权、会话、低延迟链路控制、ASR/TTS 调用和对外流式协议。
- Python Agent、RAG、LLM 编排保持现状，不迁移、不重构。
- 前端只连接 Java，不直连 Python 或第三方语音服务。

## 2. 设计原则

### 2.1 Java 仍是唯一入口

所有来自前端的语音助手请求都进入 Java：

```text
Frontend -> Java -> Python Agent / ASR / TTS / LLM Provider
```

不得让前端绕过 Java 直接访问 Python Agent 或第三方模型服务。这样可以复用现有 JWT 鉴权、用户会话、日志、限流、错误处理和跨域配置。

### 2.2 现有 Python Agent 不变

语音助手第一阶段不改 Python Agent 的 RAG、工具调用、LLM 编排和接口契约。识别出的语音文本应当被当作普通用户输入，继续走现有智能问答链路。

### 2.3 全链路必须流式

语音助手对延迟敏感，关键链路必须支持流式：

```text
音频采集 -> 流式 ASR -> 流式 LLM -> 流式 TTS -> 前端播放
```

MVP 可以先不做 TTS，但 ASR 和 LLM 的设计必须预留流式接口，避免后续返工。

### 2.4 MVP 优先稳定

第一版不做完整双工实时通话，不做自动打断，不做复杂语音状态机。先完成“语音输入 + 文本确认 + 现有 SSE 回答”，验证稳定后再增强。

## 3. 总体架构

```text
+----------------------+
| Frontend             |
| - 悬浮麦克风按钮      |
| - 录音/播放控制       |
| - 语音助手面板        |
+----------+-----------+
           |
           | HTTPS / WebSocket / SSE
           v
+----------------------+
| Java Backend         |
| - VoiceController    |
| - VoiceSession       |
| - ASR Client         |
| - TTS Client         |
| - Chat Stream Bridge |
+----------+-----------+
           |
           | 内部 HTTP / SSE
           v
+----------------------+
| Python Agent         |
| - RAG                |
| - Agent Tools        |
| - LLM Orchestration  |
+----------------------+
```

推荐链路：

```text
前端录音
-> Java /api/voice/transcribe 或 /ws/voice
-> Java 调流式 ASR
-> Java 返回识别文本
-> 用户确认发送
-> Java 复用现有聊天 SSE
-> Python Agent 保持原流程
```

## 4. 功能范围

### 4.1 MVP 功能

第一阶段只实现语音输入助手：

| 功能 | 说明 |
|---|---|
| 悬浮入口 | 页面右下角麦克风按钮，全局可用 |
| 录音控制 | 点击开始录音，再次点击结束；移动端可后续支持按住说话 |
| ASR 转文字 | Java 调第三方流式 ASR 或短音频 ASR |
| 文本确认 | 识别结果展示给用户，可编辑后发送 |
| 复用聊天 | 确认后的文本走现有聊天/RAG/Agent |
| SSE 回答 | 保持现有 `event:` / `data:` SSE 协议 |
| 状态反馈 | 待机、录音中、识别中、思考中、回答中、失败 |
| 失败重试 | ASR 或聊天失败后允许重新录音/重新发送 |

MVP 不包含：

- AI 自动朗读回答。
- 用户语音打断 AI。
- 长时间免唤醒连续对话。
- 修改 Python Agent 编排。
- 改 docker-compose 服务拓扑。

### 4.2 第二阶段功能

| 功能 | 说明 |
|---|---|
| TTS 朗读 | Java 按句调用流式 TTS，前端播放音频 |
| 停止朗读 | 用户可随时停止播放并清空音频队列 |
| 静音开关 | 用户可关闭自动朗读 |
| 语音快捷指令 | “解释这道题”“总结当前知识点”“继续”“停止朗读” |
| 页面上下文 | 前端上传当前题目、课程、知识点等上下文 |

### 4.3 第三阶段功能

| 功能 | 说明 |
|---|---|
| 实时边说边识别 | WebSocket 上传音频 chunk，ASR 增量返回文本 |
| AI 可打断 | 用户开口时停止 TTS 和当前回答 |
| 长对话模式 | 类似电话的持续语音交互 |
| 学习动作控制 | 语音触发收藏、标记不会、生成复习计划等操作 |

第三阶段需要更严格的状态机和取消语义，不能在 MVP 中混入。

### 4.4 后续完善优先级

基于当前热更新环境的延时测试结果，后续不应继续堆功能，应该先把实时链路做稳。当前 Java 控制层接口是毫秒级，但百炼 ASR/TTS WebSocket 建连和首包延迟存在明显波动。P1/P2 已完成第一轮工程化：前端改为 AudioWorklet 持续采集 16k PCM，`/api/voice/ws` 改为实时转发 ASR 音频 chunk 并回传 partial/final，同时加入 turnId、cancel、ASR 异步建连和 ASR 未就绪音频缓冲。

| 优先级 | 目标 | 主要工作 | 验收标准 |
|---|---|---|---|
| P1 真正实时化 | 从“录完再传”升级为边说边识别、边生成边播放 | 前端使用 AudioWorklet 持续采集 16k PCM chunk；通过 `/api/voice/ws` 上传；Java 为每个 session 维持百炼 ASR WebSocket；ASR partial 实时回前端；停顿后 final transcript 进入现有聊天 SSE；TTS 按 chunk 播放 | ASR partial 首字延迟 `< 800ms`；TTS 首个音频 chunk `< 1000ms`；说话结束到助手开始说话 `< 1500ms`；P95 `< 2500ms` |
| P2 打断能力 | 用户重新开口时立即停止当前朗读和回答 | 前端检测重新开口后停止本地 TTS 播放；通过 WebSocket `cancel` 或后续取消接口通知 Java；Java 取消当前 TTS provider 连接和聊天流；状态机支持 `speaking -> listening -> transcribing` | 用户开口后 `< 300ms` 停止播放；取消后不再追加旧回答或旧音频；新一轮语音可继续识别 |
| P3 稳定性 | 降低第三方语音服务波动对体验的影响 | ASR/TTS 建连超时重试；TTS 首包超时降级为文字回答；provider 错误结构化日志；采集 `asr_first_partial_ms`、`tts_first_audio_ms`、`voice_round_trip_ms`；限制单用户并发 voice session；session TTL 清理 | provider 超时不阻塞 UI；错误有可定位日志；并发和 TTL 不造成会话泄漏；降级路径可用 |
| P4 功能完善 | 在实时链路稳定后补齐学习助手能力 | 页面上下文问答；语音快捷指令扩展；朗读暂停/继续/换音色；ASR 低置信度确认；历史语音文本记录；学习动作控制 | 不破坏现有聊天 SSE；不存储原始音频；快捷指令和页面上下文在主要页面可用 |

推荐下一步继续验证 P1/P2 的运行效果：用真实麦克风测试 ASR partial 首字延迟、final transcript 延迟和打断后旧 turn 是否被丢弃。P1/P2 验收通过后，再进入 P3 稳定性治理。

### 4.5 P1/P2 当前落地状态

已落地：

- `/api/voice/ws` 握手先返回 `ready`，百炼 ASR WebSocket 在 `voiceTaskExecutor` 中异步建连，避免 provider 建连慢导致浏览器 WebSocket 握手失败。
- `/api/voice/ws` 作为浏览器 WebSocket 升级入口在 Spring Security 层放行，实际 JWT query token 和 voice session 归属仍由 `VoiceRealtimeWebSocketHandler` 校验；已补回归测试避免 401 握手问题复发。
- ASR 未 ready 时，Java 会按 turn 缓存已到达的 PCM chunk 和 commit；ASR session 建好后按顺序 flush，减少开头语音丢失。
- 每轮语音都有 `turnId`，cancel 后递增新 turn；旧 turn 的 partial/final/error 回调会被丢弃，避免旧识别结果污染新一轮。
- 前端重新录音会先停止 TTS 播放、取消当前聊天流、关闭旧 realtime socket，然后创建新 session 开始录音。
- 前端区分预期关闭和异常断开，ASR final 后主动关闭 WebSocket 不再误报“连接已断开”。

仍需真实浏览器验收：

- 麦克风授权后 ASR partial 首字延迟是否稳定低于 800ms。
- 用户在 `chatting/speaking` 中重新录音时，旧回答和旧音频是否立即停止且不再追加。
- 百炼 provider 偶发建连超时下，UI 是否能给出可重试错误，不阻塞下一轮录音。

## 5. 推荐模型与服务

语音助手必须优先选择支持流式能力的服务。

### 5.1 首选方案：阿里云 / 通义体系

| 环节 | 推荐模型/服务 | 理由 |
|---|---|---|
| ASR | Qwen-ASR-Realtime | WebSocket 实时语音识别，适合低延迟转写 |
| LLM | qwen-turbo | 首 token 延迟低，适合语音助手第一版 |
| LLM | qwen-plus | 回答质量更好，适合质量优先场景 |
| TTS | CosyVoice v3-flash / Qwen-TTS Realtime | 支持流式语音合成，适合边生成边播放 |

建议第一版：

```text
ASR: Qwen-ASR-Realtime
LLM: qwen-turbo, stream=true
TTS: 暂不启用
```

建议第二版：

```text
ASR: Qwen-ASR-Realtime
LLM: qwen-plus 或 qwen-turbo, stream=true
TTS: CosyVoice v3-flash / Qwen-TTS Realtime
```

### 5.2 备选方案：腾讯云体系

| 环节 | 推荐服务 | 理由 |
|---|---|---|
| ASR | 腾讯云实时语音识别 WebSocket | 国内网络稳定，实时返回识别结果 |
| LLM | 混元流式接口或现有 LLM | 可统一腾讯云生态 |
| TTS | 腾讯云实时语音合成 WebSocket | 支持流式合成和播放 |

### 5.3 不建议第一版使用的模型

不建议第一版默认使用深度思考类模型，例如 R1、QwQ、长推理模式。原因是语音助手更看重首字延迟和连续反馈，深度思考模型容易让用户感知为“卡住”。

## 6. 后端接口设计

### 6.1 短录音 MVP 接口

```http
POST /api/voice/transcribe
Authorization: Bearer <jwt>
Content-Type: multipart/form-data

file=<audio.webm>
```

响应：

```json
{
  "text": "请解释一下这道题",
  "durationMs": 3200,
  "provider": "qwen-asr-realtime"
}
```

适用场景：

- MVP 快速落地。
- 用户短句提问。
- 前端录完再识别。

### 6.2 实时语音 WebSocket 接口

```text
GET /api/voice/ws?sessionId=<id>
Authorization: Bearer <jwt>
```

前端发送：

```json
{
  "type": "audio_chunk",
  "format": "webm/opus",
  "seq": 12,
  "data": "<base64>"
}
```

Java 返回：

```json
{
  "type": "asr_partial",
  "text": "请解释",
  "seq": 12
}
```

```json
{
  "type": "asr_final",
  "text": "请解释一下这道题",
  "seq": 21
}
```

适用场景：

- 第二阶段或第三阶段。
- 边说边识别。
- 需要更低体感延迟。

### 6.3 复用现有聊天 SSE

语音转文字后，不新建一套聊天协议。前端将确认后的文本按普通聊天请求发送，继续复用现有 SSE：

```text
event: message
data: ...

event: done
data: ...
```

禁止修改现有 SSE `event:` / `data:` 前缀格式。

## 7. Java 模块划分

建议新增模块或包：

```text
com.xxx.voice
  VoiceController
  VoiceWebSocketHandler
  VoiceSessionService
  VoiceAsrClient
  VoiceTtsClient
  VoiceCommandParser
  dto/
    VoiceTranscribeResponse
    VoiceSessionState
    VoiceWsEvent
```

职责说明：

| 类/模块 | 职责 |
|---|---|
| VoiceController | HTTP 录音上传、识别结果返回 |
| VoiceWebSocketHandler | 实时音频 chunk 接收和 ASR 增量返回 |
| VoiceSessionService | 维护语音会话状态、超时、取消 |
| VoiceAsrClient | 封装第三方 ASR 调用 |
| VoiceTtsClient | 封装第三方 TTS 调用 |
| VoiceCommandParser | 第二阶段识别“停止朗读”等快捷指令 |

## 8. 前端交互设计

### 8.1 悬浮入口

位置：

```text
右下角，避开现有主要操作按钮和聊天输入框
```

状态：

| 状态 | UI 表现 |
|---|---|
| idle | 麦克风按钮 |
| recording | 红点/波形/计时 |
| transcribing | loading |
| ready | 展示识别文本和发送按钮 |
| thinking | 复用聊天流式状态 |
| speaking | 第二阶段展示停止朗读按钮 |
| error | 展示错误和重试 |

### 8.2 文本确认

ASR 结果必须允许编辑。推荐交互：

```text
录音完成 -> 显示识别文本 -> 用户可编辑 -> 点击发送
```

原因：

- 学科术语、英文、公式、专有名词容易识别错误。
- 可编辑确认能显著降低错误请求进入 RAG 的概率。

### 8.3 页面上下文

第二阶段开始，前端可以在语音请求中携带上下文：

```json
{
  "pageType": "question_detail",
  "questionId": "q_123",
  "courseId": "math_7",
  "knowledgePointId": "linear_equation"
}
```

Java 不直接解释复杂学习逻辑，只把上下文作为现有聊天请求的一部分传入原有链路。

## 9. 状态机

MVP 状态机：

```text
idle
  -> recording
  -> transcribing
  -> ready_to_send
  -> chatting
  -> idle
```

异常流：

```text
recording -> error -> idle
transcribing -> error -> idle
chatting -> error -> idle
```

第二阶段状态机增加：

```text
chatting -> tts_generating -> speaking -> idle
speaking -> stopped -> idle
```

第三阶段打断状态：

```text
speaking -> interrupted -> recording
```

实现要求：

- 每个 voice session 必须有 TTL。
- 用户关闭悬浮窗时清理录音和播放资源。
- 后端 session 超时后主动释放第三方连接。
- 前端停止朗读时清空音频播放队列。

## 10. 配置与安全

新增配置应遵守环境变量规范，不硬编码密钥：

```text
VOICE_ASR_PROVIDER=qwen
VOICE_ASR_API_KEY=...
VOICE_TTS_PROVIDER=qwen
VOICE_TTS_API_KEY=...
VOICE_SESSION_TTL_SECONDS=300
VOICE_MAX_AUDIO_SECONDS=60
VOICE_MAX_AUDIO_BYTES=10485760
```

安全要求：

- 所有接口必须校验 JWT。
- 限制单次音频时长和大小。
- 限制用户并发 voice session 数。
- 第三方 API key 只存在服务端。
- 不在日志中打印完整音频内容、token、API key。
- 识别文本进入聊天前走现有敏感词/权限/审计逻辑。

## 11. 性能目标

MVP 建议目标：

| 指标 | 目标 |
|---|---|
| 录音结束到识别文本返回 | P95 <= 1500ms |
| 文本发送到首个 SSE token | P95 <= 2000ms |
| ASR 失败率 | <= 2% |
| 前端 Console | 无 CORS/401/ERR_CONNECTION_REFUSED |

第二阶段目标：

| 指标 | 目标 |
|---|---|
| LLM 句子完成到 TTS 首包 | P95 <= 1000ms |
| 停止朗读响应 | <= 200ms |
| 长任务 SSE | >5min 不被截断 |

## 12. 验证方案

每次改动后必须验证相关指标。

### 12.1 后端验证

```bash
docker compose ps
pytest tests/ -v
curl -s http://localhost:8081/api/health
```

### 12.2 RAG 验证

```bash
pytest python-agent/tests/ -k rag -v
```

### 12.3 前端验证

```bash
cd frontend
npx tsc --noEmit
npx vite build
```

### 12.4 语音助手专项验证

MVP：

```text
□ 首次点击麦克风会触发浏览器权限请求
□ 拒绝权限后提示清晰且可重试
□ 录音结束后能返回识别文本
□ 识别文本可编辑
□ 发送后复用现有 SSE 流式回答
□ Console 无 CORS/401/ERR_CONNECTION_REFUSED
□ JWT 过期时走现有登录/刷新逻辑
```

第二阶段：

```text
□ TTS 可按句播放
□ 停止朗读可立即生效
□ 关闭悬浮窗会停止录音和播放
□ 长回答不会导致音频队列无限增长
```

## 13. 实施计划

### 13.1 第一阶段：MVP

1. 前端新增悬浮语音助手组件。
2. 前端实现浏览器录音，生成 `webm/opus` 音频。
3. Java 新增 `/api/voice/transcribe`。
4. Java 封装 ASR client。
5. 前端展示识别文本并允许编辑。
6. 用户确认后复用现有聊天发送和 SSE 渲染。
7. 补充基础测试和联调检查。

### 13.2 第二阶段：TTS

1. Java 新增 TTS client。
2. 前端新增播放队列。
3. Java 或前端按句切分回答文本。
4. 前端支持停止朗读和静音开关。
5. 验证长回答、取消、异常恢复。

### 13.3 第三阶段：实时对话

1. 新增 `/api/voice/ws`。
2. 前端以 chunk 上传音频。
3. Java 转发 ASR partial/final。
4. 引入 VAD 或前端静音检测。
5. 增加打断和取消语义。

## 14. 风险与规避

| 风险 | 影响 | 规避 |
|---|---|---|
| ASR 识别错误 | RAG 输入错误 | MVP 必须支持文本确认和编辑 |
| 首包延迟高 | 用户感觉卡顿 | 选择流式模型，避免深度思考模型 |
| TTS 队列堆积 | 播放滞后 | 按句合成，限制队列长度，支持停止 |
| 前端直连第三方 | API key 泄露 | 所有第三方调用只在 Java |
| 状态机复杂 | 打断/取消错乱 | MVP 不做自动打断 |
| CORS/鉴权问题 | 功能不可用 | 前端只连 Java，复用现有 JWT |
| 长连接截断 | 语音会话中断 | 检查 nginx/SSE/WebSocket 超时配置 |

## 15. 结论

悬浮智能语音助手适合加入智学引擎，但第一版应控制范围。推荐将语音助手作为 Java 侧 Voice Gateway 实现，负责低延迟语音链路、会话和安全边界；Python Agent、RAG 和现有聊天编排保持不变。

最终推荐 MVP：

```text
悬浮入口
+ 录音
+ Java ASR
+ 文本确认编辑
+ 复用现有聊天 SSE
```

后续再逐步加入：

```text
流式 TTS
+ 停止朗读
+ 页面上下文
+ 实时 WebSocket
+ 用户打断
```

该方案能在保持项目架构稳定的同时，为演示和真实学习场景提供明显的产品亮点。

## 16. 当前实现状态

分支：`feature/voice-assistant`

已完成：

| 模块 | 状态 | 说明 |
|---|---|---|
| Java Voice Gateway | 已完成 | 新增 `/api/voice/**`，Java 作为唯一前端入口 |
| ASR 转文字 | 已完成 | `/api/voice/transcribe` 接收 16k 单声道 PCM，调用百炼实时语音 WebSocket |
| TTS 流式合成 | 已完成 | `/api/voice/tts/stream` 通过 SSE 返回 base64 PCM 音频 chunk |
| 语音 session | 已完成 | `/api/voice/sessions` 创建 TTL session，供实时 WebSocket 使用 |
| 实时 WebSocket ASR | 已完成 | `/api/voice/ws` 支持音频 chunk 实时转发百炼 ASR、partial/final 回传、commit、cancel 和 turnId |
| 语音快捷指令解析 | 已完成 | `/api/voice/commands/parse` 支持停止朗读、继续、解释、总结、类似题等 intent |
| 前端悬浮助手 | 已完成 | 全局右下角麦克风按钮、AudioWorklet 实时 PCM 采集、partial 识别展示、可编辑确认、发送 |
| 复用聊天 SSE | 已完成 | 识别文本创建会话后走现有 `/api/conversations/{id}/messages/stream` |
| 自动朗读开关 | 已完成 | 前端可开启回答后自动 TTS 播放，支持停止当前任务 |
| 安全配置 | 已完成 | 语音 REST 接口走 Spring Security JWT；`/api/voice/ws` 放行浏览器升级握手，但 handler 校验 query token 和 session 归属；第三方 key 仅服务端环境变量读取 |

配置项：

```text
VOICE_ENABLED=true
VOICE_PROVIDER=bailian
VOICE_API_KEY=...
BAILIAN_API_KEY=...
VOICE_ASR_MODEL=qwen3-asr-flash-realtime
VOICE_ASR_WEBSOCKET_URL=wss://dashscope.aliyuncs.com/api-ws/v1/realtime
VOICE_TTS_MODEL=qwen3-tts-flash-realtime
VOICE_TTS_WEBSOCKET_URL=wss://dashscope.aliyuncs.com/api-ws/v1/realtime
VOICE_TTS_VOICE=Cherry
VOICE_SAMPLE_RATE=16000
VOICE_MAX_AUDIO_SECONDS=60
VOICE_MAX_AUDIO_BYTES=10485760
VOICE_CONNECT_TIMEOUT=5s
VOICE_REQUEST_TIMEOUT=90s
VOICE_SESSION_TTL=300s
```

验收结果：

```text
docker compose ps -> app/frontend/python-agent/postgres/mongo/redis 均 Up，数据服务 healthy
project: mvn test -> 81 tests passed
frontend: npx tsc --noEmit -> passed
frontend: npx vite build -> passed
pytest tests/ -v -> 当前宿主 shell 无 pytest/python 可执行文件，未能运行；python-agent 容器有 pytest 但镜像内未包含 tests 目录
pytest python-agent/tests/ -k rag -v -> 当前宿主 shell 无 pytest/python 可执行文件，未能运行；python-agent 容器有 pytest 但镜像内未包含 tests 目录
docker compose ps -> app/frontend/python-agent/postgres/mongo/redis 均 Up，数据服务 healthy
curl http://localhost:8081/api/health -> {"status":"UP"}
authenticated /api/voice/commands/parse 停止朗读 -> STOP_SPEAKING
authenticated /api/voice/ws smoke -> ready 36ms, cancel 4ms, sampleRate 16000, turn-1 -> turn-2
```

当前限制：

- 浏览器端录音使用 16k PCM 上传，MVP 不是直接上传 webm 容器。
- `/api/voice/ws` 已从分片收集后提交识别升级为实时 ASR 通道；自动化 smoke 已验证 ready/cancel 链路，仍需用真实麦克风验证 ASR partial 首字延迟和 final transcript 延迟。
- 本轮未执行真实麦克风 + 百炼 API 端到端延迟验收；需要浏览器授权麦克风后人工确认。
- 用户提供过的百炼 API Key 未写入代码、配置或文档；建议在百炼控制台轮换该 key。

## 17. 参考资料

- JavaGuide：《AI 语音技术详解》 https://javaguide.cn/ai/system-design/ai-voice.html
- 阿里云 Model Studio：Qwen-ASR-Realtime https://www.alibabacloud.com/help/zh/model-studio/qwen-asr-realtime-interaction-process
- 阿里云 Model Studio：通义千问流式输出 https://help.aliyun.com/zh/model-studio/stream
- 阿里云 Model Studio：实时语音合成 https://help.aliyun.com/zh/model-studio/realtime-tts-user-guide
- 阿里云 Model Studio：CosyVoice WebSocket API https://www.alibabacloud.com/help/zh/model-studio/cosyvoice-websocket-api
- 腾讯云：实时语音识别 https://cloud.tencent.com/document/product/586/48982
- 腾讯云：实时语音合成 https://cloud.tencent.com/document/product/1073/94308
- DeepSeek API：Chat Completion Streaming https://api-docs.deepseek.com/api/create-chat-completion
