package com.project.application.conversation;

import com.project.api.conversation.dto.ConversationMessageItemResponse;
import com.project.domain.conversation.QnaSession;
import com.project.domain.conversation.QnaSessionRepository;
import com.project.security.JwtAuthenticatedUser;
import org.apache.catalina.connector.ClientAbortException;
import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.web.context.request.async.AsyncRequestNotUsableException;

import java.io.IOException;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class ConversationServiceClientDisconnectTest {

    private final ConversationService conversationService = new ConversationService(null, null, null, null, null);

    @Test
    void detectsSpringAsyncRequestNotUsableExceptionInCauseChain() {
        RuntimeException wrapped = new RuntimeException(new AsyncRequestNotUsableException("response unavailable"));

        assertThat(isClientDisconnect(wrapped)).isTrue();
    }

    @Test
    void detectsTomcatClientAbortExceptionInCauseChain() {
        RuntimeException wrapped = new RuntimeException(new ClientAbortException(new IOException("socket closed")));

        assertThat(isClientDisconnect(wrapped)).isTrue();
    }

    @Test
    void detectsClientDisconnectFromCommonIOExceptionMessages() {
        assertThat(isClientDisconnect(new IOException("Broken pipe"))).isTrue();
        assertThat(isClientDisconnect(new IOException("Connection reset by peer"))).isTrue();
        assertThat(isClientDisconnect(new IOException("AsyncRequestNotUsableException: response unavailable"))).isTrue();
    }

    @Test
    void ignoresUnrelatedIoExceptionMessages() {
        assertThat(isClientDisconnect(new IOException("disk write failed"))).isFalse();
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
        ConversationService service = new ConversationService(repository, null, messageClient, null, null);

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

    private boolean isClientDisconnect(Throwable throwable) {
        Boolean result = ReflectionTestUtils.invokeMethod(
            conversationService,
            "isClientDisconnect",
            throwable
        );
        return Boolean.TRUE.equals(result);
    }
}
