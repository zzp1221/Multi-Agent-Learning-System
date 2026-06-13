import { useCallback, useEffect, useRef, useState, type Dispatch, type MutableRefObject, type SetStateAction } from 'react';
import { conversationApi, type ConversationMessageStreamRequest } from '../api/conversation';
import { getErrorMessage } from '../api/request';
import { learningPathApi, type LearningPathCurrentResponse } from '../api/smartEngine';
import type { LayoutOutletContext } from '../components/Layout';
import {
  QNA_GREETING,
  QNA_STREAM_STOPPED_MESSAGE,
  type ChatMessage,
  type PendingChatImage,
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
import { recordConversationResourceEvent } from './resourceGenerationStore';
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
}

interface QnaLearningContext {
  voiceContext?: QnaVoiceContext;
}

interface ActiveLearningStepContext {
  stepId: string;
  title: string;
  progress: number;
  summary: string;
}

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
  const activeQnaStreamRef = useRef<{
    conversationId: string;
    assistantMessageId: string;
    abortController: AbortController;
    streamToken?: string;
  } | null>(null);
  const qnaConversationCacheRef = useRef<PersistedQnaConversationCache>({});
  const qnaStreamTokensRef = useRef<Record<string, string>>({});
  const qnaHistorySyncTokensRef = useRef<Record<string, number>>({});
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
        latestMessages = preferredMessages;
        qnaMessagesRef.current = preferredMessages;
        setQnaMessages(preferredMessages);
        if (shouldPollStreaming) {
          setQnaStateView(nextState);
        }
        cacheConversationView(normalizedConversationId, {
          qnaInput: nextInput ?? qnaInputRef.current,
          qnaMessages: preferredMessages,
          qnaState: nextState,
        });

        const currentSignature = buildConversationSyncSignature(preferredMessages);
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
            && hasPendingAssistantResponse(preferredMessages)
          ) {
            const cleanedMessages = removePendingAssistantPlaceholder(preferredMessages);
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
    const qnaLearningContext = await buildQnaLearningContext();
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
    activeQnaStreamRef.current = { conversationId: originConversationId, assistantMessageId, abortController };

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
      activeQnaStreamRef.current = {
        conversationId: currentConversationId,
        assistantMessageId,
        abortController,
        streamToken,
      };
      await conversationApi.streamMessage(
        currentConversationId,
        {
          message: text,
          imageUrls: uploadedImageUrls,
          serviceType: 'TUTORING',
          webSearchEnabled: useWebSearch,
          reasoningMode: useDeepReasoning ? 'DEEP' : 'NORMAL',
          voiceContext: qnaLearningContext.voiceContext,
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
              const externalLinkMarkdown = formatConversationResourceLink(event.data.payload);
              if (externalLinkMarkdown) {
                updateQnaConversationMessages(
                  currentConversationId,
                  (messages) => appendAssistantResourceLink(messages, assistantMessageId, externalLinkMarkdown),
                  { qnaState: 'QNA_STREAMING' },
                );
              }
              return;
            }
            if (event.event === 'question_batch') {
              handleConversationQuestionBatch(event.data.payload);
            }
            if (event.event === 'reasoning_chunk') {
              const reasoningChunk = readReasoningChunk(event.data.payload);
              if (!reasoningChunk) {
                return;
              }
              updateQnaConversationMessages(
                currentConversationId,
                (messages) => appendReasoningChunk(messages, assistantMessageId, reasoningChunk),
                { qnaState: 'QNA_STREAMING' },
              );
              return;
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
            if (qnaStreamControllersRef.current[currentConversationId] === abortController) {
              delete qnaStreamControllersRef.current[currentConversationId];
            }
            if (activeQnaStreamRef.current?.abortController === abortController) {
              activeQnaStreamRef.current = null;
            }
            updateQnaConversationMessages(
              currentConversationId,
              (messages) => markReasoningDone(removePendingAssistantPlaceholder(messages), assistantMessageId),
              { qnaState: 'QNA_IDLE' },
            );
            window.dispatchEvent(new Event('app:conversation-updated'));
          },
          onError: (error) => {
            if (qnaStreamTokensRef.current[currentConversationId] !== streamToken) {
              return;
            }
            delete qnaStreamTokensRef.current[currentConversationId];
            if (qnaStreamControllersRef.current[currentConversationId] === abortController) {
              delete qnaStreamControllersRef.current[currentConversationId];
            }
            if (activeQnaStreamRef.current?.abortController === abortController) {
              activeQnaStreamRef.current = null;
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
      if (abortController.signal.aborted) {
        const stoppedConversationId = streamConversationId || originConversationId;
        delete qnaStreamTokensRef.current[stoppedConversationId];
        if (qnaStreamControllersRef.current[stoppedConversationId] === abortController) {
          delete qnaStreamControllersRef.current[stoppedConversationId];
        }
        if (activeQnaStreamRef.current?.abortController === abortController) {
          activeQnaStreamRef.current = null;
        }
        markQnaStreamStopped(stoppedConversationId, assistantMessageId);
        return false;
      }
      const message = getErrorMessage(error);
      const targetConversationId = streamConversationId || (
        conversationIdRef.current === originConversationId
          ? conversationIdRef.current
          : originConversationId
      );
      if (targetConversationId) {
        delete qnaStreamTokensRef.current[targetConversationId];
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

  const handleQnaStop = useCallback(() => {
    const activeStream = activeQnaStreamRef.current;
    if (!activeStream) {
      return;
    }
    activeStream.abortController.abort();
    const targetConversationId = activeStream.conversationId.trim();
    if (targetConversationId) {
      delete qnaStreamTokensRef.current[targetConversationId];
      if (qnaStreamControllersRef.current[targetConversationId] === activeStream.abortController) {
        delete qnaStreamControllersRef.current[targetConversationId];
      }
    }
    activeQnaStreamRef.current = null;
    markQnaStreamStopped(targetConversationId, activeStream.assistantMessageId);
    window.dispatchEvent(new Event('app:conversation-updated'));
  }, []);

  async function buildQnaLearningContext(): Promise<QnaLearningContext> {
    const activeStep = await loadActiveLearningStepContext();
    if (!activeStep) {
      return {};
    }
    const voiceContext: QnaVoiceContext = {
      pageType: 'learning_studio_qna',
      source: 'learning_studio_qna',
      activeLearningStepId: activeStep?.stepId,
      activeLearningStepTitle: activeStep?.title,
      activeLearningStepProgress: activeStep ? String(activeStep.progress) : undefined,
      activeLearningStepSummary: activeStep?.summary,
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
      onStop: handleQnaStop,
      onToggleDeepReasoning: () => setDeepReasoningEnabled((prev) => !prev),
      onToggleWebSearch: () => setQnaWebSearchEnabled((prev) => !prev),
      onPickImages: handlePickQnaImages,
      onRemoveImage: handleRemovePendingQnaImage,
    },
  };

  function markQnaStreamStopped(targetConversationId: string, assistantMessageId: string): void {
    const applyStoppedState = (messages: ChatMessage[]) => {
      let updatedAssistant = false;
      const nextMessages = messages.map((item) => {
        if (item.id !== assistantMessageId) {
          return item;
        }
        updatedAssistant = true;
        return {
          ...item,
          content: stoppedAssistantContent(item.content),
          reasoningState: item.reasoningContent?.trim() ? 'stopped' as const : item.reasoningState,
        };
      });
      if (updatedAssistant) {
        return nextMessages;
      }
      return [
        ...removePendingAssistantPlaceholder(messages),
        { id: assistantMessageId, role: 'assistant' as const, content: QNA_STREAM_STOPPED_MESSAGE },
      ];
    };

    const normalizedConversationId = targetConversationId.trim();
    if (normalizedConversationId) {
      updateQnaConversationMessages(normalizedConversationId, applyStoppedState, { qnaState: 'QNA_IDLE' });
      return;
    }
    const nextMessages = applyStoppedState(qnaMessagesRef.current);
    qnaMessagesRef.current = nextMessages;
    setQnaMessages(nextMessages);
    setQnaStateView('QNA_IDLE');
    cacheConversationView('', {
      qnaInput: qnaInputRef.current,
      qnaMessages: nextMessages,
      qnaState: 'QNA_IDLE',
    });
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

function stoppedAssistantContent(content: string): string {
  const value = content ?? '';
  if (!value.trim()) {
    return QNA_STREAM_STOPPED_MESSAGE;
  }
  if (value.includes(QNA_STREAM_STOPPED_MESSAGE)) {
    return value;
  }
  return `${value.trimEnd()}\n\n${QNA_STREAM_STOPPED_MESSAGE}`;
}

function appendReasoningChunk(messages: ChatMessage[], assistantMessageId: string, chunk: string): ChatMessage[] {
  let updatedAssistant = false;
  const nextMessages = messages.map((item) => {
    if (item.id !== assistantMessageId) {
      return item;
    }
    updatedAssistant = true;
    return {
      ...item,
      reasoningContent: `${item.reasoningContent ?? ''}${chunk}`,
      reasoningState: 'streaming' as const,
    };
  });
  return updatedAssistant
    ? nextMessages
    : [
      ...messages,
      {
        id: assistantMessageId,
        role: 'assistant' as const,
        content: '',
        reasoningContent: chunk,
        reasoningState: 'streaming' as const,
      },
    ];
}

function markReasoningDone(messages: ChatMessage[], assistantMessageId: string): ChatMessage[] {
  return messages.map((item) => {
    if (item.id !== assistantMessageId || !item.reasoningContent?.trim()) {
      return item;
    }
    return { ...item, reasoningState: 'done' as const };
  });
}

function readReasoningChunk(payload: Record<string, unknown> | undefined): string {
  if (!payload) {
    return '';
  }
  const text = payload.text;
  return typeof text === 'string' ? text : '';
}

function appendAssistantResourceLink(messages: ChatMessage[], assistantMessageId: string, linkMarkdown: string): ChatMessage[] {
  const linkUrl = readFirstMarkdownLinkUrl(linkMarkdown);
  let updatedAssistant = false;
  const nextMessages = messages.map((item) => {
    if (item.id !== assistantMessageId) {
      return item;
    }
    updatedAssistant = true;
    if (item.content.includes(linkMarkdown) || (linkUrl && item.content.includes(linkUrl))) {
      return item;
    }
    const separator = item.content.trim() ? '\n\n' : '';
    return { ...item, content: `${item.content.trimEnd()}${separator}${linkMarkdown}` };
  });
  return updatedAssistant
    ? nextMessages
    : [...messages, { id: assistantMessageId, role: 'assistant' as const, content: linkMarkdown }];
}

function formatConversationResourceLink(payload: Record<string, unknown> | undefined): string {
  if (!payload) {
    return '';
  }
  const rawUrl = readLooseString(payload.downloadUrl) || readLooseString(payload.resourceUrl) || readLooseString(payload.url);
  if (!/^https?:\/\//i.test(rawUrl)) {
    return '';
  }
  const title = escapeMarkdownLinkText(
    readLooseString(payload.title) || readLooseString(payload.fileName) || readLooseString(payload.sourceName) || '外部资源',
  );
  const summary = readLooseString(payload.summary) || readLooseString(payload.summaryText);
  const sourceName = readLooseString(payload.sourceName);
  const details = [summary, sourceName].filter(Boolean).join(' · ');
  return details
    ? `- [${title}](${rawUrl})\n  ${details}`
    : `- [${title}](${rawUrl})`;
}

function escapeMarkdownLinkText(value: string): string {
  return value.replace(/[\\[\]]/g, '\\$&').trim() || '外部资源';
}

function readFirstMarkdownLinkUrl(value: string): string {
  const match = value.match(/\]\((https?:\/\/[^)\s]+)\)/i);
  return match?.[1] ?? '';
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
