"""Helpers for enforcing real LLM-generated artifact provenance."""

from __future__ import annotations

from typing import Any

from src.ai_modules.config import get_settings
from src.ai_modules.llms.user_runtime_config import current_user_llm_config


class ProvenanceError(RuntimeError):
    """Raised when a generated artifact does not prove its LLM origin."""


def evidence_ids_from_params(params: dict[str, Any]) -> list[str]:
    """Extract stable evidence identifiers from retrieval results."""

    existing = params.get("evidenceIds")
    if isinstance(existing, list):
        normalized = [str(item).strip() for item in existing if str(item).strip()]
        if normalized:
            return normalized

    retrieval_result = params.get("retrievalResult", {})
    documents = retrieval_result.get("documents", []) if isinstance(retrieval_result, dict) else []
    evidence_ids: list[str] = []
    for index, item in enumerate(documents, start=1):
        if not isinstance(item, dict):
            continue
        raw_value = (
            item.get("id")
            or item.get("slug")
            or item.get("url")
            or item.get("title")
            or f"source-{index}"
        )
        value = str(raw_value).strip()
        if value:
            evidence_ids.append(value)
    return evidence_ids


def external_urls_from_params(params: dict[str, Any]) -> list[str]:
    """Extract adopted external URLs from the shared evidence contract."""

    existing = params.get("externalUrls")
    if isinstance(existing, list):
        return [
            str(item).strip()
            for item in existing
            if str(item).strip().startswith(("http://", "https://"))
        ]
    adopted = params.get("adoptedExternalSources")
    urls: list[str] = []
    if isinstance(adopted, list):
        for item in adopted:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            if url.startswith(("http://", "https://")):
                urls.append(url)
    return urls


def generator_metadata(generator: Any) -> tuple[str, str]:
    """Resolve provider/model metadata from the configured LLM generator object."""

    provider = str(getattr(generator, "provider_name", "") or "").strip()
    model = str(getattr(generator, "model_name", "") or "").strip()
    client = getattr(generator, "client", None)
    if not provider and client is not None:
        provider = str(getattr(client, "provider_name", "") or "").strip()
    if not model and client is not None:
        model = str(getattr(client, "model_name", "") or "").strip()
    nested_generator = getattr(generator, "generator", None)
    if (not provider or not model) and nested_generator is not None:
        nested_provider, nested_model = generator_metadata(nested_generator)
        provider = provider or nested_provider
        model = model or nested_model
    return provider, model


def build_llm_provenance(
    *,
    agent_name: str,
    generator: Any,
    params: dict[str, Any],
    from_cache: bool = False,
) -> dict[str, Any]:
    """Build the required provenance payload for a real LLM artifact."""

    provider, model = generator_metadata(generator)
    user_config = current_user_llm_config()
    if user_config is not None:
        override = user_config.component_override("generation_llm")
        runtime_provider = user_config.runtime_provider_name("")
        provider = (override.provider if override and override.provider else runtime_provider) or provider
        if provider:
            provider_config = user_config.routing_config(get_settings().build_default_model_routing_config()).providers.get(provider)
            if provider_config is not None:
                logical_model = override.model if override and override.model else "main_chat_model"
                runtime_model = provider_config.models.get(logical_model, logical_model)
                if runtime_model:
                    model = runtime_model
    return {
        "generatedBy": "LLM",
        "contentOrigin": "LLM",
        "provider": provider,
        "model": model,
        "agentName": agent_name,
        "evidenceIds": evidence_ids_from_params(params),
        "externalUrls": external_urls_from_params(params),
        "fallback": False,
        "fromCache": from_cache,
    }


def validate_llm_provenance(payload: Any, *, artifact_label: str) -> None:
    """Reject artifacts that would look generated without proving LLM origin."""

    data = payload.model_dump(by_alias=True) if hasattr(payload, "model_dump") else dict(payload or {})
    generated_by = str(data.get("generatedBy") or "").strip().upper()
    content_origin = str(data.get("contentOrigin") or "").strip().upper()
    provider = str(data.get("provider") or "").strip()
    model = str(data.get("model") or "").strip()
    agent_name = str(data.get("agentName") or "").strip()
    evidence_ids = data.get("evidenceIds")
    fallback = data.get("fallback")
    from_cache = data.get("fromCache")
    if generated_by != "LLM":
        raise ProvenanceError(f"{artifact_label} missing generatedBy=LLM")
    if content_origin != "LLM":
        raise ProvenanceError(f"{artifact_label} missing contentOrigin=LLM")
    if not provider:
        raise ProvenanceError(f"{artifact_label} missing LLM provider")
    if not model:
        raise ProvenanceError(f"{artifact_label} missing LLM model")
    if not agent_name:
        raise ProvenanceError(f"{artifact_label} missing agentName")
    if not isinstance(evidence_ids, list):
        raise ProvenanceError(f"{artifact_label} missing evidenceIds")
    if fallback is not False:
        raise ProvenanceError(f"{artifact_label} must declare fallback=false")
    if not isinstance(from_cache, bool):
        raise ProvenanceError(f"{artifact_label} missing fromCache flag")
