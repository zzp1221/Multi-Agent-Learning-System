package com.project.api.conversation.dto;

import java.time.OffsetDateTime;
import java.util.List;

/**
 * 用于会话历史回放的完整消息项。
 */
public record ConversationMessageItemResponse(
    String messageId,
    String role,
    String content,
    List<String> imageUrls,
    OffsetDateTime createdAt,
    String reasoningContent
) {
    public ConversationMessageItemResponse(
        String messageId,
        String role,
        String content,
        List<String> imageUrls,
        OffsetDateTime createdAt
    ) {
        this(messageId, role, content, imageUrls, createdAt, null);
    }
}
