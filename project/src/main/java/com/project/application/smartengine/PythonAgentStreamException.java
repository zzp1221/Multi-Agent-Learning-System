package com.project.application.smartengine;

/**
 * Python Agent 返回的结构化流式错误。
 */
public class PythonAgentStreamException extends RuntimeException {

    private final String code;
    private final Integer httpStatus;
    private final boolean retryable;

    public PythonAgentStreamException(String code, String message, Integer httpStatus, boolean retryable) {
        super(message);
        this.code = code;
        this.httpStatus = httpStatus;
        this.retryable = retryable;
    }

    public String getCode() {
        return code;
    }

    public Integer getHttpStatus() {
        return httpStatus;
    }

    public boolean isRetryable() {
        return retryable;
    }
}
