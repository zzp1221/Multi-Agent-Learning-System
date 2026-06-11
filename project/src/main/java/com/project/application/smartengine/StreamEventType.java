package com.project.application.smartengine;

import java.util.Arrays;

/**
 * Python 运行时发出的已知 SSE 事件类型。
 */
public enum StreamEventType {
    MESSAGE("message"),
    PROGRESS("progress"),
    RESULT_CHUNK("result_chunk"),
    REASONING_CHUNK("reasoning_chunk"),
    RESOURCE_FILE("resource_file"),
    QUESTION_BATCH("question_batch"),
    JUDGE_RESULT("judge_result"),
    DONE("done"),
    ERROR("error"),
    VIDEO_GEN_START("video_gen:start"),
    VIDEO_GEN_SCRIPT("video_gen:script"),
    VIDEO_GEN_SPEECH("video_gen:speech"),
    VIDEO_GEN_AVATAR("video_gen:avatar"),
    VIDEO_GEN_COMPLETE("video_gen:complete");

    private final String wireValue;

    StreamEventType(String wireValue) {
        this.wireValue = wireValue;
    }

    public String wireValue() {
        return wireValue;
    }

    public boolean isTerminal() {
        return this == DONE || this == ERROR;
    }

    private static final org.slf4j.Logger LOGGER = org.slf4j.LoggerFactory.getLogger(StreamEventType.class);

    public static StreamEventType resolve(String rawValue) {
        if (rawValue == null || rawValue.isBlank()) {
            return MESSAGE;
        }
        return Arrays.stream(values())
            .filter(candidate -> candidate.wireValue.equalsIgnoreCase(rawValue.trim()))
            .findFirst()
            .orElseGet(() -> {
                LOGGER.warn("Unknown SSE event type '{}', falling back to MESSAGE", rawValue);
                return MESSAGE;
            });
    }
}
