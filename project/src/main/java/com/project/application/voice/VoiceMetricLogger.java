package com.project.application.voice;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;

@Component
public class VoiceMetricLogger {

    private static final Logger LOGGER = LoggerFactory.getLogger(VoiceMetricLogger.class);

    public void record(
        String metric,
        UUID sessionId,
        String turnId,
        long durationMs,
        String provider,
        String model,
        String outcome
    ) {
        LOGGER.info(
            "voice_metric metric={} sessionId={} turnId={} durationMs={} provider={} model={} outcome={}",
            metric,
            sessionId,
            turnId,
            durationMs,
            provider,
            model,
            outcome
        );
    }

    public void record(
        String metric,
        VoiceMetricContext context,
        long durationMs,
        String provider,
        String model,
        String outcome,
        Integer inputLength,
        Integer outputLength,
        String errorCode
    ) {
        VoiceMetricContext safeContext = context == null
            ? VoiceMetricContext.empty(null, "")
            : context;
        Map<String, Object> fields = new LinkedHashMap<>();
        fields.put("metric", metric);
        fields.put("voiceSessionId", safeContext.voiceSessionId());
        fields.put("turnId", safeContext.turnId());
        fields.put("conversationId", safeContext.conversationId());
        fields.put("userHash", safeContext.userHash());
        fields.put("pageType", safeContext.pageType());
        fields.put("commandIntent", safeContext.commandIntent());
        fields.put("durationMs", durationMs);
        fields.put("provider", provider);
        fields.put("model", model);
        fields.put("outcome", outcome);
        fields.put("inputLength", inputLength);
        fields.put("outputLength", outputLength);
        fields.put("errorCode", errorCode);
        LOGGER.info("voice_metric {}", formatFields(fields));
    }

    private String formatFields(Map<String, Object> fields) {
        return fields.entrySet().stream()
            .map(entry -> entry.getKey() + "=" + safeValue(entry.getValue()))
            .collect(java.util.stream.Collectors.joining(" "));
    }

    private String safeValue(Object value) {
        return value == null ? "" : String.valueOf(value).replace('\n', '_').replace('\r', '_');
    }
}
