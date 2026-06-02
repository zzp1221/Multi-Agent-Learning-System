package com.project.application.common;

import org.apache.catalina.connector.ClientAbortException;
import org.springframework.web.context.request.async.AsyncRequestNotUsableException;

import java.io.IOException;
import java.util.Locale;

/**
 * 判断异常是否由客户端主动断开连接引起。
 */
public final class ClientDisconnectDetector {

    private ClientDisconnectDetector() {}

    public static boolean isClientDisconnect(Throwable throwable) {
        Throwable current = throwable;
        while (current != null) {
            if (current instanceof ClientAbortException
                || current instanceof AsyncRequestNotUsableException) {
                return true;
            }
            if ((current instanceof IOException || current instanceof IllegalStateException)
                && isClientDisconnectMessage(current.getMessage())) {
                return true;
            }
            String className = current.getClass().getName();
            if (className.contains("AsyncRequestNotUsableException")) {
                return true;
            }
            current = current.getCause();
        }
        return false;
    }

    public static boolean isClientDisconnectMessage(String message) {
        if (message == null || message.isBlank()) {
            return false;
        }
        String normalized = message.toLowerCase(Locale.ROOT);
        return normalized.contains("broken pipe")
            || normalized.contains("connection reset")
            || normalized.contains("connection aborted")
            || normalized.contains("connection has been closed")
            || normalized.contains("forcibly closed")
            || normalized.contains("failed to send")
            || normalized.contains("asyncrequestnotusableexception")
            || normalized.contains("responsebodyemitter has already completed");
    }
}
