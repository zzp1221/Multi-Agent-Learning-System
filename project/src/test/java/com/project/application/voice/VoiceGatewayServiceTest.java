package com.project.application.voice;

import com.project.application.common.ApplicationException;
import com.project.config.AppProperties;
import com.project.security.JwtAuthenticatedUser;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockMultipartFile;

import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class VoiceGatewayServiceTest {

    private final AppProperties appProperties = new AppProperties();
    private final JwtAuthenticatedUser user = new JwtAuthenticatedUser(UUID.randomUUID(), "demo", "USER");

    @Test
    void rejectsUnsupportedAudioFormat() {
        VoiceGatewayService service = new VoiceGatewayService(
            appProperties,
            (audio, sampleRate) -> new VoiceAsrResult("", 0, "test", "test"),
            new VoiceSessionService(appProperties),
            new VoiceCommandParser()
        );
        MockMultipartFile file = new MockMultipartFile("file", "voice.webm", "audio/webm", new byte[] {1, 2, 3});

        assertThatThrownBy(() -> service.transcribe(user, file))
            .isInstanceOf(ApplicationException.class)
            .hasMessageContaining("仅支持");
    }

    @Test
    void transcribesPcmAudio() {
        VoiceGatewayService service = new VoiceGatewayService(
            appProperties,
            (audio, sampleRate) -> new VoiceAsrResult("解释这道题", 100, "test", "asr"),
            new VoiceSessionService(appProperties),
            new VoiceCommandParser()
        );
        MockMultipartFile file = new MockMultipartFile("file", "voice.pcm", "audio/pcm", new byte[3200]);

        assertThat(service.transcribe(user, file).text()).isEqualTo("解释这道题");
    }
}
