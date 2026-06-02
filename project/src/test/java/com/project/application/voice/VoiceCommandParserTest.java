package com.project.application.voice;

import com.project.api.voice.dto.VoiceCommandRequest;
import com.project.api.voice.dto.VoiceCommandResponse;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

class VoiceCommandParserTest {

    private final VoiceCommandParser parser = new VoiceCommandParser();

    @Test
    void parsesStopSpeakingCommand() {
        VoiceCommandResponse response = parser.parse(request("停止朗读"));

        assertThat(response.intent()).isEqualTo("STOP_SPEAKING");
        assertThat(response.handledLocally()).isTrue();
    }

    @Test
    void parsesLocalPlaybackAndLearningActions() {
        assertLocalIntent("暂停朗读", "PAUSE_SPEAKING");
        assertLocalIntent("继续", "CONTINUE");
        assertLocalIntent("继续朗读", "CONTINUE");
        assertLocalIntent("打开错题本", "OPEN_MISTAKE_BOOK");
        assertLocalIntent("打开个人画像", "OPEN_PROFILE");
        assertLocalIntent("开始今日复习", "START_REVIEW");
        assertLocalIntent("回到问答", "OPEN_QNA");
        assertLocalIntent("生成学习计划", "GENERATE_STUDY_PLAN");
    }

    @Test
    void parsesContextAwareLearningCommands() {
        assertConversationIntent("把当前内容加入错题本", "ADD_CURRENT_TO_MISTAKE_BOOK");
        assertConversationIntent("总结当前会话", "SUMMARIZE_CURRENT");
        assertConversationIntent("继续刚才那道题", "CONTINUE_CURRENT_QUESTION");
        assertConversationIntent("再出一道类似题", "GENERATE_SIMILAR_QUESTIONS");
    }

    @Test
    void parsesLearningCommandWithContext() {
        VoiceCommandResponse response = parser.parse(new VoiceCommandRequest(
            "解释这道题",
            "question_detail",
            "q1",
            "math",
            "linear",
            "一元一次方程",
            "/mistakes",
            "voice_assistant",
            "conversation-1",
            "用户：刚才那题不会",
            "EXPLAIN_CURRENT"
        ));

        assertThat(response.intent()).isEqualTo("EXPLAIN_CURRENT");
        assertThat(response.context()).containsEntry("questionId", "q1");
        assertThat(response.context()).containsEntry("pageTitle", "一元一次方程");
        assertThat(response.context()).containsEntry("recentMessagesSummary", "用户：刚才那题不会");
    }

    private void assertLocalIntent(String text, String intent) {
        VoiceCommandResponse response = parser.parse(request(text));

        assertThat(response.intent()).isEqualTo(intent);
        assertThat(response.handledLocally()).isTrue();
    }

    private void assertConversationIntent(String text, String intent) {
        VoiceCommandResponse response = parser.parse(request(text));

        assertThat(response.intent()).isEqualTo(intent);
        assertThat(response.handledLocally()).isFalse();
    }

    private VoiceCommandRequest request(String text) {
        return new VoiceCommandRequest(text, null, null, null, null, null, null, null, null, null, null);
    }
}
