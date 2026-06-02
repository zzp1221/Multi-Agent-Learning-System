package com.project;

import com.project.application.profile.LearnerKnowledgeGraphService;
import com.project.security.JwtAuthenticatedUser;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;

import java.sql.ResultSet;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
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
        assertThat(response.nextRecommended()).contains("threadlocal");
    }

    private List<?> mapRows(String sql, RowMapper<?> mapper) throws Exception {
        if (sql.contains("learner_knowledge_edge")) {
            return List.of();
        }
        List<Map<String, Object>> rows = List.of(
            node("变量声明", "variable", 0.0, "NOT_STARTED"),
            node("学习主动性：并发编程", "initiative", 0.05, "WEAK"),
            node("Callable", "callable", 0.27, "WEAK"),
            node("并发编程基础", "concurrency", 0.4, "IN_PROGRESS"),
            node("ThreadLocal", "threadlocal", 0.19, "WEAK"),
            node("线程安全", "thread_safe", 0.0, "NOT_STARTED")
        );
        return mapWithMapper(mapper, rows);
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
