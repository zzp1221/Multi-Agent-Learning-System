import { useCallback, useEffect, useRef, useState, type Dispatch, type MutableRefObject, type SetStateAction } from 'react';
import { conversationApi, type ConversationMessageStreamRequest } from '../api/conversation';
import { getErrorMessage } from '../api/request';
import { learningPathApi, type LearningPathCurrentResponse } from '../api/smartEngine';
import type { LayoutOutletContext } from '../components/Layout';
import {
  QNA_GREETING,
  type ChatMessage,
  type PendingChatImage,
  type SlideOutlineConfirmation,
  type QnaState,
} from './LearningStudioDemoPage.types';
import {
  readConversationChunk,
  readPracticeQuestionBatch,
} from './LearningStudioDemoPage.utils';
import {
  VOICE_CONVERSATION_STREAM_EVENT,
  readVoiceConversationStreamDetail,
} from '../utils/voiceConversationBridge';
import { loadResourceGenerationSession, markSlideOutlineRejected, recordConversationResourceEvent } from './resourceGenerationStore';
import { openPracticeSession } from './practiceSessionStore';
import {
  ACTIVE_CONVERSATION_ID_STORAGE_KEY,
  QNA_CONVERSATION_CACHE_STORAGE_KEY,
  QNA_SNAPSHOT_STORAGE_KEY,
  SELECTED_CONVERSATION_STORAGE_KEY,
  buildConversationSyncSignature,
  conversationCacheKey,
  hasPendingAssistantResponse,
  hasResolvedAssistantResponse,
  isProcessingOnlyAssistantContent,
  mapConversationHistory,
  normalizeRestoredQnaMessages,
  pickPreferredConversationMessages,
  type PersistedConversationViewSnapshot,
  type PersistedQnaConversationCache,
  type PersistedQnaSnapshot,
  type QnaDrafts,
  type SelectedConversationSnapshot,
} from './LearningStudioDemoPage.model';

const QNA_STREAM_HISTORY_MAX_ATTEMPTS = 60;
const QNA_HISTORY_POLL_INTERVAL_MS = 2000;
type QnaVoiceContext = NonNullable<ConversationMessageStreamRequest['voiceContext']>;

interface QnaSendOverride {
  text: string;
  confirmedSlideOutlineText?: string;
  confirmedSlideTopic?: string;
  confirmedSlideOutline?: boolean;
}

interface ResourceIntentContext {
  voiceContext?: QnaVoiceContext;
  missingTopicMessage?: string;
}

interface ResourceConversationIntent {
  isResourceIntent: boolean;
  isSlides: boolean;
  isQuiz: boolean;
  explicitUserTopic: string;
  questionCount?: number;
  questionTypePreference?: string;
  difficultyPreference?: string;
}

interface ActiveLearningStepContext {
  stepId: string;
  title: string;
  progress: number;
  summary: string;
}

const GENERIC_RESOURCE_TOPIC_PATTERN =
  /^(?:一份|一套|一个|一种|一张|几个|几道|一些|这个|这份|这套|此|本)?(?:PPT大纲|ppt大纲|PPT文件|ppt文件|PPT|ppt|slides?|课件|幻灯片|演示文稿|大纲|文档|资料|资源|学习资源|练习题|习题|题目|视频|代码案例)?$/i;

interface UseLearningStudioQnaOptions {
  mode: 'qna' | 'engine';
  isAuthenticated: boolean;
  currentUser: LayoutOutletContext['currentUser'];
  openAuthModal: LayoutOutletContext['openAuthModal'];
  conversationId: string;
  setConversationId: Dispatch<SetStateAction<string>>;
  conversationIdRef: MutableRefObject<string>;
  mountedRef: MutableRefObject<boolean>;
}

export function useLearningStudioQna({
  mode,
  isAuthenticated,
  currentUser,
  openAuthModal,
  conversationId,
  setConversationId,
  conversationIdRef,
  mountedRef,
}: UseLearningStudioQnaOptions) {
  const qnaStreamControllersRef = useRef<Record<string, AbortController>>({});
  const qnaMessagesRef = useRef<ChatMessage[]>([{ id: 'qna-greeting', role: 'assistant', content: QNA_GREETING }]);
  const qnaInputRef = useRef('');
  const qnaStateRef = useRef<QnaState>('QNA_IDLE');
  const qnaSnapshotHydratedRef = useRef(false);
  const loadingConversationIdRef = useRef('');
  const qnaDraftsRef = useRef<QnaDrafts>({});
  const qnaConversationCacheRef = useRef<PersistedQnaConversationCache>({});
  const qnaStreamTokensRef = useRef<Record<string, string>>({});
  const qnaHistorySyncTokensRef = useRef<Record<string, number>>({});
  const confirmedSlideOutlineStreamsRef = useRef<Record<string, boolean>>({});
  const qnaRequestVersionRef = useRef(0);
  const previousModeRef = useRef(mode);

  const [qnaState, setQnaState] = useState<QnaState>('QNA_IDLE');
  const [qnaMessages, setQnaMessages] = useState<ChatMessage[]>([{ id: 'qna-greeting', role: 'assistant', content: QNA_GREETING }]);
  const [qnaInput, setQnaInput] = useState('');
  const [pendingQnaImages, setPendingQnaImages] = useState<PendingChatImage[]>([]);
  const [qnaImageError, setQnaImageError] = useState('');
  const [qnaWebSearchEnabled, setQnaWebSearchEnabled] = useState(false);
  const [deepReasoningEnabled, setDeepReasoningEnabled] = useState(false);
  const qnaBusy = qnaState === 'QNA_STREAMING';
  const hasStartedConversation = Boolean(conversationId)
    || qnaMessages.length > 1
    || qnaMessages.some((item) => item.role === 'user');

  const abortQnaStreams = useCallback(() => {
    Object.values(qnaStreamControllersRef.current).forEach((controller) => controller.abort());
    qnaStreamControllersRef.current = {};
    qnaStreamTokensRef.current = {};
    qnaHistorySyncTokensRef.current = {};
  }, []);

  const clearPersistedQnaSnapshot = useCallback(() => {
    if (typeof window === 'undefined') {
      return;
    }
    window.sessionStorage.removeItem(QNA_SNAPSHOT_STORAGE_KEY);
  }, []);

  const persistQnaConversationCache = useCallback(() => {
    if (typeof window === 'undefined') {
      return;
    }
    window.sessionStorage.setItem(
      QNA_CONVERSATION_CACHE_STORAGE_KEY,
      JSON.stringify(qnaConversationCacheRef.current),
    );
  }, []);

  const cacheConversationView = useCallback((
    targetConversationId: string,
    snapshot: PersistedConversationViewSnapshot,
  ) => {
    qnaConversationCacheRef.current[conversationCacheKey(targetConversationId)] = snapshot;
    persistQnaConversationCache();
  }, [persistQnaConversationCache]);

  const setQnaStateView = useCallback((nextState: QnaState) => {
    qnaStateRef.current = nextState;
    setQnaState(nextState);
  }, []);

  const updateQnaConversationMessages = useCallback((
    targetConversationId: string,
    updater: (messages: ChatMessage[]) => ChatMessage[],
    options: { qnaInput?: string; qnaState?: QnaState } = {},
  ) => {
    const normalizedConversationId = targetConversationId.trim();
    const cacheKey = conversationCacheKey(normalizedConversationId);
    const cachedSnapshot = qnaConversationCacheRef.current[cacheKey];
    const isVisibleConversation = conversationIdRef.current === normalizedConversationId;
    const canUpdateVisibleConversation = isVisibleConversation && mountedRef.current;
    const sourceMessages = canUpdateVisibleConversation
      ? qnaMessagesRef.current
      : cachedSnapshot?.qnaMessages ?? (isVisibleConversation ? qnaMessagesRef.current : []);
    const nextMessages = updater(sourceMessages);
    const nextSnapshot: PersistedConversationViewSnapshot = {
      qnaInput: options.qnaInput ?? cachedSnapshot?.qnaInput ?? (isVisibleConversation ? qnaInputRef.current : ''),
      qnaMessages: nextMessages,
      qnaState: options.qnaState ?? cachedSnapshot?.qnaState ?? (isVisibleConversation ? qnaStateRef.current : 'QNA_IDLE'),
    };

    cacheConversationView(normalizedConversationId, nextSnapshot);

    if (!canUpdateVisibleConversation) {
      return;
    }
    qnaMessagesRef.current = nextMessages;
    setQnaMessages(nextMessages);
    if (options.qnaInput !== undefined) {
      qnaInputRef.current = options.qnaInput;
      setQnaInput(options.qnaInput);
    }
    if (options.qnaState !== undefined) {
      setQnaStateView(options.qnaState);
    }
  }, [cacheConversationView, conversationIdRef, mountedRef, setQnaStateView]);

  const resetQnaConversation = useCallback(() => {
    qnaRequestVersionRef.current += 1;
    cacheConversationView(conversationIdRef.current, {
      qnaInput: qnaInputRef.current,
      qnaMessages: qnaMessagesRef.current,
      qnaState: qnaStateRef.current,
    });
    const nextMessages: ChatMessage[] = [{ id: 'qna-greeting', role: 'assistant', content: QNA_GREETING }];
    const nextInput = qnaDraftsRef.current.__new__ ?? '';
    conversationIdRef.current = '';
    qnaMessagesRef.current = nextMessages;
    qnaInputRef.current = nextInput;
    setConversationId('');
    setQnaMessages(nextMessages);
    setQnaInput(nextInput);
    setQnaWebSearchEnabled(false);
    setQnaStateView('QNA_IDLE');
    clearPersistedQnaSnapshot();
    if (typeof window !== 'undefined') {
      window.sessionStorage.removeItem(ACTIVE_CONVERSATION_ID_STORAGE_KEY);
    }
    window.dispatchEvent(new CustomEvent('app:active-conversation-changed', { detail: { conversationId: '' } }));
  }, [cacheConversationView, clearPersistedQnaSnapshot, conversationIdRef, setConversationId, setQnaStateView]);

  useEffect(() => {
    qnaMessagesRef.current = qnaMessages;
  }, [qnaMessages]);

  useEffect(() => {
    qnaInputRef.current = qnaInput;
    qnaDraftsRef.current[conversationIdRef.current || '__new__'] = qnaInput;
  }, [conversationIdRef, qnaInput]);

  useEffect(() => {
    qnaStateRef.current = qnaState;
  }, [qnaState]);

  useEffect(() => {
    if (mode !== 'qna' || typeof window === 'undefined') {
      return;
    }
    const raw = window.sessionStorage.getItem(QNA_CONVERSATION_CACHE_STORAGE_KEY);
    if (!raw) {
      return;
    }
    try {
      qnaConversationCacheRef.current = JSON.parse(raw) as PersistedQnaConversationCache;
    } catch {
      qnaConversationCacheRef.current = {};
      window.sessionStorage.removeItem(QNA_CONVERSATION_CACHE_STORAGE_KEY);
    }
  }, [mode]);

  const syncConversationHistory = useCallback(async ({
    targetConversationId,
    requestVersion,
    cachedMessages,
    nextInput,
    expectStreaming = false,
  }: {
    targetConversationId: string;
    requestVersion?: number;
    cachedMessages?: ChatMessage[];
    nextInput?: string;
    expectStreaming?: boolean;
  }): Promise<boolean> => {
    const normalizedConversationId = targetConversationId.trim();
    if (!normalizedConversationId) {
      return false;
    }

    const hasActiveStream = Boolean(qnaStreamTokensRef.current[normalizedConversationId]);
    const shouldPollStreaming = expectStreaming && conversationIdRef.current === normalizedConversationId;
    const shouldShowStreamingState = shouldPollStreaming && hasActiveStream;
    const syncToken = Date.now();
    qnaHistorySyncTokensRef.current[normalizedConversationId] = syncToken;
    let latestMessages = cachedMessages;
    let previousSignature = latestMessages ? buildConversationSyncSignature(latestMessages) : '';
    let unchangedPolls = 0;
    const maxAttempts = shouldPollStreaming
      ? QNA_STREAM_HISTORY_MAX_ATTEMPTS
      : expectStreaming
        ? 3
        : 1;

    if (expectStreaming && !hasActiveStream && hasPendingAssistantResponse(latestMessages)) {
      const cleanedMessages = removePendingAssistantPlaceholder(latestMessages ?? []);
      latestMessages = cleanedMessages;
      qnaMessagesRef.current = cleanedMessages;
      setQnaMessages(cleanedMessages);
      setQnaStateView('QNA_IDLE');
      cacheConversationView(normalizedConversationId, {
        qnaInput: nextInput ?? qnaInputRef.current,
        qnaMessages: cleanedMessages,
        qnaState: 'QNA_IDLE',
      });
    }

    for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
      if (qnaHistorySyncTokensRef.current[normalizedConversationId] !== syncToken) {
        return Boolean(latestMessages?.length);
      }
      const history = await conversationApi.getConversationMessages(normalizedConversationId, {
        dedupe: false,
        retry: 1,
      });
      if (!mountedRef.current || conversationIdRef.current !== normalizedConversationId) {
        return Boolean(latestMessages?.length);
      }
      if (requestVersion !== undefined && qnaRequestVersionRef.current !== requestVersion) {
        return Boolean(latestMessages?.length);
      }

      const mapped = mapConversationHistory(history);
      if (mapped.length > 0) {
        const mappedHasResolvedAssistant = hasResolvedAssistantResponse(mapped);
        const preferredMessages = mappedHasResolvedAssistant
          ? mapped
          : pickPreferredConversationMessages(latestMessages, mapped);
        const nextState: QnaState =
          shouldShowStreamingState && hasPendingAssistantResponse(preferredMessages) && !mappedHasResolvedAssistant
            ? 'QNA_STREAMING'
            : 'QNA_IDLE';
        const restoredMessages = restorePendingSlideOutlineMessages(normalizedConversationId, preferredMessages);
        latestMessages = restoredMessages;
        qnaMessagesRef.current = restoredMessages;
        setQnaMessages(restoredMessages);
        if (shouldPollStreaming) {
          setQnaStateView(nextState);
        }
        cacheConversationView(normalizedConversationId, {
          qnaInput: nextInput ?? qnaInputRef.current,
          qnaMessages: restoredMessages,
          qnaState: nextState,
        });

        const currentSignature = buildConversationSyncSignature(restoredMessages);
        if (currentSignature === previousSignature) {
          unchangedPolls += 1;
        } else {
          previousSignature = currentSignature;
          unchangedPolls = 0;
        }

        if (!shouldPollStreaming || mappedHasResolvedAssistant) {
          if (
            shouldShowStreamingState
            && !mappedHasResolvedAssistant
            && hasPendingAssistantResponse(restoredMessages)
          ) {
            const cleanedMessages = removePendingAssistantPlaceholder(restoredMessages);
            latestMessages = cleanedMessages;
            qnaMessagesRef.current = cleanedMessages;
            setQnaMessages(cleanedMessages);
            setQnaStateView('QNA_IDLE');
            cacheConversationView(normalizedConversationId, {
              qnaInput: nextInput ?? qnaInputRef.current,
              qnaMessages: cleanedMessages,
              qnaState: 'QNA_IDLE',
            });
          }
          return true;
        }
      }

      if (attempt < maxAttempts - 1) {
        await new Promise((resolve) => window.setTimeout(resolve, QNA_HISTORY_POLL_INTERVAL_MS));
      }
    }

    if (shouldPollStreaming && latestMessages) {
      const cleanedMessages = removePendingAssistantPlaceholder(latestMessages);
      qnaMessagesRef.current = cleanedMessages;
      setQnaMessages(cleanedMessages);
      setQnaStateView('QNA_IDLE');
      cacheConversationView(normalizedConversationId, {
        qnaInput: nextInput ?? qnaInputRef.current,
        qnaMessages: cleanedMessages,
        qnaState: 'QNA_IDLE',
      });
    }

    return Boolean(latestMessages?.length);
  }, [cacheConversationView, conversationIdRef, mountedRef, setQnaStateView]);

  const openExistingConversation = useCallback(async (payload: SelectedConversationSnapshot) => {
    const nextConversationId = payload.conversationId?.trim();
    if (!nextConversationId) {
      return;
    }
    if (loadingConversationIdRef.current === nextConversationId) {
      return;
    }
    qnaRequestVersionRef.current += 1;
    const requestVersion = qnaRequestVersionRef.current;
    loadingConversationIdRef.current = nextConversationId;
    qnaDraftsRef.current[conversationIdRef.current || '__new__'] = qnaInputRef.current;
    cacheConversationView(conversationIdRef.current, {
      qnaInput: qnaInputRef.current,
      qnaMessages: qnaMessagesRef.current,
      qnaState: qnaStateRef.current,
    });
    const cachedSnapshot = qnaConversationCacheRef.current[conversationCacheKey(nextConversationId)];
    const shouldResumeStreaming = Boolean(qnaStreamTokensRef.current[nextConversationId]);
    const nextInput = cachedSnapshot?.qnaInput ?? qnaDraftsRef.current[nextConversationId] ?? '';
    const nextMessages: ChatMessage[] = cachedSnapshot?.qnaMessages?.length
      ? cachedSnapshot.qnaMessages
      : [
        { id: 'qna-greeting', role: 'assistant', content: QNA_GREETING },
        { id: `qna-loading-${nextConversationId}`, role: 'assistant', content: '正在加载历史对话...' },
      ];

    conversationIdRef.current = nextConversationId;
    qnaInputRef.current = nextInput;
    qnaMessagesRef.current = nextMessages;
    setConversationId(nextConversationId);
    setQnaInput(nextInput);
    setQnaStateView(shouldResumeStreaming ? 'QNA_STREAMING' : 'QNA_IDLE');
    setQnaMessages(cachedSnapshot?.qnaMessages?.length
      ? cachedSnapshot.qnaMessages
      : [
        { id: 'qna-greeting', role: 'assistant', content: QNA_GREETING },
        { id: `qna-loading-${nextConversationId}`, role: 'assistant', content: '正在加载历史对话...' },
      ]);

    try {
      const synced = await syncConversationHistory({
        targetConversationId: nextConversationId,
        requestVersion,
        cachedMessages: cachedSnapshot?.qnaMessages,
        nextInput: cachedSnapshot?.qnaInput ?? qnaDraftsRef.current[nextConversationId] ?? '',
        expectStreaming: shouldResumeStreaming,
      });
      if (synced) {
        return;
      }
      setQnaMessages([
        { id: 'qna-greeting', role: 'assistant', content: QNA_GREETING },
        {
          id: `qna-history-${nextConversationId}`,
          role: 'assistant',
          content: payload.lastMessagePreview?.trim()
            ? `已进入历史对话“${payload.title || '历史对话'}”。\n上次对话摘要：${payload.lastMessagePreview}\n你可以继续追问。`
            : `已进入历史对话“${payload.title || '历史对话'}”。你可以继续追问。`,
        },
      ]);
    } catch (error) {
      if (conversationIdRef.current !== nextConversationId || qnaRequestVersionRef.current !== requestVersion) {
        return;
      }
      const message = getErrorMessage(error);
      setQnaMessages([
        { id: 'qna-greeting', role: 'assistant', content: QNA_GREETING },
        {
          id: `qna-history-error-${nextConversationId}`,
          role: 'assistant',
          content:
            message.includes('(429)')
              ? '历史对话加载过于频繁，请稍等一两秒后重试。当前会话已选中，但消息列表还未重新拉取完成。'
              : `历史对话加载失败：${message}`,
        },
      ]);
    } finally {
      loadingConversationIdRef.current = '';
    }
  }, [cacheConversationView, conversationIdRef, setConversationId, setQnaStateView, syncConversationHistory]);

  useEffect(() => {
    if (mode !== 'qna' || qnaSnapshotHydratedRef.current || typeof window === 'undefined') {
      return;
    }

    qnaSnapshotHydratedRef.current = true;
    const raw = window.sessionStorage.getItem(QNA_SNAPSHOT_STORAGE_KEY);
    if (!raw) {
      return;
    }
    try {
      const snapshot = JSON.parse(raw) as PersistedQnaSnapshot;
      const restoredConversationId = snapshot.conversationId ?? '';
      const restoredInput = snapshot.qnaInput ?? '';
      const restoredMessages = normalizeRestoredQnaMessages(snapshot);
      const restoredState: QnaState = 'QNA_IDLE';
      cacheConversationView(snapshot.conversationId ?? '', {
        qnaInput: restoredInput,
        qnaMessages: restoredMessages,
        qnaState: restoredState,
      });
      conversationIdRef.current = restoredConversationId;
      qnaInputRef.current = restoredInput;
      qnaMessagesRef.current = restoredMessages;
      setConversationId(restoredConversationId);
      setQnaInput(restoredInput);
      setQnaStateView(restoredState);
      setQnaMessages(restoredMessages);
      if (snapshot.qnaState === 'QNA_STREAMING' && snapshot.conversationId?.trim()) {
        const restoredConversationId = snapshot.conversationId.trim();
        void syncConversationHistory({
          targetConversationId: restoredConversationId,
          cachedMessages: normalizeRestoredQnaMessages(snapshot),
          nextInput: snapshot.qnaInput ?? '',
          expectStreaming: true,
        }).catch(() => undefined);
      }
    } catch {
      clearPersistedQnaSnapshot();
    }
  }, [
    cacheConversationView,
    clearPersistedQnaSnapshot,
    conversationIdRef,
    mode,
    setConversationId,
    setQnaStateView,
    syncConversationHistory,
  ]);

  useEffect(() => {
    const previousMode = previousModeRef.current;
    previousModeRef.current = mode;
    if (mode !== 'qna' || previousMode === 'qna') {
      return;
    }
    const restoredConversationId = conversationIdRef.current.trim();
    if (!restoredConversationId) {
      return;
    }
    void syncConversationHistory({
      targetConversationId: restoredConversationId,
      cachedMessages: qnaMessagesRef.current,
      nextInput: qnaInputRef.current,
      expectStreaming: qnaStateRef.current === 'QNA_STREAMING',
    }).catch(() => undefined);
  }, [conversationIdRef, mode, syncConversationHistory]);

  useEffect(() => {
    if (mode !== 'qna' || !qnaSnapshotHydratedRef.current || typeof window === 'undefined') {
      return;
    }

    cacheConversationView(conversationId, {
      qnaInput,
      qnaMessages,
      qnaState,
    });

    const snapshot: PersistedQnaSnapshot = {
      conversationId,
      qnaInput,
      qnaState,
      qnaMessages,
    };
    const isEmpty =
      !snapshot.conversationId &&
      !snapshot.qnaInput &&
      snapshot.qnaState === 'QNA_IDLE' &&
      snapshot.qnaMessages.length === 1 &&
      snapshot.qnaMessages[0]?.id === 'qna-greeting';

    if (isEmpty) {
      clearPersistedQnaSnapshot();
      return;
    }
    window.sessionStorage.setItem(QNA_SNAPSHOT_STORAGE_KEY, JSON.stringify(snapshot));
  }, [cacheConversationView, clearPersistedQnaSnapshot, conversationId, mode, qnaInput, qnaMessages, qnaState]);

  useEffect(() => {
    const restoreSelectedConversation = (payload?: SelectedConversationSnapshot) => {
      if (mode !== 'qna') {
        return;
      }
      if (payload?.conversationId) {
        openExistingConversation(payload);
        return;
      }
      if (typeof window === 'undefined') {
        return;
      }
      const raw = window.sessionStorage.getItem(SELECTED_CONVERSATION_STORAGE_KEY);
      if (!raw) {
        return;
      }
      try {
        const parsed = JSON.parse(raw) as SelectedConversationSnapshot;
        openExistingConversation(parsed);
      } catch {
        window.sessionStorage.removeItem(SELECTED_CONVERSATION_STORAGE_KEY);
      }
    };

    const onOpenConversation = (event: Event) => {
      const customEvent = event as CustomEvent<SelectedConversationSnapshot>;
      restoreSelectedConversation(customEvent.detail);
    };

    restoreSelectedConversation();
    window.addEventListener('app:open-conversation', onOpenConversation as EventListener);
    return () => {
      window.removeEventListener('app:open-conversation', onOpenConversation as EventListener);
    };
  }, [mode, openExistingConversation]);

  useEffect(() => {
    const handleVoiceConversationStream = (event: Event) => {
      const detail = readVoiceConversationStreamDetail(event);
      if (!detail) {
        return;
      }
      const conversationId = detail.conversationId.trim();
      const streamId = detail.streamId.trim();
      const userMessageId = `voice-user-${streamId}`;
      const assistantMessageId = `voice-assistant-${streamId}`;

      if (detail.phase === 'start') {
        const userText = detail.userText?.trim();
        if (!userText) {
          return;
        }
        updateQnaConversationMessages(
          conversationId,
          (messages) => {
            if (messages.some((item) => item.id === userMessageId)) {
              return messages;
            }
            const baseMessages = messages.length
              ? messages
              : [{ id: 'qna-greeting', role: 'assistant' as const, content: QNA_GREETING }];
            return [
              ...baseMessages,
              { id: userMessageId, role: 'user' as const, content: userText },
              { id: assistantMessageId, role: 'assistant' as const, content: '' },
            ];
          },
          { qnaInput: '', qnaState: 'QNA_STREAMING' },
        );
        return;
      }

      if (detail.phase === 'chunk') {
        const chunk = readConversationChunk(
          { payload: { text: detail.chunk ?? '', stage: 'tutoring' } },
          'result_chunk',
        );
        if (!chunk) {
          return;
        }
        updateQnaConversationMessages(
          conversationId,
          (messages) => {
            let updatedAssistant = false;
            const nextMessages = messages.map((item) => {
              if (item.id !== assistantMessageId) {
                return item;
              }
              updatedAssistant = true;
              return { ...item, content: (item.content ?? '') + chunk };
            });
            return updatedAssistant
              ? nextMessages
              : [...messages, { id: assistantMessageId, role: 'assistant' as const, content: chunk }];
          },
          { qnaState: 'QNA_STREAMING' },
        );
        return;
      }

      if (detail.phase === 'done') {
        updateQnaConversationMessages(conversationId, (messages) => messages, { qnaState: 'QNA_IDLE' });
        return;
      }

      if (detail.phase === 'error') {
        const errorText = `语音对话失败：${detail.errorMessage || '请稍后重试'}`;
        updateQnaConversationMessages(
          conversationId,
          (messages) => {
            let updatedAssistant = false;
            const nextMessages = messages.map((item) => {
              if (item.id !== assistantMessageId) {
                return item;
              }
              updatedAssistant = true;
              return {
                ...item,
                content: item.content && !isProcessingOnlyAssistantContent(item.content)
                  ? item.content
                  : errorText,
              };
            });
            return updatedAssistant
              ? nextMessages
              : [...messages, { id: assistantMessageId, role: 'assistant' as const, content: errorText }];
          },
          { qnaState: 'QNA_IDLE' },
        );
      }
    };

    window.addEventListener(VOICE_CONVERSATION_STREAM_EVENT, handleVoiceConversationStream);
    return () => {
      window.removeEventListener(VOICE_CONVERSATION_STREAM_EVENT, handleVoiceConversationStream);
    };
  }, [updateQnaConversationMessages]);

  const handleQnaSend = async (override?: QnaSendOverride): Promise<boolean> => {
    const text = (override?.text ?? qnaInput).trim();
    const uploadedImageUrls = override
      ? []
      : pendingQnaImages
        .filter((item) => item.uploadStatus === 'uploaded' && item.uploadedUrl)
        .map((item) => item.uploadedUrl as string);
    if ((!text && uploadedImageUrls.length === 0) || qnaBusy) {
      return false;
    }
    if (!isAuthenticated) {
      openAuthModal('login', '请先登录');
      return false;
    }

    const assistantMessageId = `qna-assistant-${Date.now()}`;
    const userMessageId = `qna-user-${Date.now()}`;
    const pendingPreviewUrls = override ? [] : pendingQnaImages.map((item) => item.previewUrl);
    const useWebSearch = override ? false : qnaWebSearchEnabled;
    const useDeepReasoning = override ? false : deepReasoningEnabled;
    const resourceContext = await buildResourceIntentContext(text, override);
    if (resourceContext.missingTopicMessage) {
      const missingMessages: ChatMessage[] = [
        ...qnaMessagesRef.current,
        {
          id: userMessageId,
          role: 'user',
          content: text,
          imageUrls: uploadedImageUrls,
          localImagePreviews: pendingPreviewUrls,
          webSearchEnabled: useWebSearch,
          deepReasoningEnabled: useDeepReasoning,
        },
        {
          id: assistantMessageId,
          role: 'assistant',
          content: resourceContext.missingTopicMessage,
        },
      ];
      qnaInputRef.current = '';
      qnaMessagesRef.current = missingMessages;
      setQnaInput('');
      setQnaWebSearchEnabled(false);
      setQnaImageError('');
      setQnaMessages(missingMessages);
      setPendingQnaImages([]);
      cacheConversationView(conversationIdRef.current, {
        qnaInput: '',
        qnaMessages: missingMessages,
        qnaState: 'QNA_IDLE',
      });
      return false;
    }
    const pendingMessages: ChatMessage[] = [
      ...qnaMessagesRef.current,
      {
        id: userMessageId,
        role: 'user',
        content: text,
        imageUrls: uploadedImageUrls,
        localImagePreviews: pendingPreviewUrls,
        webSearchEnabled: useWebSearch,
        deepReasoningEnabled: useDeepReasoning,
      },
      { id: assistantMessageId, role: 'assistant', content: '' },
    ];
    qnaInputRef.current = '';
    qnaMessagesRef.current = pendingMessages;
    setQnaInput('');
    setQnaWebSearchEnabled(false);
    setQnaImageError('');
    setQnaMessages(pendingMessages);
    setPendingQnaImages([]);
    setQnaStateView('QNA_STREAMING');
    cacheConversationView(conversationIdRef.current, {
      qnaInput: '',
      qnaMessages: pendingMessages,
      qnaState: 'QNA_STREAMING',
    });

    qnaRequestVersionRef.current += 1;
    const abortController = new AbortController();
    const originConversationId = conversationIdRef.current.trim();
    let streamConversationId = originConversationId;

    try {
      const currentConversationId = conversationId || (await conversationApi.createConversation()).conversationId;
      streamConversationId = currentConversationId;
      if (abortController.signal.aborted || !mountedRef.current) {
        return false;
      }
      const stillViewingOrigin = conversationIdRef.current === originConversationId;
      if (!conversationId && stillViewingOrigin) {
        conversationIdRef.current = currentConversationId;
        setConversationId(currentConversationId);
        qnaDraftsRef.current.__new__ = '';
        window.dispatchEvent(new Event('app:conversation-updated'));
      }
      cacheConversationView(currentConversationId, {
        qnaInput: '',
        qnaMessages: pendingMessages,
        qnaState: 'QNA_STREAMING',
      });
      const streamToken = `${currentConversationId}:${assistantMessageId}`;
      qnaStreamControllersRef.current[currentConversationId]?.abort();
      qnaStreamControllersRef.current[currentConversationId] = abortController;
      qnaStreamTokensRef.current[currentConversationId] = streamToken;
      confirmedSlideOutlineStreamsRef.current[streamToken] = Boolean(override?.confirmedSlideOutlineText);

      await conversationApi.streamMessage(
        currentConversationId,
        {
          message: text,
          imageUrls: uploadedImageUrls,
          serviceType: 'TUTORING',
          webSearchEnabled: useWebSearch,
          reasoningMode: useDeepReasoning ? 'DEEP' : 'NORMAL',
          voiceContext: resourceContext.voiceContext,
        },
        {
          onOpen: () => {
            if (qnaStreamTokensRef.current[currentConversationId] !== streamToken) {
              return;
            }
            window.dispatchEvent(new Event('app:conversation-updated'));
          },
          onEvent: (event) => {
            if (qnaStreamTokensRef.current[currentConversationId] !== streamToken) {
              return;
            }
            recordResourceStreamEvent(currentConversationId, event.event, event.data);
            if (event.event === 'resource_file') {
              const handledSlideOutline = handleConversationSlideOutline(
                currentConversationId,
                event.data.payload,
                {
                  confirmedRequest: Boolean(confirmedSlideOutlineStreamsRef.current[streamToken]),
                  assistantMessageId,
                },
              );
              if (handledSlideOutline) {
                return;
              }
            }
            if (event.event === 'question_batch') {
              handleConversationQuestionBatch(event.data.payload);
            }
            const chunk = readConversationChunk(event.data, event.event);
            if (!chunk) {
              return;
            }
            updateQnaConversationMessages(
              currentConversationId,
              (messages) => {
                let updatedAssistant = false;
                const nextMessagesForChunk = messages.map((item) => {
                  if (item.id !== assistantMessageId) {
                    return item;
                  }
                  updatedAssistant = true;
                  return { ...item, content: (item.content ?? '') + chunk };
                });
                return updatedAssistant
                  ? nextMessagesForChunk
                  : [...messages, { id: assistantMessageId, role: 'assistant', content: chunk }];
              },
              { qnaState: 'QNA_STREAMING' },
            );
          },
          onDone: () => {
            if (qnaStreamTokensRef.current[currentConversationId] !== streamToken) {
              return;
            }
            delete qnaStreamTokensRef.current[currentConversationId];
            delete confirmedSlideOutlineStreamsRef.current[streamToken];
            if (qnaStreamControllersRef.current[currentConversationId] === abortController) {
              delete qnaStreamControllersRef.current[currentConversationId];
            }
            updateQnaConversationMessages(currentConversationId, removePendingAssistantPlaceholder, { qnaState: 'QNA_IDLE' });
            window.dispatchEvent(new Event('app:conversation-updated'));
          },
          onError: (error) => {
            if (qnaStreamTokensRef.current[currentConversationId] !== streamToken) {
              return;
            }
            delete qnaStreamTokensRef.current[currentConversationId];
            delete confirmedSlideOutlineStreamsRef.current[streamToken];
            if (qnaStreamControllersRef.current[currentConversationId] === abortController) {
              delete qnaStreamControllersRef.current[currentConversationId];
            }
            const message = getErrorMessage(error);
            updateQnaConversationMessages(
              currentConversationId,
              (messages) =>
                messages.map((item) =>
                  item.id === assistantMessageId
                    ? {
                      ...item,
                      content: item.content && !isProcessingOnlyAssistantContent(item.content)
                        ? item.content
                        : `会话失败：${message}`,
                    }
                    : item,
                ),
              { qnaState: 'QNA_IDLE' },
            );
            if (conversationIdRef.current !== currentConversationId) {
              window.dispatchEvent(new Event('app:conversation-updated'));
              return;
            }
            setQnaMessages((prev) =>
              prev.map((item) =>
                item.id === assistantMessageId ? { ...item, content: item.content || `会话失败：${message}` } : item,
              ),
            );
            setQnaStateView('QNA_IDLE');
            window.dispatchEvent(new Event('app:conversation-updated'));
          },
        },
        abortController.signal,
      );
      return true;
    } catch (error) {
      const message = getErrorMessage(error);
      const targetConversationId = streamConversationId || (
        conversationIdRef.current === originConversationId
          ? conversationIdRef.current
          : originConversationId
      );
      if (targetConversationId) {
        delete qnaStreamTokensRef.current[targetConversationId];
        delete confirmedSlideOutlineStreamsRef.current[`${targetConversationId}:${assistantMessageId}`];
        if (qnaStreamControllersRef.current[targetConversationId] === abortController) {
          delete qnaStreamControllersRef.current[targetConversationId];
        }
        updateQnaConversationMessages(
          targetConversationId,
          (messages) =>
            messages.map((item) =>
              item.id === assistantMessageId
                ? { ...item, content: item.content || `会话失败：${message}` }
                : item,
            ),
          { qnaState: 'QNA_IDLE' },
        );
        return false;
      }
      if (conversationIdRef.current !== originConversationId) {
        return false;
      }
      setQnaMessages((prev) =>
        prev.map((item) => (item.id === assistantMessageId ? { ...item, content: `会话失败：${message}` } : item)),
      );
      setQnaStateView('QNA_IDLE');
      return false;
    }
  };

  async function buildResourceIntentContext(
    text: string,
    override?: QnaSendOverride,
  ): Promise<ResourceIntentContext> {
    const intent = analyzeResourceConversationIntent(text, override);
    if (!intent.isResourceIntent) {
      return {};
    }
    const activeStep = intent.explicitUserTopic
      ? null
      : await loadActiveLearningStepContext();
    if (!intent.explicitUserTopic && !activeStep) {
      return {
        missingTopicMessage: '当前没有可用学习阶段。请先生成学习路径，或在对话里补充明确的资源主题后再生成。',
      };
    }
    const voiceContext: QnaVoiceContext = {
      pageType: 'learning_studio_qna',
      source: 'conversation_resource_generation',
      selectedService: 'RESOURCE_GENERATION',
      commandIntent: intent.isQuiz ? 'generate_practice' : intent.isSlides ? 'generate_slides' : 'generate_resources',
      activeLearningStepId: activeStep?.stepId,
      activeLearningStepTitle: activeStep?.title,
      activeLearningStepProgress: activeStep ? String(activeStep.progress) : undefined,
      activeLearningStepSummary: activeStep?.summary,
      explicitUserTopic: intent.explicitUserTopic || undefined,
      questionCount: intent.questionCount ? String(intent.questionCount) : undefined,
      questionTypePreference: intent.questionTypePreference,
      difficultyPreference: intent.difficultyPreference,
      requiresSlideOutlineConfirmation: intent.isSlides && !override?.confirmedSlideOutlineText ? 'true' : undefined,
      confirmedSlideOutline: override?.confirmedSlideOutlineText || override?.confirmedSlideOutline ? 'true' : undefined,
      confirmedSlideOutlineText: override?.confirmedSlideOutlineText,
    };
    return { voiceContext: pruneVoiceContext(voiceContext) };
  }

  async function loadActiveLearningStepContext(): Promise<ActiveLearningStepContext | null> {
    try {
      const response = await learningPathApi.current();
      return resolveActiveLearningStep(response);
    } catch (error) {
      console.warn('Failed to load active learning path context:', error);
      return null;
    }
  }

  const revokePendingImage = useCallback((image: PendingChatImage) => {
    if (image.previewUrl.startsWith('blob:')) {
      URL.revokeObjectURL(image.previewUrl);
    }
  }, []);

  const validateImageFile = useCallback((file: File): string => {
    const allowedTypes = new Set(['image/jpeg', 'image/png', 'image/webp']);
    if (!allowedTypes.has(file.type)) {
      return '仅支持 jpg、png、webp 图片';
    }
    if (file.size > 10 * 1024 * 1024) {
      return '图片不能超过 10MB';
    }
    return '';
  }, []);

  const handlePickQnaImages = useCallback(async (files: File[]) => {
    if (!files.length) {
      return;
    }
    setQnaImageError('');
    for (const file of files) {
      const validationMessage = validateImageFile(file);
      if (validationMessage) {
        setQnaImageError(validationMessage);
        continue;
      }
      const imageId = `pending-image-${Date.now()}-${Math.random().toString(16).slice(2)}`;
      const previewUrl = URL.createObjectURL(file);
      const pendingImage: PendingChatImage = {
        id: imageId,
        file,
        previewUrl,
        uploadStatus: 'uploading',
        uploadProgress: 0,
      };
      setPendingQnaImages((prev) => [...prev, pendingImage]);
      try {
        const uploaded = await conversationApi.uploadImage(file, (percent) => {
          setPendingQnaImages((prev) =>
            prev.map((item) => (item.id === imageId ? { ...item, uploadProgress: percent, uploadStatus: 'uploading' } : item)),
          );
        });
        setPendingQnaImages((prev) =>
          prev.map((item) =>
            item.id === imageId
              ? { ...item, uploadProgress: 100, uploadStatus: 'uploaded', uploadedUrl: uploaded.imageUrl }
              : item,
          ),
        );
      } catch (error) {
        const message = getErrorMessage(error);
        setPendingQnaImages((prev) =>
          prev.map((item) =>
            item.id === imageId
              ? { ...item, uploadStatus: 'failed', errorMessage: message }
              : item,
          ),
        );
        setQnaImageError(message);
      }
    }
  }, [validateImageFile]);

  const handleRemovePendingQnaImage = useCallback((id: string) => {
    setPendingQnaImages((prev) => {
      const target = prev.find((item) => item.id === id);
      if (target) {
        revokePendingImage(target);
      }
      return prev.filter((item) => item.id !== id);
    });
  }, [revokePendingImage]);

  useEffect(() => () => {
    pendingQnaImages.forEach(revokePendingImage);
  }, [pendingQnaImages, revokePendingImage]);

  const handleConfirmSlideOutline = useCallback((message: ChatMessage) => {
    const confirmation = message.slideConfirmation;
    if (!confirmation || confirmation.status !== 'pending') {
      return;
    }
    const confirmedTopic = cleanupTopic(confirmation.topic || '');
    void handleQnaSend({
      text: '确认此大纲并生成 PPT 文件',
      confirmedSlideOutline: true,
      confirmedSlideOutlineText: confirmation.outline,
      confirmedSlideTopic: confirmedTopic || undefined,
    }).then((accepted) => {
      if (!accepted) {
        return;
      }
      updateQnaConversationMessages(
        conversationIdRef.current,
        (messages) => messages.map((item) =>
          item.id === message.id
            ? { ...item, slideConfirmation: { ...confirmation, status: 'confirmed' } }
            : item,
        ),
      );
    });
  }, [conversationIdRef, handleQnaSend, updateQnaConversationMessages]);

  const handleRejectSlideOutline = useCallback((message: ChatMessage) => {
    const confirmation = message.slideConfirmation;
    if (!confirmation || confirmation.status !== 'pending') {
      return;
    }
    updateQnaConversationMessages(
      conversationIdRef.current,
      (messages) => messages.map((item) =>
        item.id === message.id
          ? {
            ...item,
            content: item.content || '已取消本次 PPT 生成。',
            slideConfirmation: { ...confirmation, status: 'rejected' },
          }
          : item,
      ),
    );
    markSlideOutlineRejected(conversationIdRef.current, confirmation.title);
  }, [conversationIdRef, updateQnaConversationMessages]);

  return {
    resetQnaConversation,
    abortQnaStreams,
    viewProps: {
      hasStartedConversation,
      qnaInput,
      qnaBusy,
      qnaMessages,
      pendingImages: pendingQnaImages,
      imageErrorMessage: qnaImageError,
      deepReasoningEnabled,
      webSearchEnabled: qnaWebSearchEnabled,
      onChange: setQnaInput,
      onSend: handleQnaSend,
      onToggleDeepReasoning: () => setDeepReasoningEnabled((prev) => !prev),
      onToggleWebSearch: () => setQnaWebSearchEnabled((prev) => !prev),
      onPickImages: handlePickQnaImages,
      onRemoveImage: handleRemovePendingQnaImage,
      onConfirmSlideOutline: handleConfirmSlideOutline,
      onRejectSlideOutline: handleRejectSlideOutline,
    },
  };

  function handleConversationSlideOutline(
    targetConversationId: string,
    payload: Record<string, unknown> | undefined,
    options?: { confirmedRequest?: boolean; assistantMessageId?: string },
  ): boolean {
    if (!isSlideOutlineConfirmationPayload(payload)) {
      return false;
    }
    if (options?.confirmedRequest) {
      appendConfirmedSlideOutlineRepeatedMessage(targetConversationId, options.assistantMessageId);
      return true;
    }
    const confirmation = readSlideOutlineConfirmation(payload);
    if (!confirmation) {
      appendSlideOutlineErrorMessage(targetConversationId);
      return true;
    }
    updateQnaConversationMessages(
      targetConversationId,
      (messages) => {
        const existingIndex = messages.findIndex((item) => item.slideConfirmation?.id === confirmation.id);
        if (existingIndex >= 0) {
          return messages.map((item, index) =>
            index === existingIndex
              ? {
                ...item,
                content: item.content || 'PPT 大纲已生成，请确认后继续生成演示文件。',
                slideConfirmation: confirmation,
              }
              : item,
          );
        }
        return [
          ...removePendingAssistantPlaceholder(messages),
          {
            id: `qna-slide-outline-${Date.now()}`,
            role: 'assistant',
            content: 'PPT 大纲已生成，请确认后继续生成演示文件。',
            slideConfirmation: confirmation,
          },
        ];
      },
      { qnaState: 'QNA_STREAMING' },
    );
    return true;
  }

  function appendSlideOutlineErrorMessage(targetConversationId: string): void {
    updateQnaConversationMessages(
      targetConversationId,
      (messages) => [
        ...removePendingAssistantPlaceholder(messages),
        {
          id: `qna-slide-outline-error-${Date.now()}`,
          role: 'assistant',
          content: 'PPT 大纲事件缺少正文，请重新生成',
        },
      ],
      { qnaState: 'QNA_STREAMING' },
    );
  }

  function appendConfirmedSlideOutlineRepeatedMessage(targetConversationId: string, assistantMessageId?: string): void {
    updateQnaConversationMessages(
      targetConversationId,
      (messages) => {
        const message = '确认已提交，但后端仍要求 PPT 大纲确认。请重新生成或稍后重试。';
        if (assistantMessageId && messages.some((item) => item.id === assistantMessageId)) {
          return messages.map((item) =>
            item.id === assistantMessageId
              ? { ...item, content: message }
              : item,
          );
        }
        return [
          ...removePendingAssistantPlaceholder(messages),
          {
            id: `qna-slide-outline-repeat-error-${Date.now()}`,
            role: 'assistant',
            content: message,
          },
        ];
      },
      { qnaState: 'QNA_STREAMING' },
    );
  }

  function handleConversationQuestionBatch(payload: Record<string, unknown> | undefined): void {
    if (!hasRealLlmProvenance(payload)) {
      return;
    }
    const batch = readPracticeQuestionBatch(payload);
    if (!batch) {
      return;
    }
    openPracticeSession({
      batch,
      source: 'conversation',
      ownerUserId: currentUser?.userId ?? currentUser?.id,
      conversationId: conversationIdRef.current.trim() || conversationId.trim() || undefined,
    });
  }
}

function removePendingAssistantPlaceholder(messages: ChatMessage[]): ChatMessage[] {
  if (!hasPendingAssistantResponse(messages)) {
    return messages;
  }
  const lastIndex = messages.length - 1;
  return messages.filter((_, index) => index !== lastIndex);
}

function restorePendingSlideOutlineMessages(conversationId: string, messages: ChatMessage[]): ChatMessage[] {
  if (!conversationId.trim() || typeof window === 'undefined') {
    return messages;
  }
  const session = loadResourceGenerationSession(conversationId);
  const pendingSlides = session.resources.filter(
    (resource) => resource.type === 'SLIDES'
      && resource.status === 'waiting_confirmation'
      && resource.slideOutline?.trim(),
  );
  if (!pendingSlides.length) {
    return messages;
  }
  let nextMessages = messages;
  for (const resource of pendingSlides) {
    const confirmation = {
      id: `slides:${resource.title}`,
      title: resource.title,
      outline: resource.slideOutline?.trim() || '',
      topic: session.topic || resource.title,
      status: 'pending' as const,
    };
    if (!confirmation.outline || nextMessages.some((item) => item.slideConfirmation?.id === confirmation.id)) {
      continue;
    }
    nextMessages = [
      ...removePendingAssistantPlaceholder(nextMessages),
      {
        id: `qna-slide-outline-restored-${resource.id}`,
        role: 'assistant',
        content: 'PPT 大纲已生成，请确认后继续生成演示文件。',
        slideConfirmation: confirmation,
      },
    ];
  }
  return nextMessages;
}

function recordResourceStreamEvent(
  conversationId: string,
  eventName: string,
  data: Parameters<typeof recordConversationResourceEvent>[2],
): void {
  if (
    eventName !== 'progress' &&
    eventName !== 'resource_file' &&
    eventName !== 'question_batch' &&
    eventName !== 'done' &&
    eventName !== 'error' &&
    !eventName.startsWith('video_gen:')
  ) {
    return;
  }
  recordConversationResourceEvent(conversationId, eventName, data);
}

function hasRealLlmProvenance(payload: Record<string, unknown> | undefined): boolean {
  if (!payload) {
    return false;
  }
  const evidenceIds = payload.evidenceIds;
  return readLooseString(payload.generatedBy).toUpperCase() === 'LLM'
    && readLooseString(payload.contentOrigin).toUpperCase() === 'LLM'
    && Boolean(readLooseString(payload.provider))
    && Boolean(readLooseString(payload.model))
    && Boolean(readLooseString(payload.agentName))
    && Array.isArray(evidenceIds)
    && payload.fallback === false
    && typeof payload.fromCache === 'boolean';
}

function analyzeResourceConversationIntent(text: string, override?: QnaSendOverride): ResourceConversationIntent {
  const normalized = text.trim();
  const isConfirmedSlide = Boolean(override?.confirmedSlideOutlineText);
  const isSlides = isConfirmedSlide
    || /ppt|slides?|课件|演示文稿|幻灯片/i.test(normalized);
  const isQuiz = /出\s*\d*\s*道.*题|练习题|习题|测验|自测|刷题|题目/.test(normalized)
    && !/解答|讲解|解析/.test(normalized);
  const isResourceIntent = isConfirmedSlide
    || isSlides
    || isQuiz
    || /生成|制作|创建|整理|来一套|出一套/.test(normalized)
      && /资源|文档|资料|阅读|视频|代码案例|思维导图|导图|课件|ppt|slides?|练习|习题|题目/i.test(normalized);
  const explicitUserTopic = cleanupTopic(override?.confirmedSlideTopic ?? '')
    || (isConfirmedSlide ? '' : extractExplicitResourceTopic(normalized, { isSlides, isQuiz }));
  return {
    isResourceIntent,
    isSlides,
    isQuiz,
    explicitUserTopic,
    questionCount: extractQuestionCount(normalized),
    questionTypePreference: extractQuestionTypePreference(normalized),
    difficultyPreference: extractDifficultyPreference(normalized),
  };
}

function extractExplicitResourceTopic(text: string, intent: { isSlides: boolean; isQuiz: boolean }): string {
  if (/当前|现阶段|学习阶段|我的阶段|当前学习|当前想学|我想学的主题/.test(text)) {
    return '';
  }
  const quoted = text.match(/[「《“"]([^」》”"]{2,80})[」》”"]/);
  if (quoted?.[1]) {
    return cleanupTopic(quoted[1]);
  }
  const topicMatch = text.match(/(?:关于|围绕|根据|针对|以)([^，。,.!?！？]{2,80})(?:生成|制作|创建|整理|出|来|的|为主题)/);
  if (topicMatch?.[1]) {
    return cleanupTopic(topicMatch[1]);
  }
  if (intent.isSlides) {
    const slideMatch = text.match(/(?:生成|制作|创建)?\s*([^，。,.!?！？]{2,80}?)(?:PPT|ppt|课件|演示文稿|幻灯片)/);
    if (slideMatch?.[1]) {
      return cleanupTopic(slideMatch[1]);
    }
  }
  if (intent.isQuiz) {
    const quizMatch = text.match(/(?:出|生成|来)\s*(?:\d+\s*)?道?([^，。,.!?！？]{2,80}?)(?:练习题|习题|题目|题)/);
    if (quizMatch?.[1]) {
    return cleanupTopic(quizMatch[1]);
    }
  }
  return '';
}

function cleanupTopic(value: string): string {
  const cleaned = stripResourceCommandTail(value)
    .replace(/^(一份|一个|一种|一张|一套|一些|几个|几道|当前|我的|请|帮我|给我|关于|围绕|根据|针对|以)/, '')
    .replace(/(学习资源|资源|文档|资料|课件|练习题|习题|题目|PPT大纲|ppt大纲|PPT|ppt|大纲|的)$/i, '')
    .trim();
  return isRealResourceTopic(cleaned) ? cleaned : '';
}

function stripResourceCommandTail(value: string): string {
  return value
    .replace(
      /[，,；;。]?\s*(?:包括|包含|含|涵盖)?\s*(?:文档|资料|PPT|ppt|课件|幻灯片|思维导图|导图|练习题|习题|题目|短视频|视频|代码案例|代码)(?:\s*[、,，和及与]\s*(?:文档|资料|PPT|ppt|课件|幻灯片|思维导图|导图|练习题|习题|题目|短视频|视频|代码案例|代码))*\s*$/i,
      '',
    )
    .trim();
}

function extractQuestionCount(text: string): number | undefined {
  const match = text.match(/(\d{1,2})\s*道/);
  if (!match) {
    return undefined;
  }
  const count = Number(match[1]);
  return Number.isFinite(count) && count > 0 ? Math.min(count, 20) : undefined;
}

function extractQuestionTypePreference(text: string): string | undefined {
  if (/选择题|客观题|单选/.test(text) && !/主观题|简答/.test(text)) {
    return 'SINGLE_CHOICE';
  }
  if (/主观题|简答题|问答题/.test(text) && !/选择题|客观题|单选/.test(text)) {
    return 'SHORT_ANSWER';
  }
  if (/混合|都有|搭配|组合/.test(text)) {
    return 'MIXED';
  }
  return undefined;
}

function extractDifficultyPreference(text: string): string | undefined {
  if (/困难|高难|进阶|高级/.test(text)) {
    return 'ADVANCED';
  }
  if (/简单|基础|入门/.test(text)) {
    return 'BASIC';
  }
  if (/中等|适中/.test(text)) {
    return 'INTERMEDIATE';
  }
  return undefined;
}

function resolveActiveLearningStep(data: LearningPathCurrentResponse | null): ActiveLearningStepContext | null {
  const directActiveStep = normalizeLearningPathStep(data?.activeStep, 0);
  if (directActiveStep && isActiveLearningStepStatus(directActiveStep.status, true)) {
    return {
      stepId: directActiveStep.stepId,
      title: directActiveStep.title,
      progress: directActiveStep.progress,
      summary: directActiveStep.summary,
    };
  }
  const steps = Array.isArray(data?.learningPath?.steps) ? data.learningPath.steps : [];
  const normalizedSteps = steps
    .map((step, index) => normalizeLearningPathStep(step, index))
    .filter((step): step is ActiveLearningStepContext & { order: number; status: string } => Boolean(step))
    .sort((left, right) => left.order - right.order);
  if (!normalizedSteps.length) {
    return null;
  }
  const active = normalizedSteps.find((step) => isActiveLearningStepStatus(step.status, false));
  if (!active) {
    return null;
  }
  return {
    stepId: active.stepId,
    title: active.title,
    progress: active.progress,
    summary: active.summary,
  };
}

function isActiveLearningStepStatus(status: string, allowMissing: boolean): boolean {
  const normalized = status.trim().toUpperCase().replace(/[^A-Z0-9]+/g, '_').replace(/^_+|_+$/g, '');
  if (!normalized) {
    return allowMissing;
  }
  if (
    normalized.startsWith('NOT_')
    || normalized.includes('INACTIVE')
    || normalized === 'PENDING'
    || normalized === 'COMPLETED'
    || normalized === 'DONE'
  ) {
    return false;
  }
  if (normalized === 'IN_PROGRESS') {
    return true;
  }
  return normalized.split(/_+/).some((token) => token === 'RUNNING' || token === 'RUN' || token === 'PROGRESS' || token === 'ACTIVE');
}

function isRealResourceTopic(value: string): boolean {
  const normalized = value.replace(/\s+/g, '').trim();
  if (normalized.length < 2 || normalized.length > 80) {
    return false;
  }
  if (GENERIC_RESOURCE_TOPIC_PATTERN.test(normalized)) {
    return false;
  }
  if (/^(生成|制作|创建|整理|给我|帮我|请|来一套|出|写|做)?(一份|一套|一些|几个|几道)?(文档|资料|资源|学习资源|PPT|课件|幻灯片|练习题|习题|题目|视频|代码案例)$/i.test(normalized)) {
    return false;
  }
  if (/^(当前|现阶段|我的)?(学习阶段|当前学习|当前想学的主题|我想学的主题)$/i.test(normalized)) {
    return false;
  }
  if (/^(包括|包含|含|涵盖)?(文档|资料|PPT|ppt|课件|幻灯片|思维导图|导图|练习题|习题|题目|短视频|视频|代码案例|代码)([、,，和及与]*(文档|资料|PPT|ppt|课件|幻灯片|思维导图|导图|练习题|习题|题目|短视频|视频|代码案例|代码))*$/i.test(normalized)) {
    return false;
  }
  if (!/[\u4e00-\u9fa5A-Za-z0-9]/.test(normalized)) {
    return false;
  }
  return true;
}

function normalizeLearningPathStep(step: unknown, index: number): (ActiveLearningStepContext & { order: number; status: string }) | null {
  if (!step || typeof step !== 'object' || Array.isArray(step)) {
    return null;
  }
  const record = step as Record<string, unknown>;
  const title = readLooseString(record.title) || readLooseString(record.intent) || readLooseString(record.objective);
  if (!title) {
    return null;
  }
  const status = readLooseString(record.status);
  const targetPoints = readLooseStringArray(record.targetKnowledgePoints);
  const checkpoint = readLooseString(record.checkpoint) || readLooseString(record.successCriteria);
  return {
    stepId: readLooseString(record.stepId) || readLooseString(record.id) || `step-${index + 1}`,
    title,
    progress: readLooseNumber(record.progress) ?? readLooseNumber(record.progressPercent) ?? 0,
    summary: [checkpoint, targetPoints.length ? `知识点：${targetPoints.join('、')}` : ''].filter(Boolean).join('；'),
    order: readLooseNumber(record.order) ?? index + 1,
    status,
  };
}

function readSlideOutlineConfirmation(payload: Record<string, unknown> | undefined): SlideOutlineConfirmation | null {
  if (!payload) {
    return null;
  }
  const outline = readLooseString(payload.inlineContent);
  if (!isSlideOutlineConfirmationPayload(payload) || !outline) {
    return null;
  }
  const title = readLooseString(payload.title) || readLooseString(payload.fileName) || 'PPT 大纲';
  return {
    id: `slides:${title}`,
    title,
    outline,
    topic: readLooseString(payload.topic) || title,
    status: 'pending',
  };
}

function isSlideOutlineConfirmationPayload(payload: Record<string, unknown> | undefined): boolean {
  if (!payload) {
    return false;
  }
  const assetType = readLooseString(payload.assetType).toUpperCase();
  const displayMode = readLooseString(payload.displayMode).toUpperCase();
  return (assetType === 'SLIDES' || assetType === 'PPT') && displayMode === 'SLIDE_OUTLINE_CONFIRMATION';
}

function pruneVoiceContext(context: QnaVoiceContext): QnaVoiceContext {
  return Object.fromEntries(
    Object.entries(context).filter(([, value]) => value !== undefined && value !== ''),
  ) as QnaVoiceContext;
}

function readLooseString(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function readLooseStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.map((item) => String(item).trim()).filter(Boolean);
}

function readLooseNumber(value: unknown): number | undefined {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : undefined;
  }
  return undefined;
}
