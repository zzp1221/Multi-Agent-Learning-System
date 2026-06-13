import { API_BASE_URL, getAuthHeaders, request } from './request';
import type { AxiosRequestConfig } from 'axios';
import {
  parseStreamEnvelope,
  readStreamMessage,
  streamSse,
  type ConversationStreamEventEnvelope,
} from './sse';

export type { ConversationDialogState } from './sse';

export interface CreateConversationResponse {
  conversationId: string;
  title?: string;
}

export interface ConversationMessageStreamRequest {
  message: string;
  imageUrls?: string[];
  serviceType?: string;
  webSearchEnabled?: boolean;
  reasoningMode?: 'NORMAL' | 'DEEP';
  voiceContext?: {
    pageType?: string;
    questionId?: string;
    courseId?: string;
    knowledgePointId?: string;
    pageTitle?: string;
    currentPath?: string;
    source?: string;
    conversationId?: string;
    recentMessagesSummary?: string;
    commandIntent?: string;
    voiceSessionId?: string;
    voiceTurnId?: string;
    selectedService?: string;
    formParametersSummary?: string;
    taskStatus?: string;
    currentMistakeSummary?: string;
    reviewStatus?: string;
    weakPointsSummary?: string;
    currentGoal?: string;
    lowestMasteryKnowledge?: string;
    resourceResultSummary?: string;
    downloadResourceSummary?: string;
    recommendedAction?: string;
    activeLearningStepId?: string;
    activeLearningStepTitle?: string;
    activeLearningStepProgress?: string;
    activeLearningStepSummary?: string;
    explicitUserTopic?: string;
    noteId?: string;
    noteTitle?: string;
    noteExcerpt?: string;
    questionCount?: string;
    questionTypePreference?: string;
    difficultyPreference?: string;
  };
}

export interface ConversationHistoryItem {
  conversationId: string;
  title: string;
  lastMessagePreview?: string;
  messageCount?: number;
  lastMessageAt?: string;
  updatedAt?: string;
}

export interface ConversationMessageItem {
  messageId: string;
  role: 'user' | 'assistant';
  content: string;
  imageUrls?: string[];
  createdAt?: string;
}

export interface UploadedConversationImage {
  imageUrl: string;
  fileName: string;
  sizeBytes: number;
  contentType: string;
}

export type ConversationStreamEventPayload = ConversationStreamEventEnvelope;

export interface ConversationStreamEvent {
  event: string;
  data: ConversationStreamEventPayload;
}

export const conversationApi = {
  async listRecentConversations(): Promise<ConversationHistoryItem[]> {
    return request.get<ConversationHistoryItem[]>('/api/conversations', {
      dedupe: true,
      dedupeKey: 'recent-conversations',
    });
  },

  async createConversation(): Promise<CreateConversationResponse> {
    return request.post<CreateConversationResponse>('/api/conversations');
  },

  async uploadImage(file: File, onUploadProgress?: (percent: number) => void): Promise<UploadedConversationImage> {
    const formData = new FormData();
    formData.append('file', file);
    return request.post<UploadedConversationImage>('/api/conversations/images/upload', formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
      onUploadProgress: (event) => {
        if (!onUploadProgress || !event.total) {
          return;
        }
        onUploadProgress(Math.round((event.loaded / event.total) * 100));
      },
    });
  },

  async getConversationMessages(
    conversationId: string,
    config?: AxiosRequestConfig & { dedupe?: boolean; retry?: number },
  ): Promise<ConversationMessageItem[]> {
    return request.get<ConversationMessageItem[]>(`/api/conversations/${conversationId}/messages`, {
      ...config,
      dedupe: config?.dedupe ?? true,
      dedupeKey: `conversation-messages:${conversationId}`,
    });
  },

  async streamMessage(
    conversationId: string,
    request: ConversationMessageStreamRequest,
    handlers: {
      onOpen?: () => void;
      onEvent: (event: ConversationStreamEvent) => void;
      onDone: () => void;
      onError: (error: Error) => void;
    },
    signal?: AbortSignal,
  ): Promise<void> {
    await streamSse(`${API_BASE_URL}/api/conversations/${conversationId}/messages/stream`, {
      init: {
        method: 'POST',
        headers: {
          Accept: 'text/event-stream',
          'Content-Type': 'application/json',
          ...getAuthHeaders(),
        },
        body: JSON.stringify(request),
        signal,
      },
      missingBodyMessage: '无法读取会话流',
      requestFailedMessage: (status) => `会话请求失败 (${status})`,
      onOpen: handlers.onOpen,
      onEvent: (rawEvent) => {
        const parsed: ConversationStreamEvent = {
          event: rawEvent.event,
          data: parseStreamEnvelope<ConversationStreamEventPayload>(rawEvent.data),
        };
        handlers.onEvent(parsed);
        if (parsed.event === 'done') {
          handlers.onDone();
          return true;
        }
        if (parsed.event === 'error') {
          const message = readStreamMessage(parsed.data.payload) || '会话流执行失败';
          handlers.onError(new Error(message));
          return true;
        }
        return false;
      },
      onDone: handlers.onDone,
      onError: handlers.onError,
    });
  },
};
