package com.project.api.study;

import com.project.api.study.dto.DailyStudyWorkbenchResponse;
import com.project.application.study.StudyWorkbenchService;
import com.project.security.AuthenticatedUserResolver;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/study-workbench")
@Tag(name = "Study Workbench")
public class StudyWorkbenchController {

    private final StudyWorkbenchService studyWorkbenchService;

    public StudyWorkbenchController(StudyWorkbenchService studyWorkbenchService) {
        this.studyWorkbenchService = studyWorkbenchService;
    }

    @GetMapping("/daily")
    @Operation(summary = "Get the current learner daily study workbench")
    public ResponseEntity<DailyStudyWorkbenchResponse> daily(Authentication authentication) {
        return ResponseEntity.ok(studyWorkbenchService.daily(AuthenticatedUserResolver.require(authentication)));
    }

    @PostMapping("/daily/refresh")
    @Operation(summary = "Refresh the daily study workbench snapshot")
    public ResponseEntity<DailyStudyWorkbenchResponse> refreshDaily(Authentication authentication) {
        return ResponseEntity.ok(studyWorkbenchService.daily(AuthenticatedUserResolver.require(authentication)));
    }
}
