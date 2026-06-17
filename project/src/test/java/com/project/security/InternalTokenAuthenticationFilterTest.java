package com.project.security;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.project.config.AppProperties;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;
import org.springframework.security.core.context.SecurityContextHolder;

import java.util.concurrent.atomic.AtomicBoolean;

import static org.assertj.core.api.Assertions.assertThat;

class InternalTokenAuthenticationFilterTest {

    @Test
    void rejectsInternalRequestWithoutToken() throws Exception {
        InternalTokenAuthenticationFilter filter = filterWithToken("expected-token");
        MockHttpServletRequest request = new MockHttpServletRequest("POST", "/internal/ping");
        MockHttpServletResponse response = new MockHttpServletResponse();
        AtomicBoolean delegated = new AtomicBoolean(false);

        filter.doFilter(request, response, (servletRequest, servletResponse) -> delegated.set(true));

        assertThat(response.getStatus()).isEqualTo(401);
        assertThat(response.getContentAsString()).contains("INVALID_INTERNAL_TOKEN");
        assertThat(delegated).isFalse();
    }

    @Test
    void rejectsInternalRequestWhenTokenIsNotConfigured() throws Exception {
        InternalTokenAuthenticationFilter filter = filterWithToken("");
        MockHttpServletRequest request = new MockHttpServletRequest("POST", "/internal/ping");
        request.addHeader(InternalTokenVerifier.INTERNAL_TOKEN_HEADER, "supplied-token");
        MockHttpServletResponse response = new MockHttpServletResponse();

        filter.doFilter(request, response, (servletRequest, servletResponse) -> {
            throw new AssertionError("request should not reach controller");
        });

        assertThat(response.getStatus()).isEqualTo(503);
        assertThat(response.getContentAsString()).contains("INTERNAL_TOKEN_NOT_CONFIGURED");
    }

    @Test
    void acceptsValidInternalToken() throws Exception {
        InternalTokenAuthenticationFilter filter = filterWithToken("expected-token");
        MockHttpServletRequest request = new MockHttpServletRequest("POST", "/internal/ping");
        request.addHeader(InternalTokenVerifier.INTERNAL_TOKEN_HEADER, "expected-token");
        MockHttpServletResponse response = new MockHttpServletResponse();
        AtomicBoolean delegated = new AtomicBoolean(false);

        filter.doFilter(request, response, (servletRequest, servletResponse) -> delegated.set(true));

        assertThat(delegated).isTrue();
        assertThat(response.getStatus()).isEqualTo(200);
        assertThat(SecurityContextHolder.getContext().getAuthentication()).isNull();
    }

    private InternalTokenAuthenticationFilter filterWithToken(String token) {
        AppProperties properties = new AppProperties();
        properties.getPythonAgent().setInternalToken(token);
        return new InternalTokenAuthenticationFilter(
            new InternalTokenVerifier(new InternalTokenProvider(properties)),
            new ObjectMapper()
        );
    }
}
