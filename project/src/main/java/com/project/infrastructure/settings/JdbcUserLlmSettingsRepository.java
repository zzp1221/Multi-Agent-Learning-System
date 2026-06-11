package com.project.infrastructure.settings;

import com.project.domain.settings.UserLlmSettingsRepository;
import org.springframework.dao.EmptyResultDataAccessException;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.stereotype.Repository;

import java.util.Optional;
import java.util.UUID;

@Repository
public class JdbcUserLlmSettingsRepository implements UserLlmSettingsRepository {

    private final NamedParameterJdbcTemplate jdbcTemplate;

    public JdbcUserLlmSettingsRepository(NamedParameterJdbcTemplate jdbcTemplate) {
        this.jdbcTemplate = jdbcTemplate;
    }

    @Override
    public Optional<UserLlmSettingsRecord> findByUserId(UUID userId) {
        try {
            return Optional.ofNullable(jdbcTemplate.queryForObject(
                """
                SELECT user_id, enabled, active_provider, fallback_provider,
                       provider_config_json::text AS provider_config_json,
                       component_overrides_json::text AS component_overrides_json,
                       encrypted_secrets_json::text AS encrypted_secrets_json,
                       secret_meta_json::text AS secret_meta_json
                FROM app.user_llm_config
                WHERE user_id = :userId
                """,
                new MapSqlParameterSource("userId", userId),
                (rs, rowNum) -> new UserLlmSettingsRecord(
                    (UUID) rs.getObject("user_id"),
                    rs.getBoolean("enabled"),
                    rs.getString("active_provider"),
                    rs.getString("fallback_provider"),
                    rs.getString("provider_config_json"),
                    rs.getString("component_overrides_json"),
                    rs.getString("encrypted_secrets_json"),
                    rs.getString("secret_meta_json")
                )
            ));
        } catch (EmptyResultDataAccessException ignored) {
            return Optional.empty();
        }
    }

    @Override
    public void upsert(UserLlmSettingsRecord record) {
        jdbcTemplate.update(
            """
            INSERT INTO app.user_llm_config(
                user_id, enabled, active_provider, fallback_provider,
                provider_config_json, component_overrides_json,
                encrypted_secrets_json, secret_meta_json
            )
            VALUES (
                :userId, :enabled, :activeProvider, :fallbackProvider,
                CAST(:providerConfigJson AS jsonb), CAST(:componentOverridesJson AS jsonb),
                CAST(:encryptedSecretsJson AS jsonb), CAST(:secretMetaJson AS jsonb)
            )
            ON CONFLICT (user_id) DO UPDATE SET
                enabled = EXCLUDED.enabled,
                active_provider = EXCLUDED.active_provider,
                fallback_provider = EXCLUDED.fallback_provider,
                provider_config_json = EXCLUDED.provider_config_json,
                component_overrides_json = EXCLUDED.component_overrides_json,
                encrypted_secrets_json = EXCLUDED.encrypted_secrets_json,
                secret_meta_json = EXCLUDED.secret_meta_json,
                updated_at = now()
            """,
            new MapSqlParameterSource()
                .addValue("userId", record.userId())
                .addValue("enabled", record.enabled())
                .addValue("activeProvider", record.activeProvider())
                .addValue("fallbackProvider", record.fallbackProvider())
                .addValue("providerConfigJson", record.providerConfigJson())
                .addValue("componentOverridesJson", record.componentOverridesJson())
                .addValue("encryptedSecretsJson", record.encryptedSecretsJson())
                .addValue("secretMetaJson", record.secretMetaJson())
        );
    }

    @Override
    public void deleteByUserId(UUID userId) {
        jdbcTemplate.update(
            "DELETE FROM app.user_llm_config WHERE user_id = :userId",
            new MapSqlParameterSource("userId", userId)
        );
    }
}
