package com.project.api.profile;

import com.project.api.profile.dto.KnowledgeGraphResponse;
import com.project.api.profile.dto.ProfileOnboardingRequest;
import com.project.api.profile.dto.UserProfileResponse;
import com.project.api.profile.dto.UserProfileAnalyticsResponse;
import com.project.application.profile.LearnerKnowledgeGraphService;
import com.project.application.profile.ProfileOnboardingService;
import com.project.application.profile.UserProfileAnalyticsService;
import com.project.application.profile.UserProfileQueryService;
import com.project.security.AuthenticatedUserResolver;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.UUID;

/**
 * 控制平面拥有的用户相关只读端点。
 */
@RestController
@RequestMapping("/api/users")
@Tag(name = "Users")
public class UserController {

    private final UserProfileQueryService userProfileQueryService;
    private final UserProfileAnalyticsService userProfileAnalyticsService;
    private final LearnerKnowledgeGraphService learnerKnowledgeGraphService;
    private final ProfileOnboardingService profileOnboardingService;

    public UserController(
        UserProfileQueryService userProfileQueryService,
        UserProfileAnalyticsService userProfileAnalyticsService,
        LearnerKnowledgeGraphService learnerKnowledgeGraphService,
        ProfileOnboardingService profileOnboardingService
    ) {
        this.userProfileQueryService = userProfileQueryService;
        this.userProfileAnalyticsService = userProfileAnalyticsService;
        this.learnerKnowledgeGraphService = learnerKnowledgeGraphService;
        this.profileOnboardingService = profileOnboardingService;
    }

    @GetMapping("/{userId}/profile/current")
    @Operation(summary = "Get the current profile of a user")
    public ResponseEntity<UserProfileResponse> getCurrentProfile(
        Authentication authentication,
        @PathVariable UUID userId
    ) {
        return ResponseEntity.ok(
            userProfileQueryService.getCurrentProfile(AuthenticatedUserResolver.require(authentication), userId)
        );
    }

    @GetMapping("/{userId}/profile/analytics")
    @Operation(summary = "Get real-data analytics for a user profile")
    public ResponseEntity<UserProfileAnalyticsResponse> getProfileAnalytics(
        Authentication authentication,
        @PathVariable UUID userId,
        @RequestParam(defaultValue = "30") Integer days
    ) {
        return ResponseEntity.ok(
            userProfileAnalyticsService.getAnalytics(AuthenticatedUserResolver.require(authentication), userId, days)
        );
    }

    @GetMapping("/{userId}/knowledge-graph")
    @Operation(summary = "Get the learner knowledge graph for a user")
    public ResponseEntity<KnowledgeGraphResponse> getKnowledgeGraph(
        Authentication authentication,
        @PathVariable UUID userId
    ) {
        return ResponseEntity.ok(
            learnerKnowledgeGraphService.getGraph(AuthenticatedUserResolver.require(authentication), userId)
        );
    }

    @PostMapping("/me/profile/onboarding")
    @Operation(summary = "Complete required onboarding profile for the current user")
    public ResponseEntity<UserProfileResponse> completeOnboardingProfile(
        Authentication authentication,
        @Valid @RequestBody ProfileOnboardingRequest request
    ) {
        return ResponseEntity.ok(
            profileOnboardingService.complete(AuthenticatedUserResolver.require(authentication), request)
        );
    }
}
