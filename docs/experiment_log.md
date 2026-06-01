2026-06-01 | 新增悬浮智能语音助手设计文档，明确 Java Voice Gateway + 现有 Python Agent 不变的架构 | 未改运行代码，未执行功能测试 | 无运行指标变化 | 保留
2026-06-01 | 在 feature/voice-assistant 实现 Java 语音助手网关、前端悬浮录音助手、ASR/TTS 流式接口和语音指令解析 | mvn test 77 passed；frontend npx tsc --noEmit passed；frontend npx vite build passed | 后端测试/前端构建保持通过，新增语音助手能力 | 保留
2026-06-01 | 补充语音助手验收检查 | docker compose ps 全部核心服务 Up/healthy；pytest tests/ -v 与 python-agent/tests/ -k rag 因当前 shell 无 pytest/python 可执行文件未能运行 | 容器状态正常；Python 测试待具备 pytest 环境后补跑 | 保留
