package com.project.application.profile;

import com.project.api.profile.dto.ProfileOnboardingRequest;
import com.project.api.profile.dto.UserProfileResponse;
import com.project.application.common.ApplicationException;
import com.project.application.learningpath.PersonalizedLearningRefreshService;
import com.project.domain.profile.UserProfileCurrent;
import com.project.domain.profile.UserProfileCurrentRepository;
import com.project.domain.profile.UserProfileSnapshot;
import com.project.domain.profile.UserProfileSnapshotRepository;
import com.project.domain.user.UserAccount;
import com.project.domain.user.UserAccountRepository;
import com.project.security.JwtAuthenticatedUser;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.math.BigDecimal;
import java.time.OffsetDateTime;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;

/**
 * 保存新用户首次登录后的基础画像，并触发首版学习路径规划。
 */
@Service
public class ProfileOnboardingService {

    private final UserAccountRepository userAccountRepository;
    private final UserProfileCurrentRepository currentRepository;
    private final UserProfileSnapshotRepository snapshotRepository;
    private final UserProfileQueryService queryService;
    private final PersonalizedLearningRefreshService learningRefreshService;

    public ProfileOnboardingService(
        UserAccountRepository userAccountRepository,
        UserProfileCurrentRepository currentRepository,
        UserProfileSnapshotRepository snapshotRepository,
        UserProfileQueryService queryService,
        PersonalizedLearningRefreshService learningRefreshService
    ) {
        this.userAccountRepository = userAccountRepository;
        this.currentRepository = currentRepository;
        this.snapshotRepository = snapshotRepository;
        this.queryService = queryService;
        this.learningRefreshService = learningRefreshService;
    }

    @Transactional
    public UserProfileResponse complete(JwtAuthenticatedUser currentUser, ProfileOnboardingRequest request) {
        UserAccount user = userAccountRepository.findById(currentUser.userId())
            .orElseThrow(() -> new ApplicationException("USER_NOT_FOUND", "用户不存在", HttpStatus.NOT_FOUND));

        String majorCode = request.majorCode().trim();
        user.setMajorCode(majorCode);

        OffsetDateTime now = OffsetDateTime.now();
        Map<String, Object> profileJson = buildProfileJson(request);
        String summary = buildSummary(request);
        UserProfileCurrent existingCurrent = currentRepository.findById(currentUser.userId()).orElse(null);
        if (isSameOnboardingProfile(existingCurrent, profileJson, summary)) {
            return queryService.getCurrentProfile(currentUser, currentUser.userId());
        }

        UserProfileSnapshot snapshot = new UserProfileSnapshot();
        snapshot.setId(UUID.randomUUID());
        snapshot.setUserId(currentUser.userId());
        snapshot.setVersion(nextVersion(currentUser.userId()));
        snapshot.setProfileJson(profileJson);
        snapshot.setSummaryText(summary);
        snapshot.setConfidence(BigDecimal.valueOf(0.68));
        snapshot.setCreatedAt(now);
        snapshotRepository.save(snapshot);

        UserProfileCurrent current = existingCurrent == null ? new UserProfileCurrent() : existingCurrent;
        current.setUserId(currentUser.userId());
        current.setActiveSnapshotId(snapshot.getId());
        current.setProfileJson(profileJson);
        current.setSummaryText(summary);
        current.setUpdatedAt(now);
        currentRepository.save(current);

        triggerInitialLearningPathAfterCommit(currentUser.userId(), majorCode);
        return queryService.getCurrentProfile(currentUser, currentUser.userId());
    }

    private boolean isSameOnboardingProfile(
        UserProfileCurrent current,
        Map<String, Object> profileJson,
        String summary
    ) {
        return current != null
            && profileJson.equals(current.getProfileJson())
            && summary.equals(current.getSummaryText());
    }

    private Map<String, Object> buildProfileJson(ProfileOnboardingRequest request) {
        Map<String, Object> profile = new LinkedHashMap<>();
        profile.put("source", "onboarding");
        profile.put("majorCode", request.majorCode().trim());
        profile.put("knowledgeBase", request.knowledgeBase().trim());
        profile.put("learningGoal", request.learningGoal().trim());
        profile.put("currentGoal", Map.of("shortTerm", request.learningGoal().trim()));
        profile.put("preference", request.learningPreference().trim());
        profile.put("learningPreference", request.learningPreference().trim());
        profile.put("preferredResourceTypes", java.util.List.of(request.resourcePreference().trim()));
        profile.put("resourcePreference", request.resourcePreference().trim());
        profile.put("onboardingCompleted", true);
        return profile;
    }

    private String buildSummary(ProfileOnboardingRequest request) {
        return String.format(
            "%s，目标：%s，学习偏好：%s，资源偏好：%s",
            request.knowledgeBase().trim(),
            request.learningGoal().trim(),
            request.learningPreference().trim(),
            request.resourcePreference().trim()
        );
    }

    private int nextVersion(UUID userId) {
        return snapshotRepository.findTop8ByUserIdOrderByVersionDesc(userId).stream()
            .map(UserProfileSnapshot::getVersion)
            .filter(version -> version != null)
            .max(Integer::compareTo)
            .orElse(0) + 1;
    }

    private void triggerInitialLearningPathAfterCommit(UUID userId, String majorCode) {
        Runnable trigger = () -> learningRefreshService.triggerInitialPlan(userId, majorCode);
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
