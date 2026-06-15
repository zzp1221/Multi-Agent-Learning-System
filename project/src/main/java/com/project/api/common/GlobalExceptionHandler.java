package com.project.api.common;

import com.project.api.common.dto.ApiMessageResponse;
import com.project.application.common.ApplicationException;
import com.project.application.common.ClientDisconnectDetector;
import org.apache.catalina.connector.ClientAbortException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.validation.FieldError;
import org.springframework.web.HttpRequestMethodNotSupportedException;
import org.springframework.web.HttpMediaTypeNotSupportedException;
import org.springframework.web.bind.MissingServletRequestParameterException;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.context.request.async.AsyncRequestNotUsableException;
import org.springframework.web.method.annotation.MethodArgumentTypeMismatchException;
import org.springframework.web.multipart.MultipartException;
import org.springframework.web.multipart.support.MissingServletRequestPartException;
import org.springframework.web.servlet.NoHandlerFoundException;
import org.springframework.web.servlet.resource.NoResourceFoundException;

import java.io.IOException;
import java.util.Arrays;
import java.util.List;
import java.util.Locale;

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
        return ResponseEntity.status(HttpStatus.BAD_REQUEST)
            .body(new ApiMessageResponse("INVALID_ARGUMENT", validationMessage(ex)));
    }

    @ExceptionHandler(HttpMessageNotReadableException.class)
    public ResponseEntity<ApiMessageResponse> handleMessageNotReadableException(HttpMessageNotReadableException ex) {
        LOGGER.debug("Invalid request body: {}", ex.getMessage());
        return ResponseEntity.status(HttpStatus.BAD_REQUEST)
            .body(new ApiMessageResponse("INVALID_ARGUMENT", "请求体格式或字段值不正确"));
    }

    @ExceptionHandler({
        MethodArgumentTypeMismatchException.class,
        MissingServletRequestParameterException.class,
        MissingServletRequestPartException.class,
        MultipartException.class,
        HttpMediaTypeNotSupportedException.class
    })
    public ResponseEntity<ApiMessageResponse> handleBadRequestArgument(Exception ex) {
        LOGGER.debug("Invalid request argument: {}", ex.getMessage());
        return ResponseEntity.status(HttpStatus.BAD_REQUEST)
            .body(new ApiMessageResponse("INVALID_ARGUMENT", "请求参数缺失或格式不正确"));
    }

    @ExceptionHandler({NoHandlerFoundException.class, NoResourceFoundException.class})
    public ResponseEntity<ApiMessageResponse> handleNotFound(Exception ex) {
        LOGGER.debug("Request path not found: {}", ex.getMessage());
        return ResponseEntity.status(HttpStatus.NOT_FOUND)
            .body(new ApiMessageResponse("NOT_FOUND", "请求资源不存在"));
    }

    @ExceptionHandler(HttpRequestMethodNotSupportedException.class)
    public ResponseEntity<ApiMessageResponse> handleMethodNotSupported(HttpRequestMethodNotSupportedException ex) {
        LOGGER.debug("Request method not supported: {}", ex.getMessage());
        return ResponseEntity.status(HttpStatus.METHOD_NOT_ALLOWED)
            .headers(ex.getHeaders())
            .body(new ApiMessageResponse("METHOD_NOT_ALLOWED", "请求方法不支持"));
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

    private String validationMessage(MethodArgumentNotValidException ex) {
        List<String> messages = ex.getBindingResult().getFieldErrors().stream()
            .map(this::validationMessageForField)
            .filter(message -> message != null && !message.isBlank())
            .distinct()
            .toList();
        if (messages.isEmpty()) {
            return "请求参数校验失败";
        }
        return "请求参数校验失败：" + String.join("；", messages);
    }

    private String validationMessageForField(FieldError error) {
        if (hasConstraintCode(error, "NotBlank") || hasConstraintCode(error, "NotEmpty") || hasConstraintCode(error, "NotNull")) {
            return "必填项不能为空";
        }
        if (hasConstraintCode(error, "Size") || hasConstraintCode(error, "Length")) {
            return "字段长度不符合要求";
        }
        if (hasConstraintCode(error, "Pattern")) {
            return chineseOrDefault(error.getDefaultMessage(), "字段格式不符合要求");
        }
        if (hasConstraintCode(error, "Email")) {
            return "邮箱格式不正确";
        }
        if (hasConstraintCode(error, "Min")
            || hasConstraintCode(error, "Max")
            || hasConstraintCode(error, "DecimalMin")
            || hasConstraintCode(error, "DecimalMax")
            || hasConstraintCode(error, "Positive")
            || hasConstraintCode(error, "PositiveOrZero")
            || hasConstraintCode(error, "Negative")
            || hasConstraintCode(error, "NegativeOrZero")) {
            return "数值范围不符合要求";
        }
        return chineseOrDefault(error.getDefaultMessage(), "字段值不符合要求");
    }

    private boolean hasConstraintCode(FieldError error, String constraintCode) {
        if (constraintCode.equals(error.getCode())) {
            return true;
        }
        String[] codes = error.getCodes();
        return codes != null && Arrays.stream(codes)
            .anyMatch(code -> code.equals(constraintCode) || code.startsWith(constraintCode + "."));
    }

    private String chineseOrDefault(String message, String fallback) {
        if (message == null || message.isBlank()) {
            return fallback;
        }
        String trimmed = message.trim();
        if (containsCjk(trimmed) && !looksLikeBeanValidationTemplate(trimmed)) {
            return trimmed;
        }
        return fallback;
    }

    private boolean containsCjk(String value) {
        return value.codePoints().anyMatch(codePoint ->
            (codePoint >= 0x4E00 && codePoint <= 0x9FFF)
                || (codePoint >= 0x3400 && codePoint <= 0x4DBF)
                || (codePoint >= 0xF900 && codePoint <= 0xFAFF)
        );
    }

    private boolean looksLikeBeanValidationTemplate(String value) {
        String lower = value.toLowerCase(Locale.ROOT);
        return lower.contains("must ")
            || lower.contains("size ")
            || lower.contains("length ")
            || lower.contains("between ")
            || lower.contains("not be ")
            || lower.contains("invalid");
    }
}
