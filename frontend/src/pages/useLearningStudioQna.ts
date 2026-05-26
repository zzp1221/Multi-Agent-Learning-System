import { useCallback, useEffect, useRef, useState, type Dispatch, type MutableRefObject, type SetStateAction } from 'react';
import { conversationApi } from '../api/conversation';
import { getErrorMessage } from '../api/request';
import type { LayoutOutletContext } from '../components/Layout';
import {
  QNA_GREETING,
  type ChatMessage,
  type PendingChatImage,
  type QnaState,
} from './LearningStudioDemoPage.types';
import { readConversationChunk } from './LearningStudioDemoPage.utils';
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

interface UseLearningStudioQnaOptions {
  mode: 'qna' | 'engine';
  isAuthenticated: boolean;
  openAuthModal: LayoutOutletContext['openAuthModal'];
  conversationId: string;
  setConversationId: Dispatch<SetStateAction<string>>;
  conversationIdRef: MutableRefObject<string>;
  mountedRef: MutableRefObject<boolean>;
}

export function useLearningStudioQna({
  mode,
  isAuthenticated,
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
    const sourceMessages = isVisibleConversation
      ? qnaMessagesRef.current
      : cachedSnapshot?.qnaMessages ?? [];
    const nextMessages = updater(sourceMessages);
    const nextSnapshot: PersistedConversationViewSnapshot = {
      qnaInput: options.qnaInput ?? cachedSnapshot?.qnaInput ?? (isVisibleConversation ? qnaInputRef.current : ''),
      qnaMessages: nextMessages,
      qnaState: options.qnaState ?? cachedSnapshot?.qnaState ?? (isVisibleConversation ? qnaStateRef.current : 'QNA_IDLE'),
    };

    cacheConversationView(normalizedConversationId, nextSnapshot);

    if (!isVisibleConversation || !mountedRef.current) {
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

    let latestMessages = cachedMessages;
    let previousSignature = latestMessages ? buildConversationSyncSignature(latestMessages) : '';
    let unchangedPolls = 0;
    const maxAttempts = expectStreaming ? 360 : 1;

    for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
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
          expectStreaming && hasPendingAssistantResponse(preferredMessages) && !mappedHasResolvedAssistant
            ? 'QNA_STREAMING'
            : 'QNA_IDLE';
        latestMessages = preferredMessages;
        qnaMessagesRef.current = preferredMessages;
        setQnaMessages(preferredMessages);
        if (expectStreaming) {
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

        if (
          !expectStreaming
          || mappedHasResolvedAssistant
          || (attempt >= 2 && unchangedPolls >= 2 && !hasPendingAssistantResponse(preferredMessages))
        ) {
          return true;
        }
      }

      if (attempt < maxAttempts - 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 2000));
      }
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
    const shouldResumeStreaming =
      cachedSnapshot?.qnaState === 'QNA_STREAMING'
      || Boolean(qnaStreamTokensRef.current[nextConversationId])
      || hasPendingAssistantResponse(cachedSnapshot?.qnaMessages);
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
      cacheConversationView(snapshot.conversationId ?? '', {
        qnaInput: restoredInput,
        qnaMessages: restoredMessages,
        qnaState: snapshot.qnaState === 'QNA_STREAMING' ? 'QNA_STREAMING' : 'QNA_IDLE',
      });
      conversationIdRef.current = restoredConversationId;
      qnaInputRef.current = restoredInput;
      qnaMessagesRef.current = restoredMessages;
      setConversationId(restoredConversationId);
      setQnaInput(restoredInput);
      setQnaStateView(snapshot.qnaState === 'QNA_STREAMING' ? 'QNA_STREAMING' : 'QNA_IDLE');
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

  const handleQnaSend = async () => {
    const text = qnaInput.trim();
    const uploadedImageUrls = pendingQnaImages
      .filter((item) => item.uploadStatus === 'uploaded' && item.uploadedUrl)
      .map((item) => item.uploadedUrl as string);
    if ((!text && uploadedImageUrls.length === 0) || qnaBusy) {
      return;
    }
    if (!isAuthenticated) {
      openAuthModal('login', '请先登录');
      return;
    }

    const assistantMessageId = `qna-assistant-${Date.now()}`;
    const userMessageId = `qna-user-${Date.now()}`;
    const pendingPreviewUrls = pendingQnaImages.map((item) => item.previewUrl);
    const useWebSearch = qnaWebSearchEnabled;
    const useDeepReasoning = deepReasoningEnabled;
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
        return;
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

      await conversationApi.streamMessage(
        currentConversationId,
        {
          message: text,
          imageUrls: uploadedImageUrls,
          serviceType: 'TUTORING',
          webSearchEnabled: useWebSearch,
          reasoningMode: useDeepReasoning ? 'DEEP' : 'NORMAL',
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
            updateQnaConversationMessages(currentConversationId, (messages) => messages, { qnaState: 'QNA_IDLE' });
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
    } catch (error) {
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
        return;
      }
      if (conversationIdRef.current !== originConversationId) {
        return;
      }
      setQnaMessages((prev) =>
        prev.map((item) => (item.id === assistantMessageId ? { ...item, content: `会话失败：${message}` } : item)),
      );
      setQnaStateView('QNA_IDLE');
    }
  };

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
      onToggleDeepReasoning: () => setDeepReasoningEnabled((prev) => !prev),
      onToggleWebSearch: () => setQnaWebSearchEnabled((prev) => !prev),
      onPickImages: handlePickQnaImages,
      onRemoveImage: handleRemovePendingQnaImage,
    },
  };
}
