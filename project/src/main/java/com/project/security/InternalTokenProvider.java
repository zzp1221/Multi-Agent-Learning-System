package com.project.security;

import com.project.config.AppProperties;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

@Component
public class InternalTokenProvider {

    static final Path DEFAULT_INTERNAL_TOKEN_FILE = Path.of("/run/secrets/zhixue-python-agent-internal-token");

    private final AppProperties appProperties;
    private final Path tokenFile;

    @Autowired
    public InternalTokenProvider(AppProperties appProperties) {
        this(appProperties, DEFAULT_INTERNAL_TOKEN_FILE);
    }

    InternalTokenProvider(AppProperties appProperties, Path tokenFile) {
        this.appProperties = appProperties;
        this.tokenFile = tokenFile;
    }

    public String resolve() {
        String fileToken = readInternalTokenFile();
        if (!fileToken.isBlank()) {
            return fileToken.trim();
        }
        String configuredToken = appProperties.getPythonAgent().getInternalToken();
        return configuredToken == null ? "" : configuredToken.trim();
    }

    public String requireConfigured() {
        String token = resolve();
        if (token.isBlank()) {
            throw new IllegalStateException("PYTHON_AGENT_INTERNAL_TOKEN must be configured");
        }
        return token;
    }

    private String readInternalTokenFile() {
        try {
            return Files.exists(tokenFile) ? Files.readString(tokenFile, StandardCharsets.UTF_8).trim() : "";
        } catch (IOException ex) {
            throw new IllegalStateException("Failed to read Python agent internal token file", ex);
        }
    }
}
