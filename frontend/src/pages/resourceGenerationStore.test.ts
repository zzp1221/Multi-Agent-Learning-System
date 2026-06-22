import { beforeEach, describe, expect, it } from 'vitest';
import { AUTH_USER_STORAGE_KEY } from '../api/request';
import {
  loadResourceGenerationSession,
  recordConversationResourceEvent,
} from './resourceGenerationStore';

const llmProvenance = {
  generatedBy: 'LLM',
  contentOrigin: 'LLM',
  provider: 'unit-provider',
  model: 'unit-model',
  agentName: 'document_generator',
  evidenceIds: ['doc-1'],
  fallback: false,
  fromCache: false,
};

describe('resourceGenerationStore', () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
  });

  it('restores the last resource session after sessionStorage is cleared', () => {
    window.localStorage.setItem(AUTH_USER_STORAGE_KEY, JSON.stringify({ id: 'user-1' }));

    recordConversationResourceEvent('conversation-1', 'resource_file', {
      event: 'resource_file',
      seq: 1,
      payload: {
        ...llmProvenance,
        taskId: 'task-1',
        assetType: 'DOCUMENT',
        title: '联合索引讲义',
        summary: '已生成讲义',
        displayMode: 'MARKDOWN_CARD',
        fileName: 'guide.md',
        downloadUrl: '/api/assets/download/token',
        expiresAt: '2026-06-19T12:00:00Z',
      },
    });
    recordConversationResourceEvent('conversation-1', 'done', {
      event: 'done',
      seq: 2,
      payload: { taskId: 'task-1', status: 'SUCCESS', summary: '资源生成完成' },
    });

    window.sessionStorage.clear();
    const restored = loadResourceGenerationSession('');

    expect(restored.conversationId).toBe('conversation-1');
    expect(restored.ownerUserId).toBe('user-1');
    expect(restored.taskId).toBe('task-1');
    expect(restored.taskStatus).toBe('completed');
    expect(restored.resources[0]).toMatchObject({
      title: '联合索引讲义',
      status: 'ready',
      downloadUrl: '/api/assets/download/token',
      expiresAt: '2026-06-19T12:00:00Z',
    });
  });

  it('keeps sessions isolated by authenticated user', () => {
    window.localStorage.setItem(AUTH_USER_STORAGE_KEY, JSON.stringify({ id: 'user-1' }));
    recordConversationResourceEvent('conversation-1', 'question_batch', {
      event: 'question_batch',
      seq: 1,
      payload: {
        ...llmProvenance,
        taskId: 'task-quiz',
        title: '索引练习',
        topic: '联合索引',
        questions: [{ id: 'q1', stem: '题干' }],
      },
    });

    window.localStorage.setItem(AUTH_USER_STORAGE_KEY, JSON.stringify({ id: 'user-2' }));
    const userTwoSession = loadResourceGenerationSession('');

    expect(userTwoSession.ownerUserId).toBe('user-2');
    expect(userTwoSession.resources).toHaveLength(0);
  });

  it('preserves another user resource session when the current user writes', () => {
    window.localStorage.setItem(AUTH_USER_STORAGE_KEY, JSON.stringify({ id: 'user-1' }));
    recordConversationResourceEvent('conversation-1', 'resource_file', {
      event: 'resource_file',
      seq: 1,
      payload: {
        ...llmProvenance,
        taskId: 'task-user-1',
        assetType: 'DOCUMENT',
        title: '用户一讲义',
        displayMode: 'MARKDOWN_CARD',
        fileName: 'user-1.md',
        downloadUrl: '/api/assets/download/user-1',
      },
    });

    window.localStorage.setItem(AUTH_USER_STORAGE_KEY, JSON.stringify({ id: 'user-2' }));
    recordConversationResourceEvent('conversation-2', 'resource_file', {
      event: 'resource_file',
      seq: 1,
      payload: {
        ...llmProvenance,
        taskId: 'task-user-2',
        assetType: 'DOCUMENT',
        title: '用户二讲义',
        displayMode: 'MARKDOWN_CARD',
        fileName: 'user-2.md',
        downloadUrl: '/api/assets/download/user-2',
      },
    });

    expect(loadResourceGenerationSession('').resources[0]).toMatchObject({
      title: '用户二讲义',
      downloadUrl: '/api/assets/download/user-2',
    });

    window.localStorage.setItem(AUTH_USER_STORAGE_KEY, JSON.stringify({ id: 'user-1' }));
    const restoredUserOneSession = loadResourceGenerationSession('');

    expect(restoredUserOneSession.conversationId).toBe('conversation-1');
    expect(restoredUserOneSession.ownerUserId).toBe('user-1');
    expect(restoredUserOneSession.resources[0]).toMatchObject({
      title: '用户一讲义',
      downloadUrl: '/api/assets/download/user-1',
    });
  });

  it('drops legacy slide outline confirmation resources from incoming events', () => {
    window.localStorage.setItem(AUTH_USER_STORAGE_KEY, JSON.stringify({ id: 'user-1' }));

    recordConversationResourceEvent('conversation-1', 'resource_file', {
      event: 'resource_file',
      seq: 1,
      payload: {
        ...llmProvenance,
        taskId: 'task-outline',
        assetType: 'SLIDES',
        displayMode: 'SLIDE_OUTLINE_CONFIRMATION',
        title: 'Java SpringPPT大纲',
        summary: 'PPT 大纲已生成，等待用户确认后再生成演示文件',
        inlineContent: '# Java SpringPPT大纲',
        fileName: 'outline.md',
        downloadUrl: '/api/assets/download/outline',
      },
    });

    expect(loadResourceGenerationSession('conversation-1').resources).toHaveLength(0);
  });

  it('records failed resources from done resourceFailures with retry params', () => {
    window.localStorage.setItem(AUTH_USER_STORAGE_KEY, JSON.stringify({ id: 'user-1' }));

    recordConversationResourceEvent('conversation-1', 'resource_file', {
      event: 'resource_file',
      seq: 1,
      payload: {
        ...llmProvenance,
        taskId: 'task-partial',
        assetType: 'DOCUMENT',
        title: '索引讲义',
        displayMode: 'MARKDOWN_CARD',
        fileName: 'guide.md',
        downloadUrl: '/api/assets/download/guide',
        params: {
          topic: '数据库索引',
          resourceTypes: ['DOCUMENT', 'SLIDES'],
        },
      },
    });
    recordConversationResourceEvent('conversation-1', 'done', {
      event: 'done',
      seq: 2,
      payload: {
        taskId: 'task-partial',
        status: 'PARTIAL_FAILED',
        summary: '部分完成',
        resourceFailures: [
          {
            resourceType: 'SLIDES',
            agentName: 'slide_generator',
            error: 'off topic',
            verdict: 'REJECT',
            issues: ['off topic'],
            suggestions: ['regenerate'],
            criticReview: {
              verdict: 'REJECT',
              summaryText: 'off topic',
              issues: ['off topic'],
              suggestions: ['regenerate'],
            },
          },
        ],
      },
    });

    const session = loadResourceGenerationSession('conversation-1');
    expect(session.taskStatus).toBe('partial_failed');
    expect(session.resources).toHaveLength(2);
    expect(session.resources[1]).toMatchObject({
      type: 'SLIDES',
      status: 'failed',
      failureReason: 'off topic',
      retryParams: {
        topic: '数据库索引',
        resourceTypes: ['SLIDES'],
      },
    });
    expect(session.resources[1].criticReview?.verdict).toBe('REJECT');
  });

  it('migrates stored legacy slide outlines while keeping final PPTist decks', () => {
    window.localStorage.setItem(AUTH_USER_STORAGE_KEY, JSON.stringify({ id: 'user-1' }));
    window.localStorage.setItem('learning_studio_last_resource_session', JSON.stringify({ 'user-1': 'conversation-1' }));
    window.localStorage.setItem('learning_studio_conversation_resources', JSON.stringify({
      'user-1::conversation-1': {
        conversationId: 'conversation-1',
        ownerUserId: 'user-1',
        taskStatus: 'completed',
        conversationTriggered: true,
        progress: 100,
        statusText: '资源生成完成',
        updatedAt: 1,
        resources: [
          {
            id: 'SLIDES:Java SpringPPT大纲',
            type: 'SLIDES',
            title: 'Java SpringPPT大纲',
            summary: 'PPT 大纲已生成，等待用户确认后再生成演示文件',
            status: 'ready',
            inline: {
              kind: 'markdown',
              title: 'Java SpringPPT大纲',
              content: '# Java SpringPPT大纲',
            },
            updatedAt: 1,
          },
          {
            id: 'SLIDES:Java SpringPPT课件',
            type: 'SLIDES',
            title: 'Java SpringPPT课件',
            status: 'ready',
            pptistSlides: '{"slides":[{"id":"1"}]}',
            updatedAt: 2,
          },
        ],
      },
    }));

    const restored = loadResourceGenerationSession('');
    const stored = JSON.parse(window.localStorage.getItem('learning_studio_conversation_resources') || '{}');

    expect(restored.resources).toHaveLength(1);
    expect(restored.resources[0]).toMatchObject({
      title: 'Java SpringPPT课件',
      pptistSlides: '{"slides":[{"id":"1"}]}',
    });
    expect(stored['user-1::conversation-1'].resources).toHaveLength(1);
    expect(stored['user-1::conversation-1'].resources[0].title).toBe('Java SpringPPT课件');
  });
});
