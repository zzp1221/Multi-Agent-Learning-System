# Judge Agent 优化说明：客观题去 LLM + 主观题本地模型

最后更新：2026-05-31

本文记录 Judge Agent 的当前实现和本地 Judge 模型接入方式。历史目标是降低客观题 LLM 调用成本，并允许主观题用本地 GGUF 模型替代远端 LLM。

## 一、当前结论

当前代码已经完成两项关键优化：

1. 客观题判分不再调用 LLM，直接使用标准化字符串比对。
2. 主观题评估通过 `SubjectiveJudgeEvaluatorFactory` 切换：
   - `ENABLE_LOCAL_JUDGE=true`：使用 `LocalSubjectiveJudgeEvaluator`，模型路径来自 `LOCAL_JUDGE_MODEL_PATH`。
   - 否则使用 `judge_llm` 组件对应的远端 provider。
   - 如果远端 provider 不可用，直接失败，不再用启发式评估冒充主观题判分。

真实运行路径：

```python
PythonAgentSupervisor.agent_registry["judge"] = JudgeAgent()
JudgeAgent._evaluate_subjective()
  -> SubjectiveJudgeEvaluatorFactory.create()
  -> LocalSubjectiveJudgeEvaluator 或 OpenAICompatibleSubjectiveJudgeEvaluator
```

## 二、当前架构

`JudgeAgent` 位于：

`python-agent/src/ai_modules/agents/judge_agent.py`

当前主流程：

```text
_run_direct_judge_pipeline
├── _validate_reused_question_batch_provenance
├── _tool_grade_objective          # 客观题本地判分
├── _tool_evaluate_subjective      # 主观题远端/本地 Judge
├── _validate_complete_judge_items
├── _tool_generate_feedback        # LLM 反馈，失败时只回退本地统计摘要
└── _tool_save_practice_result     # PostgreSQL，失败时 InMemory 存储降级
```

说明：

- 当前 `AgentCoreLoop` 判题路径已收敛为直接 pipeline，避免 LLM 自主工具循环引入不稳定。
- 客观题使用 `_is_objective_answer_correct()`，不消耗 LLM。
- 主观题必须有真实远端 LLM 或本地 GGUF；无可用 Judge 时抛错。
- 存储失败可以降级到内存存储，这是容灾，不是内容生成兜底。

## 三、客观题判分

当前 `_tool_grade_objective()` 行为：

- 遍历 `practiceQuestionBatch.questions`。
- `SHORT_ANSWER` 放入 `pendingSubjective`。
- 其他题型走 `_is_objective_answer_correct()`。
- 正确给 20 分，错误给 0 分。
- 生成每题 `profileDelta`。

成本变化：

| 项 | 旧方案 | 当前方案 |
|---|---|---|
| 客观题 LLM 调用 | 每批至少 1 次 | 0 次 |
| 判分稳定性 | 受模型格式影响 | 确定性 |
| 适用题型 | 客观题 | 选择/判断/填空等标准答案题 |

## 四、主观题评估

### 4.1 工厂切换

文件：

`python-agent/src/ai_modules/llms/judge_subjective_evaluator.py`

当前工厂逻辑：

```python
settings = get_settings()
if settings.enable_local_judge:
    return LocalSubjectiveJudgeEvaluator(model_path=settings.local_judge_model_path)

provider_name = settings.resolve_component_provider("judge_llm")
if settings.provider_ready(provider_name):
    return OpenAICompatibleSubjectiveJudgeEvaluator()

raise RuntimeError("judge_llm provider is not ready; subjective questions require a real LLM judge")
```

### 4.2 远端评估器

`OpenAICompatibleSubjectiveJudgeEvaluator` 使用 `judge_llm` 组件路由：

- provider：`settings.resolve_component_provider("judge_llm")`
- model：`settings.resolve_component_model("judge_llm", default_logical_model="fast_model")`
- 输出必须解析成 `SubjectiveJudgeEvaluation`
- 分数 clamp 到 `[0, 20]`
- 指定异常类型重试，不使用裸 `except: pass`

### 4.3 本地评估器

文件：

`python-agent/src/ai_modules/llms/local_subjective_evaluator.py`

当前本地实现：

- 使用 `llama_cpp.Llama` 加载 GGUF。
- 默认 Docker 路径：`/app/models/judge_model.gguf`。
- prompt 要求只返回 JSON。
- 输出解析、Pydantic 校验、分数 clamp 到 `[0, 20]`。
- 异常类型：`ValidationError`、`ValueError`、`RuntimeError`、`KeyError`、`TypeError`。

## 五、部署本地 Judge

准备模型：

```bash
mkdir -p models
# 放置 ./models/judge_model.gguf
```

全新部署或维护窗口：

```bash
docker compose -f docker-compose.yml -f docker-compose.local-judge.yml up -d --build
```

相关环境变量：

```env
ENABLE_LOCAL_JUDGE=true
LOCAL_JUDGE_MODEL_PATH=/app/models/judge_model.gguf
LOCAL_JUDGE_MODEL_DIR=./models
LOCAL_JUDGE_WORKERS=1
```

当前联调/演示环境禁止重建容器；如需切换本地 Judge，必须等待维护窗口或采用明确批准的热更新方案。

## 六、训练与导出路线

### 6.1 数据生成

脚本：

`python-agent/scripts/generate_judge_train_data.py`

目标数据格式：

```json
{
  "question": "题目文本",
  "referenceAnswer": "标准答案",
  "knowledgeTags": ["知识点"],
  "learnerAnswer": "学生答案",
  "evaluation": {
    "score": 0,
    "isCorrect": false,
    "reason": "评分理由",
    "feedback": "反馈",
    "confidenceLevel": "LOW"
  }
}
```

### 6.2 SFT / GRPO

脚本：

- `python-agent/scripts/sft_train.py`
- `python-agent/scripts/grpo_train.py`

训练产物不要提交到仓库；提交脚本和说明即可。

### 6.3 GGUF 导出

推荐导出量化：

```text
FP16 -> GGUF Q4_K_M
```

最终模型放到：

```text
./models/judge_model.gguf
```

Compose overlay 挂载到：

```text
/app/models/judge_model.gguf
```

## 七、验证命令

主观题评估相关测试：

```bash
cd python-agent
pytest tests/test_judge_subjective_evaluator.py tests/test_practice_judge_agent.py -q
```

本地模型 smoke：

```bash
cd python-agent
python - <<'PY'
from src.ai_modules.config import get_settings
s = get_settings()
print(s.enable_local_judge, s.local_judge_model_path)
PY
```

完整链路建议：

```bash
cd python-agent
pytest tests/test_practice_judge_agent.py tests/test_supervisor.py -k "judge or practice" -q
```

## 八、风险与后续优化

| 风险 | 处理 |
|---|---|
| 本地 GGUF CPU 推理慢 | 限制 worker=1，控制主观题数量 |
| 模型输出非 JSON | 保留 JSON 提取、Pydantic 校验和有限重试 |
| 远端 provider 不可用 | 明确失败，不启发式伪判 |
| 主观题标准答案过短 | 训练数据中增加评分 rubric |
| 客观题同义答案 | 后续可为题目增加 acceptedAnswers 列表，而不是让 LLM 判客观题 |

当前优先级：保持“客观题确定性、主观题真实 Judge、无伪判分”的边界。
