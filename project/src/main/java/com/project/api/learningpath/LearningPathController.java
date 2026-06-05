package com.project.api.learningpath;

import com.project.api.learningpath.dto.LearningPathAdjustRequest;
import com.project.api.learningpath.dto.LearningPathCurrentResponse;
import com.project.api.smartengine.dto.SubmitTaskResponse;
import com.project.application.learningpath.LearningPathQueryService;
import com.project.application.learningpath.PersonalizedLearningRefreshService;
import com.project.domain.task.SmartEngineTask;
import com.project.security.AuthenticatedUserResolver;
import com.project.security.JwtAuthenticatedUser;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 个性化学习路径的当前计划读取与手动调整入口。
 */
@RestController
@RequestMapping("/api/learning-path")
@Tag(name = "Learning Path")
public class LearningPathController {

    private final LearningPathQueryService queryService;
    private final PersonalizedLearningRefreshService refreshService;

    public LearningPathController(
        LearningPathQueryService queryService,
        PersonalizedLearningRefreshService refreshService
    ) {
        this.queryService = queryService;
        this.refreshService = refreshService;
    }

    @GetMapping("/current")
    @Operation(summary = "Get current personalized learning path")
    public ResponseEntity<LearningPathCurrentResponse> current(Authentication authentication) {
        JwtAuthenticatedUser principal = AuthenticatedUserResolver.require(authentication);
        return ResponseEntity.ok(queryService.getCurrent(principal.userId()));
    }

    @PostMapping("/adjust")
    @Operation(summary = "Trigger manual personalized learning path adjustment")
    public ResponseEntity<SubmitTaskResponse> adjust(
        Authentication authentication,
        @Valid @RequestBody(required = false) LearningPathAdjustRequest request
    ) {
        JwtAuthenticatedUser principal = AuthenticatedUserResolver.require(authentication);
        SmartEngineTask task = refreshService.triggerManualAdjustment(
            principal.userId(),
            request == null ? "" : request.adjustmentIntent()
        );
        return ResponseEntity.ok(new SubmitTaskResponse(task.getId(), task.getTraceId(), task.getTaskStatus()));
    }

    @PostMapping("/resources/refresh")
    @Operation(summary = "Refresh recommended resources for the current personalized learning path")
    public ResponseEntity<SubmitTaskResponse> refreshResources(
        Authentication authentication,
        @Valid @RequestBody(required = false) LearningPathAdjustRequest request
    ) {
        JwtAuthenticatedUser principal = AuthenticatedUserResolver.require(authentication);
        LearningPathCurrentResponse current = queryService.getCurrent(principal.userId());
        SmartEngineTask task = refreshService.triggerResourceRecommendationRefresh(
            principal.userId(),
            request == null ? "" : request.adjustmentIntent(),
            current.learningPath(),
            current.pushedResources()
        );
        return ResponseEntity.ok(new SubmitTaskResponse(task.getId(), task.getTraceId(), task.getTaskStatus()));
    }
}
