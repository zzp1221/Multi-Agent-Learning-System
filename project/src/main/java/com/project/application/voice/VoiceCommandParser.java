package com.project.application.voice;

import com.project.api.voice.dto.VoiceCommandRequest;
import com.project.api.voice.dto.VoiceCommandResponse;
import org.springframework.stereotype.Component;

import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;

@Component
public class VoiceCommandParser {

    public VoiceCommandResponse parse(VoiceCommandRequest request) {
        String text = request.normalizedText();
        String lower = text.toLowerCase(Locale.ROOT);
        String intent = resolveIntent(text, lower);
        return new VoiceCommandResponse(
            intent,
            text,
            "STOP_SPEAKING".equals(intent) || "CONTINUE".equals(intent),
            buildContext(request)
        );
    }

    private String resolveIntent(String text, String lower) {
        if (text.isBlank()) {
            return "EMPTY";
        }
        if (text.contains("停止") || text.contains("别读") || text.contains("不要读") || lower.contains("stop")) {
            return "STOP_SPEAKING";
        }
        if (text.contains("继续") || lower.contains("continue")) {
            return "CONTINUE";
        }
        if (text.contains("解释") || text.contains("讲解")) {
            return "EXPLAIN_CURRENT";
        }
        if (text.contains("总结")) {
            return "SUMMARIZE_CURRENT";
        }
        if (text.contains("类似题") || text.contains("同类题")) {
            return "GENERATE_SIMILAR_QUESTIONS";
        }
        if (text.contains("简单") || text.contains("听不懂")) {
            return "SIMPLIFY_EXPLANATION";
        }
        return "ASK";
    }

    private Map<String, String> buildContext(VoiceCommandRequest request) {
        Map<String, String> context = new LinkedHashMap<>();
        putIfPresent(context, "pageType", request.pageType());
        putIfPresent(context, "questionId", request.questionId());
        putIfPresent(context, "courseId", request.courseId());
        putIfPresent(context, "knowledgePointId", request.knowledgePointId());
        return context;
    }

    private void putIfPresent(Map<String, String> target, String key, String value) {
        if (value != null && !value.isBlank()) {
            target.put(key, value.trim());
        }
    }
}
