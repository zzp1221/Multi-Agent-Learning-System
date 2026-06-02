package com.project.application.voice;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

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
}
