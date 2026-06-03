package com.project.application.voice;

import com.project.application.smartengine.PythonAgentClient;
import com.project.application.smartengine.PythonStreamEvent;
import org.junit.jupiter.api.Test;

import java.util.Map;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.Consumer;

import static org.assertj.core.api.Assertions.assertThat;

class VoicePartialDraftServiceTest {

    @Test
    void cancelsDraftWhenFinalTranscriptDiffers() throws Exception {
        CapturingPythonClient pythonClient = new CapturingPythonClient();
        VoiceTurnMetricsService turnMetrics = new VoiceTurnMetricsService();
        UUID userId = UUID.randomUUID();
        UUID voiceSessionId = UUID.randomUUID();
        UUID conversationId = UUID.randomUUID();
        turnMetrics.startAsrTurn(voiceSessionId, userId, "turn-1");
        VoicePartialDraftService service = new VoicePartialDraftService(
            pythonClient,
            command -> new Thread(command, "voice-draft-test").start(),
            new VoiceMetricLogger(),
            turnMetrics
        );

        service.startDraft(new VoicePartialDraftService.VoiceDraftRequest(
            userId,
            voiceSessionId,
            "turn-1",
            conversationId,
            "qna_chat",
            "ASK",
            "解释 Java 线程池"
        ));
        assertThat(pythonClient.started.await(1, TimeUnit.SECONDS)).isTrue();
        boolean kept = service.keepOrCancel(voiceSessionId, "turn-1", "解释 数据库 索引");

        assertThat(kept).isFalse();
        assertThat(pythonClient.cancelledTaskId.get()).isNotBlank();
    }

    @Test
    void reusableDraftStreamsExistingChunksWhenFinalTranscriptMatches() throws Exception {
        CapturingPythonClient pythonClient = new CapturingPythonClient();
        VoiceTurnMetricsService turnMetrics = new VoiceTurnMetricsService();
        UUID userId = UUID.randomUUID();
        UUID voiceSessionId = UUID.randomUUID();
        UUID conversationId = UUID.randomUUID();
        turnMetrics.startAsrTurn(voiceSessionId, userId, "turn-1");
        VoicePartialDraftService service = new VoicePartialDraftService(
            pythonClient,
            command -> new Thread(command, "voice-draft-test").start(),
            new VoiceMetricLogger(),
            turnMetrics
        );

        service.startDraft(new VoicePartialDraftService.VoiceDraftRequest(
            userId,
            voiceSessionId,
            "turn-1",
            conversationId,
            "qna_chat",
            "ASK",
            "解释 Java 线程池"
        ));
        assertThat(pythonClient.started.await(1, TimeUnit.SECONDS)).isTrue();
        VoicePartialDraftService.ReusableDraft draft = service.takeReusableDraft(voiceSessionId, "turn-1", "解释 Java 线程池");
        pythonClient.finish.countDown();
        StringBuilder answer = new StringBuilder();

        service.streamDraft(draft, event -> {
            Object text = event.safePayload().get("text");
            if (text instanceof String value) {
                answer.append(value);
            }
        });

        assertThat(answer).hasToString("草稿");
        assertThat(pythonClient.cancelledTaskId.get()).isBlank();
    }

    private static final class CapturingPythonClient implements PythonAgentClient {
        private final AtomicReference<String> cancelledTaskId = new AtomicReference<>("");
        private final CountDownLatch started = new CountDownLatch(1);
        private final CountDownLatch finish = new CountDownLatch(1);

        @Override
        public void stream(com.project.application.smartengine.SmartEngineInvocation invocation, Consumer<PythonStreamEvent> eventConsumer) {
            started.countDown();
            eventConsumer.accept(new PythonStreamEvent("result_chunk", "tutoring", Map.of("text", "草稿")));
            try {
                while (cancelledTaskId.get().isBlank() && !finish.await(10, TimeUnit.MILLISECONDS)) {
                    // 等待测试触发完成或取消。
                }
            } catch (InterruptedException ex) {
                Thread.currentThread().interrupt();
            }
        }

        @Override
        public void cancel(String taskId) {
            cancelledTaskId.set(taskId);
        }
    }
}
