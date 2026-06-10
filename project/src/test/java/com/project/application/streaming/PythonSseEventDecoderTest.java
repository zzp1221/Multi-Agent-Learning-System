package com.project.application.streaming;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.project.application.smartengine.PythonStreamEvent;
import org.junit.jupiter.api.Test;

import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;

class PythonSseEventDecoderTest {

    private final PythonSseEventDecoder decoder = new PythonSseEventDecoder(new ObjectMapper());

    @Test
    void decodesPayloadEnvelopeAndKeepsCurrentStage() throws Exception {
        Optional<PythonStreamEvent> first = decoder.decode(
            "progress",
            "{\"payload\":{\"stage\":\"retrieving\",\"percent\":20}}"
        );
        Optional<PythonStreamEvent> second = decoder.decode(
            "result_chunk",
            "{\"payload\":{\"text\":\"hello\"}}"
        );

        assertThat(first).isPresent();
        assertThat(first.orElseThrow().eventType()).isEqualTo("progress");
        assertThat(first.orElseThrow().stage()).isEqualTo("retrieving");
        assertThat(first.orElseThrow().safePayload()).containsEntry("percent", 20);
        assertThat(second).isPresent();
        assertThat(second.orElseThrow().stage()).isEqualTo("retrieving");
    }

    @Test
    void fallsBackToEnvelopeEventWhenSseEventNameIsMissing() throws Exception {
        Optional<PythonStreamEvent> event = decoder.decode(
            null,
            "{\"event\":\"done\",\"payload\":{\"status\":\"SUCCESS\",\"summary\":\"ok\"}}"
        );

        assertThat(event).isPresent();
        assertThat(event.orElseThrow().eventType()).isEqualTo("done");
        assertThat(event.orElseThrow().safePayload()).containsEntry("summary", "ok");
    }

    @Test
    void ignoresEmptyFrame() throws Exception {
        assertThat(decoder.decode(null, "")).isEmpty();
    }
}
