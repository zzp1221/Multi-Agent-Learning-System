"""Structured models for conversation-level multi-agent plans."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


PlanStatus = Literal["PLANNED", "RUNNING", "SUCCESS", "PARTIAL_FAILED", "FAILED"]
PlanStepStatus = Literal["PENDING", "RUNNING", "SUCCESS", "SKIPPED", "FAILED"]


class ConversationPlanStep(BaseModel):
    """A single executable step produced by the conversation Planner."""

    step_id: str = Field(alias="stepId")
    title: str
    intent: str
    agent_name: str | None = Field(default=None, alias="agentName")
    service_type: str | None = Field(default=None, alias="serviceType")
    depends_on: list[str] = Field(default_factory=list, alias="dependsOn")
    input_keys: list[str] = Field(default_factory=list, alias="inputKeys")
    output_keys: list[str] = Field(default_factory=list, alias="outputKeys")
    requires_approval: bool = Field(default=False, alias="requiresApproval")
    quality_gate: str | None = Field(default=None, alias="qualityGate")
    status: PlanStepStatus = "PENDING"

    model_config = ConfigDict(populate_by_name=True)

    @model_validator(mode="after")
    def require_agent_or_service(self) -> "ConversationPlanStep":
        if not self.agent_name and not self.service_type:
            raise ValueError("plan step must include agentName or serviceType")
        return self


class ConversationPlan(BaseModel):
    """A real LLM-generated plan used to drive multi-agent execution."""

    plan_id: str = Field(alias="planId")
    goal: str
    service_type: str = Field(alias="serviceType")
    steps: list[ConversationPlanStep]
    created_by: Literal["llm_planner"] = Field(default="llm_planner", alias="createdBy")
    status: PlanStatus = "PLANNED"
    provider: str
    model: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(populate_by_name=True)

    @model_validator(mode="after")
    def require_steps(self) -> "ConversationPlan":
        if not self.steps:
            raise ValueError("conversation plan must contain at least one step")
        if not self.provider.strip() or not self.model.strip():
            raise ValueError("conversation plan must include provider and model")
        return self
