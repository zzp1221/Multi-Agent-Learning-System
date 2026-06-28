package com.project.application.study;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.project.api.learningpath.dto.LearningPathCurrentResponse;
import com.project.api.mistake.dto.MistakeRecordResponse;
import com.project.api.profile.dto.KnowledgeGraphResponse;
import com.project.api.profile.dto.KnowledgeGraphResponse.CurationStats;
import com.project.api.profile.dto.KnowledgeGraphResponse.KnowledgeGraphMetadata;
import com.project.api.profile.dto.UserProfileResponse;
import com.project.api.resource.dto.ResourceItemResponse;
import com.project.api.resource.dto.ResourceSemanticResultResponse;
import com.project.api.resource.dto.ResourceSemanticSearchResponse;
import com.project.application.common.ApplicationException;
import com.project.application.learningpath.LearningPathQueryService;
import com.project.application.learningpath.PersonalizedLearningRefreshService;
import com.project.application.profile.LearnerKnowledgeGraphService;
import com.project.application.profile.UserProfileQueryService;
import com.project.application.resource.ResourceLibraryService;
import com.project.security.JwtAuthenticatedUser;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;

import java.math.BigDecimal;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class StudyWorkbenchServiceTest {

    @Test
    void dailyAggregatesExistingPathMistakesResourcesGraphAndProfile() {
        UUID userId = UUID.fromString("61000000-0000-0000-0000-000000000001");
        JwtAuthenticatedUser currentUser = new JwtAuthenticatedUser(userId, "learner@example.com", "USER");
        MistakeRecordResponse dueMistake = mistake("循环边界题", "conceptual", "循环");
        ResourceItemResponse resource = resource(
            "循环讲解",
            false,
            "summary",
            List.of("循环"),
            Map.of("contentHash", "large-internal-hash", "wikiTitle", "循环")
        );
        KnowledgeGraphResponse graph = graph();
        LearningPathCurrentResponse learningPath = learningPath(userId, 40);
        UserProfileResponse profile = new UserProfileResponse(
            userId,
            Map.of("goal", "补齐 Java 基础"),
            "当前画像摘要",
            OffsetDateTime.now(),
            List.of()
        );

        NamedParameterJdbcTemplate jdbc = mock(NamedParameterJdbcTemplate.class);
        LearningPathQueryService pathService = mock(LearningPathQueryService.class);
        ResourceLibraryService resourceService = mock(ResourceLibraryService.class);
        LearnerKnowledgeGraphService graphService = mock(LearnerKnowledgeGraphService.class);
        UserProfileQueryService profileService = mock(UserProfileQueryService.class);
        when(pathService.getCurrent(userId)).thenReturn(learningPath);
        when(resourceService.recommendations(userId, 6)).thenReturn(List.of(resource));
        when(graphService.getGraph(currentUser, userId)).thenReturn(graph);
        when(profileService.getCurrentProfile(currentUser, userId)).thenReturn(profile);
        when(jdbc.query(anyString(), any(MapSqlParameterSource.class), anyMistakeMapper()))
            .thenReturn(List.of(dueMistake));

        var response = service(jdbc, pathService, resourceService, graphService, profileService).daily(currentUser);

        assertThat(response.userId()).isEqualTo(userId);
        assertThat(response.dataAvailable()).isTrue();
        assertThat(response.dueMistakes()).containsExactly(dueMistake);
        assertThat(response.recommendedResources()).hasSize(1);
        assertThat(response.recommendedResources().getFirst().title()).isEqualTo(resource.title());
        assertThat(response.recommendedResources().getFirst().sourceUrl()).isEqualTo(resource.sourceUrl());
        assertThat(response.recommendedResources().getFirst().metadata()).isEmpty();
        assertThat(response.summary().dueMistakeCount()).isEqualTo(1);
        assertThat(response.summary().recommendedResourceCount()).isEqualTo(1);
        assertThat(response.summary().weakKnowledgeCount()).isEqualTo(1);
        assertThat(response.tasks()).extracting("type")
            .contains("STAGE", "STAGE_TEST", "MISTAKE_REVIEW", "RESOURCE", "KNOWLEDGE");
        assertThat(response.executionPlan().primaryTask().type()).isEqualTo("MISTAKE_REVIEW");
        assertThat(response.executionPlan().title()).isEqualTo("复习到期错题");
        assertThat(response.executionPlan().steps())
            .extracting("phase")
            .containsExactly("热身", "补强", "检测", "反思");
        assertThat(response.executionPlan().steps())
            .extracting("sourceTaskType")
            .contains("MISTAKE_REVIEW", "RESOURCE")
            .doesNotContain("STAGE_TEST");
        assertThat(response.executionPlan().supportItems())
            .extracting("type")
            .contains("STAGE", "MISTAKE_REVIEW", "RESOURCE", "KNOWLEDGE");
        assertThat(response.executionPlan().focusReason()).contains("1 道错题");
        assertThat(response.executionPlan().successCriteria()).contains("错因");
    }

    @Test
    void dailyAllowsStageTestStepOnlyAfterStageProgressIsComplete() {
        UUID userId = UUID.fromString("61000000-0000-0000-0000-000000000006");
        JwtAuthenticatedUser currentUser = new JwtAuthenticatedUser(userId, "learner@example.com", "USER");
        LearningPathCurrentResponse learningPath = learningPath(userId, 100);
        KnowledgeGraphResponse graph = graph();
        UserProfileResponse profile = new UserProfileResponse(userId, Map.of(), "", OffsetDateTime.now(), List.of());

        NamedParameterJdbcTemplate jdbc = mock(NamedParameterJdbcTemplate.class);
        LearningPathQueryService pathService = mock(LearningPathQueryService.class);
        ResourceLibraryService resourceService = mock(ResourceLibraryService.class);
        LearnerKnowledgeGraphService graphService = mock(LearnerKnowledgeGraphService.class);
        UserProfileQueryService profileService = mock(UserProfileQueryService.class);
        when(pathService.getCurrent(userId)).thenReturn(learningPath);
        when(resourceService.recommendations(userId, 6)).thenReturn(List.of());
        when(graphService.getGraph(currentUser, userId)).thenReturn(graph);
        when(profileService.getCurrentProfile(currentUser, userId)).thenReturn(profile);
        when(jdbc.query(anyString(), any(MapSqlParameterSource.class), anyMistakeMapper()))
            .thenReturn(List.of());

        var response = service(jdbc, pathService, resourceService, graphService, profileService).daily(currentUser);

        assertThat(response.tasks())
            .filteredOn(task -> "STAGE_TEST".equals(task.type()))
            .extracting("status")
            .containsExactly("READY");
        assertThat(response.executionPlan().primaryTask().type()).isEqualTo("STAGE_TEST");
        assertThat(response.executionPlan().steps())
            .extracting("sourceTaskType")
            .contains("STAGE_TEST");
    }

    @Test
    void refreshDailyTriggersResourceRecommendationRefreshBeforeReturningSnapshot() {
        UUID userId = UUID.fromString("61000000-0000-0000-0000-000000000008");
        JwtAuthenticatedUser currentUser = new JwtAuthenticatedUser(userId, "learner@example.com", "USER");
        LearningPathCurrentResponse learningPath = learningPath(userId, 40);
        ResourceItemResponse resource = resource("循环讲解", false);

        NamedParameterJdbcTemplate jdbc = mock(NamedParameterJdbcTemplate.class);
        LearningPathQueryService pathService = mock(LearningPathQueryService.class);
        ResourceLibraryService resourceService = mock(ResourceLibraryService.class);
        LearnerKnowledgeGraphService graphService = mock(LearnerKnowledgeGraphService.class);
        UserProfileQueryService profileService = mock(UserProfileQueryService.class);
        PersonalizedLearningRefreshService refreshService = mock(PersonalizedLearningRefreshService.class);
        when(pathService.getCurrent(userId)).thenReturn(learningPath);
        when(resourceService.recommendations(userId, 6)).thenReturn(List.of(resource));
        when(graphService.getGraph(currentUser, userId)).thenReturn(graph());
        when(profileService.getCurrentProfile(currentUser, userId))
            .thenReturn(new UserProfileResponse(userId, Map.of(), "", OffsetDateTime.now(), List.of()));
        when(jdbc.query(anyString(), any(MapSqlParameterSource.class), anyMistakeMapper()))
            .thenReturn(List.of());

        var response = service(jdbc, pathService, resourceService, graphService, profileService, refreshService)
            .refreshDaily(currentUser);

        assertThat(response.recommendedResources()).hasSize(1);
        assertThat(response.recommendedResources().getFirst().title()).isEqualTo(resource.title());
        assertThat(response.recommendedResources().getFirst().metadata()).isEmpty();
        verify(refreshService).triggerResourceRecommendationRefresh(
            eq(userId),
            eq("刷新今日学习工作台推荐资源"),
            eq(learningPath.learningPath()),
            eq(learningPath.pushedResources())
        );
    }

    @Test
    void knowledgeNodeDetailRejectsDifferentUserBeforeLoadingGraph() {
        UUID currentUserId = UUID.fromString("61000000-0000-0000-0000-000000000002");
        UUID requestedUserId = UUID.fromString("61000000-0000-0000-0000-000000000003");

        assertThatThrownBy(() -> service().knowledgeNodeDetail(
            new JwtAuthenticatedUser(currentUserId, "learner@example.com", "USER"),
            requestedUserId,
            "loop"
        ))
            .isInstanceOfSatisfying(ApplicationException.class, ex ->
                assertThat(ex.getStatus()).isEqualTo(HttpStatus.FORBIDDEN));
    }

    @Test
    void knowledgeNodeDetailReturnsRelationsMistakesResourcesAndPracticeContext() {
        UUID userId = UUID.fromString("61000000-0000-0000-0000-000000000004");
        JwtAuthenticatedUser currentUser = new JwtAuthenticatedUser(userId, "learner@example.com", "USER");
        MistakeRecordResponse relatedMistake = mistake("循环条件错题", "procedural", "循环");
        ResourceItemResponse relatedResource = resource("循环微课", false);

        NamedParameterJdbcTemplate jdbc = mock(NamedParameterJdbcTemplate.class);
        ResourceLibraryService resourceService = mock(ResourceLibraryService.class);
        LearnerKnowledgeGraphService graphService = mock(LearnerKnowledgeGraphService.class);
        when(graphService.getGraph(currentUser, userId)).thenReturn(graph());
        when(jdbc.query(anyString(), any(MapSqlParameterSource.class), anyMistakeMapper()))
            .thenReturn(List.of(relatedMistake));
        when(resourceService.semanticSearch(userId, "循环", 6)).thenReturn(new ResourceSemanticSearchResponse(
            "循环",
            true,
            "ok",
            List.of(new ResourceSemanticResultResponse(relatedResource.id(), relatedResource, 0.91, "match", List.of()))
        ));

        var detail = service(
            jdbc,
            mock(LearningPathQueryService.class),
            resourceService,
            graphService,
            mock(UserProfileQueryService.class)
        ).knowledgeNodeDetail(currentUser, userId, "loop");

        assertThat(detail.node().topic()).isEqualTo("循环");
        assertThat(detail.prerequisites()).extracting("key").containsExactly("basic");
        assertThat(detail.nextNodes()).extracting("key").containsExactly("array");
        assertThat(detail.relatedMistakes()).containsExactly(relatedMistake);
        assertThat(detail.relatedResources()).containsExactly(relatedResource);
        assertThat(detail.recommendedNextActions()).isNotEmpty();
        assertThat(detail.practiceContext()).containsEntry("source", "KNOWLEDGE_GRAPH_DETAIL");
        assertThat(detail.practiceContext()).containsKey("semanticScope");
        Map<String, Object> semanticScope = (Map<String, Object>) detail.practiceContext().get("semanticScope");
        assertThat(semanticScope)
            .containsEntry("topic", "循环")
            .containsEntry("source", "KNOWLEDGE_GRAPH_DETAIL");
    }

    @Test
    void knowledgeNodeDetailFiltersUnrelatedSemanticResourceFallbacks() {
        UUID userId = UUID.fromString("61000000-0000-0000-0000-000000000007");
        JwtAuthenticatedUser currentUser = new JwtAuthenticatedUser(userId, "learner@example.com", "USER");
        KnowledgeGraphResponse graph = new KnowledgeGraphResponse(
            List.of(new KnowledgeGraphResponse.KnowledgeNodeDto("deadlock", "操作系统死锁知识", 0.28, "WEAK", "PROFILE")),
            List.of(),
            List.of("deadlock")
        );
        ResourceItemResponse relevantResource = resource(
            "操作系统死锁与银行家算法",
            false,
            "讲解死锁产生条件、进程同步和避免策略。",
            List.of("操作系统", "死锁", "银行家算法"),
            Map.of()
        );
        ResourceItemResponse unrelatedResource = resource(
            "Barcode Detection API",
            false,
            "Detect barcodes from images in the browser.",
            List.of("web api", "browser", "死锁", "操作系统"),
            Map.of()
        );

        NamedParameterJdbcTemplate jdbc = mock(NamedParameterJdbcTemplate.class);
        ResourceLibraryService resourceService = mock(ResourceLibraryService.class);
        LearnerKnowledgeGraphService graphService = mock(LearnerKnowledgeGraphService.class);
        when(graphService.getGraph(currentUser, userId)).thenReturn(graph);
        when(jdbc.query(anyString(), any(MapSqlParameterSource.class), anyMistakeMapper()))
            .thenReturn(List.of());
        when(resourceService.semanticSearch(userId, "操作系统死锁知识", 6)).thenReturn(new ResourceSemanticSearchResponse(
            "操作系统死锁知识",
            true,
            "ok",
            List.of(
                new ResourceSemanticResultResponse(unrelatedResource.id(), unrelatedResource, 0.92, "fallback", List.of()),
                new ResourceSemanticResultResponse(relevantResource.id(), relevantResource, 0.84, "match", List.of())
            )
        ));

        var detail = service(
            jdbc,
            mock(LearningPathQueryService.class),
            resourceService,
            graphService,
            mock(UserProfileQueryService.class)
        ).knowledgeNodeDetail(currentUser, userId, "deadlock");

        assertThat(detail.relatedResources()).containsExactly(relevantResource);
        assertThat(detail.recommendedNextActions())
            .anyMatch(action -> action.contains("操作系统死锁与银行家算法"))
            .noneMatch(action -> action.contains("Barcode Detection API"));
    }

    @Test
    void dailyUsesKnowledgeGraphPageRouteForKnowledgeTasks() {
        UUID userId = UUID.fromString("61000000-0000-0000-0000-000000000009");
        JwtAuthenticatedUser currentUser = new JwtAuthenticatedUser(userId, "learner@example.com", "USER");

        NamedParameterJdbcTemplate jdbc = mock(NamedParameterJdbcTemplate.class);
        LearningPathQueryService pathService = mock(LearningPathQueryService.class);
        ResourceLibraryService resourceService = mock(ResourceLibraryService.class);
        LearnerKnowledgeGraphService graphService = mock(LearnerKnowledgeGraphService.class);
        UserProfileQueryService profileService = mock(UserProfileQueryService.class);
        when(pathService.getCurrent(userId)).thenReturn(learningPath(userId, 40));
        when(resourceService.recommendations(userId, 6)).thenReturn(List.of());
        when(graphService.getGraph(currentUser, userId)).thenReturn(graph());
        when(profileService.getCurrentProfile(currentUser, userId))
            .thenReturn(new UserProfileResponse(userId, Map.of(), "", OffsetDateTime.now(), List.of()));
        when(jdbc.query(anyString(), any(MapSqlParameterSource.class), anyMistakeMapper()))
            .thenReturn(List.of());

        var response = service(jdbc, pathService, resourceService, graphService, profileService).daily(currentUser);

        assertThat(response.tasks())
            .filteredOn(task -> "KNOWLEDGE".equals(task.type()))
            .extracting("actionRoute")
            .containsExactly("/knowledge-graph?node=loop");
        assertThat(response.executionPlan().supportItems())
            .filteredOn(item -> "KNOWLEDGE".equals(item.type()))
            .extracting("actionRoute")
            .allMatch(route -> String.valueOf(route).startsWith("/knowledge-graph?node="));
    }

    @Test
    void dailyDoesNotPromoteSparseGraphNodeToKnowledgeTask() {
        UUID userId = UUID.fromString("61000000-0000-0000-0000-000000000010");
        JwtAuthenticatedUser currentUser = new JwtAuthenticatedUser(userId, "learner@example.com", "USER");
        KnowledgeGraphResponse sparseGraph = new KnowledgeGraphResponse(
            List.of(new KnowledgeGraphResponse.KnowledgeNodeDto("isolated", "联合索引", 0.2, "WEAK", "PRACTICE")),
            List.of(),
            List.of("isolated"),
            new KnowledgeGraphMetadata(
                "isolated",
                28,
                true,
                1,
                new CurationStats(0, 0, 0),
                List.of()
            )
        );

        NamedParameterJdbcTemplate jdbc = mock(NamedParameterJdbcTemplate.class);
        LearningPathQueryService pathService = mock(LearningPathQueryService.class);
        ResourceLibraryService resourceService = mock(ResourceLibraryService.class);
        LearnerKnowledgeGraphService graphService = mock(LearnerKnowledgeGraphService.class);
        UserProfileQueryService profileService = mock(UserProfileQueryService.class);
        when(pathService.getCurrent(userId)).thenReturn(learningPath(userId, 40));
        when(resourceService.recommendations(userId, 6)).thenReturn(List.of());
        when(graphService.getGraph(currentUser, userId)).thenReturn(sparseGraph);
        when(profileService.getCurrentProfile(currentUser, userId))
            .thenReturn(new UserProfileResponse(userId, Map.of(), "", OffsetDateTime.now(), List.of()));
        when(jdbc.query(anyString(), any(MapSqlParameterSource.class), anyMistakeMapper()))
            .thenReturn(List.of());

        var response = service(jdbc, pathService, resourceService, graphService, profileService).daily(currentUser);

        assertThat(response.tasks()).extracting("type").doesNotContain("KNOWLEDGE");
        assertThat(response.executionPlan().primaryTask().type()).isNotEqualTo("KNOWLEDGE");
    }

    @Test
    void mistakeTrainingCampsGroupMistakesAndExposeMicroPractices() {
        UUID userId = UUID.fromString("61000000-0000-0000-0000-000000000005");
        OffsetDateTime nextReviewAt = OffsetDateTime.parse("2026-06-10T09:00:00+08:00");
        MistakeRecordResponse representative = mistake("循环迁移题", "conceptual", "循环");
        NamedParameterJdbcTemplate jdbc = mock(NamedParameterJdbcTemplate.class);
        when(jdbc.queryForList(anyString(), any(MapSqlParameterSource.class))).thenReturn(List.of(Map.of(
            "normalized_type", "conceptual",
            "primary_tag", "循环",
            "mistake_count", 3L,
            "due_count", 2L,
            "mastered_count", 1L,
            "total_wrong_count", 5L,
            "total_review_count", 4L,
            "next_review_at", nextReviewAt
        )));
        when(jdbc.query(anyString(), any(MapSqlParameterSource.class), anyMistakeMapper()))
            .thenReturn(List.of(representative));

        var response = service(jdbc).mistakeTrainingCamps(new JwtAuthenticatedUser(userId, "learner@example.com", "USER"));

        assertThat(response.summary().campCount()).isEqualTo(1);
        assertThat(response.summary().activeMistakeCount()).isEqualTo(2);
        assertThat(response.summary().dueMistakeCount()).isEqualTo(2);
        assertThat(response.summary().masteredMistakeCount()).isEqualTo(1);
        assertThat(response.camps()).hasSize(1);
        var camp = response.camps().getFirst();
        assertThat(camp.knowledgeTag()).isEqualTo("循环");
        assertThat(camp.mistakeType()).isEqualTo("conceptual");
        assertThat(camp.nextReviewAt()).isEqualTo(nextReviewAt);
        assertThat(camp.representativeMistakes()).containsExactly(representative);
        assertThat(camp.microPractices()).hasSize(2);
        assertThat(camp.practiceContext()).containsEntry("source", "MISTAKE_TRAINING_CAMP");
        assertThat(camp.practiceContext()).containsKey("semanticScope");
        Map<String, Object> semanticScope = (Map<String, Object>) camp.practiceContext().get("semanticScope");
        assertThat(semanticScope)
            .containsEntry("topic", "循环")
            .containsEntry("source", "MISTAKE_TRAINING_CAMP");
    }

    private static StudyWorkbenchService service() {
        return service(mock(NamedParameterJdbcTemplate.class));
    }

    private static StudyWorkbenchService service(NamedParameterJdbcTemplate jdbc) {
        return service(
            jdbc,
            mock(LearningPathQueryService.class),
            mock(ResourceLibraryService.class),
            mock(LearnerKnowledgeGraphService.class),
            mock(UserProfileQueryService.class)
        );
    }

    private static StudyWorkbenchService service(
        NamedParameterJdbcTemplate jdbc,
        LearningPathQueryService pathService,
        ResourceLibraryService resourceService,
        LearnerKnowledgeGraphService graphService,
        UserProfileQueryService profileService
    ) {
        return new StudyWorkbenchService(
            jdbc,
            new ObjectMapper(),
            pathService,
            resourceService,
            graphService,
            profileService,
            mock(PersonalizedLearningRefreshService.class)
        );
    }

    private static StudyWorkbenchService service(
        NamedParameterJdbcTemplate jdbc,
        LearningPathQueryService pathService,
        ResourceLibraryService resourceService,
        LearnerKnowledgeGraphService graphService,
        UserProfileQueryService profileService,
        PersonalizedLearningRefreshService refreshService
    ) {
        return new StudyWorkbenchService(
            jdbc,
            new ObjectMapper(),
            pathService,
            resourceService,
            graphService,
            profileService,
            refreshService
        );
    }

    private static LearningPathCurrentResponse learningPath(UUID userId, int progress) {
        return new LearningPathCurrentResponse(
            UUID.fromString("62000000-0000-0000-0000-000000000001"),
            userId,
            null,
            "ACTIVE",
            Map.of(),
            Map.of(
                "stepId", "stage-1",
                "title", "Java 控制流",
                "progress", progress,
                "checkpoint", "完成循环练习",
                "targetKnowledgePoints", List.of("循环")
            ),
            Map.of(),
            List.of(),
            1,
            "test",
            "学习路径摘要",
            OffsetDateTime.now(),
            null,
            null
        );
    }

    private static KnowledgeGraphResponse graph() {
        return new KnowledgeGraphResponse(
            List.of(
                new KnowledgeGraphResponse.KnowledgeNodeDto("basic", "Java 基础", 0.8, "MASTERED", "PROFILE"),
                new KnowledgeGraphResponse.KnowledgeNodeDto("loop", "循环", 0.35, "WEAK", "PRACTICE"),
                new KnowledgeGraphResponse.KnowledgeNodeDto("array", "数组", 0.1, "NOT_STARTED", "PATH")
            ),
            List.of(
                new KnowledgeGraphResponse.KnowledgeEdgeDto("basic", "loop", "PREREQUISITE", 0.9),
                new KnowledgeGraphResponse.KnowledgeEdgeDto("loop", "array", "PREREQUISITE", 0.8)
            ),
            List.of("loop")
        );
    }

    private static MistakeRecordResponse mistake(String stem, String mistakeType, String tag) {
        OffsetDateTime now = OffsetDateTime.parse("2026-06-10T10:00:00+08:00");
        return new MistakeRecordResponse(
            UUID.randomUUID(),
            UUID.randomUUID(),
            UUID.randomUUID(),
            "SINGLE_CHOICE",
            stem,
            List.of("A", "B"),
            Map.of("answer", "A"),
            "B",
            Map.of("feedback", "注意边界"),
            BigDecimal.ZERO,
            now,
            List.of(tag),
            "BASIC",
            mistakeType,
            "复盘笔记",
            2,
            1,
            now.minusHours(1),
            new BigDecimal("2.50"),
            1,
            false,
            now.minusDays(3),
            now.minusDays(1),
            now.minusDays(3),
            now
        );
    }

    private static ResourceItemResponse resource(String title, boolean completed) {
        return resource(title, completed, "summary", List.of("循环"), Map.of());
    }

    private static ResourceItemResponse resource(
        String title,
        boolean completed,
        String summary,
        List<String> tags,
        Map<String, Object> metadata
    ) {
        return new ResourceItemResponse(
            UUID.randomUUID(),
            title,
            "COMPUTER_SCIENCE",
            "READING",
            "DOCUMENT",
            "BASIC",
            "WEB",
            summary,
            tags,
            "https://example.com/resource",
            "example.com",
            "",
            "",
            "AUTHORIZED",
            "ACCESSIBLE",
            200,
            null,
            0.9,
            0.1,
            0L,
            0L,
            0L,
            null,
            null,
            false,
            completed ? 100 : 0,
            completed,
            null,
            null,
            null,
            "GENERAL_CS",
            "",
            metadata
        );
    }

    @SuppressWarnings("unchecked")
    private static RowMapper<MistakeRecordResponse> anyMistakeMapper() {
        return any(RowMapper.class);
    }
}
