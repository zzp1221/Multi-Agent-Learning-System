package com.project.api.common;

import com.project.api.common.dto.ApiMessageResponse;
import org.junit.jupiter.api.Test;
import org.springframework.core.MethodParameter;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.validation.BeanPropertyBindingResult;
import org.springframework.validation.FieldError;
import org.springframework.web.HttpRequestMethodNotSupportedException;
import org.springframework.web.bind.MissingServletRequestParameterException;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.multipart.MultipartException;
import org.springframework.web.multipart.support.MissingServletRequestPartException;
import org.springframework.web.servlet.NoHandlerFoundException;
import org.springframework.web.servlet.resource.NoResourceFoundException;

import java.io.IOException;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

class GlobalExceptionHandlerTest {

    private final GlobalExceptionHandler handler = new GlobalExceptionHandler();

    @Test
    void handlesBrokenPipeAsClientDisconnect() {
        ResponseEntity<ApiMessageResponse> response = handler.handleIOException(new IOException("Broken pipe"));

        assertThat(response.getStatusCode().value()).isEqualTo(204);
        assertThat(response.getBody()).isNull();
    }

    @Test
    void handlesUnrelatedIoExceptionAsServerError() {
        ResponseEntity<ApiMessageResponse> response = handler.handleIOException(new IOException("disk write failed"));

        assertThat(response.getStatusCode().value()).isEqualTo(500);
        assertThat(response.getBody()).isNotNull();
        assertThat(response.getBody().code()).isEqualTo("INTERNAL_ERROR");
    }

    @Test
    void handlesUnreadableJsonAsBadRequest() {
        ResponseEntity<ApiMessageResponse> response = handler.handleMessageNotReadableException(
            new HttpMessageNotReadableException("Unsupported serviceType: BAD_TYPE_99")
        );

        assertThat(response.getStatusCode().value()).isEqualTo(400);
        assertThat(response.getBody()).isNotNull();
        assertThat(response.getBody().code()).isEqualTo("INVALID_ARGUMENT");
    }

    @Test
    void validationErrorsReturnStableChineseSummaryWithoutLeakingFieldCount() throws Exception {
        BeanPropertyBindingResult bindingResult = new BeanPropertyBindingResult(new Object(), "request");
        bindingResult.addError(fieldError("loginId", "NotBlank", "must not be blank"));
        bindingResult.addError(fieldError("password", "NotBlank", "must not be blank"));
        bindingResult.addError(fieldError("fullName", "NotBlank", "must not be blank"));
        bindingResult.addError(fieldError("password", "Size", "size must be between 8 and 128"));

        ResponseEntity<ApiMessageResponse> response = handler.handleValidationException(
            new MethodArgumentNotValidException(validationProbeParameter(), bindingResult)
        );

        assertThat(response.getStatusCode().value()).isEqualTo(400);
        assertThat(response.getBody()).isNotNull();
        assertThat(response.getBody().code()).isEqualTo("INVALID_ARGUMENT");
        assertThat(response.getBody().message())
            .isEqualTo("请求参数校验失败：必填项不能为空；字段长度不符合要求")
            .doesNotContain("must not", "size must", "loginId", "password", "fullName");
    }

    @Test
    void validationErrorsKeepExplicitChineseDomainMessage() throws Exception {
        BeanPropertyBindingResult bindingResult = new BeanPropertyBindingResult(new Object(), "request");
        bindingResult.addError(fieldError("password", "Pattern", "密码至少 8 位，且需同时包含字母和数字"));

        ResponseEntity<ApiMessageResponse> response = handler.handleValidationException(
            new MethodArgumentNotValidException(validationProbeParameter(), bindingResult)
        );

        assertThat(response.getStatusCode().value()).isEqualTo(400);
        assertThat(response.getBody()).isNotNull();
        assertThat(response.getBody().message()).isEqualTo("请求参数校验失败：密码至少 8 位，且需同时包含字母和数字");
    }

    @Test
    void handlesMissingRequestParameterAsBadRequest() {
        ResponseEntity<ApiMessageResponse> response = handler.handleBadRequestArgument(
            new MissingServletRequestParameterException("query", "String")
        );

        assertThat(response.getStatusCode().value()).isEqualTo(400);
        assertThat(response.getBody()).isNotNull();
        assertThat(response.getBody().code()).isEqualTo("INVALID_ARGUMENT");
    }

    @Test
    void handlesMissingMultipartPartAsBadRequest() {
        ResponseEntity<ApiMessageResponse> response = handler.handleBadRequestArgument(
            new MissingServletRequestPartException("file")
        );

        assertThat(response.getStatusCode().value()).isEqualTo(400);
        assertThat(response.getBody()).isNotNull();
        assertThat(response.getBody().code()).isEqualTo("INVALID_ARGUMENT");
    }

    @Test
    void handlesMalformedMultipartRequestAsBadRequest() {
        ResponseEntity<ApiMessageResponse> response = handler.handleBadRequestArgument(
            new MultipartException("Current request is not a multipart request")
        );

        assertThat(response.getStatusCode().value()).isEqualTo(400);
        assertThat(response.getBody()).isNotNull();
        assertThat(response.getBody().code()).isEqualTo("INVALID_ARGUMENT");
    }

    @Test
    void handlesMissingRouteAsNotFound() {
        ResponseEntity<ApiMessageResponse> response = handler.handleNotFound(
            new NoHandlerFoundException("GET", "/api/missing", null)
        );

        assertThat(response.getStatusCode().value()).isEqualTo(404);
        assertThat(response.getBody()).isNotNull();
        assertThat(response.getBody().code()).isEqualTo("NOT_FOUND");
    }

    @Test
    void handlesMissingStaticResourceAsNotFound() {
        ResponseEntity<ApiMessageResponse> response = handler.handleNotFound(
            new NoResourceFoundException(null, "/api/missing")
        );

        assertThat(response.getStatusCode().value()).isEqualTo(404);
        assertThat(response.getBody()).isNotNull();
        assertThat(response.getBody().code()).isEqualTo("NOT_FOUND");
    }

    @Test
    void handlesUnsupportedMethodAsMethodNotAllowed() {
        ResponseEntity<ApiMessageResponse> response = handler.handleMethodNotSupported(
            new HttpRequestMethodNotSupportedException("POST", List.of("GET"))
        );

        assertThat(response.getStatusCode().value()).isEqualTo(405);
        assertThat(response.getBody()).isNotNull();
        assertThat(response.getBody().code()).isEqualTo("METHOD_NOT_ALLOWED");
    }

    private static FieldError fieldError(String field, String constraint, String message) {
        return new FieldError(
            "request",
            field,
            null,
            false,
            new String[]{constraint + ".request." + field, constraint + "." + field, constraint},
            null,
            message
        );
    }

    private static MethodParameter validationProbeParameter() throws NoSuchMethodException {
        return new MethodParameter(
            GlobalExceptionHandlerTest.class.getDeclaredMethod("validationProbe", Object.class),
            0
        );
    }

    @SuppressWarnings("unused")
    private void validationProbe(Object request) {
    }
}
