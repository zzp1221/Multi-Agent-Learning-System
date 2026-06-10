package com.project.api.mistake;

import com.project.api.mistake.dto.CreateReviewSessionRequest;
import com.project.api.mistake.dto.MistakeListResponse;
import com.project.api.mistake.dto.MistakeRecordResponse;
import com.project.api.mistake.dto.MistakeReviewSessionResponse;
import com.project.api.mistake.dto.MistakeUpdateRequest;
import com.project.api.mistake.dto.SubmitReviewSessionRequest;
import com.project.api.study.dto.MistakeTrainingCampResponse;
import com.project.application.mistake.MistakeBookService;
import com.project.application.study.StudyWorkbenchService;
import com.project.security.AuthenticatedUserResolver;
import com.project.security.JwtAuthenticatedUser;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.UUID;

/**
 * 错题本 API，支持列表查询、标注和复习排期。
 */
@RestController
@RequestMapping("/api/mistakes")
@Tag(name = "Mistake Book")
public class MistakeBookController {

    private final MistakeBookService mistakeBookService;
    private final StudyWorkbenchService studyWorkbenchService;

    public MistakeBookController(
        MistakeBookService mistakeBookService,
        StudyWorkbenchService studyWorkbenchService
    ) {
        this.mistakeBookService = mistakeBookService;
        this.studyWorkbenchService = studyWorkbenchService;
    }

    @GetMapping
    @Operation(summary = "List mistakes for the current learner")
    public ResponseEntity<MistakeListResponse> listMistakes(
        Authentication authentication,
        @RequestParam(name = "status", required = false) String status,
        @RequestParam(name = "knowledgeTag", required = false) String knowledgeTag,
        @RequestParam(name = "difficulty", required = false) String difficulty,
        @RequestParam(name = "page", required = false) Integer page,
        @RequestParam(name = "size", required = false) Integer size
    ) {
        JwtAuthenticatedUser principal = AuthenticatedUserResolver.require(authentication);
        return ResponseEntity.ok(
            mistakeBookService.listMistakes(
                principal.userId(),
                status,
                knowledgeTag,
                difficulty,
                page,
                size
            )
        );
    }

    @GetMapping("/training-camps")
    @Operation(summary = "Get mistake cause training camps for the current learner")
    public ResponseEntity<MistakeTrainingCampResponse> trainingCamps(Authentication authentication) {
        JwtAuthenticatedUser principal = AuthenticatedUserResolver.require(authentication);
        return ResponseEntity.ok(studyWorkbenchService.mistakeTrainingCamps(principal));
    }

    @PatchMapping("/{id}")
    @Operation(summary = "Update user-editable mistake fields")
    public ResponseEntity<MistakeRecordResponse> updateMistake(
        Authentication authentication,
        @PathVariable UUID id,
        @RequestBody MistakeUpdateRequest request
    ) {
        JwtAuthenticatedUser principal = AuthenticatedUserResolver.require(authentication);
        return ResponseEntity.ok(mistakeBookService.updateMistake(principal.userId(), id, request));
    }

    @PostMapping("/review")
    @Operation(summary = "Create a due mistake review session")
    public ResponseEntity<MistakeReviewSessionResponse> createReviewSession(
        Authentication authentication,
        @RequestBody(required = false) CreateReviewSessionRequest request
    ) {
        JwtAuthenticatedUser principal = AuthenticatedUserResolver.require(authentication);
        return ResponseEntity.ok(mistakeBookService.createReviewSession(principal.userId(), request));
    }

    @GetMapping("/review/{sessionId}")
    @Operation(summary = "Get a mistake review session")
    public ResponseEntity<MistakeReviewSessionResponse> getReviewSession(
        Authentication authentication,
        @PathVariable UUID sessionId
    ) {
        JwtAuthenticatedUser principal = AuthenticatedUserResolver.require(authentication);
        return ResponseEntity.ok(mistakeBookService.getReviewSession(principal.userId(), sessionId));
    }

    @PostMapping("/review/{sessionId}/submit")
    @Operation(summary = "Submit review quality scores and update spaced repetition schedule")
    public ResponseEntity<MistakeReviewSessionResponse> submitReviewSession(
        Authentication authentication,
        @PathVariable UUID sessionId,
        @RequestBody SubmitReviewSessionRequest request
    ) {
        JwtAuthenticatedUser principal = AuthenticatedUserResolver.require(authentication);
        return ResponseEntity.ok(mistakeBookService.submitReviewSession(principal.userId(), sessionId, request));
    }
}
