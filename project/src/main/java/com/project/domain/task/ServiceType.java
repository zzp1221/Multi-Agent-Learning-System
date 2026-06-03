package com.project.domain.task;

import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonValue;

import java.util.Arrays;

/**
 * Supported service types across tutoring and smart-engine flows.
 */
public enum ServiceType {
    PERSONALIZED_LEARNING,
    RESOURCE_GENERATION,
    PATH_PLANNING,
    RESOURCE_PUSH,
    EVALUATION,
    LEARNING_EVALUATION,
    VIDEO_GENERATION,
    PROFILE_BUILD,
    PRACTICE_JUDGE,
    TUTORING;

    @JsonCreator
    public static ServiceType fromValue(String rawValue) {
        if (rawValue == null || rawValue.isBlank()) {
            throw new IllegalArgumentException("serviceType must not be blank");
        }
        return Arrays.stream(values())
            .filter(candidate -> candidate.name().equalsIgnoreCase(rawValue.trim()))
            .findFirst()
            .orElseThrow(() -> new IllegalArgumentException("Unsupported serviceType: " + rawValue));
    }

    @JsonValue
    public String value() {
        return name();
    }
}
