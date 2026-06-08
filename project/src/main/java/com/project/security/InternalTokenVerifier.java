package com.project.security;

import com.project.application.common.ApplicationException;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;

/**
 * Validates internal callbacks from the Python Agent.
 */
@Component
public class InternalTokenVerifier {

    public static final String INTERNAL_TOKEN_HEADER = "X-Zhixue-Internal-Token";

    private final InternalTokenProvider internalTokenProvider;

    public InternalTokenVerifier(InternalTokenProvider internalTokenProvider) {
        this.internalTokenProvider = internalTokenProvider;
    }

    public void requireValid(String suppliedToken) {
        String expectedToken = resolveExpectedToken();
        if (expectedToken.isBlank()) {
            throw new ApplicationException("INTERNAL_TOKEN_NOT_CONFIGURED", "Internal token is not configured", HttpStatus.SERVICE_UNAVAILABLE);
        }
        String normalizedSuppliedToken = suppliedToken == null ? "" : suppliedToken.trim();
        if (
            normalizedSuppliedToken.isBlank()
                || !MessageDigest.isEqual(
                    normalizedSuppliedToken.getBytes(StandardCharsets.UTF_8),
                    expectedToken.getBytes(StandardCharsets.UTF_8)
                )
        ) {
            throw new ApplicationException("INVALID_INTERNAL_TOKEN", "Invalid internal token", HttpStatus.UNAUTHORIZED);
        }
    }

    private String resolveExpectedToken() {
        try {
            return internalTokenProvider.resolve();
        } catch (IllegalStateException ex) {
            throw new ApplicationException("INTERNAL_TOKEN_UNAVAILABLE", "Internal token is unavailable", HttpStatus.SERVICE_UNAVAILABLE);
        }
    }
}
