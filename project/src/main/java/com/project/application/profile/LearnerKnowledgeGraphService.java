package com.project.application.profile;

import com.project.api.profile.dto.KnowledgeGraphResponse;
import com.project.api.profile.dto.KnowledgeGraphResponse.CurationStats;
import com.project.api.profile.dto.KnowledgeGraphResponse.EdgeExplanation;
import com.project.api.profile.dto.KnowledgeGraphResponse.KnowledgeEdgeDto;
import com.project.api.profile.dto.KnowledgeGraphResponse.KnowledgeGraphMetadata;
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
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

/**
 * 用户学习路径图只读查询服务。
 */
@Service
public class LearnerKnowledgeGraphService {

    private static final Logger log = LoggerFactory.getLogger(LearnerKnowledgeGraphService.class);
    private static final int CANDIDATE_NODE_LIMIT = 200;
    private static final int DEFAULT_VISIBLE_NODE_LIMIT = 28;
    private static final int DEFAULT_MIN_VISIBLE_NODE_COUNT = 15;
    private static final List<String> NON_KNOWLEDGE_DIMENSIONS = List.of(
        "学习主动性",
        "复盘闭环",
        "案例迁移",
        "概念学习",
        "实践应用",
        "复盘与巩固",
        "综合练习",
        "专项练习"
    );
    private static final List<String> TASK_STAGE_SUFFIXES = List.of(
        "概念学习",
        "实践应用",
        "复盘与巩固",
        "综合练习",
        "专项练习",
        "与复盘"
    );
    private static final Set<String> PLACEHOLDER_TOPICS = Set.of(
        "当前主题",
        "核心概念",
        "基础概念",
        "综合复盘",
        "综合练习"
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

        var params = new MapSqlParameterSource("userId", requestedUserId)
            .addValue("limit", CANDIDATE_NODE_LIMIT);

        List<KnowledgeNodeDto> candidateRows = jdbc.query(
            """
            SELECT canonical_key, topic, mastery_score, node_status, source
            FROM app.learner_knowledge_node
            WHERE user_id = :userId
            ORDER BY updated_at DESC
            LIMIT :limit
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

        int rawNodeCount = candidateRows.size();
        List<KnowledgeNodeDto> nodes = sortNodesForMasteryGraph(candidateRows);
        int filteredNodeCount = Math.max(0, rawNodeCount - nodes.size());

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

        List<KnowledgeEdgeDto> candidateEdges = jdbc.query(
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

        List<KnowledgeEdgeDto> trustedEdges = candidateEdges.stream()
            .filter(edge -> nodeKeys.contains(edge.from()) && nodeKeys.contains(edge.to()))
            .filter(this::isTrustedEdge)
            .toList();
        List<String> nextRecommended = computeNextRecommended(nodes, trustedEdges);
        String rootKey = chooseRootKey(nodes, nextRecommended);
        GraphSelection selection = selectDefaultGraph(nodes, trustedEdges, rootKey, nextRecommended);
        List<String> selectedRecommendations = nextRecommended.stream()
            .filter(selection.nodeKeys()::contains)
            .limit(5)
            .toList();
        KnowledgeGraphMetadata metadata = new KnowledgeGraphMetadata(
            rootKey,
            DEFAULT_VISIBLE_NODE_LIMIT,
            selection.sparseState(),
            selection.orphanNodeCount(),
            new CurationStats(
                filteredNodeCount,
                candidateEdges.size() - trustedEdges.size(),
                countSuspiciousEdges(candidateEdges, nodeKeys)
            ),
            edgeExplanations()
        );
        return new KnowledgeGraphResponse(selection.nodes(), selection.edges(), selectedRecommendations, metadata);
    }

    private List<KnowledgeNodeDto> sortNodesForMasteryGraph(List<KnowledgeNodeDto> nodes) {
        List<KnowledgeNodeDto> sortedNodes = nodes.stream()
            .filter(node -> !isNonKnowledgeDimension(node.topic()))
            .sorted(
                Comparator
                    .comparingInt((KnowledgeNodeDto node) -> statusRank(node.status()))
                    .thenComparing(KnowledgeNodeDto::mastery)
                    .thenComparing(KnowledgeNodeDto::topic, String.CASE_INSENSITIVE_ORDER)
            )
            .toList();
        return compactDuplicateTopics(sortedNodes);
    }

    private List<KnowledgeNodeDto> compactDuplicateTopics(List<KnowledgeNodeDto> nodes) {
        Map<String, KnowledgeNodeDto> compacted = new LinkedHashMap<>();
        for (KnowledgeNodeDto node : nodes) {
            String topicKey = normalizeTopicKey(node.topic());
            if (topicKey.isBlank()) {
                continue;
            }
            KnowledgeNodeDto existing = compacted.get(topicKey);
            if (existing == null || shouldReplaceTopicRepresentative(node, existing)) {
                compacted.put(topicKey, node);
            }
        }
        return new ArrayList<>(compacted.values());
    }

    private boolean shouldReplaceTopicRepresentative(KnowledgeNodeDto candidate, KnowledgeNodeDto existing) {
        int statusComparison = Integer.compare(statusRank(candidate.status()), statusRank(existing.status()));
        if (statusComparison != 0) {
            return statusComparison < 0;
        }
        int masteryComparison = Double.compare(candidate.mastery(), existing.mastery());
        if (masteryComparison != 0) {
            return masteryComparison < 0;
        }
        return candidate.topic().length() < existing.topic().length();
    }

    private String normalizeTopicKey(String topic) {
        String normalized = topic == null ? "" : topic.trim().toLowerCase(Locale.ROOT);
        normalized = normalized
            .replace('\uFF1A', ':')
            .replace('\u3002', '.')
            .replace('\u00B7', '.')
            .replace('\u30FB', '.')
            .replace('\uFF0F', '/')
            .replace('\uFF0D', '-');
        int splitIndex = firstTopicDelimiter(normalized);
        if (splitIndex > 0) {
            normalized = normalized.substring(0, splitIndex);
        }
        normalized = normalized.replaceAll("[\\s_]+", "");
        return stripGenericTopicSuffix(normalized);
    }

    private int firstTopicDelimiter(String topic) {
        int first = -1;
        for (char delimiter : new char[] { ':', '.', '/', '\\', '-', '|' }) {
            int index = topic.indexOf(delimiter);
            if (index >= 0 && (first < 0 || index < first)) {
                first = index;
            }
        }
        return first;
    }

    private String stripGenericTopicSuffix(String topic) {
        String[] suffixes = {
            "\u57fa\u7840\u8bed\u6cd5\u5165\u95e8",
            "\u57fa\u7840\u8bed\u6cd5",
            "\u57fa\u7840\u5165\u95e8",
            "\u5165\u95e8",
            "\u57fa\u7840",
            "\u6982\u8ff0"
        };
        for (String suffix : suffixes) {
            if (topic.endsWith(suffix) && topic.length() > suffix.length()) {
                return topic.substring(0, topic.length() - suffix.length());
            }
        }
        return topic;
    }

    private boolean isNonKnowledgeDimension(String topic) {
        if (topic == null) {
            return true;
        }
        String trimmed = topic.trim();
        String normalized = normalizeDisplayTopic(trimmed);
        return normalized.isEmpty()
            || PLACEHOLDER_TOPICS.contains(normalized)
            || NON_KNOWLEDGE_DIMENSIONS.stream().anyMatch(dimension ->
            trimmed.equals(dimension) || startsWithDimensionSeparator(trimmed, dimension)
        )
            || TASK_STAGE_SUFFIXES.stream().anyMatch(normalized::endsWith)
            || looksLikeActionSentence(normalized);
    }

    private boolean startsWithDimensionSeparator(String topic, String dimension) {
        if (!topic.startsWith(dimension)) {
            return false;
        }
        String suffix = topic.substring(dimension.length()).stripLeading();
        return suffix.startsWith(":")
            || suffix.startsWith("：")
            || suffix.startsWith("-")
            || suffix.startsWith("－")
            || suffix.startsWith("—")
            || suffix.startsWith("–");
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

    private String normalizeDisplayTopic(String topic) {
        return topic == null ? "" : topic.trim()
            .replace('\uFF1A', ':')
            .replaceAll("\\s+", "");
    }

    private boolean looksLikeActionSentence(String topic) {
        String[] actions = {"建议", "优先", "完成", "进行", "复习", "巩固", "迁移", "路径规划", "学习反馈", "做练习"};
        for (String action : actions) {
            if (topic.contains(action) && !containsCourseTerm(topic)) {
                return true;
            }
        }
        return false;
    }

    private boolean containsCourseTerm(String topic) {
        String[] terms = {"算法", "协议", "机制", "模型", "数据结构", "模式", "索引", "事务", "线程", "锁", "网络", "编译", "内存", "函数", "图", "树"};
        for (String term : terms) {
            if (topic.contains(term)) {
                return true;
            }
        }
        return topic.matches(".*[A-Za-z][A-Za-z0-9+#.]{1,}.*");
    }

    private boolean isTrustedEdge(KnowledgeEdgeDto edge) {
        if (edge.from() == null || edge.to() == null || edge.from().equals(edge.to())) {
            return false;
        }
        if (!Set.of("PREREQUISITE", "RELATED", "PART_OF").contains(edge.type())) {
            return false;
        }
        if ("PREREQUISITE".equals(edge.type()) && edge.weight() < 0.75) {
            return false;
        }
        return edge.weight() >= 0.1;
    }

    private int countSuspiciousEdges(List<KnowledgeEdgeDto> edges, Set<String> nodeKeys) {
        int count = 0;
        for (KnowledgeEdgeDto edge : edges) {
            if (edge.from() == null || edge.to() == null || edge.from().equals(edge.to())) {
                count += 1;
                continue;
            }
            if (!nodeKeys.contains(edge.from()) || !nodeKeys.contains(edge.to())) {
                count += 1;
                continue;
            }
            if ("PREREQUISITE".equals(edge.type()) && edge.weight() < 0.75) {
                count += 1;
            }
        }
        return count;
    }

    private String chooseRootKey(List<KnowledgeNodeDto> nodes, List<String> nextRecommended) {
        Set<String> nodeKeys = nodes.stream().map(KnowledgeNodeDto::key).collect(java.util.stream.Collectors.toSet());
        return nextRecommended.stream()
            .filter(nodeKeys::contains)
            .findFirst()
            .orElseGet(() -> nodes.stream()
                .filter(node -> "WEAK".equals(node.status()))
                .min(Comparator.comparingDouble(KnowledgeNodeDto::mastery))
                .orElse(nodes.getFirst())
                .key());
    }

    private GraphSelection selectDefaultGraph(
        List<KnowledgeNodeDto> nodes,
        List<KnowledgeEdgeDto> edges,
        String rootKey,
        List<String> nextRecommended
    ) {
        Map<String, KnowledgeNodeDto> nodeByKey = new LinkedHashMap<>();
        nodes.forEach(node -> nodeByKey.put(node.key(), node));
        Set<String> selectedKeys = new LinkedHashSet<>();
        if (!rootKey.isBlank()) {
            selectedKeys.add(rootKey);
        }
        addNeighborKeys(selectedKeys, edges, rootKey, 2, DEFAULT_VISIBLE_NODE_LIMIT);
        for (String key : nextRecommended) {
            selectedKeys.add(key);
            addNeighborKeys(selectedKeys, edges, key, 1, DEFAULT_VISIBLE_NODE_LIMIT);
            if (selectedKeys.size() >= DEFAULT_VISIBLE_NODE_LIMIT) {
                break;
            }
        }
        for (KnowledgeEdgeDto edge : edges) {
            if (selectedKeys.size() >= DEFAULT_MIN_VISIBLE_NODE_COUNT) {
                break;
            }
            selectedKeys.add(edge.from());
            selectedKeys.add(edge.to());
        }
        for (KnowledgeNodeDto node : nodes) {
            if (selectedKeys.size() >= DEFAULT_MIN_VISIBLE_NODE_COUNT || selectedKeys.size() >= DEFAULT_VISIBLE_NODE_LIMIT) {
                break;
            }
            selectedKeys.add(node.key());
        }
        selectedKeys.removeIf(key -> !nodeByKey.containsKey(key));
        if (selectedKeys.size() > DEFAULT_VISIBLE_NODE_LIMIT) {
            selectedKeys = selectedKeys.stream()
                .limit(DEFAULT_VISIBLE_NODE_LIMIT)
                .collect(java.util.stream.Collectors.toCollection(LinkedHashSet::new));
        }
        Set<String> stableKeys = selectedKeys;
        List<KnowledgeNodeDto> selectedNodes = nodes.stream()
            .filter(node -> stableKeys.contains(node.key()))
            .toList();
        List<KnowledgeEdgeDto> selectedEdges = edges.stream()
            .filter(edge -> stableKeys.contains(edge.from()) && stableKeys.contains(edge.to()))
            .toList();
        Set<String> connectedKeys = new HashSet<>();
        for (KnowledgeEdgeDto edge : selectedEdges) {
            connectedKeys.add(edge.from());
            connectedKeys.add(edge.to());
        }
        int orphanCount = (int) selectedNodes.stream()
            .filter(node -> !connectedKeys.contains(node.key()))
            .count();
        boolean sparseState = selectedEdges.size() < 3 || connectedKeys.size() < Math.min(selectedNodes.size(), 6);
        return new GraphSelection(selectedNodes, selectedEdges, stableKeys, sparseState, orphanCount);
    }

    private void addNeighborKeys(
        Set<String> selectedKeys,
        List<KnowledgeEdgeDto> edges,
        String rootKey,
        int depth,
        int limit
    ) {
        if (rootKey == null || rootKey.isBlank() || depth <= 0) {
            return;
        }
        Set<String> frontier = new LinkedHashSet<>();
        frontier.add(rootKey);
        for (int step = 0; step < depth && !frontier.isEmpty(); step += 1) {
            Set<String> next = new LinkedHashSet<>();
            for (KnowledgeEdgeDto edge : edges) {
                if (frontier.contains(edge.from())) {
                    next.add(edge.to());
                }
                if (frontier.contains(edge.to())) {
                    next.add(edge.from());
                }
            }
            for (String key : next) {
                selectedKeys.add(key);
                if (selectedKeys.size() >= limit) {
                    return;
                }
            }
            frontier = next;
        }
    }

    private List<EdgeExplanation> edgeExplanations() {
        return List.of(
            new EdgeExplanation("PREREQUISITE", "前置", "表示先学习起点知识，再学习目标知识。"),
            new EdgeExplanation("PART_OF", "属于", "表示知识点和主题簇的层级归属。"),
            new EdgeExplanation("RELATED", "相关", "表示概念相关，但不声明严格学习顺序。")
        );
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

    private record GraphSelection(
        List<KnowledgeNodeDto> nodes,
        List<KnowledgeEdgeDto> edges,
        Set<String> nodeKeys,
        boolean sparseState,
        int orphanNodeCount
    ) {}
}
