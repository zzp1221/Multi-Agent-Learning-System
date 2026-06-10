package com.project.application.streaming;

import org.junit.jupiter.api.Test;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.io.IOException;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;

import static org.assertj.core.api.Assertions.assertThat;

class SseStreamSenderTest {

    @Test
    void sendsEventWithIncrementedSequence() {
        CapturingEmitter emitter = new CapturingEmitter();
        SseStreamSender sender = new SseStreamSender(emitter, new AtomicInteger());
        AtomicReference<Object> payload = new AtomicReference<>();

        int seq = sender.send("progress", nextSeq -> {
            Map<String, Object> value = Map.of("seq", nextSeq);
            payload.set(value);
            return value;
        });

        assertThat(seq).isEqualTo(1);
        assertThat(payload.get()).isEqualTo(Map.of("seq", 1));
        assertThat(emitter.events).hasSize(1);
    }

    @Test
    void skipsReplayWhenSequenceGateRejectsEvent() throws Exception {
        CapturingEmitter emitter = new CapturingEmitter();
        SseStreamSender sender = new SseStreamSender(emitter, new AtomicInteger(3));

        sender.sendReplayable("progress", 2, Map.of("seq", 2), seq -> seq > 3);
        sender.sendReplayable("progress", 4, Map.of("seq", 4), seq -> seq > 3);

        assertThat(emitter.events).hasSize(1);
    }

    @Test
    void sendErrorReturnsFalseWhenClientIsUnavailable() {
        CapturingEmitter emitter = new CapturingEmitter();
        emitter.fail = true;
        SseStreamSender sender = new SseStreamSender(emitter, new AtomicInteger());

        boolean sent = sender.sendError(nextSeq -> Map.of("message", "failed"));

        assertThat(sent).isFalse();
    }

    private static final class CapturingEmitter extends SseEmitter {
        private final List<SseEventBuilder> events = new ArrayList<>();
        private boolean fail;

        @Override
        public void send(SseEventBuilder builder) throws IOException {
            if (fail) {
                throw new IOException("client closed");
            }
            events.add(builder);
        }
    }
}
