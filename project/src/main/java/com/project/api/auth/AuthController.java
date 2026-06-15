package com.project.api.auth;

import com.project.api.auth.dto.AuthResponse;
import com.project.api.auth.dto.LoginRequest;
import com.project.api.auth.dto.RegisterRequest;
import com.project.api.auth.dto.UserView;
import com.project.api.common.dto.ApiMessageResponse;
import com.project.application.audit.AuditService;
import com.project.application.auth.AuthService;
import com.project.application.common.ApplicationException;
import com.project.security.AuthenticatedUserResolver;
import com.project.security.JwtAuthenticatedUser;
import com.project.security.JwtProvider;
import com.project.security.RevokedTokenStore;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * Java 控制平面暴露的认证 API。
 */
@RestController
@RequestMapping("/api/auth")
@Tag(name = "Authentication")
public class AuthController {

    private final AuthService authService;
    private final AuditService auditService;
    private final JwtProvider jwtProvider;
    private final RevokedTokenStore revokedTokenStore;

    public AuthController(
        AuthService authService,
        AuditService auditService,
        JwtProvider jwtProvider,
        RevokedTokenStore revokedTokenStore
    ) {
        this.authService = authService;
        this.auditService = auditService;
        this.jwtProvider = jwtProvider;
        this.revokedTokenStore = revokedTokenStore;
    }

    @PostMapping("/register")
    @Operation(summary = "Register a new user")
    public ResponseEntity<AuthResponse> register(@Valid @RequestBody RegisterRequest request) {
        return ResponseEntity.ok(authService.register(request));
    }

    @PostMapping("/login")
    @Operation(summary = "Login with loginId and password")
    public ResponseEntity<AuthResponse> login(@Valid @RequestBody LoginRequest request) {
        return ResponseEntity.ok(authService.login(request));
    }

    @PostMapping("/logout")
    @Operation(summary = "Logout current user")
    public ResponseEntity<ApiMessageResponse> logout(
        Authentication authentication,
        @RequestHeader(HttpHeaders.AUTHORIZATION) String authorization
    ) {
        JwtAuthenticatedUser principal = AuthenticatedUserResolver.require(authentication);
        String token = extractBearerToken(authorization);
        revokedTokenStore.revoke(token, jwtProvider.remainingTtl(token));
        auditService.log("AUTH", "INFO", "用户退出登录", principal.userId(), null, java.util.Map.of());
        return ResponseEntity.ok(new ApiMessageResponse("SUCCESS", "退出成功"));
    }

    @GetMapping("/me")
    @Operation(summary = "Get current authenticated user")
    public ResponseEntity<UserView> me(Authentication authentication) {
        JwtAuthenticatedUser principal = AuthenticatedUserResolver.require(authentication);
        return ResponseEntity.ok(authService.getCurrentUser(principal));
    }

    private String extractBearerToken(String authorization) {
        if (authorization == null || !authorization.startsWith("Bearer ")) {
            throw new ApplicationException("AUTH_REQUIRED", "请先登录", HttpStatus.UNAUTHORIZED);
        }
        return authorization.substring(7);
    }
}
