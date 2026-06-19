package com.project.application.conversation;

import com.project.api.conversation.dto.ConversationMessageItemResponse;

import java.util.List;
import java.util.UUID;

/**
 * 读写由 Python 运行时持久化的会话记录消息。
 */
public interface PythonConversationMessageClient {

    default void appendMessage(UUID conversationId, UUID userId, String role, String content, List<String> imageUrls) {
        appendMessage(conversationId, userId, role, content, imageUrls, null);
    }

    void appendMessage(
        UUID conversationId,
        UUID userId,
        String role,
        String content,
        List<String> imageUrls,
        String reasoningContent
    );

    List<ConversationMessageItemResponse> listMessages(UUID conversationId, UUID userId);

    List<ConversationMessageItemResponse> listMessages(UUID conversationId, UUID userId, Integer page, Integer size);
}
