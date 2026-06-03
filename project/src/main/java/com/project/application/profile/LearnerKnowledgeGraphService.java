package com.project.application.profile;

import com.project.api.profile.dto.KnowledgeGraphResponse;
import com.project.api.profile.dto.KnowledgeGraphResponse.KnowledgeEdgeDto;
import com.project.api.profile.dto.KnowledgeGraphResponse.KnowledgeNodeDto;
import com.project.application.common.ApplicationException;
import com.project.security.JwtAuthenticatedUser;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

/**
 * 用户学习路径图只读查询服务。
 */
@Service
public class LearnerKnowledgeGraphService {

    private static final Logger log = LoggerFactory.getLogger(LearnerKnowledgeGraphService.class);
    private static final List<String> NON_KNOWLEDGE_PREFIXES = List.of(
        "学习主动性：",
        "学习主动性:",
        "复盘闭环：",
        "复盘闭环:",
        "案例迁移：",
        "案例迁移:"
    );

    private final NamedParameterJdbcTemplate jdbc;

    public LearnerKnowledgeGraphService(NamedParameterJdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Transactional(readOnly = true)
    public KnowledgeGraphResponse getGraph(JwtAuthenticatedUser currentUser, UUID requestedUserId) {
        if (!currentUser.userId().equals(requestedUserId)) {
            throw new ApplicationException("FORBIDDEN", "无权访问该用户知识图谱", HttpStatus.FORBIDDEN);
        }

        var params = new MapSqlParameterSource("userId", requestedUserId);

        List<KnowledgeNodeDto> nodes = jdbc.query(
            """
            SELECT canonical_key, topic, mastery_score, node_status, source
            FROM app.learner_knowledge_node
            WHERE user_id = :userId
              AND topic NOT LIKE '学习主动性：%'
              AND topic NOT LIKE '学习主动性:%'
              AND topic NOT LIKE '复盘闭环：%'
              AND topic NOT LIKE '复盘闭环:%'
              AND topic NOT LIKE '案例迁移：%'
              AND topic NOT LIKE '案例迁移:%'
            ORDER BY updated_at DESC
            LIMIT 60
            """,
            params,
            (rs, rowNum) -> new KnowledgeNodeDto(
                rs.getString("canonical_key"),
                rs.getString("topic"),
                rs.getDouble("mastery_score"),
                rs.getString("node_status"),
                rs.getString("source")
            )
        );

        nodes = sortNodesForMasteryGraph(nodes);

        if (nodes.isEmpty()) {
            return new KnowledgeGraphResponse(List.of(), List.of(), List.of());
        }

        Set<String> nodeKeys = new HashSet<>();
        for (var node : nodes) {
            nodeKeys.add(node.key());
        }

        var edgeParams = new MapSqlParameterSource()
            .addValue("userId", requestedUserId)
            .addValue("keys", nodeKeys);

        List<KnowledgeEdgeDto> edges = jdbc.query(
            """
            SELECT from_key, to_key, relation_type, weight
            FROM app.learner_knowledge_edge
            WHERE user_id = :userId
              AND from_key = ANY(ARRAY[ :keys ]::text[])
              AND to_key   = ANY(ARRAY[ :keys ]::text[])
            """,
            edgeParams,
            (rs, rowNum) -> new KnowledgeEdgeDto(
                rs.getString("from_key"),
                rs.getString("to_key"),
                rs.getString("relation_type"),
                rs.getDouble("weight")
            )
        );

        List<String> nextRecommended = computeNextRecommended(nodes, edges);
        return new KnowledgeGraphResponse(nodes, edges, nextRecommended);
    }

    private List<KnowledgeNodeDto> sortNodesForMasteryGraph(List<KnowledgeNodeDto> nodes) {
        return nodes.stream()
            .filter(node -> !isNonKnowledgeDimension(node.topic()))
            .sorted(
                Comparator
                    .comparingInt((KnowledgeNodeDto node) -> statusRank(node.status()))
                    .thenComparing(KnowledgeNodeDto::mastery)
                    .thenComparing(KnowledgeNodeDto::topic, String.CASE_INSENSITIVE_ORDER)
            )
            .toList();
    }

    private boolean isNonKnowledgeDimension(String topic) {
        if (topic == null) {
            return true;
        }
        String trimmed = topic.trim();
        return trimmed.isEmpty() || NON_KNOWLEDGE_PREFIXES.stream().anyMatch(trimmed::startsWith);
    }

    private int statusRank(String status) {
        return switch (status) {
            case "WEAK" -> 0;
            case "IN_PROGRESS" -> 1;
            case "NOT_STARTED" -> 2;
            case "MASTERED" -> 3;
            default -> 4;
        };
    }

    private List<String> computeNextRecommended(
        List<KnowledgeNodeDto> nodes,
        List<KnowledgeEdgeDto> edges
    ) {
        Set<String> mastered = new HashSet<>();

        for (var node : nodes) {
            if ("MASTERED".equals(node.status())) {
                mastered.add(node.key());
            }
        }

        // to_key -> set of from_key (prerequisites)
        Map<String, Set<String>> prerequisites = new HashMap<>();
        for (var edge : edges) {
            if ("PREREQUISITE".equals(edge.type())) {
                prerequisites.computeIfAbsent(edge.to(), k -> new HashSet<>()).add(edge.from());
            }
        }

        List<String> recommended = new ArrayList<>();
        for (var node : nodes) {
            if ("MASTERED".equals(node.status())) {
                continue;
            }
            Set<String> prereqs = prerequisites.getOrDefault(node.key(), Set.of());
            if (prereqs.isEmpty() || mastered.containsAll(prereqs)) {
                recommended.add(node.key());
            }
        }

        return recommended.size() > 5 ? recommended.subList(0, 5) : recommended;
    }
}
