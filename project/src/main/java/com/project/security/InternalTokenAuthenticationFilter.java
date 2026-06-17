package com.project.security;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.project.api.common.dto.ApiMessageResponse;
import com.project.application.common.ApplicationException;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.http.MediaType;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.List;

@Component
public class InternalTokenAuthenticationFilter extends OncePerRequestFilter {

    private final InternalTokenVerifier internalTokenVerifier;
    private final ObjectMapper objectMapper;

    public InternalTokenAuthenticationFilter(
        InternalTokenVerifier internalTokenVerifier,
        ObjectMapper objectMapper
    ) {
        this.internalTokenVerifier = internalTokenVerifier;
        this.objectMapper = objectMapper;
    }

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        String path = request.getRequestURI();
        return path == null || !path.startsWith("/internal/");
    }

    @Override
    protected void doFilterInternal(
        HttpServletRequest request,
        HttpServletResponse response,
        FilterChain filterChain
    ) throws ServletException, IOException {
        try {
            internalTokenVerifier.requireValid(request.getHeader(InternalTokenVerifier.INTERNAL_TOKEN_HEADER));
            SecurityContextHolder.getContext().setAuthentication(new UsernamePasswordAuthenticationToken(
                "internal-service",
                null,
                List.of(new SimpleGrantedAuthority("INTERNAL_SERVICE"))
            ));
            filterChain.doFilter(request, response);
        } catch (ApplicationException ex) {
            SecurityContextHolder.clearContext();
            writeError(response, ex);
        } finally {
            SecurityContextHolder.clearContext();
        }
    }

    private void writeError(HttpServletResponse response, ApplicationException ex) throws IOException {
        if (response.isCommitted()) {
            return;
        }
        response.setStatus(ex.getStatus().value());
        response.setCharacterEncoding(StandardCharsets.UTF_8.name());
        response.setContentType(MediaType.APPLICATION_JSON_VALUE);
        objectMapper.writeValue(response.getWriter(), new ApiMessageResponse(ex.getCode(), ex.getMessage()));
    }
}
