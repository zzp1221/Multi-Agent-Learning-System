package com.project;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.project.application.smartengine.PythonStreamEvent;
import com.project.application.smartengine.StreamEventType;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.EnumSource;

import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Validates Java-side SSE event parsing against the shared contract.
 *
 * <p>Ensures every wire event type the Python agent can emit
 * is correctly resolved by {@link StreamEventType#resolve} and
 * that the {@link PythonStreamEvent} parsing preserves expected payload fields.</p>
 */
class SseEventContractValidationTest {

    private final ObjectMapper objectMapper = new ObjectMapper();

    @ParameterizedTest
    @EnumSource(StreamEventType.class)
    void everyStreamEventTypeIsResolvableFromWireValue(StreamEventType eventType) {
        String wireValue = eventType.wireValue();
        StreamEventType resolved = StreamEventType.resolve(wireValue);
        assertThat(resolved).isEqualTo(eventType);
    }

    @Test
    void unknownEventTypeFallsBackToMessage() {
        StreamEventType resolved = StreamEventType.resolve("nonexistent_type");
        assertThat(resolved).isEqualTo(StreamEventType.MESSAGE);
    }

    @Test
    void nullEventTypeFallsBackToMessage() {
        StreamEventType resolved = StreamEventType.resolve(null);
        assertThat(resolved).isEqualTo(StreamEventType.MESSAGE);
    }

    @Test
    void blankEventTypeFallsBackToMessage() {
        StreamEventType resolved = StreamEventType.resolve("   ");
        assertThat(resolved).isEqualTo(StreamEventType.MESSAGE);
    }

    @Test
    void onlyDoneAndErrorAreTerminal() {
        for (StreamEventType eventType : StreamEventType.values()) {
            if (eventType == StreamEventType.DONE || eventType == StreamEventType.ERROR) {
                assertThat(eventType.isTerminal()).isTrue();
            } else {
                assertThat(eventType.isTerminal()).isFalse();
            }
        }
    }

    @Test
    void pythonStreamEventPreservesStageAndPayload() throws Exception {
        PythonStreamEvent event = new PythonStreamEvent(
            "progress",
            "retrieving",
            Map.of("percent", 50, "stage", "retrieving")
        );

        assertThat(event.eventType()).isEqualTo("progress");
        assertThat(event.stage()).isEqualTo("retrieving");
        assertThat(event.safePayload()).containsEntry("percent", 50);
        assertThat(event.resolvedEventType()).isEqualTo(StreamEventType.PROGRESS);
    }

    @Test
    void resourceFilePayloadRoundTrips() throws Exception {
        Map<String, Object> rawPayload = Map.of(
            "assetType", "DOCUMENT",
            "title", "Test文档",
            "fileName", "test.md",
            "localPath", "/data/sandbox-temp/task-1/test.md",
            "mimeType", "text/markdown"
        );

        PythonStreamEvent event = new PythonStreamEvent("resource_file", "generating", rawPayload);

        assertThat(event.resolvedEventType()).isEqualTo(StreamEventType.RESOURCE_FILE);
        assertThat(event.safePayload().get("assetType")).isEqualTo("DOCUMENT");
        assertThat(event.safePayload().get("title")).isEqualTo("Test文档");
    }

    @Test
    void allVideoGenEventTypesAreResolvable() {
        assertThat(StreamEventType.resolve("video_gen:start")).isEqualTo(StreamEventType.VIDEO_GEN_START);
        assertThat(StreamEventType.resolve("video_gen:script")).isEqualTo(StreamEventType.VIDEO_GEN_SCRIPT);
        assertThat(StreamEventType.resolve("video_gen:speech")).isEqualTo(StreamEventType.VIDEO_GEN_SPEECH);
        assertThat(StreamEventType.resolve("video_gen:avatar")).isEqualTo(StreamEventType.VIDEO_GEN_AVATAR);
        assertThat(StreamEventType.resolve("video_gen:complete")).isEqualTo(StreamEventType.VIDEO_GEN_COMPLETE);
    }

    @Test
    void eventTypeCountMatchesContract() {
        // The shared contract defines exactly 14 event types
        assertThat(StreamEventType.values()).hasSize(14);
    }
}
