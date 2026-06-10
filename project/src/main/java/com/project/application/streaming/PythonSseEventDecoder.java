package com.project.application.streaming;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.project.application.smartengine.PythonStreamEvent;
import com.project.application.smartengine.StreamEventType;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Optional;

/**
 * Decodes Python agent SSE frames into the Java internal stream event model.
 */
public final class PythonSseEventDecoder {

    private static final TypeReference<Map<String, Object>> MAP_TYPE = new TypeReference<>() {
    };

    private final ObjectMapper objectMapper;
    private String currentStage;

    public PythonSseEventDecoder(ObjectMapper objectMapper) {
        this.objectMapper = objectMapper;
    }

    public Optional<PythonStreamEvent> decode(String eventType, String rawData) throws JsonProcessingException {
        if ((eventType == null || eventType.isBlank()) && (rawData == null || rawData.isBlank())) {
            return Optional.empty();
        }

        Map<String, Object> envelope = rawData == null || rawData.isBlank()
            ? new LinkedHashMap<>()
            : objectMapper.readValue(rawData, MAP_TYPE);
        Map<String, Object> payload = resolvePayload(envelope);

        String resolvedEventType = resolveEventType(eventType, envelope);
        String stage = payload.get("stage") instanceof String stageValue ? stageValue : currentStage;
        currentStage = stage;
        return Optional.of(new PythonStreamEvent(
            StreamEventType.resolve(resolvedEventType).wireValue(),
            stage,
            payload
        ));
    }

    private Map<String, Object> resolvePayload(Map<String, Object> envelope) {
        Object payloadCandidate = envelope.get("payload");
        if (payloadCandidate instanceof Map<?, ?> rawPayload) {
            Map<String, Object> payload = new LinkedHashMap<>();
            rawPayload.forEach((key, value) -> payload.put(String.valueOf(key), value));
            return payload;
        }
        return new LinkedHashMap<>(envelope);
    }

    private String resolveEventType(String eventType, Map<String, Object> envelope) {
        if (eventType != null && !eventType.isBlank()) {
            return eventType;
        }
        return envelope.get("event") instanceof String envelopeEventType ? envelopeEventType : eventType;
    }
}
