package com.project.api.common;

import com.project.api.common.dto.ApiMessageResponse;
import org.junit.jupiter.api.Test;
import org.springframework.http.ResponseEntity;

import java.io.IOException;

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
}
