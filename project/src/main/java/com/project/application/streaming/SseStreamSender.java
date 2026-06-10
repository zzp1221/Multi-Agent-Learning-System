package com.project.application.streaming;

import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.io.IOException;
import java.util.Map;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.function.IntPredicate;
import java.util.function.IntFunction;

/**
 * Shared helper for emitting typed SSE messages without changing the wire contract.
 */
public final class SseStreamSender {

    private final SseEmitter emitter;
    private final AtomicInteger sequence;

    public SseStreamSender(SseEmitter emitter, AtomicInteger sequence) {
        this.emitter = emitter;
        this.sequence = sequence;
    }

    public int send(String eventName, IntFunction<Object> payloadFactory) {
        int nextSeq = sequence.incrementAndGet();
        sendWithId(eventName, nextSeq, payloadFactory.apply(nextSeq));
        return nextSeq;
    }

    public boolean sendError(IntFunction<Object> payloadFactory) {
        int nextSeq = sequence.incrementAndGet();
        try {
            sendRaw("error", nextSeq, payloadFactory.apply(nextSeq));
            return true;
        } catch (IOException | IllegalStateException ex) {
            return false;
        }
    }

    public void sendReplayable(String eventName, int seq, Object payload, IntPredicate shouldSend) throws IOException {
        if (!shouldSend.test(seq)) {
            return;
        }
        sendRaw(eventName, seq, payload);
    }

    public void sendWithId(String eventName, int seq, Object payload) {
        try {
            sendRaw(eventName, seq, payload);
        } catch (IOException | IllegalStateException ex) {
            throw new SseClientDisconnectedException(ex);
        }
    }

    private void sendRaw(String eventName, int seq, Object payload) throws IOException {
        emitter.send(SseEmitter.event()
            .name(eventNameOrDefault(eventName))
            .id(String.valueOf(seq))
            .data(payload));
    }

    private String eventNameOrDefault(String eventName) {
        return eventName == null || eventName.isBlank() ? "message" : eventName;
    }

    public static Map<String, Object> errorPayload(String message, String fallbackMessage) {
        return Map.of("message", message == null ? fallbackMessage : message);
    }

}
