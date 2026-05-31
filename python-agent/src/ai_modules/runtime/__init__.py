"""智学引擎智能体循环的运行时原语。"""

from src.ai_modules.runtime.agent_core_loop import (
    AgentCoreLoop,
    AgentLoopResult,
    AssistantTurn,
    MaxIterationsExceededError,
    ToolCall,
    ToolExecutionResult,
)
from src.ai_modules.runtime.conversation_compactor import (
    CompactionResult,
    ConversationCompactor,
    StructuredConversationSummary,
)
from src.ai_modules.runtime.context_snapshot import SnapshotBuilder, SystemSnapshot
from src.ai_modules.runtime.hook_chain import HookChain, HookResult
from src.ai_modules.runtime.hooks import KnowledgeGuardHook
from src.ai_modules.runtime.permission_policy import (
    PermissionDecision,
    PermissionLevel,
    PermissionPolicy,
    PermissionRule,
)
from src.ai_modules.runtime.recovery_engine import (
    LLMRateLimitError,
    RecoveryEngine,
    RecoveryFailureType,
)
from src.ai_modules.runtime.ttl_cache import InMemoryTTLCache, stable_cache_key
from src.ai_modules.runtime.topic_canonicalizer import (
    CanonicalTopic,
    canonicalize_topic,
    canonicalize_topics,
)
from src.ai_modules.runtime.tool_registry import ToolDefinition, ToolRegistry

__all__ = [
    "AgentCoreLoop",
    "AgentLoopResult",
    "AssistantTurn",
    "CompactionResult",
    "ConversationCompactor",
    "StructuredConversationSummary",
    "HookChain",
    "HookResult",
    "KnowledgeGuardHook",
    "MaxIterationsExceededError",
    "SnapshotBuilder",
    "SystemSnapshot",
    "PermissionDecision",
    "PermissionLevel",
    "PermissionPolicy",
    "PermissionRule",
    "LLMRateLimitError",
    "RecoveryEngine",
    "RecoveryFailureType",
    "ToolCall",
    "ToolDefinition",
    "ToolExecutionResult",
    "ToolRegistry",
    "InMemoryTTLCache",
    "stable_cache_key",
    "CanonicalTopic",
    "canonicalize_topic",
    "canonicalize_topics",
]
