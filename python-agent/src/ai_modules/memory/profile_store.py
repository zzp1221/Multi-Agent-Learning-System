"""学习者画像快照的持久化层。"""

from __future__ import annotations

import asyncio
import json
import math
import os
from collections.abc import Callable
from datetime import datetime, timezone
from uuid import UUID
from typing import Any, Protocol

from src.ai_modules.config import get_settings
from src.ai_modules.models import LearnerProfileDimensions, LearnerProfileSnapshot
from src.ai_modules.models.profile import ErrorPattern, WeakPointDetail
from src.ai_modules.memory.profile_feature_registry import (
    canonical_match_dimensions,
    exclusive_dimensions,
    get_feature_dimension_spec,
    resolved_by_skill_mastery_dimensions,
)
from src.ai_modules.runtime.topic_canonicalizer import canonicalize_topic


class ProfileStore(Protocol):
    """学习者画像的持久化契约。"""

    async def read_profile(self, user_id: str) -> LearnerProfileSnapshot | None: ...

    async def update_profile(
        self,
        *,
        user_id: str,
        dimensions: LearnerProfileDimensions,
        source_session_id: str | None = None,
    ) -> LearnerProfileSnapshot: ...


def _adapt_json_payload(payload: Any) -> Any:
    try:
        from psycopg2.extras import Json

        return Json(payload)
    except ModuleNotFoundError:
        return json.dumps(payload, ensure_ascii=False)


class InMemoryProfileStore:
    """用于测试和本地回退的内存存储。"""

    def __init__(self) -> None:
        self.snapshots: dict[str, LearnerProfileSnapshot] = {}

    async def read_profile(self, user_id: str) -> LearnerProfileSnapshot | None:
        return self.snapshots.get(user_id)

    async def update_profile(
        self,
        *,
        user_id: str,
        dimensions: LearnerProfileDimensions,
        source_session_id: str | None = None,
    ) -> LearnerProfileSnapshot:
        del source_session_id
        current = self.snapshots.get(user_id)
        version = 1 if current is None else current.version + 1
        snapshot = LearnerProfileSnapshot(
            userId=user_id,
            version=version,
            profile=dimensions,
        )
        self.snapshots[user_id] = snapshot
        return snapshot


class PostgresProfileStore:
    """基于 PostgreSQL 的画像存储，支持快照表和当前表。"""

    def __init__(
        self,
        db_config: dict[str, Any] | None = None,
        connect_fn: Callable[..., Any] | None = None,
        embedding_fn: Callable[[str], list[float] | None] | None = None,
    ) -> None:
        settings = get_settings()
        self.settings = settings
        self.db_config = db_config or {
            "host": settings.postgres_host,
            "port": settings.postgres_port,
            "dbname": settings.postgres_db,
            "user": settings.postgres_user,
            "password": settings.postgres_password,
        }
        self._connect_fn = connect_fn
        self._embedding_fn = embedding_fn
        self._table_ensured: bool = False

    def _connect(self) -> Any:
        if self._connect_fn is not None:
            return self._connect_fn(**self.db_config)

        import psycopg2

        return psycopg2.connect(**self.db_config)

    def _read_profile_sync(self, user_id: str) -> LearnerProfileSnapshot | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT profile_json, summary_text
                    FROM app.user_profile_current
                    WHERE user_id = %s::uuid
                    """,
                    (user_id,),
                )
                row = cur.fetchone()
                if row is None:
                    return None
                profile_json, summary_text = row
                dimensions = LearnerProfileDimensions.model_validate(
                    {
                        **profile_json,
                        "summaryText": summary_text or profile_json.get("summaryText", ""),
                    }
                )
                cur.execute(
                    """
                    SELECT COALESCE(MAX(version), 0)
                    FROM app.user_profile_snapshot
                    WHERE user_id = %s::uuid
                    """,
                    (user_id,),
                )
                version = int(cur.fetchone()[0])
                return LearnerProfileSnapshot(
                    userId=user_id,
                    version=max(version, 1),
                    profile=dimensions,
                )

    def _ensure_feature_table(self, cur: Any) -> None:
        if self._table_ensured:
            return
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS app.learner_feature (
                id BIGSERIAL PRIMARY KEY,
                user_id UUID NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
                dimension TEXT NOT NULL,
                feature_key TEXT NOT NULL,
                canonical_key TEXT,
                feature_value JSONB NOT NULL DEFAULT '{}'::jsonb,
                aliases JSONB NOT NULL DEFAULT '[]'::jsonb,
                confidence REAL NOT NULL DEFAULT 0.5 CHECK (confidence >= 0 AND confidence <= 1),
                source_type TEXT NOT NULL DEFAULT 'CONVERSATION',
                source_ref JSONB,
                reasoning TEXT NOT NULL DEFAULT '',
                evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
                verification_count INT NOT NULL DEFAULT 1,
                decay_enabled BOOLEAN NOT NULL DEFAULT TRUE,
                stability_period_days INT NOT NULL DEFAULT 30,
                decay_rate REAL NOT NULL DEFAULT 0.05,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'RESOLVED', 'REGRESSED', 'ARCHIVED')),
                resolved_at TIMESTAMPTZ,
                resolved_reason TEXT NOT NULL DEFAULT '',
                resolved_by JSONB NOT NULL DEFAULT '{}'::jsonb,
                last_observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                inferred BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (user_id, dimension, feature_key)
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_learner_feature_user_dim
            ON app.learner_feature(user_id, dimension, is_active, updated_at DESC)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_learner_feature_confidence
            ON app.learner_feature(user_id, confidence DESC, updated_at DESC)
            """
        )
        cur.execute(
            """
            ALTER TABLE app.learner_feature
            ADD COLUMN IF NOT EXISTS canonical_key TEXT
            """
        )
        cur.execute(
            """
            ALTER TABLE app.learner_feature
            ADD COLUMN IF NOT EXISTS aliases JSONB NOT NULL DEFAULT '[]'::jsonb
            """
        )
        cur.execute(
            """
            ALTER TABLE app.learner_feature
            ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'ACTIVE'
            """
        )
        cur.execute(
            """
            ALTER TABLE app.learner_feature
            ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ
            """
        )
        cur.execute(
            """
            ALTER TABLE app.learner_feature
            ADD COLUMN IF NOT EXISTS resolved_reason TEXT NOT NULL DEFAULT ''
            """
        )
        cur.execute(
            """
            ALTER TABLE app.learner_feature
            ADD COLUMN IF NOT EXISTS resolved_by JSONB NOT NULL DEFAULT '{}'::jsonb
            """
        )
        cur.execute(
            """
            ALTER TABLE app.learner_feature
            ADD COLUMN IF NOT EXISTS last_observed_at TIMESTAMPTZ NOT NULL DEFAULT now()
            """
        )
        cur.execute(
            """
            UPDATE app.learner_feature
            SET canonical_key = feature_key
            WHERE canonical_key IS NULL OR btrim(canonical_key) = ''
            """
        )
        cur.execute(
            """
            UPDATE app.learner_feature
            SET canonical_key = CASE
                WHEN feature_key IN (U&'\\6B7B\\9501', 'deadlock', 'dead lock')
                    OR feature_key ILIKE '%' || U&'\\4E24\\4E2A\\9501\\4E92\\76F8\\7B49' || '%'
                    OR feature_key ILIKE '%' || U&'\\4E24\\628A\\9501\\4E92\\76F8\\7B49' || '%'
                    THEN 'deadlock'
                WHEN feature_key IN (U&'\\5FAA\\73AF\\7B49\\5F85', U&'\\73AF\\8DEF\\7B49\\5F85')
                    THEN 'deadlock.circular_wait'
                WHEN feature_key IN (U&'\\4E92\\65A5\\6761\\4EF6', U&'\\4E92\\65A5')
                    THEN 'deadlock.mutual_exclusion'
                WHEN feature_key IN (U&'\\5360\\6709\\5E76\\7B49\\5F85', U&'\\6301\\6709\\5E76\\7B49\\5F85')
                    THEN 'deadlock.hold_and_wait'
                WHEN feature_key IN (U&'\\4E0D\\53EF\\62A2\\5360', U&'\\4E0D\\80FD\\62A2\\5360')
                    THEN 'deadlock.no_preemption'
                ELSE canonical_key
            END
            WHERE dimension IN ('weak_points', 'skill_mastery', 'error_patterns')
              AND feature_key IS NOT NULL
            """
        )
        cur.execute(
            """
            UPDATE app.learner_feature
            SET aliases = to_jsonb(ARRAY[feature_key])
            WHERE aliases = '[]'::jsonb AND feature_key IS NOT NULL AND btrim(feature_key) <> ''
            """
        )
        cur.execute(
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1
                    FROM pg_constraint
                    WHERE conname = 'learner_feature_status_check'
                      AND conrelid = 'app.learner_feature'::regclass
                ) THEN
                    ALTER TABLE app.learner_feature
                        ADD CONSTRAINT learner_feature_status_check
                        CHECK (status IN ('ACTIVE', 'RESOLVED', 'REGRESSED', 'ARCHIVED'));
                END IF;
            END $$;
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_learner_feature_canonical_status
            ON app.learner_feature(user_id, dimension, canonical_key, status, is_active, updated_at DESC)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_learner_feature_resolved
            ON app.learner_feature(user_id, status, resolved_at DESC)
            """
        )
        self._table_ensured = True

    def _fetch_active_features(self, cur: Any, user_id: str) -> list[dict[str, Any]]:
        cur.execute(
            """
            SELECT id, dimension, feature_key, canonical_key, feature_value, aliases, confidence,
                   source_type, source_ref, reasoning, evidence, verification_count, decay_enabled,
                   stability_period_days, decay_rate, is_active, status, resolved_at,
                   resolved_reason, resolved_by, last_observed_at, inferred, created_at, updated_at
            FROM app.learner_feature
            WHERE user_id = %s::uuid AND is_active = TRUE
              AND status IN ('ACTIVE', 'REGRESSED')
            ORDER BY confidence DESC, updated_at DESC
            """,
            (user_id,),
        )
        if not hasattr(cur, "fetchall"):
            return []
        rows = cur.fetchall() or []
        description = getattr(cur, "description", None)
        if not description:
            return []
        columns = [desc[0] for desc in description]
        return [dict(zip(columns, row, strict=False)) for row in rows]

    def _apply_decay(self, cur: Any, *, user_id: str) -> None:
        active_features = self._fetch_active_features(cur, user_id)
        now = datetime.now(timezone.utc)
        for feature in active_features:
            if not feature.get("decay_enabled", True):
                continue
            updated_at = feature.get("updated_at")
            if updated_at is None:
                continue
            days_since_update = max(0.0, (now - updated_at).total_seconds() / 86400.0)
            stability_period = int(feature.get("stability_period_days") or 30)
            if days_since_update <= stability_period:
                continue
            decay_days = days_since_update - stability_period
            decay_rate = float(feature.get("decay_rate") or 0.05)
            initial_confidence = float(feature.get("confidence") or 0.5)
            decayed = initial_confidence - (math.log1p(decay_days * decay_rate) * (initial_confidence - 0.3) * 0.3)
            decayed = max(0.3, min(initial_confidence, round(decayed, 4)))
            is_active = decayed >= 0.31
            cur.execute(
                """
                UPDATE app.learner_feature
                SET confidence = %s,
                    is_active = %s,
                    status = CASE WHEN %s THEN status ELSE 'ARCHIVED' END
                WHERE id = %s
                """,
                (decayed, is_active, is_active, feature["id"]),
            )

    def _singleton_dimensions(self) -> set[str]:
        return exclusive_dimensions()

    def _canonical_match_dimensions(self) -> set[str]:
        return canonical_match_dimensions()

    def _extract_features(self, dimensions: LearnerProfileDimensions) -> list[dict[str, Any]]:
        base_confidence = max(0.35, min(0.95, float(dimensions.confidence_score or 0.65)))
        features: list[dict[str, Any]] = [
            self._feature_record(
                dimension="knowledge_foundation",
                feature_key="current",
                feature_value={"level": dimensions.knowledge_foundation},
                confidence=base_confidence,
                source_type=dimensions.source,
                evidence=dimensions.evidence,
            ),
            self._feature_record(
                dimension="professional_background",
                feature_key="current",
                feature_value={"text": dimensions.professional_background},
                confidence=max(0.45, base_confidence - 0.05),
                source_type=dimensions.source,
                evidence=dimensions.evidence,
            ),
            self._feature_record(
                dimension="learning_preference",
                feature_key="overall",
                feature_value={
                    "mode": dimensions.learning_preference,
                    "preferredResourceTypes": dimensions.preferred_resource_types,
                },
                confidence=base_confidence,
                source_type=dimensions.source,
                evidence=dimensions.evidence,
            ),
            self._feature_record(
                dimension="cognitive_style",
                feature_key="overall",
                feature_value={"style": dimensions.cognitive_style},
                confidence=base_confidence,
                source_type=dimensions.source,
                evidence=dimensions.evidence,
            ),
            self._feature_record(
                dimension="learning_pace",
                feature_key="overall",
                feature_value={"pace": dimensions.learning_pace},
                confidence=max(0.4, base_confidence - 0.05),
                source_type=dimensions.source,
                evidence=dimensions.evidence,
            ),
            self._feature_record(
                dimension="confidence_level",
                feature_key="overall",
                feature_value={
                    "level": dimensions.confidence_level,
                    "score": round(base_confidence, 4),
                },
                confidence=base_confidence,
                source_type=dimensions.source,
                evidence=dimensions.evidence,
            ),
            self._feature_record(
                dimension="current_goal",
                feature_key="short_term",
                feature_value=dimensions.current_goal.model_dump(by_alias=True),
                confidence=max(0.5, base_confidence),
                source_type=dimensions.source,
                evidence=dimensions.evidence,
            ),
            self._feature_record(
                dimension="learning_habits",
                feature_key="overall",
                feature_value=dimensions.learning_habits.model_dump(by_alias=True),
                confidence=max(0.4, base_confidence - 0.08),
                source_type=dimensions.source,
                evidence=dimensions.evidence,
            ),
            self._feature_record(
                dimension="explanation_preference",
                feature_key="overall",
                feature_value={"value": dimensions.explanation_preference},
                confidence=max(0.45, base_confidence - 0.04),
                source_type=dimensions.source,
                evidence=dimensions.evidence,
            ),
        ]

        for topic, score in (dimensions.skill_mastery or {}).items():
            normalized_topic = str(topic).strip()
            if not normalized_topic:
                continue
            features.append(
                self._feature_record(
                    dimension="skill_mastery",
                    feature_key=normalized_topic,
                    feature_value={"topic": normalized_topic, "score": max(0.0, min(1.0, float(score)))},
                    confidence=max(0.45, base_confidence - 0.03),
                    source_type=dimensions.source,
                    evidence=dimensions.evidence,
                )
            )

        weak_point_details = dimensions.weak_point_details or [
            WeakPointDetail(topic=topic, severity=0.65)
            for topic in dimensions.weak_points
            if str(topic).strip()
        ]
        for item in weak_point_details:
            features.append(
                self._feature_record(
                    dimension="weak_points",
                    feature_key=item.topic.strip(),
                    feature_value=item.model_dump(by_alias=True),
                    confidence=max(0.55, min(0.95, item.severity * 0.75 + 0.2)),
                    source_type=dimensions.source,
                    evidence=[*dimensions.evidence, item.last_error] if item.last_error else dimensions.evidence,
                )
            )

        for item in dimensions.error_patterns:
            if not item.pattern.strip():
                continue
            features.append(
                self._feature_record(
                    dimension="error_patterns",
                    feature_key=item.pattern.strip(),
                    feature_value=item.model_dump(by_alias=True),
                    confidence=max(0.45, min(0.92, float(item.frequency) * 0.7 + 0.2)),
                    source_type=dimensions.source,
                    evidence=[*dimensions.evidence, *item.examples],
                )
            )

        for resource_type in dimensions.preferred_resource_types:
            normalized_type = str(resource_type).strip().upper()
            if not normalized_type:
                continue
            features.append(
                self._feature_record(
                    dimension="preferred_resource_type",
                    feature_key=normalized_type,
                    feature_value={"resourceType": normalized_type},
                    confidence=max(0.45, base_confidence - 0.04),
                    source_type=dimensions.source,
                    evidence=dimensions.evidence,
                )
            )

        for recommendation in dimensions.inferred_recommendations:
            normalized = str(recommendation).strip()
            if not normalized:
                continue
            features.append(
                self._feature_record(
                    dimension="inferred_recommendation",
                    feature_key=normalized[:80],
                    feature_value={"text": normalized},
                    confidence=max(0.4, base_confidence - 0.12),
                    source_type="INFERRED",
                    evidence=dimensions.evidence,
                    inferred=True,
                )
            )

        return [feature for feature in features if self._feature_has_value(feature)]

    def _feature_record(
        self,
        *,
        dimension: str,
        feature_key: str,
        feature_value: dict[str, Any],
        confidence: float,
        source_type: str,
        evidence: list[str] | None = None,
        stability_period_days: int | None = None,
        decay_rate: float | None = None,
        inferred: bool = False,
    ) -> dict[str, Any]:
        spec = get_feature_dimension_spec(dimension)
        canonical_topic = canonicalize_topic(feature_key)
        return {
            "dimension": dimension,
            "feature_key": feature_key,
            "canonical_key": canonical_topic.canonical_key or feature_key,
            "feature_value": feature_value,
            "aliases": canonical_topic.aliases or [feature_key],
            "confidence": max(0.0, min(1.0, round(float(confidence), 4))),
            "source_type": source_type,
            "source_ref": {},
            "reasoning": "",
            "evidence": list(dict.fromkeys(filter(None, evidence or [])))[:6],
            "verification_count": 1,
            "decay_enabled": True,
            "stability_period_days": stability_period_days if stability_period_days is not None else spec.stability_period_days,
            "decay_rate": decay_rate if decay_rate is not None else spec.decay_rate,
            "is_active": True,
            "status": "ACTIVE",
            "resolved_at": None,
            "resolved_reason": "",
            "resolved_by": {},
            "last_observed_at": datetime.now(timezone.utc),
            "inferred": inferred,
        }

    def _feature_has_value(self, feature: dict[str, Any]) -> bool:
        value = feature["feature_value"]
        if not feature["feature_key"].strip():
            return False
        if isinstance(value, dict):
            return any(
                item not in ("", None, [], {})
                for item in value.values()
            )
        return value not in ("", None, [], {})

    def _merge_feature_value(self, existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
        merged = dict(existing)
        for key, value in incoming.items():
            if value in ("", None, [], {}):
                continue
            if isinstance(value, list):
                merged[key] = list(dict.fromkeys([*(merged.get(key) or []), *value]))
                continue
            merged[key] = value
        return merged

    def _merge_aliases(self, existing: Any, incoming: Any, *, feature_key: str) -> list[str]:
        aliases: list[str] = []
        for value in [existing, incoming]:
            if isinstance(value, list):
                aliases.extend(str(item).strip() for item in value if str(item).strip())
            elif isinstance(value, str) and value.strip():
                aliases.append(value.strip())
        if feature_key.strip():
            aliases.append(feature_key.strip())
        return list(dict.fromkeys(aliases))

    def _deactivate_conflicts(self, cur: Any, *, user_id: str, dimension: str, feature_key: str) -> None:
        if dimension not in self._singleton_dimensions():
            return
        cur.execute(
            """
            UPDATE app.learner_feature
            SET is_active = FALSE,
                status = 'ARCHIVED'
            WHERE user_id = %s::uuid
              AND dimension = %s
              AND feature_key <> %s
              AND is_active = TRUE
            """,
            (user_id, dimension, feature_key),
        )

    def _upsert_features(
        self,
        cur: Any,
        *,
        user_id: str,
        features: list[dict[str, Any]],
        source_session_id: str | None,
    ) -> None:
        source_ref = {"sourceSessionId": source_session_id} if source_session_id else {}
        for feature in features:
            feature["source_ref"] = source_ref
            self._deactivate_conflicts(
                cur,
                user_id=user_id,
                dimension=feature["dimension"],
                feature_key=feature["feature_key"],
            )
            match_by_canonical = (
                feature["dimension"] in self._canonical_match_dimensions()
                and bool(str(feature.get("canonical_key") or "").strip())
            )
            cur.execute(
                """
                SELECT id, feature_value, confidence, verification_count, evidence, aliases, status
                FROM app.learner_feature
                WHERE user_id = %s::uuid
                  AND dimension = %s
                  AND (
                    feature_key = %s
                    OR (%s AND canonical_key = %s)
                  )
                ORDER BY CASE WHEN feature_key = %s THEN 0 ELSE 1 END
                LIMIT 1
                """,
                (
                    user_id,
                    feature["dimension"],
                    feature["feature_key"],
                    match_by_canonical,
                    feature["canonical_key"],
                    feature["feature_key"],
                ),
            )
            existing = cur.fetchone()
            if existing is None:
                cur.execute(
                    """
                    INSERT INTO app.learner_feature(
                        user_id, dimension, feature_key, canonical_key, feature_value, aliases,
                        confidence, source_type, source_ref, reasoning, evidence,
                        verification_count, decay_enabled, stability_period_days, decay_rate,
                        is_active, status, resolved_at, resolved_reason, resolved_by,
                        last_observed_at, inferred
                    )
                    VALUES (%s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        user_id,
                        feature["dimension"],
                        feature["feature_key"],
                        feature["canonical_key"],
                        _adapt_json_payload(feature["feature_value"]),
                        _adapt_json_payload(feature["aliases"]),
                        feature["confidence"],
                        feature["source_type"],
                        _adapt_json_payload(feature["source_ref"]),
                        feature["reasoning"],
                        _adapt_json_payload(feature["evidence"]),
                        feature["verification_count"],
                        feature["decay_enabled"],
                        feature["stability_period_days"],
                        feature["decay_rate"],
                        feature["status"],
                        feature["resolved_at"],
                        feature["resolved_reason"],
                        _adapt_json_payload(feature["resolved_by"]),
                        feature["last_observed_at"],
                        feature["inferred"],
                    ),
                )
                continue

            existing_id, existing_value, existing_confidence, verification_count, existing_evidence, existing_aliases, existing_status = existing
            merged_value = self._merge_feature_value(existing_value or {}, feature["feature_value"])
            merged_evidence = list(
                dict.fromkeys([*(existing_evidence or []), *feature["evidence"]])
            )[:8]
            merged_aliases = self._merge_aliases(
                existing_aliases,
                feature["aliases"],
                feature_key=feature["feature_key"],
            )
            next_verification_count = int(verification_count or 1) + 1
            merged_confidence = min(
                0.98,
                round(
                    max(float(existing_confidence or 0.5), feature["confidence"]) * 0.7
                    + feature["confidence"] * 0.3
                    + min(0.08, next_verification_count * 0.01),
                    4,
                ),
            )
            next_status = feature["status"]
            if str(existing_status or "").upper() == "RESOLVED" and feature["dimension"] == "weak_points":
                next_status = "REGRESSED"
            cur.execute(
                """
                UPDATE app.learner_feature
                SET feature_value = %s,
                    canonical_key = %s,
                    aliases = %s,
                    confidence = %s,
                    source_type = %s,
                    source_ref = %s,
                    evidence = %s,
                    verification_count = %s,
                    decay_enabled = %s,
                    stability_period_days = %s,
                    decay_rate = %s,
                    is_active = TRUE,
                    status = %s,
                    resolved_at = %s,
                    resolved_reason = %s,
                    resolved_by = %s,
                    last_observed_at = now(),
                    inferred = %s,
                    updated_at = now()
                WHERE id = %s
                """,
                (
                    _adapt_json_payload(merged_value),
                    feature["canonical_key"],
                    _adapt_json_payload(merged_aliases),
                    merged_confidence,
                    feature["source_type"],
                    _adapt_json_payload(feature["source_ref"]),
                    _adapt_json_payload(merged_evidence),
                    next_verification_count,
                    feature["decay_enabled"],
                    feature["stability_period_days"],
                    feature["decay_rate"],
                    next_status,
                    None if next_status != "RESOLVED" else feature["resolved_at"],
                    "" if next_status != "RESOLVED" else feature["resolved_reason"],
                    _adapt_json_payload(feature["resolved_by"]),
                    feature["inferred"],
                    existing_id,
                ),
            )

    def _resolve_mastered_weak_points(self, cur: Any, *, user_id: str, threshold: float = 0.85) -> None:
        resolvable_dimensions = resolved_by_skill_mastery_dimensions()
        if "weak_points" not in resolvable_dimensions:
            return
        cur.execute(
            """
            SELECT feature_key, canonical_key, feature_value
            FROM app.learner_feature
            WHERE user_id = %s::uuid
              AND dimension = 'skill_mastery'
              AND is_active = TRUE
              AND status IN ('ACTIVE', 'REGRESSED')
            """,
            (user_id,),
        )
        if not hasattr(cur, "fetchall"):
            return
        rows = cur.fetchall() or []
        for feature_key, canonical_key, feature_value in rows:
            value = feature_value or {}
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except json.JSONDecodeError:
                    continue
            if not isinstance(value, dict):
                continue
            try:
                score = float(value.get("score") or 0.0)
            except (TypeError, ValueError):
                continue
            if score < threshold:
                continue
            resolved_key = canonical_key or canonicalize_topic(str(feature_key)).canonical_key
            if not resolved_key:
                continue
            cur.execute(
                """
                UPDATE app.learner_feature
                SET status = 'RESOLVED',
                    is_active = FALSE,
                    resolved_at = now(),
                    resolved_reason = %s,
                    resolved_by = %s,
                    updated_at = now()
                WHERE user_id = %s::uuid
                  AND dimension = 'weak_points'
                  AND canonical_key = %s
                  AND is_active = TRUE
                  AND status IN ('ACTIVE', 'REGRESSED')
                """,
                (
                    f"skill_mastery >= {threshold}",
                    _adapt_json_payload(
                        {
                            "type": "skill_mastery",
                            "topic": str(feature_key),
                            "canonicalKey": resolved_key,
                            "score": round(score, 4),
                            "threshold": threshold,
                        }
                    ),
                    user_id,
                    resolved_key,
                ),
            )

    def _aggregate_profile(self, features: list[dict[str, Any]], dimensions: LearnerProfileDimensions) -> dict[str, Any]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for feature in features:
            grouped.setdefault(str(feature["dimension"]), []).append(feature)
        for feature_list in grouped.values():
            feature_list.sort(
                key=lambda item: (
                    -float(item.get("confidence") or 0.0),
                    -(item.get("updated_at").timestamp() if item.get("updated_at") else 0.0),
                    str(item.get("feature_key") or ""),
                )
            )

        weak_details = [
            {
                "topic": feature["feature_key"],
                "severity": float((feature.get("feature_value") or {}).get("severity") or feature.get("confidence") or 0.6),
                "lastError": str((feature.get("feature_value") or {}).get("lastError") or ""),
                "updatedRank": item_updated_at.timestamp() if (item_updated_at := feature.get("updated_at")) else 0.0,
            }
            for feature in grouped.get("weak_points", [])
        ]
        weak_details.sort(
            key=lambda item: (
                -float(item["severity"]),
                -float(item["updatedRank"]),
                item["topic"],
            )
        )
        weak_points = [item["topic"] for item in weak_details]
        weak_details_payload = [
            {
                "topic": item["topic"],
                "severity": item["severity"],
                "lastError": item["lastError"],
            }
            for item in weak_details
        ]

        skill_mastery = {
            feature["feature_key"]: max(
                0.0,
                min(1.0, float((feature.get("feature_value") or {}).get("score") or 0.0)),
            )
            for feature in grouped.get("skill_mastery", [])
        }
        preferred_resource_types = [
            str((feature.get("feature_value") or {}).get("resourceType") or feature["feature_key"]).upper()
            for feature in grouped.get("preferred_resource_type", [])
        ]
        inferred_recommendations = [
            str((feature.get("feature_value") or {}).get("text") or feature["feature_key"])
            for feature in grouped.get("inferred_recommendation", [])
        ]
        error_patterns = [
            ErrorPattern.model_validate(feature.get("feature_value") or {"pattern": feature["feature_key"]}).model_dump(by_alias=True)
            for feature in grouped.get("error_patterns", [])
        ]
        learning_habits = (
            (grouped.get("learning_habits", [{}])[0].get("feature_value") if grouped.get("learning_habits") else {})
            or dimensions.learning_habits.model_dump(by_alias=True)
        )
        current_goal = (
            (grouped.get("current_goal", [{}])[0].get("feature_value") if grouped.get("current_goal") else {})
            or dimensions.current_goal.model_dump(by_alias=True)
        )
        explanation_preference = str(
            ((grouped.get("explanation_preference", [{}])[0].get("feature_value") if grouped.get("explanation_preference") else {}) or {}).get("value")
            or dimensions.explanation_preference
        )
        knowledge_foundation = str(
            ((grouped.get("knowledge_foundation", [{}])[0].get("feature_value") if grouped.get("knowledge_foundation") else {}) or {}).get("level")
            or dimensions.knowledge_foundation
        )
        professional_background = str(
            ((grouped.get("professional_background", [{}])[0].get("feature_value") if grouped.get("professional_background") else {}) or {}).get("text")
            or dimensions.professional_background
        )
        learning_preference_feature = (
            (grouped.get("learning_preference", [{}])[0].get("feature_value") if grouped.get("learning_preference") else {})
            or {}
        )
        learning_preference = str(learning_preference_feature.get("mode") or dimensions.learning_preference)
        cognitive_style = str(
            ((grouped.get("cognitive_style", [{}])[0].get("feature_value") if grouped.get("cognitive_style") else {}) or {}).get("style")
            or dimensions.cognitive_style
        )
        learning_pace = str(
            ((grouped.get("learning_pace", [{}])[0].get("feature_value") if grouped.get("learning_pace") else {}) or {}).get("pace")
            or dimensions.learning_pace
        )
        confidence_feature = (grouped.get("confidence_level", [{}])[0].get("feature_value") if grouped.get("confidence_level") else {}) or {}
        confidence_score = self._compute_snapshot_confidence(features, dimensions)
        confidence_level = str(confidence_feature.get("level") or self._confidence_score_to_level(confidence_score))
        summary_text = self._build_profile_summary(
            knowledge_foundation=knowledge_foundation,
            learning_goal=str(current_goal.get("shortTerm") or dimensions.learning_goal),
            weak_points=weak_points,
            learning_preference=learning_preference,
            cognitive_style=cognitive_style,
            preferred_resource_types=preferred_resource_types,
            skill_mastery=skill_mastery,
            inferred_recommendations=inferred_recommendations,
        )
        recent_mistakes: list[str] = []
        for item in error_patterns:
            recent_mistakes.extend(item.get("examples", []))
        recent_mistakes = list(dict.fromkeys(filter(None, recent_mistakes)))[:6]

        return {
            "knowledgeFoundation": knowledge_foundation,
            "studentLevel": knowledge_foundation,
            "knowledgeBase": knowledge_foundation,
            "foundationLevel": knowledge_foundation,
            "learningGoal": str(current_goal.get("shortTerm") or dimensions.learning_goal),
            "currentGoal": current_goal,
            "professionalBackground": professional_background,
            "learningPreference": learning_preference,
            "preference": preferred_resource_types or [learning_preference],
            "preferredStyle": explanation_preference or learning_preference or learning_pace,
            "cognitiveStyle": cognitive_style,
            "learningPace": learning_pace,
            "weakPoints": weak_points,
            "weakPointDetails": weak_details_payload,
            "knowledgeGaps": weak_points,
            "skillMastery": skill_mastery,
            "learningHabits": learning_habits,
            "errorPatterns": error_patterns,
            "confidenceLevel": confidence_level,
            "confidenceScore": round(confidence_score, 4),
            "preferredResourceTypes": preferred_resource_types,
            "explanationPreference": explanation_preference,
            "inferredRecommendations": inferred_recommendations,
            "recentMistakes": recent_mistakes,
            "evidence": dimensions.evidence,
            "summaryText": summary_text,
        }

    def _build_profile_summary(
        self,
        *,
        knowledge_foundation: str,
        learning_goal: str,
        weak_points: list[str],
        learning_preference: str,
        cognitive_style: str,
        preferred_resource_types: list[str],
        skill_mastery: dict[str, float],
        inferred_recommendations: list[str],
    ) -> str:
        weakest_skills = [
            name
            for name, score in sorted(skill_mastery.items(), key=lambda item: item[1])[:2]
            if score < 0.7
        ]
        localized_knowledge_foundation = self._localize_knowledge_foundation(knowledge_foundation)
        localized_learning_preference = self._localize_learning_preference(learning_preference or "step_by_step")
        localized_cognitive_style = self._localize_cognitive_style(cognitive_style or "mixed")
        localized_resource_types = [
            self._localize_resource_type(item) for item in preferred_resource_types[:3]
        ]
        return (
            f"当前画像显示学生知识基础为 {localized_knowledge_foundation}，"
            f"近期目标是“{learning_goal or '巩固当前主题'}”；"
            f"主要薄弱点集中在 {', '.join(weak_points[:3]) or '暂无明确薄弱点'}，"
            f"学习偏好偏向 {localized_learning_preference}，认知风格为 {localized_cognitive_style}。"
            f"建议优先提供 {', '.join(localized_resource_types) or '讲解文档'} 类型资源"
            + (f'，重点补强 {", ".join(weakest_skills)}' if weakest_skills else '')
            + (f'；下一步建议：{inferred_recommendations[0]}' if inferred_recommendations else '')
            + '。'
        )

    def _localize_knowledge_foundation(self, value: str) -> str:
        mapping = {
            "BEGINNER": "入门",
            "BASIC": "基础",
            "INTERMEDIATE": "进阶",
            "ADVANCED": "熟练",
            "UNKNOWN": "待分析",
        }
        normalized = str(value or "").strip().upper()
        return mapping.get(normalized, str(value or "").strip() or "待分析")

    def _localize_learning_preference(self, value: str) -> str:
        mapping = {
            "step_by_step": "循序渐进",
            "concept_then_question": "先概念后练习",
            "example_first": "先例子后原理",
            "visual_first": "先图示后讲解",
        }
        normalized = str(value or "").strip()
        return mapping.get(normalized, normalized or "循序渐进")

    def _localize_cognitive_style(self, value: str) -> str:
        mapping = {
            "reasoning_oriented": "偏原理推导",
            "procedural_oriented": "偏步骤实操",
            "mixed": "混合型",
        }
        normalized = str(value or "").strip()
        return mapping.get(normalized, normalized or "混合型")

    def _localize_resource_type(self, value: str) -> str:
        mapping = {
            "DOCUMENT": "讲解文档",
            "READING": "拓展阅读",
            "MINDMAP": "思维导图",
            "CODE": "代码案例",
            "CODE_CASE": "代码案例",
            "QUIZ": "练习题",
            "VIDEO": "数字人视频",
        }
        normalized = str(value or "").strip().upper()
        return mapping.get(normalized, str(value or "").strip())

    def _compute_snapshot_confidence(
        self,
        features: list[dict[str, Any]],
        dimensions: LearnerProfileDimensions,
    ) -> float:
        if features:
            top_confidences = [float(item.get("confidence") or 0.5) for item in features[:8]]
            return max(0.35, min(0.96, sum(top_confidences) / len(top_confidences)))
        return max(0.35, min(0.96, float(dimensions.confidence_score or 0.65)))

    def _confidence_score_to_level(self, score: float) -> str:
        if score >= 0.78:
            return "HIGH"
        if score >= 0.58:
            return "MEDIUM"
        return "LOW"

    def _build_embedding_text(self, profile_payload: dict[str, Any], summary_text: str) -> str:
        pieces = [
            summary_text,
            f"knowledgeFoundation={profile_payload.get('knowledgeFoundation', '')}",
            f"learningGoal={profile_payload.get('learningGoal', '')}",
            f"weakPoints={','.join(profile_payload.get('weakPoints', []))}",
            f"cognitiveStyle={profile_payload.get('cognitiveStyle', '')}",
            f"preferredResourceTypes={','.join(profile_payload.get('preferredResourceTypes', []))}",
        ]
        return "\n".join(piece for piece in pieces if piece)

    def _generate_embedding(self, text: str) -> list[float] | None:
        if self._embedding_fn is not None:
            return self._embedding_fn(text)
        api_key = self.settings.effective_embedding_api_key
        if not api_key or not text.strip():
            return None
        os.environ["DASHSCOPE_API_KEY"] = api_key
        try:
            from dashscope import MultiModalEmbedding

            response = MultiModalEmbedding.call(
                model=self.settings.knowledge_embedding_model_name,
                input=[{"text": text}],
                dimension=self.settings.knowledge_embedding_dimension,
                output_type="dense",
            )
            if response.status_code != 200:
                return None
            embeddings = response.output.get("embeddings", [])
            if not embeddings:
                return None
            embedding = embeddings[0].get("embedding")
            if not isinstance(embedding, list):
                return None
            return [float(item) for item in embedding]
        except Exception:
            return None

    def _update_profile_sync(
        self,
        *,
        user_id: str,
        dimensions: LearnerProfileDimensions,
        source_session_id: str | None = None,
    ) -> LearnerProfileSnapshot:
        raw_payload = dimensions.model_dump(by_alias=True)
        normalized_session_id = _normalize_uuid(source_session_id)
        with self._connect() as conn:
            with conn.cursor() as cur:
                self._ensure_feature_table(cur)
                self._apply_decay(cur, user_id=user_id)
                self._upsert_features(
                    cur,
                    user_id=user_id,
                    features=self._extract_features(dimensions),
                    source_session_id=normalized_session_id,
                )
                self._resolve_mastered_weak_points(cur, user_id=user_id)
                active_features = self._fetch_active_features(cur, user_id)
                profile_payload = self._aggregate_profile(active_features, dimensions)
                summary_text = str(profile_payload.get("summaryText") or raw_payload.get("summaryText") or dimensions.summary_text)
                snapshot_confidence = self._compute_snapshot_confidence(active_features, dimensions)
                cur.execute(
                    """
                    SELECT COALESCE(MAX(version), 0) + 1
                    FROM app.user_profile_snapshot
                    WHERE user_id = %s::uuid
                    """,
                    (user_id,),
                )
                version = int(cur.fetchone()[0])
                cur.execute(
                    """
                    INSERT INTO app.user_profile_snapshot(
                        user_id, source_session_id, version, profile_json, summary_text, confidence
                    )
                    VALUES (%s::uuid, %s::uuid, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        user_id,
                        normalized_session_id,
                        version,
                        _adapt_json_payload(profile_payload),
                        summary_text,
                        round(snapshot_confidence, 4),
                    ),
                )
                snapshot_id = cur.fetchone()[0]
                cur.execute(
                    """
                    INSERT INTO app.user_profile_current(user_id, active_snapshot_id, profile_json, summary_text)
                    VALUES (%s::uuid, %s, %s, %s)
                    ON CONFLICT (user_id) DO UPDATE
                    SET active_snapshot_id = EXCLUDED.active_snapshot_id,
                        profile_json = EXCLUDED.profile_json,
                        summary_text = EXCLUDED.summary_text,
                        updated_at = now()
                    """,
                    (
                        user_id,
                        snapshot_id,
                        _adapt_json_payload(profile_payload),
                        summary_text,
                    ),
                )

                # 保持快照/当前表的原子性，但在嵌入向量不可用时跳过画像向量。
                cur.execute("SAVEPOINT profile_vector_insert")
                try:
                    embedding = self._generate_embedding(self._build_embedding_text(profile_payload, summary_text))
                    if embedding is None:
                        raise RuntimeError("profile embedding unavailable")
                    embedding_str = "[" + ",".join(str(value) for value in embedding) + "]"
                    cur.execute(
                        """
                        INSERT INTO rag.user_profile_vector(profile_snapshot_id, user_id, version, embedding, is_active)
                        VALUES (%s, %s::uuid, %s, %s::vector, TRUE)
                        ON CONFLICT (user_id, version) DO NOTHING
                        """,
                        (snapshot_id, user_id, version, embedding_str),
                    )
                except Exception:
                    cur.execute("ROLLBACK TO SAVEPOINT profile_vector_insert")
                finally:
                    cur.execute("RELEASE SAVEPOINT profile_vector_insert")
            conn.commit()

        return LearnerProfileSnapshot(
            userId=user_id,
            version=version,
            profile=LearnerProfileDimensions.model_validate(profile_payload),
        )

    async def read_profile(self, user_id: str) -> LearnerProfileSnapshot | None:
        return await asyncio.to_thread(self._read_profile_sync, user_id)

    async def update_profile(
        self,
        *,
        user_id: str,
        dimensions: LearnerProfileDimensions,
        source_session_id: str | None = None,
    ) -> LearnerProfileSnapshot:
        return await asyncio.to_thread(
            self._update_profile_sync,
            user_id=user_id,
            dimensions=dimensions,
            source_session_id=source_session_id,
        )


def _normalize_uuid(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError):
        return None
