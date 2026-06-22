"""批评审查和安全审查工作流的提示词构建器。"""

from __future__ import annotations

from src.ai_modules.runtime import SnapshotBuilder, SystemSnapshot


def build_critic_system_prompt(snapshot: SystemSnapshot) -> str:
    context = SnapshotBuilder.render_prompt_context(snapshot)
    return "\n".join(
        [
            "你是 Critic Agent，负责复核教学资源内容质量。",
            "输出必须是 JSON，字段为 verdict、factConsistency、difficultyMatch、sourceCoverage、issues、suggestions、summaryText。",
            "请重点核对事实一致性、难度匹配度、来源覆盖度，并给出简洁可执行建议。",
            "Use verdict only from: PASS, PASS_WITH_ISSUES, NEEDS_MINOR_REVISION, REVISE, REJECT. PASS means publishable; PASS_WITH_ISSUES and NEEDS_MINOR_REVISION mean publishable with improvement suggestions; REVISE and REJECT mean do not publish.",
            context,
        ]
    )


def build_safety_system_prompt(snapshot: SystemSnapshot) -> str:
    context = SnapshotBuilder.render_prompt_context(snapshot)
    return "\n".join(
        [
            "你是 Safety Agent，负责识别教学内容安全与合规风险。",
            "输出必须是 JSON，字段为 allowed、riskLevel、categories、riskTags、blockedReason、suggestions、summaryText。",
            "请重点识别越界内容、学术违规、作弊建议、危险操作和不适合学生当前水平的风险。",
            context,
        ]
    )
