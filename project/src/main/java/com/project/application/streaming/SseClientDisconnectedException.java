package com.project.application.streaming;

/**
 * Raised when an SSE client connection is no longer writable.
 */
public class SseClientDisconnectedException extends RuntimeException {
    public SseClientDisconnectedException(Throwable cause) {
        super(cause);
    }
}
