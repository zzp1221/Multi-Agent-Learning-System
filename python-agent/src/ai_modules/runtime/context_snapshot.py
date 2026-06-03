"""智能体提示词的上下文快照模型与构建器。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class SystemSnapshot:
    """注入每个智能体提示词的聚合运行时上下文。"""

    current_course: str
    current_chapter: str
    course_progress: float
    student_name: str
    student_level: str
    knowledge_gaps: list[str] = field(default_factory=list)
    preferred_style: str = "step_by_step"
    recent_mistakes: list[str] = field(default_factory=list)
    session_id: str = ""
    conversation_length: int = 0
    total_tokens_used: int = 0
    wiki_pages_count: int = 0
    last_index_update: str = "unknown"
    recent_activities: list[str] = field(default_factory=list)


class SnapshotBuilder:
    """根据当前请求上下文和占位默认值构建快照。"""

    async def build(
        self,
        *,
        user_id: str | None,
        task_id: str,
        conversation_id: str | None,
        params: dict,
    ) -> SystemSnapshot:
        profile = params.get("profile") if isinstance(params.get("profile"), dict) else {}
        profile_analysis = params.get("profileAnalysis") if isinstance(params.get("profileAnalysis"), dict) else {}
        if profile_analysis:
            profile = self._merge_non_empty(profile, profile_analysis)
            if profile_analysis.get("weakPoints") and not profile_analysis.get("knowledgeGaps"):
                profile["knowledgeGaps"] = profile_analysis["weakPoints"]
            if profile_analysis.get("learningPreference") and not profile_analysis.get("preferredStyle"):
                profile["preferredStyle"] = profile_analysis["learningPreference"]
        learning_context = params.get("learningContext", {})
        weak_points = list(
            profile.get("knowledgeGaps")
            or profile.get("weakPoints")
            or [
                item.get("topic", "")
                for item in profile.get("weakPointDetails", [])
                if isinstance(item, dict)
            ]
        )
        preferred_style = (
            profile.get("preferredStyle")
            or profile.get("learningPreference")
            or profile.get("explanationPreference")
            or "step_by_step"
        )
        student_level = (
            profile.get("studentLevel")
            or profile.get("knowledgeFoundation")
            or profile.get("knowledgeBase")
            or "UNKNOWN"
        )
        recent_mistakes = list(profile.get("recentMistakes", []))
        if not recent_mistakes:
            for item in profile.get("errorPatterns", []):
                if isinstance(item, dict):
                    recent_mistakes.extend(item.get("examples", []))

        return SystemSnapshot(
            current_course=learning_context.get("course", "未指定课程"),
            current_chapter=learning_context.get("chapter", "未指定章节"),
            course_progress=float(learning_context.get("progress", 0.0)),
            student_name=profile.get("studentName", user_id or "匿名学生"),
            student_level=student_level,
            knowledge_gaps=[item for item in weak_points if str(item).strip()],
            preferred_style=str(preferred_style),
            recent_mistakes=[item for item in recent_mistakes if str(item).strip()],
            session_id=conversation_id or task_id,
            conversation_length=int(params.get("conversationLength", 0)),
            total_tokens_used=int(params.get("totalTokensUsed", 0)),
            wiki_pages_count=int(params.get("wikiPagesCount", 0)),
            last_index_update=str(params.get("lastIndexUpdate", "unknown")),
            recent_activities=list(params.get("recentActivities", [])),
        )

    @staticmethod
    def render_prompt_context(snapshot: SystemSnapshot) -> str:
        """将快照渲染为系统提示词片段。"""

        return "\n".join(
            [
                "## 当前上下文",
                f"- 课程: {snapshot.current_course}",
                f"- 章节: {snapshot.current_chapter}",
                f"- 进度: {snapshot.course_progress}",
                f"- 学生: {snapshot.student_name}",
                f"- 水平: {snapshot.student_level}",
                f"- 薄弱点: {', '.join(snapshot.knowledge_gaps) or '暂无'}",
                f"- 偏好: {snapshot.preferred_style}",
                f"- 最近错误: {', '.join(snapshot.recent_mistakes) or '暂无'}",
                f"- 会话ID: {snapshot.session_id}",
                f"- 对话长度: {snapshot.conversation_length}",
                f"- 已用Token: {snapshot.total_tokens_used}",
                f"- Wiki页数: {snapshot.wiki_pages_count}",
                f"- 最近索引时间: {snapshot.last_index_update}",
                f"- 最近活动: {', '.join(snapshot.recent_activities) or '暂无'}",
            ]
        )

    def _merge_non_empty(self, base: dict, incoming: dict) -> dict:
        merged = dict(base)
        for key, value in incoming.items():
            if value is None or value == "" or value == [] or value == {}:
                continue
            merged[key] = value
        return merged
