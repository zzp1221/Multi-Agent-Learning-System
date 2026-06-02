package com.project.api.common;

import com.project.api.common.dto.ApiMessageResponse;
import com.project.application.common.ApplicationException;
import com.project.application.common.ClientDisconnectDetector;
import org.apache.catalina.connector.ClientAbortException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.validation.FieldError;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.context.request.async.AsyncRequestNotUsableException;

import java.io.IOException;
import java.util.stream.Collectors;

/**
 * 将内部异常转换为稳定的 API 响应。
 */
@RestControllerAdvice
public class GlobalExceptionHandler {

    private static final Logger LOGGER = LoggerFactory.getLogger(GlobalExceptionHandler.class);

    @ExceptionHandler(ApplicationException.class)
    public ResponseEntity<ApiMessageResponse> handleApplicationException(ApplicationException ex) {
        return ResponseEntity.status(ex.getStatus()).body(new ApiMessageResponse(ex.getCode(), ex.getMessage()));
    }

    @ExceptionHandler(AccessDeniedException.class)
    public ResponseEntity<ApiMessageResponse> handleAccessDeniedException(AccessDeniedException ex) {
        LOGGER.debug("Access denied: {}", ex.getMessage());
        return ResponseEntity.status(HttpStatus.FORBIDDEN)
            .body(new ApiMessageResponse("FORBIDDEN", "权限不足"));
    }

    @ExceptionHandler(MethodArgumentNotValidException.class)
    public ResponseEntity<ApiMessageResponse> handleValidationException(MethodArgumentNotValidException ex) {
        String message = ex.getBindingResult().getFieldErrors().stream()
            .map(FieldError::getDefaultMessage)
            .collect(Collectors.joining("; "));
        return ResponseEntity.status(HttpStatus.BAD_REQUEST)
            .body(new ApiMessageResponse("INVALID_ARGUMENT", message));
    }

    @ExceptionHandler({ClientAbortException.class, AsyncRequestNotUsableException.class})
    public void handleKnownClientDisconnect(Exception ex) {
        LOGGER.debug("Client disconnected before response completed: {}", ex.getMessage());
    }

    @ExceptionHandler(IOException.class)
    public ResponseEntity<ApiMessageResponse> handleIOException(IOException ex) {
        if (ClientDisconnectDetector.isClientDisconnect(ex)) {
            LOGGER.debug("Client disconnected before response completed: {}", ex.getMessage());
            return ResponseEntity.noContent().build();
        }
        LOGGER.error("Unhandled I/O exception", ex);
        return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
            .body(new ApiMessageResponse("INTERNAL_ERROR", "系统开小差了，请稍后重试"));
    }

    @ExceptionHandler(Exception.class)
    public ResponseEntity<ApiMessageResponse> handleUnexpectedException(Exception ex) {
        if (ClientDisconnectDetector.isClientDisconnect(ex)) {
            LOGGER.debug("Client disconnected before response completed: {}", ex.getMessage());
            return ResponseEntity.noContent().build();
        }
        LOGGER.error("Unhandled application exception", ex);
        return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
            .body(new ApiMessageResponse("INTERNAL_ERROR", "系统开小差了，请稍后重试"));
    }
}
