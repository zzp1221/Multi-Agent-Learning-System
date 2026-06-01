package com.project.application.voice;

import com.project.api.voice.dto.VoiceCommandRequest;
import com.project.api.voice.dto.VoiceCommandResponse;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

class VoiceCommandParserTest {

    private final VoiceCommandParser parser = new VoiceCommandParser();

    @Test
    void parsesStopSpeakingCommand() {
        VoiceCommandResponse response = parser.parse(new VoiceCommandRequest("停止朗读", null, null, null, null));

        assertThat(response.intent()).isEqualTo("STOP_SPEAKING");
        assertThat(response.handledLocally()).isTrue();
    }

    @Test
    void parsesLearningCommandWithContext() {
        VoiceCommandResponse response = parser.parse(new VoiceCommandRequest(
            "解释这道题",
            "question_detail",
            "q1",
            "math",
            "linear"
        ));

        assertThat(response.intent()).isEqualTo("EXPLAIN_CURRENT");
        assertThat(response.context()).containsEntry("questionId", "q1");
    }
}
