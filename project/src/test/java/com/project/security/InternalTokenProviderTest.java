package com.project.security;

import com.project.config.AppProperties;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.file.Files;
import java.nio.file.Path;

import static org.assertj.core.api.Assertions.assertThat;

class InternalTokenProviderTest {

    @TempDir
    private Path tempDir;

    @Test
    void resolvePrefersSecretFileOverConfiguredToken() throws Exception {
        AppProperties appProperties = new AppProperties();
        appProperties.getPythonAgent().setInternalToken("stale-config-token");
        Path tokenFile = tempDir.resolve("internal-token");
        Files.writeString(tokenFile, "fresh-file-token\n");

        InternalTokenProvider provider = new InternalTokenProvider(appProperties, tokenFile);

        assertThat(provider.resolve()).isEqualTo("fresh-file-token");
    }

    @Test
    void resolveFallsBackToConfiguredTokenWhenSecretFileIsMissing() {
        AppProperties appProperties = new AppProperties();
        appProperties.getPythonAgent().setInternalToken("configured-token");

        InternalTokenProvider provider = new InternalTokenProvider(appProperties, tempDir.resolve("missing-token"));

        assertThat(provider.resolve()).isEqualTo("configured-token");
    }
}
