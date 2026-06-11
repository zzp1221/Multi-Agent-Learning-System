package com.project.config;

import org.junit.jupiter.api.Test;

import java.time.Duration;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Verifies safe defaults for infrastructure-facing configuration.
 */
class AppPropertiesTest {

    @Test
    void pythonAgentReadTimeoutDefaultsToTenMinutes() {
        AppProperties properties = new AppProperties();

        assertThat(properties.getPythonAgent().getConnectTimeout()).isEqualTo(Duration.ofSeconds(5));
        assertThat(properties.getPythonAgent().getReadTimeout()).isEqualTo(Duration.ofMinutes(10));
        assertThat(properties.getPythonAgent().getInternalToken()).isEmpty();
        assertThat(properties.getSmartEngineQueue().getStreamKey()).isEqualTo("zhixue:smart-engine:tasks");
        assertThat(properties.getSmartEngineQueue().getCancelTtl()).isEqualTo(Duration.ofHours(24));
        assertThat(properties.getUpload().getImageTokenTtlSeconds()).isEqualTo(1_800);
        assertThat(properties.getUpload().getImageStorageDir()).isEqualTo("sandbox-temp/chat-images");
        assertThat(properties.getUserLlm().isEnabled()).isTrue();
        assertThat(properties.getUserLlm().getEncryptionKey()).isEmpty();
    }
}
