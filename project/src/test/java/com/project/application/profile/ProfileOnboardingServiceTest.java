package com.project.application.profile;

import com.project.api.profile.dto.ProfileOnboardingRequest;
import com.project.api.profile.dto.UserProfileResponse;
import com.project.application.learningpath.PersonalizedLearningRefreshService;
import com.project.domain.profile.UserProfileCurrent;
import com.project.domain.profile.UserProfileCurrentRepository;
import com.project.domain.profile.UserProfileSnapshotRepository;
import com.project.domain.user.UserAccount;
import com.project.domain.user.UserAccountRepository;
import com.project.security.JwtAuthenticatedUser;
import org.junit.jupiter.api.Test;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class ProfileOnboardingServiceTest {

    @Test
    void duplicateOnboardingPayloadIsIdempotent() {
        UUID userId = UUID.fromString("50000000-0000-0000-0000-000000000101");
        JwtAuthenticatedUser currentUser = new JwtAuthenticatedUser(userId, "learner", "USER");
        ProfileOnboardingRequest request = new ProfileOnboardingRequest(
            "CS",
            "数据库基础",
            "掌握数据库索引优化",
            "项目驱动",
            "READING"
        );
        UserAccountRepository userRepository = mock(UserAccountRepository.class);
        UserProfileCurrentRepository currentRepository = mock(UserProfileCurrentRepository.class);
        UserProfileSnapshotRepository snapshotRepository = mock(UserProfileSnapshotRepository.class);
        UserProfileQueryService queryService = mock(UserProfileQueryService.class);
        PersonalizedLearningRefreshService refreshService = mock(PersonalizedLearningRefreshService.class);
        UserAccount userAccount = new UserAccount();
        userAccount.setMajorCode("CS");
        UserProfileCurrent current = new UserProfileCurrent();
        current.setUserId(userId);
        current.setProfileJson(profileJson());
        current.setSummaryText("数据库基础，目标：掌握数据库索引优化，学习偏好：项目驱动，资源偏好：READING");
        current.setUpdatedAt(OffsetDateTime.now());
        UserProfileResponse expected = new UserProfileResponse(userId, current.getProfileJson(), current.getSummaryText(), current.getUpdatedAt(), List.of());
        when(userRepository.findById(userId)).thenReturn(Optional.of(userAccount));
        when(currentRepository.findById(userId)).thenReturn(Optional.of(current));
        when(queryService.getCurrentProfile(currentUser, userId)).thenReturn(expected);

        ProfileOnboardingService service = new ProfileOnboardingService(
            userRepository,
            currentRepository,
            snapshotRepository,
            queryService,
            refreshService
        );

        UserProfileResponse response = service.complete(currentUser, request);

        assertThat(response).isSameAs(expected);
        verify(snapshotRepository, never()).save(org.mockito.ArgumentMatchers.any());
        verify(currentRepository, never()).save(org.mockito.ArgumentMatchers.any());
        verify(refreshService, never()).triggerInitialPlan(org.mockito.ArgumentMatchers.any(), org.mockito.ArgumentMatchers.any());
    }

    private Map<String, Object> profileJson() {
        return Map.of(
            "source", "onboarding",
            "majorCode", "CS",
            "knowledgeBase", "数据库基础",
            "learningGoal", "掌握数据库索引优化",
            "currentGoal", Map.of("shortTerm", "掌握数据库索引优化"),
            "preference", "项目驱动",
            "learningPreference", "项目驱动",
            "preferredResourceTypes", List.of("READING"),
            "resourcePreference", "READING",
            "onboardingCompleted", true
        );
    }
}
