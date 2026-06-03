"""结构化资源生成的提示词构建器。"""

from __future__ import annotations

from typing import Any


def build_document_system_prompt() -> str:
    """返回结构化教学内容生成的系统提示词。"""

    return (
        "你是一个严谨的教学内容生成助手。"
        "你必须基于提供的课程上下文、学生画像和检索来源生成结构化中文教学文档。"
        "优先保证正确性、条理性和教学可执行性，不要编造未给出的事实来源。"
        "只输出最终 JSON，不要输出分析过程、解释文字、markdown 代码块或额外字段。"
        "输出必须是 JSON，对象结构为 "
        '{"sections":[{"title":"...","body":"...","tips":["..."],"citations":["..."]}]}.'
        "每个 body 必须是可直接渲染的 Markdown 正文，至少包含概念解释、原理说明、一个贴近课程场景的示例、"
        "以及和学生薄弱点相关的提醒；严禁只写概述句。"
        "正文不要展示课程、章节、学生水平、学习风格、参考来源、证据说明等生成元信息。"
    )


def build_document_user_prompt(
    *,
    title: str,
    topic: str,
    snapshot: dict[str, Any],
    section_plans: list[dict[str, Any]],
    sources: list[dict[str, Any]],
) -> str:
    """构建结构化文档生成请求的用户提示词。"""

    source_lines = [
        (
            f"- 标题: {source.get('title', '未知来源')}; "
            f"渠道: {source.get('channel', 'unknown')}; "
            f"证据: {source.get('evidence', '无')}; "
            f"片段: {source.get('snippet', '无正文片段')}"
        )
        for source in sources[:5]
    ] or ["- 暂无稳定来源，请谨慎生成并保持表述保守。"]

    section_lines = [
        (
            f"- 标题: {plan.get('title', '')}; "
            f"目标: {plan.get('objective', '')}; "
            f"可用来源: {', '.join(plan.get('sourceTitles', [])) or '暂无'}"
        )
        for plan in section_plans
    ]

    return "\n".join(
        [
            f"文档标题: {title}",
            f"核心主题: {topic}",
            "",
            "学生与课程上下文:",
            f"- 课程: {snapshot.get('current_course', '未指定课程')}",
            f"- 章节: {snapshot.get('current_chapter', '未指定章节')}",
            f"- 学生水平: {snapshot.get('student_level', 'UNKNOWN')}",
            f"- 学习风格: {snapshot.get('preferred_style', 'step_by_step')}",
            f"- 薄弱点: {', '.join(snapshot.get('knowledge_gaps', [])) or '暂无'}",
            f"- 偏好资源: {', '.join(snapshot.get('preferred_resource_types', [])) or 'DOCUMENT'}",
            f"- 当前目标: {snapshot.get('learning_goal', '') or '巩固当前主题'}",
            "",
            "请按以下大纲生成章节正文:",
            *section_lines,
            "",
            "可引用来源:",
            *source_lines,
            "",
            "生成要求:",
            "- 每个 section 都要写出完整正文，不能只重复标题。",
            "- 每个 section 的正文至少 220 字，必须包含“这是什么/为什么重要/怎么用/常见误区”中的至少 3 项。",
            "- 至少 1 个 section 要给出带步骤的最小案例或思考题。",
            "- tips 需要是学生可执行的学习建议。",
            "- citations 必须优先引用给定来源标题。",
            "- body 和 tips 中不要写课程、章节、学生水平、学习风格、参考来源、证据说明等元信息。",
            "- 若来源不足，表述保持保守，避免编造具体事实。",
        ]
    )


def build_reading_system_prompt() -> str:
    return (
        "你是一个严谨的教学阅读材料生成助手。"
        "你必须基于课程上下文、学生画像和检索来源生成中文延伸阅读材料。"
        "只输出最终 JSON，不要输出分析过程、解释文字、markdown 代码块或额外字段。"
        "输出必须是 JSON，对象结构为 "
        '{"title":"...","summary":"...","body":"..."}。'
        "其中 body 必须是完整 Markdown 文档，且必须按顺序包含以下二级标题："
        "## 阅读目标、## 核心概念拆解、## 典型场景与示例、## 易错点与纠偏、## 小结与下一步练习。"
        "每个小节都要写实质内容，不能只写一句话。"
    )


def build_reading_user_prompt(
    *,
    title: str,
    topic: str,
    snapshot: dict[str, Any],
    sources: list[dict[str, Any]],
) -> str:
    source_lines = [
        (
            f"- 标题: {source.get('title', '未知来源')}; "
            f"渠道: {source.get('channel', 'unknown')}; "
            f"证据: {source.get('evidence', '无')}; "
            f"片段: {source.get('snippet', '无正文片段')}"
        )
        for source in sources[:5]
    ] or ["- 暂无稳定来源，请保持表述保守。"]
    return "\n".join(
        [
            f"材料标题: {title}",
            f"主题: {topic}",
            f"课程: {snapshot.get('current_course', '未指定课程')}",
            f"学生水平: {snapshot.get('student_level', 'UNKNOWN')}",
            f"学习风格: {snapshot.get('preferred_style', 'step_by_step')}",
            f"薄弱点: {', '.join(snapshot.get('knowledge_gaps', [])) or '暂无'}",
            f"当前目标: {snapshot.get('learning_goal', '') or '巩固当前主题'}",
            "",
            "可引用来源:",
            *source_lines,
            "",
            "生成要求:",
            "- body 需要是完整的中文阅读材料，不是列表拼接。",
            "- “核心概念拆解”必须拆解至少 3 个关键概念，并解释彼此关系。",
            "- “典型场景与示例”必须给出至少 1 个结合课程的具体案例或伪代码片段。",
            "- “易错点与纠偏”必须给出至少 3 个常见误区，并写清纠偏方法。",
            "- “小结与下一步练习”必须给出 2-3 条可立即执行的练习建议。",
            "- 若来源不足，保持保守，不要编造事实。",
        ]
    )


def build_slides_system_prompt() -> str:
    return (
        "你是一个教学 PPT 大纲生成助手。"
        "请生成适合授课的幻灯片大纲。"
        "只输出最终 JSON，不要输出分析过程、解释文字、markdown 代码块或额外字段。"
        "输出必须是 JSON，对象结构为 "
        '{"title":"...","summary":"...","slides":[{"title":"...","bullets":["..."],"speakerNotes":"..."}]}.'
    )


def build_slides_user_prompt(
    *,
    title: str,
    topic: str,
    snapshot: dict[str, Any],
    sources: list[dict[str, Any]],
) -> str:
    source_lines = [
        f"- {source.get('title', '未知来源')}: {source.get('evidence', '无')}"
        for source in sources[:5]
    ] or ["- 暂无稳定来源，请保持概括性表达。"]
    return "\n".join(
        [
            f"PPT标题: {title}",
            f"主题: {topic}",
            f"课程: {snapshot.get('current_course', '未指定课程')}",
            f"章节: {snapshot.get('current_chapter', '未指定章节')}",
            f"学生水平: {snapshot.get('student_level', 'UNKNOWN')}",
            f"偏好资源: {', '.join(snapshot.get('preferred_resource_types', [])) or 'DOCUMENT'}",
            "",
            "可引用来源:",
            *source_lines,
            "",
            "生成要求:",
            "- 生成 5-7 页幻灯片。",
            "- 每页 bullets 必须可直接用于展示。",
            "- speakerNotes 要说明这一页如何讲解。",
        ]
    )


def build_mindmap_system_prompt() -> str:
    return (
        "你是一个教学思维导图生成助手。"
        "请直接输出 Mermaid mindmap 源码。"
        "不要输出```代码块围栏、解释文字、分析过程或任何额外说明。"
        "输出格式示例：\n"
        "mindmap\n"
        "  root((中心主题))\n"
        "    分支一\n"
        "      子要点A\n"
        "      子要点B\n"
        "    分支二\n"
        "      子要点C\n"
        "输出必须以 mindmap 开头。"
    )


def build_mindmap_user_prompt(
    *,
    title: str,
    topic: str,
    snapshot: dict[str, Any],
    sources: list[dict[str, Any]],
) -> str:
    source_lines = [
        f"- {source.get('title', '未知来源')}: {source.get('evidence', '无')}"
        for source in sources[:5]
    ] or ["- 暂无稳定来源，请保持结构化概括。"]
    return "\n".join(
        [
            f"导图标题: {title}",
            f"主题: {topic}",
            f"课程: {snapshot.get('current_course', '未指定课程')}",
            f"薄弱点: {', '.join(snapshot.get('knowledge_gaps', [])) or '暂无'}",
            f"讲解风格: {snapshot.get('preferred_style', 'step_by_step')}",
            "",
            "可引用来源:",
            *source_lines,
            "",
            "生成要求:",
            "- 根节点下至少 3 个一级分支。",
            "- 每个一级分支尽量有 2-4 个二级分支。",
            "- 结构要体现概念、原理、误区、练习或迁移。",
            "- 用 mermaid mindmap 格式输出，根节点用 ((标题)) 双括号。",
        ]
    )


def build_code_system_prompt() -> str:
    return (
        "你是一个教学代码案例生成助手。"
        "你必须生成可以直接在前端展示、适合教学讲解的示例代码。"
        "只输出最终 JSON，不要输出分析过程、解释文字、markdown 代码块或额外字段。"
        "输出必须是 JSON，对象结构为 "
        '{"title":"...","summary":"...","language":"...","code":"...","explanation":"..."}。'
        "code 必须带有足够详细的中文注释，解释关键变量、执行步骤、边界情况和易错点。"
    )


def build_code_user_prompt(
    *,
    title: str,
    topic: str,
    snapshot: dict[str, Any],
    sources: list[dict[str, Any]],
) -> str:
    source_lines = [
        (
            f"- {source.get('title', '未知来源')}: "
            f"{source.get('evidence', '无')} | 片段: {source.get('snippet', '无正文片段')}"
        )
        for source in sources[:5]
    ] or ["- 暂无稳定来源，请生成保守的演示性代码。"]
    return "\n".join(
        [
            f"代码案例标题: {title}",
            f"主题: {topic}",
            f"课程: {snapshot.get('current_course', '未指定课程')}",
            f"学生水平: {snapshot.get('student_level', 'UNKNOWN')}",
            f"薄弱点: {', '.join(snapshot.get('knowledge_gaps', [])) or '暂无'}",
            "",
            "可引用来源:",
            *source_lines,
            "",
            "生成要求:",
            "- language 必须和当前课程上下文一致；如课程是 Java 程序设计，则输出 Java。",
            "- code 必须是完整、可运行或接近可运行的教学示例。",
            "- 关键逻辑前都要写中文注释，说明目的、原理、易错点和与知识点的对应关系。",
            "- explanation 需要解释代码如何对应当前知识点，并指出应该先看哪几段注释。",
            "- 不要依赖复杂第三方库。",
        ]
    )


def build_video_script_system_prompt() -> str:
    return (
        "你是一个教学视频脚本生成助手。"
        "你必须基于课程上下文、学生画像和检索来源生成适合数字人讲解的中文脚本。"
        "只输出最终 JSON，不要输出分析过程、解释文字、markdown 代码块或额外字段。"
        "输出必须是 JSON，对象结构为 "
        '{"title":"...","totalDuration":60,"segments":[{"id":1,"type":"intro","text":"...","duration":12,"visualHint":"...","codeSnippet":"..."}],"fullText":"...","videoStyle":"talking_head"}。'
        "segments 至少 4 段，必须覆盖导入、核心概念、案例讲解、误区纠偏、总结练习中的至少 4 类。"
        "fullText 必须是可直接朗读的完整中文讲稿，不能只是提纲，也不能出现“结合上下文”“根据检索证据”这种空话。"
    )


def build_video_script_user_prompt(
    *,
    title: str,
    topic: str,
    snapshot: dict[str, Any],
    sources: list[dict[str, Any]],
    duration_seconds: int,
    style: str,
) -> str:
    source_lines = [
        (
            f"- 标题: {source.get('title', '未知来源')}; "
            f"渠道: {source.get('channel', 'unknown')}; "
            f"证据: {source.get('evidence', '无')}; "
            f"片段: {source.get('snippet', '无正文片段')}"
        )
        for source in sources[:5]
    ] or ["- 暂无稳定来源，请保持保守，不要编造具体事实。"]

    return "\n".join(
        [
            f"脚本标题: {title}",
            f"主题: {topic}",
            f"目标时长(秒): {duration_seconds}",
            f"视频风格: {style}",
            "",
            "学生与课程上下文:",
            f"- 课程: {snapshot.get('current_course', '未指定课程')}",
            f"- 章节: {snapshot.get('current_chapter', '未指定章节')}",
            f"- 学生水平: {snapshot.get('student_level', 'UNKNOWN')}",
            f"- 学习风格: {snapshot.get('preferred_style', 'step_by_step')}",
            f"- 薄弱点: {', '.join(snapshot.get('knowledge_gaps', [])) or '暂无'}",
            f"- 当前目标: {snapshot.get('learning_goal', '') or '巩固当前主题'}",
            "",
            "可引用来源:",
            *source_lines,
            "",
            "生成要求:",
            "- 讲稿要像老师面对学生讲解，语言自然、完整、具体。",
            "- 必须把主题讲明白，至少覆盖：概念定义、原理解释、一个具体案例、常见误区、下一步练习。",
            "- fullText 至少 260 字，不能只写提纲句。",
            "- segments 的 text 应该和 fullText 对齐，便于后续分段展示。",
            "- visualHint 使用简洁英文短语，例如 show_title_card / show_case_demo / show_mistake_warning。",
            "- 如需案例，可给简短伪代码或步骤，不要编造未提供的外部事实。",
        ]
    )
