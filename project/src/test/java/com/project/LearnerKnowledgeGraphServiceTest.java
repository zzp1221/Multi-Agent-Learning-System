package com.project;

import com.project.application.profile.LearnerKnowledgeGraphService;
import com.project.security.JwtAuthenticatedUser;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;

import java.sql.ResultSet;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.tuple;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class LearnerKnowledgeGraphServiceTest {

    @Test
    void graphFiltersProfileDimensionsAndSortsByMasteryStatus() throws Exception {
        NamedParameterJdbcTemplate jdbc = mock(NamedParameterJdbcTemplate.class);
        when(jdbc.query(anyString(), any(MapSqlParameterSource.class), any(RowMapper.class)))
            .thenAnswer(invocation -> mapRows(invocation.getArgument(0), invocation.getArgument(2)));

        UUID userId = UUID.fromString("6ed05529-7f37-4601-865b-a942a6017c7a");
        var service = new LearnerKnowledgeGraphService(jdbc);
        var response = service.getGraph(new JwtAuthenticatedUser(userId, "student@example.com", "USER"), userId);

        assertThat(response.nodes())
            .extracting("topic")
            .containsExactly("ThreadLocal", "Callable", "并发编程基础", "变量声明", "线程安全");
        assertThat(response.nodes()).hasSize(5);
        assertThat(response.nextRecommended()).contains("threadlocal");
        assertThat(response.metadata().curationStats().filteredNodeCount()).isGreaterThanOrEqualTo(4);
        assertThat(response.metadata().edgeExplanations()).hasSize(3);
    }

    @Test
    void graphKeepsRealRelationWhenRecentCandidatesWouldHaveCutEndpoint() throws Exception {
        NamedParameterJdbcTemplate jdbc = mock(NamedParameterJdbcTemplate.class);
        when(jdbc.query(anyString(), any(MapSqlParameterSource.class), any(RowMapper.class)))
            .thenAnswer(invocation -> mapRelationRows(invocation.getArgument(0), invocation.getArgument(2)));

        UUID userId = UUID.fromString("6ed05529-7f37-4601-865b-a942a6017c7a");
        var service = new LearnerKnowledgeGraphService(jdbc);
        var response = service.getGraph(new JwtAuthenticatedUser(userId, "student@example.com", "USER"), userId);

        assertThat(response.nodes()).extracting("key").contains("old-prereq", "fresh-target");
        assertThat(response.edges())
            .extracting("from", "to", "type")
            .contains(tuple("old-prereq", "fresh-target", "PREREQUISITE"));
        assertThat(response.nextRecommended()).contains("fresh-target");
        assertThat(response.metadata().rootKey()).isNotBlank();
    }

    @Test
    void graphIgnoresLowConfidencePrerequisiteForRecommendation() throws Exception {
        NamedParameterJdbcTemplate jdbc = mock(NamedParameterJdbcTemplate.class);
        when(jdbc.query(anyString(), any(MapSqlParameterSource.class), any(RowMapper.class)))
            .thenAnswer(invocation -> mapLowConfidenceRows(invocation.getArgument(0), invocation.getArgument(2)));

        UUID userId = UUID.fromString("6ed05529-7f37-4601-865b-a942a6017c7a");
        var service = new LearnerKnowledgeGraphService(jdbc);
        var response = service.getGraph(new JwtAuthenticatedUser(userId, "student@example.com", "USER"), userId);

        assertThat(response.edges()).isEmpty();
        assertThat(response.nextRecommended()).contains("target");
        assertThat(response.metadata().curationStats().lowConfidenceEdgeCount()).isEqualTo(1);
    }

    private List<?> mapRows(String sql, RowMapper<?> mapper) throws Exception {
        if (sql.contains("learner_knowledge_edge")) {
            return List.of();
        }
        List<Map<String, Object>> rows = List.of(
            node("变量声明", "variable", 0.0, "NOT_STARTED"),
            node("学习主动性：并发编程", "initiative", 0.05, "WEAK"),
            node("学习主动性 ：并发编程", "initiative_space", 0.05, "WEAK"),
            node("复盘闭环 - Callable", "review_loop", 0.05, "WEAK"),
            node("案例迁移—ThreadLocal", "case_transfer", 0.05, "WEAK"),
            node("Java线程创建基础概念学习", "java-thread-stage", 0.0, "NOT_STARTED"),
            node("Callable", "callable", 0.27, "WEAK"),
            node("Callable:回调细节", "callable_detail", 0.33, "WEAK"),
            node("并发编程基础", "concurrency", 0.4, "IN_PROGRESS"),
            node("ThreadLocal", "threadlocal", 0.19, "WEAK"),
            node("ThreadLocal.detail", "threadlocal_detail", 0.31, "IN_PROGRESS"),
            node("线程安全", "thread_safe", 0.0, "NOT_STARTED")
        );
        return mapWithMapper(mapper, rows);
    }

    private List<?> mapRelationRows(String sql, RowMapper<?> mapper) throws Exception {
        if (sql.contains("learner_knowledge_edge")) {
            return mapWithMapper(mapper, List.of(
                edge("old-prereq", "fresh-target", "PREREQUISITE", 0.92),
                edge("fresh-target", "related", "RELATED", 0.62)
            ));
        }
        List<Map<String, Object>> rows = new ArrayList<>();
        for (int index = 0; index < 70; index += 1) {
            rows.add(node("填充知识点" + index, "filler-" + index, 0.6, "IN_PROGRESS"));
        }
        rows.add(node("真实前置", "old-prereq", 0.8, "MASTERED"));
        rows.add(node("目标知识", "fresh-target", 0.2, "WEAK"));
        rows.add(node("相关概念", "related", 0.3, "WEAK"));
        return mapWithMapper(mapper, rows);
    }

    private List<?> mapLowConfidenceRows(String sql, RowMapper<?> mapper) throws Exception {
        if (sql.contains("learner_knowledge_edge")) {
            return mapWithMapper(mapper, List.of(edge("dirty-step", "target", "PREREQUISITE", 0.42)));
        }
        return mapWithMapper(mapper, List.of(
            node("综合练习与复盘", "dirty-step", 0.0, "NOT_STARTED"),
            node("联合索引", "target", 0.25, "WEAK")
        ));
    }

    private Map<String, Object> node(String topic, String key, double mastery, String status) {
        return Map.of(
            "canonical_key", key,
            "topic", topic,
            "mastery_score", mastery,
            "node_status", status,
            "source", "PRACTICE"
        );
    }

    private Map<String, Object> edge(String from, String to, String type, double weight) {
        return Map.of(
            "from_key", from,
            "to_key", to,
            "relation_type", type,
            "weight", weight
        );
    }

    private List<?> mapWithMapper(RowMapper<?> mapper, List<Map<String, Object>> rows) throws Exception {
        var mapped = new java.util.ArrayList<>();
        for (int index = 0; index < rows.size(); index++) {
            mapped.add(mapper.mapRow(resultSet(rows.get(index)), index));
        }
        return mapped;
    }

    private ResultSet resultSet(Map<String, Object> row) throws Exception {
        ResultSet rs = mock(ResultSet.class);
        when(rs.getString(anyString())).thenAnswer(invocation -> String.valueOf(row.get(invocation.getArgument(0))));
        when(rs.getDouble(anyString())).thenAnswer(invocation -> ((Number) row.get(invocation.getArgument(0))).doubleValue());
        return rs;
    }
}
