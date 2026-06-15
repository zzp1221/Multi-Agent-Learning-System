package com.project.application.conversation;

import com.project.api.conversation.dto.ConversationMessageItemResponse;
import com.project.api.conversation.dto.ConversationMessageStreamRequest;
import com.project.application.common.ClientDisconnectDetector;
import com.project.application.smartengine.PythonAgentClient;
import com.project.application.settings.UserLlmSettingsService;
import com.project.domain.conversation.QnaSession;
import com.project.domain.conversation.QnaSessionRepository;
import com.project.domain.task.ServiceType;
import com.project.security.JwtAuthenticatedUser;
import org.apache.catalina.connector.ClientAbortException;
import org.junit.jupiter.api.Test;
import org.springframework.web.context.request.async.AsyncRequestNotUsableException;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.io.IOException;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class ConversationServiceClientDisconnectTest {

    @Test
    void detectsSpringAsyncRequestNotUsableExceptionInCauseChain() {
        RuntimeException wrapped = new RuntimeException(new AsyncRequestNotUsableException("response unavailable"));

        assertThat(ClientDisconnectDetector.isClientDisconnect(wrapped)).isTrue();
    }

    @Test
    void detectsTomcatClientAbortExceptionInCauseChain() {
        RuntimeException wrapped = new RuntimeException(new ClientAbortException(new IOException("socket closed")));

        assertThat(ClientDisconnectDetector.isClientDisconnect(wrapped)).isTrue();
    }

    @Test
    void detectsClientDisconnectFromCommonIOExceptionMessages() {
        assertThat(ClientDisconnectDetector.isClientDisconnect(new IOException("Broken pipe"))).isTrue();
        assertThat(ClientDisconnectDetector.isClientDisconnect(new IOException("Connection reset by peer"))).isTrue();
        assertThat(ClientDisconnectDetector.isClientDisconnect(new IOException("AsyncRequestNotUsableException: response unavailable"))).isTrue();
    }

    @Test
    void ignoresUnrelatedIoExceptionMessages() {
        assertThat(ClientDisconnectDetector.isClientDisconnect(new IOException("disk write failed"))).isFalse();
    }

    @Test
    void listConversationMessagesChecksOwnershipBeforeCallingPythonClient() {
        UUID userId = UUID.randomUUID();
        UUID conversationId = UUID.randomUUID();
        QnaSessionRepository repository = mock(QnaSessionRepository.class);
        PythonConversationMessageClient messageClient = mock(PythonConversationMessageClient.class);
        QnaSession session = new QnaSession();
        session.setUserId(userId);
        when(repository.findByIdAndUserId(conversationId, userId)).thenReturn(Optional.of(session));
        when(messageClient.listMessages(conversationId, userId, 0, 50)).thenReturn(List.of(
            new ConversationMessageItemResponse("message-1", "assistant", "ok", List.of(), null)
        ));
        ConversationService service = new ConversationService(repository, null, messageClient, null, null, null, null, null, null, null, null);

        List<ConversationMessageItemResponse> messages = service.listConversationMessages(
            new JwtAuthenticatedUser(userId, "demo", "USER"),
            conversationId,
            null,
            null
        );

        assertThat(messages).hasSize(1);
        verify(repository).findByIdAndUserId(conversationId, userId);
        verify(messageClient).listMessages(conversationId, userId, 0, 50);
    }

    @Test
    void streamMessageReturnsSseErrorBeforePythonWhenFormalUserLlmIsMissing() {
        UUID userId = UUID.randomUUID();
        UUID conversationId = UUID.randomUUID();
        QnaSessionRepository repository = mock(QnaSessionRepository.class);
        PythonAgentClient pythonAgentClient = mock(PythonAgentClient.class);
        PythonConversationMessageClient messageClient = mock(PythonConversationMessageClient.class);
        UserLlmSettingsService userLlmSettingsService = mock(UserLlmSettingsService.class);
        QnaSession session = new QnaSession();
        session.setUserId(userId);
        when(repository.findByIdAndUserId(conversationId, userId)).thenReturn(Optional.of(session));
        when(userLlmSettingsService.isUserLlmReadyOrAllowedFallback(userId)).thenReturn(false);
        ConversationService service = new ConversationService(
            repository,
            pythonAgentClient,
            messageClient,
            Runnable::run,
            null,
            null,
            null,
            null,
            null,
            null,
            userLlmSettingsService
        );

        SseEmitter emitter = service.streamMessage(
            new JwtAuthenticatedUser(userId, "demo", "USER"),
            conversationId,
            new ConversationMessageStreamRequest("hello", List.of(), ServiceType.TUTORING, false, null, null)
        );

        assertThat(emitter).isNotNull();
        verify(pythonAgentClient, never()).stream(any(), any());
        verify(messageClient, never()).appendMessage(any(), any(), any(), any(), any());
    }

    @Test
    void streamMessageReturnsSseErrorForEmptyInputBeforeRepositoryLookup() {
        UUID userId = UUID.randomUUID();
        UUID conversationId = UUID.randomUUID();
        QnaSessionRepository repository = mock(QnaSessionRepository.class);
        PythonAgentClient pythonAgentClient = mock(PythonAgentClient.class);
        PythonConversationMessageClient messageClient = mock(PythonConversationMessageClient.class);
        ConversationService service = new ConversationService(
            repository,
            pythonAgentClient,
            messageClient,
            Runnable::run,
            null,
            null,
            null,
            null,
            null,
            null,
            null
        );

        SseEmitter emitter = service.streamMessage(
            new JwtAuthenticatedUser(userId, "demo", "USER"),
            conversationId,
            new ConversationMessageStreamRequest("  ", List.of(), ServiceType.TUTORING, false, null, null)
        );

        assertThat(emitter).isNotNull();
        verify(repository, never()).findByIdAndUserId(any(), any());
        verify(pythonAgentClient, never()).stream(any(), any());
        verify(messageClient, never()).appendMessage(any(), any(), any(), any(), any());
    }

    @Test
    void streamMessageReturnsSseErrorWhenConversationIsMissing() {
        UUID userId = UUID.randomUUID();
        UUID conversationId = UUID.randomUUID();
        QnaSessionRepository repository = mock(QnaSessionRepository.class);
        PythonAgentClient pythonAgentClient = mock(PythonAgentClient.class);
        PythonConversationMessageClient messageClient = mock(PythonConversationMessageClient.class);
        when(repository.findByIdAndUserId(conversationId, userId)).thenReturn(Optional.empty());
        ConversationService service = new ConversationService(
            repository,
            pythonAgentClient,
            messageClient,
            Runnable::run,
            null,
            null,
            null,
            null,
            null,
            null,
            null
        );

        SseEmitter emitter = service.streamMessage(
            new JwtAuthenticatedUser(userId, "demo", "USER"),
            conversationId,
            new ConversationMessageStreamRequest("hello", List.of(), ServiceType.TUTORING, false, null, null)
        );

        assertThat(emitter).isNotNull();
        verify(repository).findByIdAndUserId(conversationId, userId);
        verify(pythonAgentClient, never()).stream(any(), any());
        verify(messageClient, never()).appendMessage(any(), any(), any(), any(), any());
    }
}
