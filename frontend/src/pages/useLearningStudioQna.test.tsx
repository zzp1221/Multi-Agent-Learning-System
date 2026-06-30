import { render, renderHook, screen, act, waitFor } from '@testing-library/react';
import { useCallback, useRef } from 'react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { conversationApi, type ConversationStreamEvent } from '../api/conversation';
import { learningPathApi } from '../api/smartEngine';
import { useLearningStudioQna } from './useLearningStudioQna';

vi.mock('../api/conversation', () => ({
  conversationApi: {
    createConversation: vi.fn(),
    getConversationMessages: vi.fn(),
    streamMessage: vi.fn(),
    uploadImage: vi.fn(),
  },
}));

vi.mock('../api/smartEngine', () => ({
  learningPathApi: {
    current: vi.fn(),
  },
}));

function renderQnaHook() {
  const conversationIdRef = { current: '' };
  const mountedRef = { current: true };
  const setConversationId = vi.fn((next: string | ((previous: string) => string)) => {
    conversationIdRef.current = typeof next === 'function' ? next(conversationIdRef.current) : next;
  });

  return renderHook(() => useLearningStudioQna({
    mode: 'qna',
    isAuthenticated: true,
    currentUser: null,
    openAuthModal: vi.fn(),
    conversationId: conversationIdRef.current,
    setConversationId,
    conversationIdRef,
    mountedRef,
  }));
}

function QnaHarness() {
  const conversationIdRef = useRef('');
  const mountedRef = useRef(true);
  const setConversationId = useCallback((next: string | ((previous: string) => string)) => {
    conversationIdRef.current = typeof next === 'function' ? next(conversationIdRef.current) : next;
  }, []);
  const qna = useLearningStudioQna({
    mode: 'qna',
    isAuthenticated: true,
    currentUser: null,
    openAuthModal: vi.fn(),
    conversationId: conversationIdRef.current,
    setConversationId,
    conversationIdRef,
    mountedRef,
  });

  return (
    <div>
      <textarea
        aria-label="qna input"
        value={qna.viewProps.qnaInput}
        onChange={(event) => qna.viewProps.onChange(event.target.value)}
      />
      <button type="button" onClick={qna.viewProps.onToggleWebSearch}>web</button>
      <button type="button" onClick={qna.viewProps.onToggleDeepReasoning}>deep</button>
      <button type="button" onClick={() => void qna.viewProps.onSend()}>send</button>
    </div>
  );
}

describe('useLearningStudioQna', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.sessionStorage.clear();
    vi.mocked(conversationApi.createConversation).mockResolvedValue({ conversationId: 'conversation-1' });
    vi.mocked(conversationApi.streamMessage).mockResolvedValue();
    vi.mocked(learningPathApi.current).mockResolvedValue(
      null as unknown as Awaited<ReturnType<typeof learningPathApi.current>>,
    );
  });

  it('sends with DEEP reasoning and web search after deep reasoning is toggled', async () => {
    const user = userEvent.setup();
    render(<QnaHarness />);

    await user.type(screen.getByLabelText('qna input'), 'Explain dynamic programming');
    await user.click(screen.getByRole('button', { name: 'web' }));
    await user.click(screen.getByRole('button', { name: 'deep' }));
    expect(conversationApi.streamMessage).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'send' }));

    await waitFor(() => expect(conversationApi.streamMessage).toHaveBeenCalled());

    const request = vi.mocked(conversationApi.streamMessage).mock.calls[0][1];
    expect(request.message).toBe('Explain dynamic programming');
    expect(request.reasoningMode).toBe('DEEP');
    expect(request.webSearchEnabled).toBe(true);
  });

  it('keeps DEEP and web search when send receives a click event argument', async () => {
    const { result } = renderQnaHook();

    act(() => {
      result.current.viewProps.onChange('Explain HashMap');
      result.current.viewProps.onToggleWebSearch();
      result.current.viewProps.onToggleDeepReasoning();
    });
    await act(async () => {
      await result.current.viewProps.onSend(
        { preventDefault: vi.fn() } as unknown as Parameters<typeof result.current.viewProps.onSend>[0],
      );
    });

    await waitFor(() => expect(conversationApi.streamMessage).toHaveBeenCalled());
    const request = vi.mocked(conversationApi.streamMessage).mock.calls[0][1];
    expect(request.reasoningMode).toBe('DEEP');
    expect(request.webSearchEnabled).toBe(true);
  });

  it('does not show NORMAL web reasoning_chunk as deep reasoning', async () => {
    vi.mocked(conversationApi.streamMessage).mockImplementation(async (_conversationId, _request, handlers) => {
      handlers.onEvent({
        event: 'reasoning_chunk',
        data: { payload: { text: 'retrieval progress' } },
      } as ConversationStreamEvent);
    });
    const { result } = renderQnaHook();

    act(() => {
      result.current.viewProps.onChange('Find current references');
      result.current.viewProps.onToggleWebSearch();
    });
    await act(async () => {
      await result.current.viewProps.onSend();
    });

    await waitFor(() => expect(conversationApi.streamMessage).toHaveBeenCalled());
    const request = vi.mocked(conversationApi.streamMessage).mock.calls[0][1];
    const assistant = result.current.viewProps.qnaMessages.find((message) => message.role === 'assistant' && message.id.startsWith('qna-assistant-'));

    expect(request.reasoningMode).toBe('NORMAL');
    expect(request.webSearchEnabled).toBe(true);
    expect(assistant?.reasoningContent).toBeUndefined();
    expect(assistant?.reasoningState).toBeUndefined();
  });

  it('shows public resource trace in NORMAL reasoning mode', async () => {
    vi.mocked(conversationApi.streamMessage).mockImplementation(async (_conversationId, _request, handlers) => {
      handlers.onEvent({
        event: 'reasoning_chunk',
        data: {
          payload: {
            text: 'Tutor Agent 已识别到资源生成请求。',
            stage: 'resource_generation',
            publicTrace: true,
            agentName: 'Tutor',
            phase: 'intent',
            status: 'RUNNING',
            percent: 24,
          },
        },
      } as ConversationStreamEvent);
    });
    const { result } = renderQnaHook();

    act(() => {
      result.current.viewProps.onChange('生成一份联合索引 PPT');
    });
    await act(async () => {
      await result.current.viewProps.onSend();
    });

    const assistant = result.current.viewProps.qnaMessages.find((message) => message.role === 'assistant' && message.id.startsWith('qna-assistant-'));

    expect(assistant?.reasoningContent).toBeUndefined();
    expect(assistant?.agentTraceItems).toHaveLength(1);
    expect(assistant?.agentTraceItems?.[0]).toMatchObject({
      agentName: 'Tutor',
      phase: 'intent',
      status: 'RUNNING',
      percent: 24,
    });
    expect(assistant?.collaborationState).toBe('streaming');
  });

  it('keeps collaboration streaming when one public trace item fails', async () => {
    vi.mocked(conversationApi.streamMessage).mockImplementation(async (_conversationId, _request, handlers) => {
      handlers.onEvent({
        event: 'reasoning_chunk',
        data: {
          payload: {
            text: 'Slides Agent 生成失败，资源包将继续处理其他资源。',
            stage: 'resource_generation',
            publicTrace: true,
            agentName: 'Slides',
            artifactType: 'SLIDES',
            phase: 'failed',
            status: 'FAILED',
          },
        },
      } as ConversationStreamEvent);
    });
    const { result } = renderQnaHook();

    act(() => {
      result.current.viewProps.onChange('生成一个资料包');
    });
    await act(async () => {
      await result.current.viewProps.onSend();
    });

    const assistant = result.current.viewProps.qnaMessages.find((message) => message.role === 'assistant' && message.id.startsWith('qna-assistant-'));

    expect(assistant?.agentTraceItems?.[0]).toMatchObject({
      agentName: 'Slides',
      phase: 'failed',
      status: 'FAILED',
    });
    expect(assistant?.collaborationState).toBe('streaming');
  });

  it('keeps public trace message visible after done even before text content', async () => {
    vi.mocked(conversationApi.streamMessage).mockImplementation(async (_conversationId, _request, handlers) => {
      handlers.onEvent({
        event: 'reasoning_chunk',
        data: {
          payload: {
            text: 'Resource Bundle 正在汇总已完成资源。',
            stage: 'resource_generation',
            publicTrace: true,
            agentName: 'Resource Bundle',
            phase: 'publish',
            status: 'SUCCESS',
            percent: 96,
          },
        },
      } as ConversationStreamEvent);
      handlers.onDone();
    });
    const { result } = renderQnaHook();

    act(() => {
      result.current.viewProps.onChange('生成一个资料包');
    });
    await act(async () => {
      await result.current.viewProps.onSend();
    });

    const assistant = result.current.viewProps.qnaMessages.find((message) => message.role === 'assistant' && message.id.startsWith('qna-assistant-'));

    expect(assistant).toBeDefined();
    expect(assistant?.agentTraceItems).toHaveLength(1);
    expect(assistant?.content).toBe('');
    expect(assistant?.collaborationState).toBe('done');
  });

  it('dedupes repeated deep reasoning chunks line by line', async () => {
    vi.mocked(conversationApi.streamMessage).mockImplementation(async (_conversationId, _request, handlers) => {
      handlers.onEvent({
        event: 'reasoning_chunk',
        data: { payload: { text: '然后组织答案：按证据回答\n最后自检：只引用采用来源\n' } },
      } as ConversationStreamEvent);
      handlers.onEvent({
        event: 'reasoning_chunk',
        data: { payload: { text: '然后组织答案：按证据回答\n最后自检：只引用采用来源\n' } },
      } as ConversationStreamEvent);
    });
    const { result } = renderQnaHook();

    act(() => {
      result.current.viewProps.onChange('Find current references');
      result.current.viewProps.onToggleDeepReasoning();
    });
    await act(async () => {
      await result.current.viewProps.onSend();
    });

    const assistant = result.current.viewProps.qnaMessages.find((message) => message.role === 'assistant' && message.id.startsWith('qna-assistant-'));
    const reasoning = assistant?.reasoningContent ?? '';
    expect(reasoning.match(/然后组织答案/g)).toHaveLength(1);
    expect(reasoning.match(/最后自检/g)).toHaveLength(1);
    expect(reasoning).toContain('最后自检：只引用采用来源');
  });
});
