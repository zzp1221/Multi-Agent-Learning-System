package com.project.api.voice;

import com.project.api.voice.dto.VoiceTtsRequest;
import com.project.application.common.ApplicationException;
import com.project.application.voice.VoiceAsrPrewarmService;
import com.project.application.voice.VoiceGatewayService;
import com.project.application.voice.VoiceMetricContext;
import com.project.application.voice.VoiceMetricLogger;
import com.project.application.voice.VoiceTtsClient;
import com.project.application.voice.VoiceTurnMetricsService;
import com.project.config.AppProperties;
import com.project.security.JwtAuthenticatedUser;
import org.junit.jupiter.api.Test;
import org.springframework.core.task.SyncTaskExecutor;
import org.springframework.security.core.Authentication;

import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.ArgumentMatchers.isNull;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class VoiceControllerTest {

    private final JwtAuthenticatedUser user = new JwtAuthenticatedUser(UUID.randomUUID(), "demo", "USER");

    @Test
    void emptyTtsTextCanCompleteVoiceTurnWithoutSynthesizingAudio() {
        VoiceTtsClient ttsClient = mock(VoiceTtsClient.class);
        VoiceMetricLogger metricLogger = mock(VoiceMetricLogger.class);
        VoiceTurnMetricsService turnMetricsService = new VoiceTurnMetricsService();
        UUID voiceSessionId = UUID.randomUUID();
        String turnId = "turn-1";
        turnMetricsService.startAsrTurn(voiceSessionId, user.userId(), turnId);
        VoiceController controller = controller(ttsClient, metricLogger, turnMetricsService);

        controller.streamTts(auth(), new VoiceTtsRequest(
            "",
            null,
            voiceSessionId.toString(),
            turnId,
            UUID.randomUUID().toString(),
            "qna_chat",
            "ASK",
            true
        ));

        verify(ttsClient, never()).synthesize(any(), any(), any());
        verify(metricLogger).record(
            eq("voice_turn_total_ms"),
            any(VoiceMetricContext.class),
            anyLong(),
            eq("bailian"),
            eq("qwen3-tts-flash-realtime"),
            eq("success"),
            eq(0),
            isNull(),
            eq("")
        );
        assertThat(turnMetricsService.find(voiceSessionId, turnId)).isNull();
    }

    @Test
    void emptyTtsTextStillRequiresExplicitTurnCompleteMarker() {
        VoiceController controller = controller(mock(VoiceTtsClient.class), mock(VoiceMetricLogger.class), new VoiceTurnMetricsService());

        assertThatThrownBy(() -> controller.streamTts(auth(), new VoiceTtsRequest(
            "",
            null,
            null,
            null,
            null,
            null,
            null,
            null
        )))
            .isInstanceOf(ApplicationException.class)
            .extracting("code")
            .isEqualTo("VOICE_TTS_TEXT_EMPTY");
    }

    private VoiceController controller(
        VoiceTtsClient ttsClient,
        VoiceMetricLogger metricLogger,
        VoiceTurnMetricsService turnMetricsService
    ) {
        return new VoiceController(
            mock(VoiceGatewayService.class),
            ttsClient,
            new AppProperties(),
            new SyncTaskExecutor(),
            metricLogger,
            turnMetricsService,
            mock(VoiceAsrPrewarmService.class)
        );
    }

    private Authentication auth() {
        Authentication authentication = mock(Authentication.class);
        when(authentication.getPrincipal()).thenReturn(user);
        return authentication;
    }
}
