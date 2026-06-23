"""AgentCoreLoop 的工具注册中心。"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Awaitable, Callable


ToolHandler = Callable[[dict[str, Any]], Any] | Callable[[dict[str, Any]], Awaitable[Any]]


@dataclass(slots=True)
class ToolDefinition:
    """智能体运行时使用的工具元数据。"""

    name: str
    handler: ToolHandler
    permission_level: int
    description: str
    parameters: dict[str, Any] | None = None

    def as_llm_tool(self) -> dict[str, Any]:
        """返回用于模型调用的最小函数工具 schema。"""

        schema = self.parameters or {
            "type": "object",
            "additionalProperties": True,
        }
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": schema,
            },
        }


class ToolRegistry:
    """用于工具查找和执行的运行时注册中心。"""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(
        self,
        name: str,
        fn: ToolHandler,
        permission_level: int,
        description: str,
        parameters: dict[str, Any] | None = None,
    ) -> None:
        if name in self._tools:
            raise ValueError(f"Tool already registered: {name}")

        self._tools[name] = ToolDefinition(
            name=name,
            handler=fn,
            permission_level=permission_level,
            description=description,
            parameters=parameters,
        )

    def get(self, name: str) -> ToolDefinition:
        try:
            return self._tools[name]
        except KeyError as exc:  # pragma: no cover - 防御性分支
            raise KeyError(f"Unknown tool: {name}") from exc

    def list_for_agent(self, agent_level: int) -> list[ToolDefinition]:
        return [
            definition
            for definition in self._tools.values()
            if definition.permission_level <= agent_level
        ]

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def list_tool_schemas(self, agent_level: int) -> list[dict[str, Any]]:
        return [
            definition.as_llm_tool()
            for definition in self.list_for_agent(agent_level)
        ]

    async def execute(self, name: str, tool_input: dict[str, Any]) -> Any:
        definition = self.get(name)
        result = definition.handler(tool_input)
        if inspect.isawaitable(result):
            return await result
        return result
