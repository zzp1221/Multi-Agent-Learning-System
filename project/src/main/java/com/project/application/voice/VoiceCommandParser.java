package com.project.application.voice;

import com.project.api.voice.dto.VoiceCommandRequest;
import com.project.api.voice.dto.VoiceCommandResponse;
import org.springframework.stereotype.Component;

import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

@Component
public class VoiceCommandParser {
    private static final Set<String> LOCAL_INTENTS = Set.of(
        "STOP_SPEAKING",
        "PAUSE_SPEAKING",
        "CONTINUE",
        "OPEN_MISTAKE_BOOK",
        "OPEN_PROFILE",
        "START_REVIEW",
        "OPEN_QNA",
        "GENERATE_STUDY_PLAN"
    );

    public VoiceCommandResponse parse(VoiceCommandRequest request) {
        String text = request.normalizedText();
        String lower = text.toLowerCase(Locale.ROOT);
        String intent = resolveIntent(text, lower);
        return new VoiceCommandResponse(
            intent,
            text,
            LOCAL_INTENTS.contains(intent),
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
        if (text.contains("暂停") || text.contains("先停一下") || lower.contains("pause")) {
            return "PAUSE_SPEAKING";
        }
        if (containsAny(text, "今日复习", "开始复习", "继续复习", "复习错题")) {
            return "START_REVIEW";
        }
        if (containsAny(text, "学习计划", "路径规划", "学习路径", "规划路径")) {
            return "GENERATE_STUDY_PLAN";
        }
        if (containsAny(text, "加入错题本", "加入错题", "放进错题本", "记到错题本", "保存为错题")) {
            return "ADD_CURRENT_TO_MISTAKE_BOOK";
        }
        if (containsAny(text, "总结当前会话", "总结这段对话", "总结一下当前", "总结一下这段")) {
            return "SUMMARIZE_CURRENT";
        }
        if (containsAny(text, "继续这道题", "继续刚才那道题", "接着这道题", "继续刚才的问题")) {
            return "CONTINUE_CURRENT_QUESTION";
        }
        if (text.contains("错题本") || containsAny(text, "打开错题", "查看错题", "看看错题", "去错题")) {
            return "OPEN_MISTAKE_BOOK";
        }
        if (containsAny(text, "个人画像", "学习画像", "我的画像", "打开画像", "查看画像")) {
            return "OPEN_PROFILE";
        }
        if (containsAny(text, "回到问答", "打开问答", "智能问答", "新对话", "回首页", "回到首页")) {
            return "OPEN_QNA";
        }
        if (containsAny(text, "继续", "接着读", "继续播放") || lower.contains("continue")) {
            return "CONTINUE";
        }
        if (text.contains("解释") || text.contains("讲解")) {
            return "EXPLAIN_CURRENT";
        }
        if (containsAny(text, "总结", "概括")) {
            return "SUMMARIZE_CURRENT";
        }
        if (containsAny(text, "类似题", "同类题", "变式题", "再出一题")) {
            return "GENERATE_SIMILAR_QUESTIONS";
        }
        if (text.contains("简单") || text.contains("听不懂")) {
            return "SIMPLIFY_EXPLANATION";
        }
        return "ASK";
    }

    private boolean containsAny(String text, String... keywords) {
        for (String keyword : keywords) {
            if (text.contains(keyword)) {
                return true;
            }
        }
        return false;
    }

    private Map<String, String> buildContext(VoiceCommandRequest request) {
        Map<String, String> context = new LinkedHashMap<>();
        putIfPresent(context, "pageType", request.pageType());
        putIfPresent(context, "questionId", request.questionId());
        putIfPresent(context, "courseId", request.courseId());
        putIfPresent(context, "knowledgePointId", request.knowledgePointId());
        putIfPresent(context, "pageTitle", request.pageTitle());
        putIfPresent(context, "currentPath", request.currentPath());
        putIfPresent(context, "source", request.source());
        putIfPresent(context, "conversationId", request.conversationId());
        putIfPresent(context, "recentMessagesSummary", request.recentMessagesSummary());
        putIfPresent(context, "commandIntent", request.commandIntent());
        return context;
    }

    private void putIfPresent(Map<String, String> target, String key, String value) {
        if (value != null && !value.isBlank()) {
            target.put(key, value.trim());
        }
    }
}
