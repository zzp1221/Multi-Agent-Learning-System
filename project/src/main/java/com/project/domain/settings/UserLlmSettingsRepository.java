package com.project.domain.settings;

import java.util.Optional;
import java.util.UUID;

public interface UserLlmSettingsRepository {
    Optional<UserLlmSettingsRecord> findByUserId(UUID userId);

    void upsert(UserLlmSettingsRecord record);

    void deleteByUserId(UUID userId);

    record UserLlmSettingsRecord(
        UUID userId,
        boolean enabled,
        String activeProvider,
        String fallbackProvider,
        String providerConfigJson,
        String componentOverridesJson,
        String encryptedSecretsJson,
        String secretMetaJson
    ) {
    }
}
