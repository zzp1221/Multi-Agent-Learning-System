package com.project.api.study.dto;

import com.project.api.mistake.dto.MistakeRecordResponse;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;
import java.util.UUID;

public record MistakeTrainingCampResponse(
    UUID userId,
    OffsetDateTime generatedAt,
    TrainingCampSummary summary,
    List<MistakeCampGroup> camps
) {
    public record TrainingCampSummary(
        int campCount,
        int activeMistakeCount,
        int dueMistakeCount,
        int masteredMistakeCount,
        String topFocus
    ) {}

    public record MistakeCampGroup(
        String campId,
        String title,
        String mistakeType,
        String knowledgeTag,
        String explanation,
        int mistakeCount,
        int dueCount,
        int masteredCount,
        int totalWrongCount,
        int totalReviewCount,
        double masteryChange,
        OffsetDateTime nextReviewAt,
        List<MistakeRecordResponse> representativeMistakes,
        List<TrainingMicroPractice> microPractices,
        Map<String, Object> practiceContext
    ) {}

    public record TrainingMicroPractice(
        String id,
        String title,
        String description,
        String difficulty,
        List<String> knowledgeTags,
        String prompt
    ) {}
}
