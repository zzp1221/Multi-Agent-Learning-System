package com.project.application.auth;

import com.project.api.auth.dto.AuthResponse;
import com.project.api.auth.dto.LoginRequest;
import com.project.api.auth.dto.RegisterRequest;
import com.project.api.auth.dto.UserView;
import com.project.application.audit.AuditService;
import com.project.application.common.ApplicationException;
import com.project.application.resource.ResourceSemanticWarmupService;
import com.project.domain.user.UserAccount;
import com.project.domain.user.UserAccountRepository;
import com.project.security.JwtAuthenticatedUser;
import com.project.security.JwtProvider;
import org.springframework.http.HttpStatus;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

/**
 * 拥有注册和登录用例的应用服务。
 *
 * <p>将凭证工作流保留在专用服务中，避免密码处理逻辑泄漏到控制器，
 * 并为未来的 RBAC、刷新令牌持久化或 SSO 集成创建清晰的接缝。</p>
 */
@Service
public class AuthService {

    private static final String DEFAULT_ROLE = "USER";

    private final UserAccountRepository userAccountRepository;
    private final PasswordEncoder passwordEncoder;
    private final JwtProvider jwtProvider;
    private final AuditService auditService;
    private final ResourceSemanticWarmupService resourceSemanticWarmupService;

    public AuthService(
        UserAccountRepository userAccountRepository,
        PasswordEncoder passwordEncoder,
        JwtProvider jwtProvider,
        AuditService auditService,
        ResourceSemanticWarmupService resourceSemanticWarmupService
    ) {
        this.userAccountRepository = userAccountRepository;
        this.passwordEncoder = passwordEncoder;
        this.jwtProvider = jwtProvider;
        this.auditService = auditService;
        this.resourceSemanticWarmupService = resourceSemanticWarmupService;
    }

    @Transactional
    public AuthResponse register(RegisterRequest request) {
        if (userAccountRepository.existsByLoginId(request.loginId())) {
            throw new ApplicationException("REGISTRATION_REJECTED", "注册信息无法使用", HttpStatus.BAD_REQUEST);
        }

        UserAccount userAccount = new UserAccount();
        userAccount.setLoginId(request.loginId().trim());
        userAccount.setPasswordHash(passwordEncoder.encode(request.password()));
        userAccount.setFullName(request.fullName().trim());
        userAccount.setMajorCode(request.majorCode() == null ? null : request.majorCode().trim());

        UserAccount savedUser = userAccountRepository.save(userAccount);
        auditService.log("AUTH", "INFO", "用户注册成功", savedUser.getId(), null, java.util.Map.of("loginId", savedUser.getLoginId()));
        return buildAuthResponse(savedUser);
    }

    @Transactional
    public AuthResponse login(LoginRequest request) {
        UserAccount userAccount = userAccountRepository.findByLoginId(request.loginId().trim())
            .orElseThrow(() -> new ApplicationException("INVALID_CREDENTIALS", "账号或密码错误", HttpStatus.UNAUTHORIZED));

        if (!passwordEncoder.matches(request.password(), userAccount.getPasswordHash())) {
            throw new ApplicationException("INVALID_CREDENTIALS", "账号或密码错误", HttpStatus.UNAUTHORIZED);
        }

        auditService.log("AUTH", "INFO", "用户登录成功", userAccount.getId(), null, java.util.Map.of("loginId", userAccount.getLoginId()));
        triggerResourceWarmupAfterCommit(userAccount.getId());
        return buildAuthResponse(userAccount);
    }

    @Transactional(readOnly = true)
    public UserView getCurrentUser(JwtAuthenticatedUser authenticatedUser) {
        UserAccount userAccount = userAccountRepository.findById(authenticatedUser.userId())
            .orElseThrow(() -> new ApplicationException("USER_NOT_FOUND", "用户不存在", HttpStatus.NOT_FOUND));

        return toUserView(userAccount);
    }

    private AuthResponse buildAuthResponse(UserAccount userAccount) {
        String token = jwtProvider.generateAccessToken(
            userAccount.getId(),
            userAccount.getLoginId(),
            DEFAULT_ROLE
        );
        return new AuthResponse(token, toUserView(userAccount));
    }

    private UserView toUserView(UserAccount userAccount) {
        return new UserView(
            userAccount.getId(),
            userAccount.getLoginId(),
            userAccount.getFullName(),
            userAccount.getMajorCode()
        );
    }

    private void triggerResourceWarmupAfterCommit(java.util.UUID userId) {
        Runnable trigger = () -> resourceSemanticWarmupService.submitCurrentStageWarmup(userId);
        if (!TransactionSynchronizationManager.isSynchronizationActive()) {
            trigger.run();
            return;
        }
        TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
            @Override
            public void afterCommit() {
                trigger.run();
            }
        });
    }
}
