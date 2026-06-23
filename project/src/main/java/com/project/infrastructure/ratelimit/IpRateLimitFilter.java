package com.project.infrastructure.ratelimit;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.project.api.common.dto.ApiMessageResponse;
import com.project.application.audit.AuditService;
import com.project.application.ratelimit.RateLimiter;
import com.project.config.AppProperties;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.net.InetAddress;
import java.net.UnknownHostException;
import java.time.Duration;
import java.util.Map;

/**
 * 在 JWT 解析之前进行 IP 级限流，减少认证滥用。
 */
@Component
public class IpRateLimitFilter extends OncePerRequestFilter {

    static final Duration WINDOW = Duration.ofMinutes(1);
    private static final Duration REGISTER_WINDOW = Duration.ofHours(1);

    private final AppProperties appProperties;
    private final RateLimiter rateLimiter;
    private final ObjectMapper objectMapper;
    private final AuditService auditService;

    public IpRateLimitFilter(
        AppProperties appProperties,
        RateLimiter rateLimiter,
        ObjectMapper objectMapper,
        AuditService auditService
    ) {
        this.appProperties = appProperties;
        this.rateLimiter = rateLimiter;
        this.objectMapper = objectMapper;
        this.auditService = auditService;
    }

    @Override
    protected void doFilterInternal(
        HttpServletRequest request,
        HttpServletResponse response,
        FilterChain filterChain
    ) throws ServletException, IOException {
        if (!appProperties.getRateLimit().isEnabled() || !request.getRequestURI().startsWith("/api/")) {
            filterChain.doFilter(request, response);
            return;
        }

        String ip = resolveClientIp(request);
        String requestUri = request.getRequestURI();
        if (isPost(request) && requestUri.equals("/api/auth/login")
            && !rateLimiter.allow("auth:login:ip:" + ip, appProperties.getRateLimit().getLoginRequestsPerMinute(), WINDOW)) {
            auditService.log("SAFETY", "MEDIUM", "登录限流命中", null, null, Map.of("ip", ip, "path", requestUri));
            writeTooManyRequests(response);
            return;
        }
        if (isPost(request) && requestUri.equals("/api/auth/register")
            && !rateLimiter.allow("auth:register:ip:" + ip, appProperties.getRateLimit().getRegisterRequestsPerHour(), REGISTER_WINDOW)) {
            auditService.log("SAFETY", "MEDIUM", "注册限流命中", null, null, Map.of("ip", ip, "path", requestUri));
            writeTooManyRequests(response);
            return;
        }
        if (!rateLimiter.allow("ip:" + ip, appProperties.getRateLimit().getIpRequestsPerMinute(), WINDOW)) {
            auditService.log("SAFETY", "MEDIUM", "IP 限流命中", null, null, Map.of("ip", ip, "path", request.getRequestURI()));
            writeTooManyRequests(response);
            return;
        }

        filterChain.doFilter(request, response);
    }

    private boolean isPost(HttpServletRequest request) {
        return "POST".equalsIgnoreCase(request.getMethod());
    }

    private void writeTooManyRequests(HttpServletResponse response) throws IOException {
        response.setStatus(HttpStatus.TOO_MANY_REQUESTS.value());
        response.setCharacterEncoding("UTF-8");
        response.setContentType(MediaType.APPLICATION_JSON_VALUE);
        objectMapper.writeValue(response.getWriter(), new ApiMessageResponse("TOO_MANY_REQUESTS", "请求过于频繁，请稍后重试"));
    }

    private String resolveClientIp(HttpServletRequest request) {
        String remoteAddr = request.getRemoteAddr();
        if (!isTrustedProxy(remoteAddr)) {
            return remoteAddr;
        }

        String realIp = request.getHeader("X-Real-IP");
        if (realIp != null && !realIp.isBlank()) {
            return realIp.trim();
        }

        String forwarded = request.getHeader("X-Forwarded-For");
        if (forwarded != null && !forwarded.isBlank()) {
            return forwarded.split(",")[0].trim();
        }

        return remoteAddr;
    }

    private boolean isTrustedProxy(String address) {
        try {
            InetAddress inetAddress = InetAddress.getByName(address);
            return inetAddress.isLoopbackAddress()
                || inetAddress.isSiteLocalAddress()
                || inetAddress.isLinkLocalAddress()
                || inetAddress.isAnyLocalAddress();
        } catch (UnknownHostException ex) {
            return false;
        }
    }
}
