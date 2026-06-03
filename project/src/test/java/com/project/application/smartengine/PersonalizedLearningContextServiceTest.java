package com.project.application.smartengine;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.project.domain.profile.UserProfileCurrent;
import com.project.domain.profile.UserProfileCurrentRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.jdbc.core.namedparam.SqlParameterSource;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class PersonalizedLearningContextServiceTest {

    private final UUID userId = UUID.fromString("20000000-0000-0000-0000-000000000001");
    private NamedParameterJdbcTemplate jdbcTemplate;
    private UserProfileCurrentRepository profileRepository;
    private PersonalizedLearningContextService service;

    @BeforeEach
    void setUp() {
        jdbcTemplate = mock(NamedParameterJdbcTemplate.class);
        profileRepository = mock(UserProfileCurrentRepository.class);
        service = new PersonalizedLearningContextService(jdbcTemplate, profileRepository, new ObjectMapper());
    }

    @Test
    void buildsAutomaticContextFromAvailableLearnerSignals() {
        OffsetDateTime now = OffsetDateTime.parse("2026-06-03T10:15:30+08:00");
        when(profileRepository.findById(userId)).thenReturn(Optional.of(profile(now)));
        stubQueries(
            List.of(
                Map.of(
                    "canonical_key", "transaction_isolation",
                    "topic", "事务隔离",
                    "mastery_score", 0.42,
                    "node_status", "WEAK",
                    "source", "PRACTICE",
                    "updated_at", now
                )
            ),
            List.of(Map.of(
                "submission_count", 4,
                "correct_count", 3,
                "incorrect_count", 1,
                "last_submitted_at", now.minusHours(1)
            )),
            List.of(Map.of("service_type", "PRACTICE_JUDGE", "task_status", "COMPLETED", "task_count", 2)),
            List.of(Map.of(
                "unmastered_count", 2,
                "due_review_count", 1,
                "total_mistake_count", 3,
                "wrong_count", 6,
                "review_count", 4,
                "last_wrong_at", now.minusDays(1)
            )),
            List.of(Map.of(
                "id", "mistake-1",
                "knowledge_tags", "[\"事务隔离\",\"锁\"]",
                "difficulty_level", "MEDIUM",
                "mistake_type", "conceptual",
                "wrong_count", 3,
                "review_count", 1,
                "mastered", false,
                "next_review_at", now.plusDays(1),
                "last_wrong_at", now.minusDays(1)
            )),
            List.of(Map.of("topic", "事务隔离", "mistake_count", 2, "wrong_count", 5)),
            List.of(Map.of(
                "review_count", 2,
                "correct_review_count", 1,
                "average_quality", 3.5,
                "last_reviewed_at", now.minusHours(2)
            )),
            List.of(Map.of("resource_type", "VIDEO", "request_count", 2, "last_used_at", now.minusDays(2))),
            List.of(Map.of("resource_type", "MINDMAP", "generated_count", 1, "download_count", 1, "last_used_at", now.minusDays(3)))
        );

        Map<String, Object> context = service.buildContext(userId);

        assertThat(context).containsKeys("profile", "profileSummary", "learningProgress", "practiceSignals", "resourceSignals");
        assertThat(context.get("profileSummary")).isEqualTo("数据库基础中等，偏好视频和图解");

        @SuppressWarnings("unchecked")
        Map<String, Object> learningProgress = (Map<String, Object>) context.get("learningProgress");
        assertThat(learningProgress).containsEntry("source", "SERVER_AUTO_CONTEXT");
        assertThat(learningProgress).containsEntry("dataAvailable", true);
        @SuppressWarnings("unchecked")
        Map<String, Object> knowledgeSummary = (Map<String, Object>) learningProgress.get("knowledgeMasterySummary");
        assertThat(knowledgeSummary).containsEntry("dataAvailable", true);
        @SuppressWarnings("unchecked")
        List<Map<String, Object>> priorityKnowledge = (List<Map<String, Object>>) knowledgeSummary.get("priorityKnowledge");
        assertThat(priorityKnowledge).singleElement().satisfies(node -> {
            assertThat(node).containsEntry("key", "transaction_isolation");
            assertThat(node).containsEntry("status", "WEAK");
        });

        @SuppressWarnings("unchecked")
        Map<String, Object> practiceSignals = (Map<String, Object>) context.get("practiceSignals");
        assertThat(practiceSignals).containsEntry("dataAvailable", true);
        @SuppressWarnings("unchecked")
        Map<String, Object> practiceSummary = (Map<String, Object>) practiceSignals.get("practiceSummary");
        assertThat(practiceSummary)
            .containsEntry("submissionCount", 4)
            .containsEntry("correctCount", 3)
            .containsEntry("incorrectCount", 1)
            .containsEntry("accuracyPercent", 75.0);
        @SuppressWarnings("unchecked")
        List<Map<String, Object>> recentMistakes = (List<Map<String, Object>>) practiceSignals.get("recentMistakes");
        assertThat(recentMistakes).singleElement()
            .satisfies(mistake -> assertThat(mistake.get("knowledgeTags")).asList().contains("事务隔离", "锁"));

        @SuppressWarnings("unchecked")
        Map<String, Object> resourceSignals = (Map<String, Object>) context.get("resourceSignals");
        assertThat(resourceSignals).containsEntry("dataAvailable", true);
        @SuppressWarnings("unchecked")
        List<Map<String, Object>> resourcePreferences = (List<Map<String, Object>>) resourceSignals.get("resourcePreferences");
        assertThat(resourcePreferences)
            .extracting(item -> item.get("type"))
            .contains("VIDEO", "MINDMAP");
    }

    @SafeVarargs
    private void stubQueries(List<Map<String, Object>>... queryResults) {
        when(jdbcTemplate.queryForList(anyString(), any(SqlParameterSource.class)))
            .thenAnswer(invocation -> {
                String sql = invocation.getArgument(0);
                if (sql.contains("FROM app.learner_knowledge_node")) {
                    return queryResults[0];
                }
                if (sql.contains("FROM app.practice_submission")) {
                    return queryResults[1];
                }
                if (sql.contains("EVALUATION', 'LEARNING_EVALUATION")) {
                    return queryResults[2];
                }
                if (sql.contains("COUNT(*) FILTER")) {
                    return queryResults[3];
                }
                if (sql.contains("ORDER BY next_review_at")) {
                    return queryResults[4];
                }
                if (sql.contains("jsonb_array_elements_text")) {
                    return queryResults[5];
                }
                if (sql.contains("FROM app.mistake_review_result")) {
                    return queryResults[6];
                }
                if (sql.contains("preference_source")) {
                    return queryResults[7];
                }
                if (sql.contains("FROM app.generated_artifact")) {
                    return queryResults[8];
                }
                return List.of();
            });
    }

    private UserProfileCurrent profile(OffsetDateTime now) {
        UserProfileCurrent current = new UserProfileCurrent();
        current.setUserId(userId);
        current.setProfileJson(Map.of(
            "skillMastery", Map.of("数据库索引", 0.88, "事务隔离", 0.54),
            "weakPointDetails", List.of(Map.of("topic", "事务隔离")),
            "preferredResourceTypes", List.of("VIDEO")
        ));
        current.setSummaryText("数据库基础中等，偏好视频和图解");
        current.setUpdatedAt(now.minusDays(1));
        return current;
    }
}
