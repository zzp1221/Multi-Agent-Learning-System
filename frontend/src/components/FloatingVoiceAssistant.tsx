import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import {
  History,
  LoaderCircle,
  Mic,
  MicOff,
  Pause,
  Play,
  RotateCcw,
  SendHorizontal,
  Square,
  Volume2,
  VolumeX,
  X,
} from 'lucide-react';
import { useLocation, useNavigate } from 'react-router-dom';
import { conversationApi, type ConversationStreamEvent } from '../api/conversation';
import { getErrorMessage } from '../api/request';
import { voiceApi, type VoicePageContext, type VoiceRealtimeEvent } from '../api/voice';
import {
  ACTIVE_CONVERSATION_ID_STORAGE_KEY,
  ENGINE_TASK_STORAGE_KEY,
  QNA_CONVERSATION_CACHE_STORAGE_KEY,
  SELECTED_CONVERSATION_STORAGE_KEY,
  conversationCacheKey,
  type PersistedQnaConversationCache,
  type SelectedConversationSnapshot,
} from '../pages/LearningStudioDemoPage.model';
import { readConversationChunk } from '../pages/LearningStudioDemoPage.utils';
import { dispatchVoiceConversationStream } from '../utils/voiceConversationBridge';
import { queueVoicePageAction } from '../utils/voicePageActions';

type VoiceState = 'idle' | 'recording' | 'transcribing' | 'ready' | 'chatting' | 'speaking' | 'error';
type VoiceHistoryItem = {
  id: string;
  text: string;
  answerPreview: string;
  pageType?: string;
  pageTitle?: string;
  createdAt: string;
};
type VoiceAssistantPosition = {
  x: number;
  y: number;
};
type VoiceAssistantSize = {
  width: number;
  height: number;
};
type VoiceAssistantDragState = {
  pointerId: number;
  startClientX: number;
  startClientY: number;
  startX: number;
  startY: number;
  dragging: boolean;
};

interface FloatingVoiceAssistantProps {
  isAuthenticated: boolean;
  openAuthModal: (tab?: 'login' | 'register', hint?: string) => void;
}

const TARGET_SAMPLE_RATE = 16000;
const MAX_RECORDING_MS = 60_000;
const COMMIT_TEXT_READY_FALLBACK_MS = 1_500;
const FINAL_TRANSCRIPT_TIMEOUT_MS = 8_000;
const VOICE_WORKLET_PATH = '/audio-worklet/voice-pcm-processor.js';
const VOICE_HISTORY_STORAGE_KEY = 'voice_assistant_history';
const VOICE_POSITION_STORAGE_KEY = 'voice_assistant_position';
const MAX_VOICE_HISTORY_ITEMS = 5;
const VOICE_FAB_SIZE = 54;
const VOICE_EDGE_GAP = 16;
const VOICE_DEFAULT_EDGE_GAP = 22;
const VOICE_PANEL_GAP = 14;
const VOICE_PANEL_DEFAULT_WIDTH = 380;
const VOICE_PANEL_DEFAULT_HEIGHT = 520;
const VOICE_DRAG_THRESHOLD_PX = 6;
const VOICE_RECENT_CONTEXT_MESSAGE_LIMIT = 6;
const VOICE_RECENT_CONTEXT_MAX_LENGTH = 600;
const VOICE_TTS_SENTENCE_MIN_LENGTH = 8;

export default function FloatingVoiceAssistant({ isAuthenticated, openAuthModal }: FloatingVoiceAssistantProps) {
  const location = useLocation();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [voiceState, setVoiceState] = useState<VoiceState>('idle');
  const [recognizedText, setRecognizedText] = useState('');
  const [assistantText, setAssistantText] = useState('');
  const [errorMessage, setErrorMessage] = useState('');
  const [noticeMessage, setNoticeMessage] = useState('');
  const [autoSpeak, setAutoSpeak] = useState(false);
  const [playbackPaused, setPlaybackPaused] = useState(false);
  const [voiceHistory, setVoiceHistory] = useState<VoiceHistoryItem[]>(() => readVoiceHistory());
  const [recordingMs, setRecordingMs] = useState(0);
  const [viewportSize, setViewportSize] = useState<VoiceAssistantSize>(() => readViewportSize());
  const [assistantPosition, setAssistantPosition] = useState<VoiceAssistantPosition>(() => readVoiceAssistantPosition());
  const [assistantDragging, setAssistantDragging] = useState(false);
  const [panelSize, setPanelSize] = useState<VoiceAssistantSize>({
    width: VOICE_PANEL_DEFAULT_WIDTH,
    height: VOICE_PANEL_DEFAULT_HEIGHT,
  });

  const voiceStateRef = useRef<VoiceState>('idle');
  const panelRef = useRef<HTMLElement | null>(null);
  const dragStateRef = useRef<VoiceAssistantDragState | null>(null);
  const suppressFabClickRef = useRef(false);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const workletNodeRef = useRef<AudioWorkletNode | null>(null);
  const sourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const realtimeSocketRef = useRef<WebSocket | null>(null);
  const currentVoiceSessionIdRef = useRef('');
  const realtimeSessionClosedRef = useRef(false);
  const currentTurnIdRef = useRef('');
  const currentCommandIntentRef = useRef('ASK');
  const prewarmInFlightRef = useRef(false);
  const expectedRealtimeCloseRef = useRef(false);
  const recordingCommitRequestedRef = useRef(false);
  const recognizedTextRef = useRef('');
  const assistantTextRef = useRef('');
  const recordStartedAtRef = useRef(0);
  const recordingTimerRef = useRef<number | null>(null);
  const chatAbortRef = useRef<AbortController | null>(null);
  const ttsAbortRef = useRef<AbortController | null>(null);
  const ttsQueueRef = useRef<string[]>([]);
  const ttsProcessingRef = useRef(false);
  const ttsSentenceBufferRef = useRef('');
  const ttsTurnCompletePendingRef = useRef(false);
  const playbackContextRef = useRef<AudioContext | null>(null);
  const playbackSourcesRef = useRef<Set<AudioBufferSourceNode>>(new Set());
  const playbackSourceCountRef = useRef(0);
  const playbackGenerationRef = useRef(0);
  const playbackPausedRef = useRef(false);
  const ttsStreamDoneRef = useRef(false);
  const playbackTimeRef = useRef(0);
  const realtimeReadyRef = useRef(false);
  const currentHistoryDraftRef = useRef<Omit<VoiceHistoryItem, 'answerPreview'> | null>(null);

  const statusLabel = useMemo(() => {
    switch (voiceState) {
      case 'recording':
        return `录音中 ${Math.floor(recordingMs / 1000)}s`;
      case 'transcribing':
        return '识别中';
      case 'ready':
        return '待发送';
      case 'chatting':
        return '回答中';
      case 'speaking':
        return playbackPaused ? '朗读已暂停' : '朗读中';
      case 'error':
        return '出错了';
      default:
        return '待机';
    }
  }, [playbackPaused, recordingMs, voiceState]);

  const pageContext = useMemo(() => buildVoicePageContext(location.pathname), [location.pathname]);
  const visibleAssistantPosition = useMemo(
    () => clampVoiceAssistantPosition(assistantPosition, viewportSize),
    [assistantPosition, viewportSize],
  );
  const panelPosition = useMemo(
    () => calculateVoicePanelPosition(visibleAssistantPosition, panelSize, viewportSize),
    [panelSize, viewportSize, visibleAssistantPosition],
  );
  const assistantRootStyle = useMemo(() => ({
    '--voice-assistant-left': `${Math.round(visibleAssistantPosition.x)}px`,
    '--voice-assistant-top': `${Math.round(visibleAssistantPosition.y)}px`,
    '--voice-assistant-panel-left': `${Math.round(panelPosition.x)}px`,
    '--voice-assistant-panel-top': `${Math.round(panelPosition.y)}px`,
  }) as CSSProperties, [panelPosition, visibleAssistantPosition]);

  useEffect(() => () => {
    stopRecordingResources();
    releasePrewarmedAsr();
    stopSpeaking();
    chatAbortRef.current?.abort();
  }, []);

  useEffect(() => {
    if (!open || !isAuthenticated) {
      return;
    }
    void ensurePrewarmedAsr();
  }, [isAuthenticated, open]);

  useEffect(() => {
    const handleResize = () => setViewportSize(readViewportSize());
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  useEffect(() => {
    setAssistantPosition((prev) => {
      const next = clampVoiceAssistantPosition(prev, viewportSize);
      if (positionsEqual(prev, next)) {
        return prev;
      }
      writeVoiceAssistantPosition(next);
      return next;
    });
  }, [viewportSize]);

  useEffect(() => {
    if (!open || !panelRef.current) {
      return undefined;
    }
    const panelElement = panelRef.current;
    const updatePanelSize = () => {
      setPanelSize({
        width: Math.ceil(panelElement.offsetWidth),
        height: Math.ceil(panelElement.offsetHeight),
      });
    };
    updatePanelSize();
    if (typeof ResizeObserver === 'undefined') {
      return undefined;
    }
    const observer = new ResizeObserver(updatePanelSize);
    observer.observe(panelElement);
    return () => observer.disconnect();
  }, [open]);

  useEffect(() => {
    voiceStateRef.current = voiceState;
  }, [voiceState]);

  useEffect(() => {
    recognizedTextRef.current = recognizedText;
  }, [recognizedText]);

  useEffect(() => {
    assistantTextRef.current = assistantText;
  }, [assistantText]);

  useEffect(() => {
    playbackPausedRef.current = playbackPaused;
  }, [playbackPaused]);

  const requireLogin = useCallback(() => {
    if (isAuthenticated) {
      return true;
    }
    openAuthModal('login', '请先登录后使用语音助手');
    return false;
  }, [isAuthenticated, openAuthModal]);

  const startRecording = useCallback(async () => {
    if (!requireLogin()) {
      return;
    }
    setOpen(true);
    setErrorMessage('');
    setNoticeMessage('');
    setAssistantText('');
    setRecognizedText('');
    recognizedTextRef.current = '';
    interruptCurrentTurn();
    realtimeReadyRef.current = false;
    recordingCommitRequestedRef.current = false;
    setRecordingMs(0);
    setVoiceState('recording');
    try {
      const voiceSession = await ensureVoiceSession();
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      const audioContext = new AudioContext({ sampleRate: TARGET_SAMPLE_RATE });
      if (!audioContext.audioWorklet) {
        throw new Error('当前浏览器不支持实时语音采集，请使用新版 Chrome 或 Edge');
      }
      await audioContext.audioWorklet.addModule(VOICE_WORKLET_PATH);
      const source = audioContext.createMediaStreamSource(stream);
      const workletNode = new AudioWorkletNode(audioContext, 'voice-pcm-processor');
      const gainNode = audioContext.createGain();
      gainNode.gain.value = 0;

      const socket = await connectRealtimeSocket(voiceSession.sessionId);
      currentVoiceSessionIdRef.current = voiceSession.sessionId;
      realtimeSocketRef.current = socket;
      workletNode.port.onmessage = (event) => {
        const turnId = currentTurnIdRef.current;
        if (!turnId || !realtimeReadyRef.current || socket.readyState !== WebSocket.OPEN) {
          return;
        }
        socket.send(JSON.stringify({
          type: 'audio_chunk',
          turnId,
          conversationId: readActiveVoiceConversationId(),
          pageType: pageContext.pageType,
          commandIntent: currentCommandIntentRef.current,
          data: arrayBufferToBase64(event.data as ArrayBuffer),
        }));
      };
      source.connect(workletNode);
      workletNode.connect(gainNode);
      gainNode.connect(audioContext.destination);
      mediaStreamRef.current = stream;
      audioContextRef.current = audioContext;
      workletNodeRef.current = workletNode;
      sourceRef.current = source;
      recordStartedAtRef.current = Date.now();
      recordingTimerRef.current = window.setInterval(() => {
        const elapsed = Date.now() - recordStartedAtRef.current;
        setRecordingMs(elapsed);
        if (elapsed >= MAX_RECORDING_MS) {
          void finishRecording();
        }
      }, 250);
    } catch (error) {
      setVoiceState('error');
      setErrorMessage(getErrorMessage(error));
      stopRecordingResources();
    }
  }, [requireLogin]);

  const finishRecording = useCallback(async () => {
    if (voiceStateRef.current !== 'recording') {
      return;
    }
    const socket = realtimeSocketRef.current;
    stopRecordingResources();
    recordingCommitRequestedRef.current = true;
    setVoiceState('transcribing');
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: 'commit', turnId: currentTurnIdRef.current }));
      window.setTimeout(() => {
        if (
          voiceStateRef.current === 'transcribing'
          && recordingCommitRequestedRef.current
          && recognizedTextRef.current.trim()
        ) {
          recordingCommitRequestedRef.current = false;
          voiceStateRef.current = 'ready';
          setVoiceState('ready');
          expectedRealtimeCloseRef.current = true;
          socket.close();
        }
      }, COMMIT_TEXT_READY_FALLBACK_MS);
      window.setTimeout(() => {
        if (voiceStateRef.current !== 'transcribing') {
          return;
        }
        recordingCommitRequestedRef.current = false;
        if (recognizedTextRef.current.trim()) {
          voiceStateRef.current = 'ready';
          setVoiceState('ready');
        } else {
          voiceStateRef.current = 'error';
          setVoiceState('error');
          setErrorMessage('没有识别到文字，请重试');
        }
        expectedRealtimeCloseRef.current = true;
        socket.close();
      }, FINAL_TRANSCRIPT_TIMEOUT_MS);
      return;
    }
    setVoiceState('error');
    setErrorMessage('实时语音连接已断开，请重试');
  }, []);

  const sendVoiceText = useCallback(async (rawText: string) => {
    const text = rawText.trim();
    if (!text || !requireLogin()) {
      return;
    }
    const activeConversationId = readActiveVoiceConversationId();
    let commandIntent = 'ASK';
    try {
      const command = await voiceApi.parseCommand(text, buildVoicePageContext(location.pathname, {
        conversationId: activeConversationId,
        voiceSessionId: currentVoiceSessionIdRef.current,
        voiceTurnId: currentTurnIdRef.current,
      }));
      commandIntent = command.intent || 'ASK';
      currentCommandIntentRef.current = commandIntent;
      if (handleLocalVoiceCommand(command.intent)) {
        return;
      }
    } catch {
      // 指令解析失败不阻断普通问答。
      currentCommandIntentRef.current = commandIntent;
    }
    if (!activeConversationId) {
      setOpen(true);
      setNoticeMessage('请先在左侧选择一个已有会话，再用语音继续对话');
      setVoiceState('idle');
      return;
    }
    stopSpeaking();
    chatAbortRef.current?.abort();
    const abortController = new AbortController();
    chatAbortRef.current = abortController;
    setAssistantText('');
    assistantTextRef.current = '';
    setNoticeMessage('');
    setVoiceState('chatting');
    beginHistoryTurn(text);
    const streamId = `voice-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    dispatchVoiceConversationStream({
      conversationId: activeConversationId,
      streamId,
      phase: 'start',
      userText: text,
    });
    try {
      await conversationApi.streamMessage(
        activeConversationId,
        {
          message: text,
          serviceType: 'TUTORING',
          webSearchEnabled: false,
          reasoningMode: 'NORMAL',
          voiceContext: buildVoicePageContext(location.pathname, {
            conversationId: activeConversationId,
            commandIntent,
            voiceSessionId: currentVoiceSessionIdRef.current,
            voiceTurnId: currentTurnIdRef.current,
          }),
        },
        {
          onEvent: (event: ConversationStreamEvent) => {
            const chunk = readConversationChunk(event.data, event.event);
            if (!chunk) {
              return;
            }
            enqueueTtsChunk(chunk, false);
            dispatchVoiceConversationStream({
              conversationId: activeConversationId,
              streamId,
              phase: 'chunk',
              chunk,
            });
            setAssistantText((prev) => {
              const next = prev + chunk;
              assistantTextRef.current = next;
              return next;
            });
          },
          onDone: () => {
            chatAbortRef.current = null;
            finishHistoryTurn(assistantTextRef.current);
            if (autoSpeak) {
              flushTtsSentenceBuffer(true);
              setVoiceState('speaking');
            } else {
              void sendVoiceTurnCompletionMarker();
              setVoiceState('idle');
            }
            dispatchVoiceConversationStream({
              conversationId: activeConversationId,
              streamId,
              phase: 'done',
            });
            dispatchVoiceConversationUpdated(activeConversationId);
          },
          onError: (error) => {
            const message = getErrorMessage(error);
            chatAbortRef.current = null;
            cancelHistoryTurn();
            setVoiceState('error');
            setErrorMessage(message);
            dispatchVoiceConversationStream({
              conversationId: activeConversationId,
              streamId,
              phase: 'error',
              errorMessage: message,
            });
            dispatchVoiceConversationUpdated(activeConversationId);
          },
        },
        abortController.signal,
      );
    } catch (error) {
      chatAbortRef.current = null;
      cancelHistoryTurn();
      setVoiceState('error');
      const message = getErrorMessage(error);
      setErrorMessage(message);
      dispatchVoiceConversationStream({
        conversationId: activeConversationId,
        streamId,
        phase: 'error',
        errorMessage: message,
      });
      dispatchVoiceConversationUpdated(activeConversationId);
    }
  }, [autoSpeak, location.pathname, requireLogin]);

  const sendRecognizedText = useCallback(async () => {
    await sendVoiceText(recognizedText);
  }, [recognizedText, sendVoiceText]);

  const handleFabPointerDown = useCallback((event: ReactPointerEvent<HTMLButtonElement>) => {
    if (event.button !== 0) {
      return;
    }
    dragStateRef.current = {
      pointerId: event.pointerId,
      startClientX: event.clientX,
      startClientY: event.clientY,
      startX: visibleAssistantPosition.x,
      startY: visibleAssistantPosition.y,
      dragging: false,
    };
    suppressFabClickRef.current = false;
    event.currentTarget.setPointerCapture(event.pointerId);
  }, [visibleAssistantPosition]);

  const handleFabPointerMove = useCallback((event: ReactPointerEvent<HTMLButtonElement>) => {
    const dragState = dragStateRef.current;
    if (!dragState || dragState.pointerId !== event.pointerId) {
      return;
    }
    const deltaX = event.clientX - dragState.startClientX;
    const deltaY = event.clientY - dragState.startClientY;
    if (!dragState.dragging && Math.hypot(deltaX, deltaY) < VOICE_DRAG_THRESHOLD_PX) {
      return;
    }
    if (!dragState.dragging) {
      dragState.dragging = true;
      suppressFabClickRef.current = true;
      setAssistantDragging(true);
    }
    const next = clampVoiceAssistantPosition({
      x: dragState.startX + deltaX,
      y: dragState.startY + deltaY,
    }, viewportSize);
    setAssistantPosition(next);
  }, [viewportSize]);

  const finishFabDrag = useCallback((event: ReactPointerEvent<HTMLButtonElement>) => {
    const dragState = dragStateRef.current;
    if (!dragState || dragState.pointerId !== event.pointerId) {
      return;
    }
    const deltaX = event.clientX - dragState.startClientX;
    const deltaY = event.clientY - dragState.startClientY;
    const finalPosition = clampVoiceAssistantPosition({
      x: dragState.startX + deltaX,
      y: dragState.startY + deltaY,
    }, viewportSize);
    if (dragState.dragging) {
      event.preventDefault();
      suppressFabClickRef.current = true;
      window.setTimeout(() => {
        suppressFabClickRef.current = false;
      }, 300);
      setAssistantPosition(finalPosition);
      writeVoiceAssistantPosition(finalPosition);
    }
    dragStateRef.current = null;
    setAssistantDragging(false);
    try {
      event.currentTarget.releasePointerCapture(event.pointerId);
    } catch {
      // 指针捕获可能已被浏览器释放，忽略即可。
    }
  }, [viewportSize]);

  useEffect(() => {
    if (voiceState !== 'speaking') {
      return;
    }
    startSpeakingPlayback();
  }, [voiceState]);

  const cancelCurrent = () => {
    if (voiceState === 'recording') {
      realtimeSocketRef.current?.send(JSON.stringify({ type: 'cancel', turnId: currentTurnIdRef.current }));
    }
    interruptCurrentTurn();
    stopRecordingResources();
    setNoticeMessage('');
    setVoiceState('idle');
  };

  function closePanel() {
    setOpen(false);
    if (voiceStateRef.current === 'idle' || voiceStateRef.current === 'error') {
      releasePrewarmedAsr();
      currentVoiceSessionIdRef.current = '';
      realtimeSessionClosedRef.current = true;
    }
  }

  function interruptCurrentTurn() {
    const socket = realtimeSocketRef.current;
    socket?.send(JSON.stringify({ type: 'cancel', turnId: currentTurnIdRef.current }));
    expectedRealtimeCloseRef.current = true;
    socket?.close();
    if (socket) {
      realtimeSessionClosedRef.current = true;
    }
    realtimeSocketRef.current = null;
    realtimeReadyRef.current = false;
    chatAbortRef.current?.abort();
    stopSpeaking();
  }

  async function ensureVoiceSession(): Promise<{ sessionId: string }> {
    if (currentVoiceSessionIdRef.current && !realtimeSessionClosedRef.current) {
      return { sessionId: currentVoiceSessionIdRef.current };
    }
    const voiceSession = await voiceApi.createSession();
    currentVoiceSessionIdRef.current = voiceSession.sessionId;
    realtimeSessionClosedRef.current = false;
    return voiceSession;
  }

  async function ensurePrewarmedAsr() {
    if (prewarmInFlightRef.current || currentVoiceSessionIdRef.current) {
      return;
    }
    prewarmInFlightRef.current = true;
    try {
      const voiceSession = await voiceApi.createSession();
      currentVoiceSessionIdRef.current = voiceSession.sessionId;
      realtimeSessionClosedRef.current = false;
      await voiceApi.prewarmSession(voiceSession.sessionId);
    } catch {
      currentVoiceSessionIdRef.current = '';
      realtimeSessionClosedRef.current = true;
    } finally {
      prewarmInFlightRef.current = false;
    }
  }

  function releasePrewarmedAsr() {
    const sessionId = currentVoiceSessionIdRef.current;
    if (!sessionId) {
      return;
    }
    void voiceApi.releasePrewarm(sessionId).catch(() => undefined);
  }

  return (
    <div className="voice-assistant-root" style={assistantRootStyle}>
      <AnimatePresence>
        {open ? (
          <motion.section
            ref={panelRef}
            initial={{ opacity: 0, y: 18, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 14, scale: 0.98 }}
            transition={{ duration: 0.18 }}
            className="voice-assistant-panel"
            aria-label="智能语音助手"
          >
            <div className="voice-assistant-header">
              <div>
                <div className="voice-assistant-title">智能语音助手</div>
                <div className="voice-assistant-status">{statusLabel}</div>
              </div>
              <div className="voice-assistant-header-actions">
                <button
                  type="button"
                  className={`voice-assistant-icon ${autoSpeak ? 'is-active' : ''}`}
                  onClick={() => {
                    setNoticeMessage('');
                    setAutoSpeak((prev) => !prev);
                  }}
                  title={autoSpeak ? '关闭自动朗读' : '开启自动朗读'}
                >
                  {autoSpeak ? <Volume2 className="h-4 w-4" /> : <VolumeX className="h-4 w-4" />}
                </button>
                <button type="button" className="voice-assistant-icon" onClick={closePanel} title="收起">
                  <X className="h-4 w-4" />
                </button>
              </div>
            </div>

            <div className="voice-assistant-body">
              {recognizedText || voiceState === 'recording' || voiceState === 'transcribing' || voiceState === 'ready' ? (
                <label className="voice-assistant-field">
                  <span>识别文本</span>
                  <textarea
                    value={recognizedText}
                    onChange={(event) => setRecognizedText(event.target.value)}
                    rows={3}
                    disabled={voiceState === 'chatting' || voiceState === 'speaking'}
                  />
                </label>
              ) : null}

              {assistantText ? (
                <div className="voice-assistant-answer">
                  <span>回答</span>
                  <p>{assistantText}</p>
                </div>
              ) : null}

              {errorMessage ? (
                <div className="voice-assistant-error">{errorMessage}</div>
              ) : null}
              {noticeMessage ? (
                <div className="voice-assistant-notice">{noticeMessage}</div>
              ) : null}

              {voiceHistory.length > 0 ? (
                <div className="voice-assistant-history">
                  <div className="voice-assistant-history-title">
                    <History className="h-3.5 w-3.5" />
                    最近语音
                  </div>
                  <div className="voice-assistant-history-list">
                    {voiceHistory.map((item) => (
                      <button
                        key={item.id}
                        type="button"
                        className="voice-assistant-history-item"
                        onClick={() => void sendVoiceText(item.text)}
                        title="重新发送"
                      >
                        <RotateCcw className="h-3.5 w-3.5" />
                        <span>
                          <strong>{item.text}</strong>
                          {item.answerPreview ? <small>{item.answerPreview}</small> : null}
                        </span>
                      </button>
                    ))}
                  </div>
                </div>
              ) : null}
            </div>

            <div className="voice-assistant-actions">
              {voiceState === 'recording' ? (
                <button type="button" className="voice-assistant-primary" onClick={() => void finishRecording()}>
                  <Square className="h-4 w-4" />
                  停止
                </button>
              ) : (
                <button
                  type="button"
                  className="voice-assistant-primary"
                  onClick={() => void startRecording()}
                  disabled={voiceState === 'transcribing'}
                >
                  {voiceState === 'transcribing' ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Mic className="h-4 w-4" />}
                  录音
                </button>
              )}
              <button
                type="button"
                className="voice-assistant-secondary"
                onClick={() => void sendRecognizedText()}
                disabled={!recognizedText.trim() || voiceState === 'recording' || voiceState === 'transcribing' || voiceState === 'chatting'}
              >
                <SendHorizontal className="h-4 w-4" />
                发送
              </button>
              {(voiceState === 'recording' || voiceState === 'chatting' || voiceState === 'speaking') ? (
                <button type="button" className="voice-assistant-secondary" onClick={cancelCurrent}>
                  <Square className="h-4 w-4" />
                  停止当前
                </button>
              ) : null}
              {voiceState === 'speaking' ? (
                playbackPaused ? (
                  <button type="button" className="voice-assistant-secondary" onClick={() => void resumeSpeaking()}>
                    <Play className="h-4 w-4" />
                    继续
                  </button>
                ) : (
                  <button type="button" className="voice-assistant-secondary" onClick={() => void pauseSpeaking()}>
                    <Pause className="h-4 w-4" />
                    暂停
                  </button>
                )
              ) : null}
            </div>
          </motion.section>
        ) : null}
      </AnimatePresence>

      <button
        type="button"
        className={`voice-assistant-fab ${voiceState === 'recording' ? 'is-recording' : ''} ${assistantDragging ? 'is-dragging' : ''}`}
        onPointerDown={handleFabPointerDown}
        onPointerMove={handleFabPointerMove}
        onPointerUp={finishFabDrag}
        onPointerCancel={finishFabDrag}
        onClick={(event) => {
          if (suppressFabClickRef.current) {
            event.preventDefault();
            suppressFabClickRef.current = false;
            return;
          }
          if (!open) {
            setOpen(true);
            return;
          }
          if (voiceState === 'recording') {
            void finishRecording();
            return;
          }
          void startRecording();
        }}
        title="智能语音助手"
      >
        {voiceState === 'recording' ? <MicOff className="h-5 w-5" /> : voiceState === 'chatting' ? <LoaderCircle className="h-5 w-5 animate-spin" /> : <Mic className="h-5 w-5" />}
      </button>
    </div>
  );

  function stopRecordingResources() {
    if (recordingTimerRef.current) {
      window.clearInterval(recordingTimerRef.current);
      recordingTimerRef.current = null;
    }
    workletNodeRef.current?.port.close();
    workletNodeRef.current?.disconnect();
    sourceRef.current?.disconnect();
    audioContextRef.current?.close().catch(() => undefined);
    mediaStreamRef.current?.getTracks().forEach((track) => track.stop());
    workletNodeRef.current = null;
    sourceRef.current = null;
    audioContextRef.current = null;
    mediaStreamRef.current = null;
  }

  async function connectRealtimeSocket(sessionId: string): Promise<WebSocket> {
    return new Promise((resolve, reject) => {
      const socket = new WebSocket(voiceApi.buildRealtimeUrl(sessionId));
      expectedRealtimeCloseRef.current = false;
      let settled = false;
      const timeout = window.setTimeout(() => {
        expectedRealtimeCloseRef.current = true;
        socket.close();
        settled = true;
        reject(new Error('实时语音连接超时'));
      }, 8000);

      socket.onmessage = (event) => {
        const realtimeEvent = parseRealtimeEvent(event.data);
        if (!realtimeEvent) {
          return;
        }
        handleRealtimeEvent(realtimeEvent);
        if (realtimeEvent.type === 'ready' && realtimeEvent.turnId) {
          window.clearTimeout(timeout);
          settled = true;
          resolve(socket);
        }
      };
      socket.onerror = () => {
        window.clearTimeout(timeout);
        if (!settled) {
          settled = true;
          reject(new Error('实时语音连接失败'));
        }
      };
      socket.onclose = () => {
        const expectedClose = expectedRealtimeCloseRef.current;
        expectedRealtimeCloseRef.current = false;
        const isCurrentSocket = realtimeSocketRef.current === socket;
        if (currentVoiceSessionIdRef.current === sessionId) {
          realtimeSessionClosedRef.current = true;
        }
        if (isCurrentSocket) {
          realtimeSocketRef.current = null;
          realtimeReadyRef.current = false;
        }
        if (!settled) {
          window.clearTimeout(timeout);
          settled = true;
          reject(new Error('实时语音连接已断开，请重试'));
        }
        if (isCurrentSocket && !expectedClose && (voiceStateRef.current === 'recording' || voiceStateRef.current === 'transcribing')) {
          setVoiceState('error');
          setErrorMessage('实时语音连接已断开，请重试');
        }
      };
    });
  }

  function handleRealtimeEvent(event: VoiceRealtimeEvent) {
    if (event.turnId && event.turnId !== currentTurnIdRef.current && event.type !== 'ready' && event.type !== 'cancelled') {
      return;
    }
    if (event.type === 'ready') {
      currentTurnIdRef.current = event.turnId ?? '';
      realtimeReadyRef.current = true;
      recordingCommitRequestedRef.current = false;
      return;
    }
    if (event.type === 'asr_ready') {
      realtimeReadyRef.current = true;
      return;
    }
    if (event.type === 'asr_partial' && event.text) {
      updateRecognizedText(event.text);
      return;
    }
    if (event.type === 'asr_final') {
      const text = event.text?.trim() ?? '';
      if (text) {
        updateRecognizedText(text);
      }
      if (recordingCommitRequestedRef.current) {
        recordingCommitRequestedRef.current = false;
        const finalText = (text || recognizedTextRef.current).trim();
        const nextState = finalText ? 'ready' : 'error';
        voiceStateRef.current = nextState;
        setVoiceState(nextState);
        if (!finalText) {
          setErrorMessage('没有识别到文字，请重试');
        }
        expectedRealtimeCloseRef.current = true;
        realtimeSocketRef.current?.close();
      }
      return;
    }
    if (event.type === 'cancelled') {
      currentTurnIdRef.current = event.turnId ?? '';
      realtimeReadyRef.current = true;
      recordingCommitRequestedRef.current = false;
      setRecognizedText('');
      return;
    }
    if (event.type === 'error') {
      setVoiceState('error');
      setErrorMessage(event.message || '语音识别失败，请重试');
      recordingCommitRequestedRef.current = false;
      expectedRealtimeCloseRef.current = true;
      realtimeSocketRef.current?.close();
    }
  }

  function updateRecognizedText(text: string) {
    const next = text.trim();
    if (!next) {
      return;
    }
    setRecognizedText(next);
    recognizedTextRef.current = next;
  }

  function stopSpeaking() {
    ttsAbortRef.current?.abort();
    ttsAbortRef.current = null;
    ttsQueueRef.current = [];
    ttsProcessingRef.current = false;
    ttsSentenceBufferRef.current = '';
    ttsTurnCompletePendingRef.current = false;
    playbackGenerationRef.current += 1;
    playbackSourcesRef.current.forEach((source) => {
      try {
        source.stop();
      } catch {
        // 已结束的 source 再 stop 会抛错，忽略即可。
      }
      source.disconnect();
    });
    playbackSourcesRef.current.clear();
    playbackSourceCountRef.current = 0;
    ttsStreamDoneRef.current = true;
    playbackContextRef.current?.close().catch(() => undefined);
    playbackContextRef.current = null;
    playbackTimeRef.current = 0;
    setPlaybackPaused(false);
  }

  async function playPcmBase64(base64: string, sampleRate: number) {
    const generation = playbackGenerationRef.current;
    const bytes = Uint8Array.from(atob(base64), (char) => char.charCodeAt(0));
    const samples = new Int16Array(bytes.buffer);
    const audioContext = playbackContextRef.current ?? new AudioContext({ sampleRate });
    playbackContextRef.current = audioContext;
    if (playbackPausedRef.current && audioContext.state === 'running') {
      await audioContext.suspend().catch(() => undefined);
    }
    const buffer = audioContext.createBuffer(1, samples.length, sampleRate);
    const output = buffer.getChannelData(0);
    for (let index = 0; index < samples.length; index += 1) {
      output[index] = Math.max(-1, Math.min(1, samples[index] / 32768));
    }
    const source = audioContext.createBufferSource();
    source.buffer = buffer;
    source.connect(audioContext.destination);
    const startAt = Math.max(audioContext.currentTime, playbackTimeRef.current);
    playbackSourcesRef.current.add(source);
    playbackSourceCountRef.current += 1;
    source.onended = () => {
      source.disconnect();
      playbackSourcesRef.current.delete(source);
      playbackSourceCountRef.current = Math.max(0, playbackSourceCountRef.current - 1);
      finishSpeakingIfPlaybackComplete(generation);
    };
    source.start(startAt);
    playbackTimeRef.current = startAt + buffer.duration;
  }

  function startSpeakingPlayback() {
    const playbackGeneration = playbackGenerationRef.current + 1;
    playbackGenerationRef.current = playbackGeneration;
    playbackSourceCountRef.current = 0;
    playbackSourcesRef.current.clear();
    playbackTimeRef.current = 0;
    ttsStreamDoneRef.current = false;
    setPlaybackPaused(false);
    void drainTtsQueue(playbackGeneration);
  }

  function enqueueTtsChunk(chunk: string, forceFlush: boolean) {
    if (!autoSpeak) {
      return;
    }
    ttsSentenceBufferRef.current += chunk;
    flushTtsSentenceBuffer(forceFlush);
  }

  function flushTtsSentenceBuffer(forceFlush: boolean) {
    if (!autoSpeak) {
      return;
    }
    if (forceFlush) {
      ttsTurnCompletePendingRef.current = true;
    }
    let buffer = ttsSentenceBufferRef.current;
    const sentences: string[] = [];
    let boundaryIndex = findSentenceBoundary(buffer);
    while (boundaryIndex >= 0) {
      const sentence = buffer.slice(0, boundaryIndex + 1).trim();
      if (sentence.length >= VOICE_TTS_SENTENCE_MIN_LENGTH) {
        sentences.push(sentence);
      }
      buffer = buffer.slice(boundaryIndex + 1);
      boundaryIndex = findSentenceBoundary(buffer);
    }
    if (forceFlush && buffer.trim()) {
      sentences.push(buffer.trim());
      buffer = '';
    }
    ttsSentenceBufferRef.current = buffer;
    if (sentences.length === 0) {
      if (forceFlush) {
        void drainTtsQueue(playbackGenerationRef.current);
      }
      return;
    }
    ttsQueueRef.current.push(...sentences);
    if (voiceStateRef.current !== 'speaking') {
      setVoiceState('speaking');
      return;
    }
    void drainTtsQueue(playbackGenerationRef.current);
  }

  async function drainTtsQueue(generation: number) {
    if (ttsProcessingRef.current) {
      return;
    }
    ttsProcessingRef.current = true;
    try {
      while (generation === playbackGenerationRef.current && ttsQueueRef.current.length > 0) {
        const sentence = ttsQueueRef.current.shift();
        if (!sentence) {
          continue;
        }
        await streamSentenceTts(sentence, false, generation);
      }
      if (
        generation === playbackGenerationRef.current
        && ttsTurnCompletePendingRef.current
        && chatAbortRef.current === null
        && ttsQueueRef.current.length === 0
      ) {
        ttsTurnCompletePendingRef.current = false;
        await streamSentenceTts('', true, generation);
      }
    } finally {
      ttsProcessingRef.current = false;
      if (chatAbortRef.current === null && ttsQueueRef.current.length === 0 && !ttsTurnCompletePendingRef.current) {
        ttsStreamDoneRef.current = true;
        finishSpeakingIfPlaybackComplete(generation);
      }
    }
  }

  async function streamSentenceTts(sentence: string, turnComplete: boolean, generation: number) {
    const abortController = new AbortController();
    ttsAbortRef.current = abortController;
    await voiceApi.streamTts(
      sentence,
      buildVoicePageContext(location.pathname, {
        conversationId: readActiveVoiceConversationId(),
        commandIntent: currentCommandIntentRef.current,
        voiceSessionId: currentVoiceSessionIdRef.current,
        voiceTurnId: currentTurnIdRef.current,
      }),
      turnComplete,
      {
        onEvent: (event) => {
          if (event.event !== 'audio' || !event.payload.audio || generation !== playbackGenerationRef.current) {
            return;
          }
          void playPcmBase64(event.payload.audio, event.payload.sampleRate ?? TARGET_SAMPLE_RATE);
        },
        onDone: () => {
          ttsAbortRef.current = null;
        },
        onError: (error) => {
          ttsAbortRef.current = null;
          setNoticeMessage(getErrorMessage(error));
        },
      },
      abortController.signal,
    );
  }

  async function sendVoiceTurnCompletionMarker() {
    const voiceSessionId = currentVoiceSessionIdRef.current;
    const voiceTurnId = currentTurnIdRef.current;
    if (!voiceSessionId || !voiceTurnId) {
      return;
    }
    await voiceApi.streamTts(
      '',
      buildVoicePageContext(location.pathname, {
        conversationId: readActiveVoiceConversationId(),
        commandIntent: currentCommandIntentRef.current,
        voiceSessionId,
        voiceTurnId,
      }),
      true,
      {
        onEvent: () => undefined,
        onDone: () => undefined,
        onError: () => undefined,
      },
    ).catch(() => undefined);
  }

  function findSentenceBoundary(text: string): number {
    const matches = ['。', '！', '？', '.', '!', '?', '\n']
      .map((mark) => text.indexOf(mark))
      .filter((index) => index >= 0);
    return matches.length === 0 ? -1 : Math.min(...matches);
  }

  async function pauseSpeaking() {
    const audioContext = playbackContextRef.current;
    if (audioContext && audioContext.state === 'running') {
      await audioContext.suspend();
    }
    setPlaybackPaused(true);
    setNoticeMessage('朗读已暂停');
  }

  async function resumeSpeaking() {
    const audioContext = playbackContextRef.current;
    if (audioContext && audioContext.state === 'suspended') {
      await audioContext.resume();
    }
    setPlaybackPaused(false);
    setNoticeMessage('继续朗读');
  }

  function finishSpeakingIfPlaybackComplete(generation: number) {
    if (generation !== playbackGenerationRef.current) {
      return;
    }
    if (!ttsStreamDoneRef.current || playbackSourceCountRef.current > 0) {
      return;
    }
    setPlaybackPaused(false);
    setVoiceState('idle');
  }

  function handleLocalVoiceCommand(intent: string) {
    if (intent === 'STOP_SPEAKING') {
      chatAbortRef.current?.abort();
      chatAbortRef.current = null;
      cancelHistoryTurn();
      stopSpeaking();
      setAssistantText('');
      assistantTextRef.current = '';
      setNoticeMessage('已停止朗读');
      setVoiceState('idle');
      return true;
    }
    if (intent === 'PAUSE_SPEAKING') {
      if (voiceStateRef.current === 'speaking') {
        void pauseSpeaking();
      } else {
        setNoticeMessage('当前没有正在朗读的内容');
      }
      return true;
    }
    if (intent === 'CONTINUE') {
      if (playbackPausedRef.current) {
        void resumeSpeaking();
        return true;
      }
      if (assistantTextRef.current.trim()) {
        setAutoSpeak(true);
        setNoticeMessage('继续朗读上一段回答');
        setVoiceState('speaking');
        return true;
      }
    }
    if (intent === 'OPEN_MISTAKE_BOOK') {
      navigate('/mistakes');
      setOpen(true);
      setNoticeMessage('已打开错题本');
      setVoiceState('idle');
      return true;
    }
    if (intent === 'OPEN_PROFILE') {
      navigate('/profile');
      setOpen(true);
      setNoticeMessage('已打开个人画像');
      setVoiceState('idle');
      return true;
    }
    if (intent === 'START_REVIEW') {
      queueVoicePageAction('start_review');
      navigate('/mistakes');
      setOpen(true);
      setNoticeMessage('已开始今日复习');
      setVoiceState('idle');
      return true;
    }
    if (intent === 'OPEN_QNA') {
      navigate('/');
      setOpen(true);
      setNoticeMessage('已回到智能问答');
      setVoiceState('idle');
      return true;
    }
    if (intent === 'GENERATE_STUDY_PLAN') {
      queueVoicePageAction('generate_study_plan');
      navigate('/engine');
      setOpen(true);
      setNoticeMessage('已提交学习路径规划');
      setVoiceState('idle');
      return true;
    }
    return false;
  }

  function beginHistoryTurn(text: string) {
    currentHistoryDraftRef.current = {
      id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      text,
      pageType: pageContext.pageType,
      pageTitle: pageContext.pageTitle,
      createdAt: new Date().toISOString(),
    };
  }

  function finishHistoryTurn(answer: string) {
    const draft = currentHistoryDraftRef.current;
    currentHistoryDraftRef.current = null;
    if (!draft) {
      return;
    }
    const item: VoiceHistoryItem = {
      ...draft,
      answerPreview: answer.trim().slice(0, 120),
    };
    setVoiceHistory((prev) => {
      const next = [item, ...prev.filter((history) => history.text !== item.text)]
        .slice(0, MAX_VOICE_HISTORY_ITEMS);
      writeVoiceHistory(next);
      return next;
    });
  }

  function cancelHistoryTurn() {
    currentHistoryDraftRef.current = null;
  }
}

function arrayBufferToBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = '';
  const chunkSize = 0x8000;
  for (let index = 0; index < bytes.length; index += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(index, index + chunkSize));
  }
  return btoa(binary);
}

function parseRealtimeEvent(raw: string): VoiceRealtimeEvent | null {
  try {
    return JSON.parse(raw) as VoiceRealtimeEvent;
  } catch {
    return null;
  }
}

function buildVoicePageContext(pathname: string, overrides: Partial<VoicePageContext> = {}): VoicePageContext {
  const baseContext = buildBaseVoicePageContext(pathname);
  return {
    ...baseContext,
    currentPath: pathname,
    source: 'voice_assistant',
    ...overrides,
    recentMessagesSummary: overrides.recentMessagesSummary
      ?? readRecentConversationSummary(overrides.conversationId),
  };
}

function buildBaseVoicePageContext(pathname: string): VoicePageContext {
  if (pathname.startsWith('/engine')) {
    const engineContext = readEngineStructuredContext();
    return {
      pageType: 'learning_service',
      pageTitle: readDocumentTitle('学习服务'),
      selectedService: engineContext.selectedService,
      formParametersSummary: engineContext.formParametersSummary,
      taskStatus: engineContext.taskStatus,
      resourceResultSummary: engineContext.resourceResultSummary,
      downloadResourceSummary: engineContext.downloadResourceSummary,
      recommendedAction: engineContext.recommendedAction,
    };
  }
  if (pathname.startsWith('/mistakes')) {
    const mistakeContext = readVisiblePageContext(['错题', '知识点', '复习', '掌握'], 500);
    return {
      pageType: 'mistake_book',
      pageTitle: readDocumentTitle('错题本'),
      currentMistakeSummary: mistakeContext,
      reviewStatus: mistakeContext,
    };
  }
  if (pathname.startsWith('/profile')) {
    const profileContext = readProfileStructuredContext();
    return {
      pageType: 'learner_profile',
      pageTitle: readDocumentTitle('个人画像'),
      weakPointsSummary: profileContext.weakPointsSummary,
      currentGoal: profileContext.currentGoal,
      lowestMasteryKnowledge: profileContext.lowestMasteryKnowledge,
    };
  }
  return {
    pageType: 'qna_chat',
    pageTitle: readDocumentTitle('智能对话'),
  };
}

function readEngineStructuredContext(): Partial<VoicePageContext> {
  if (typeof window === 'undefined') {
    return {};
  }
  try {
    const raw = window.sessionStorage.getItem(ENGINE_TASK_STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) as {
      selectedService?: string;
      snapshots?: Record<string, Record<string, unknown>>;
    } : {};
    const selectedService = parsed.selectedService || '';
    const snapshot = selectedService && parsed.snapshots ? parsed.snapshots[selectedService] : null;
    const taskStatus = readString(snapshot, 'engineState') || readString(snapshot, 'status');
    const taskSummary = readString(snapshot, 'taskSummary');
    const resultLines = Array.isArray(snapshot?.serviceResultLines)
      ? snapshot.serviceResultLines.filter((item): item is string => typeof item === 'string').slice(0, 3).join('；')
      : '';
    const downloads = Array.isArray(snapshot?.downloadLinks) ? snapshot.downloadLinks.length : 0;
    return {
      selectedService,
      taskStatus,
      formParametersSummary: compactText([selectedService, taskStatus, taskSummary].join('；'), 500),
      resourceResultSummary: compactText([taskSummary, resultLines].join('；'), 700),
      downloadResourceSummary: downloads > 0 ? `可下载资源 ${downloads} 个` : '',
      recommendedAction: taskStatus === 'ENGINE_COMPLETED' ? '查看结果或下载资源' : '',
    };
  } catch {
    return {
      formParametersSummary: readVisiblePageContext(['当前服务', '参数', '任务'], 500),
    };
  }
}

function readProfileStructuredContext(): Partial<VoicePageContext> {
  const visible = readVisiblePageContext(['薄弱', '目标', '掌握', '知识'], 700);
  return {
    weakPointsSummary: visible,
    currentGoal: firstVisibleLineContaining(['目标']),
    lowestMasteryKnowledge: firstVisibleLineContaining(['最低', '薄弱', '掌握']),
  };
}

function readVisiblePageContext(keywords: string[], maxLength: number): string {
  if (typeof document === 'undefined') {
    return '';
  }
  const text = Array.from(document.querySelectorAll('main h1, main h2, main h3, main p, main li, main button, main [aria-label]'))
    .map((node) => node.textContent?.replace(/\s+/g, ' ').trim() ?? '')
    .filter((line) => line && keywords.some((keyword) => line.includes(keyword)))
    .slice(0, 8)
    .join('；');
  return compactText(text, maxLength);
}

function firstVisibleLineContaining(keywords: string[]): string {
  if (typeof document === 'undefined') {
    return '';
  }
  return Array.from(document.querySelectorAll('main h1, main h2, main h3, main p, main li'))
    .map((node) => node.textContent?.replace(/\s+/g, ' ').trim() ?? '')
    .find((line) => line && keywords.some((keyword) => line.includes(keyword))) ?? '';
}

function readString(source: Record<string, unknown> | null | undefined, key: string): string {
  const value = source?.[key];
  return typeof value === 'string' ? value : '';
}

function compactText(text: string, maxLength: number): string {
  const normalized = text.replace(/\s+/g, ' ').replace(/；+/g, '；').trim();
  return normalized.length <= maxLength ? normalized : `${normalized.slice(0, maxLength)}...`;
}

function readRecentConversationSummary(conversationId?: string): string {
  if (typeof window === 'undefined' || !conversationId) {
    return '';
  }
  try {
    const raw = window.sessionStorage.getItem(QNA_CONVERSATION_CACHE_STORAGE_KEY);
    if (!raw) {
      return '';
    }
    const cache = JSON.parse(raw) as PersistedQnaConversationCache;
    const messages = cache[conversationCacheKey(conversationId)]?.qnaMessages ?? [];
    return messages
      .filter((item) => item.content?.trim() && item.id !== 'qna-greeting')
      .slice(-VOICE_RECENT_CONTEXT_MESSAGE_LIMIT)
      .map((item) => `${item.role === 'user' ? '用户' : '助手'}：${item.content.trim().replace(/\s+/g, ' ')}`)
      .join('\n')
      .slice(0, VOICE_RECENT_CONTEXT_MAX_LENGTH);
  } catch {
    return '';
  }
}

function readDocumentTitle(fallback: string): string {
  if (typeof document === 'undefined') {
    return fallback;
  }
  return document.title?.trim() || fallback;
}

function readActiveVoiceConversationId(): string {
  if (typeof window === 'undefined') {
    return '';
  }
  const activeConversationId = window.sessionStorage.getItem(ACTIVE_CONVERSATION_ID_STORAGE_KEY)?.trim() ?? '';
  if (activeConversationId) {
    return activeConversationId;
  }
  try {
    const raw = window.sessionStorage.getItem(SELECTED_CONVERSATION_STORAGE_KEY);
    if (!raw) {
      return '';
    }
    const selected = JSON.parse(raw) as SelectedConversationSnapshot;
    return selected.conversationId?.trim() ?? '';
  } catch {
    return '';
  }
}

function dispatchVoiceConversationUpdated(conversationId: string) {
  if (typeof window === 'undefined') {
    return;
  }
  window.dispatchEvent(new Event('app:conversation-updated'));
  window.dispatchEvent(new CustomEvent('app:open-conversation', {
    detail: {
      conversationId,
    },
  }));
}

function readViewportSize(): VoiceAssistantSize {
  if (typeof window === 'undefined') {
    return { width: 1280, height: 720 };
  }
  return {
    width: window.innerWidth,
    height: window.innerHeight,
  };
}

function readVoiceAssistantPosition(): VoiceAssistantPosition {
  const viewport = readViewportSize();
  const fallback = getDefaultVoiceAssistantPosition(viewport);
  if (typeof window === 'undefined') {
    return fallback;
  }
  try {
    const raw = window.localStorage.getItem(VOICE_POSITION_STORAGE_KEY);
    if (!raw) {
      return fallback;
    }
    const parsed = JSON.parse(raw) as Partial<VoiceAssistantPosition>;
    if (!Number.isFinite(parsed.x) || !Number.isFinite(parsed.y)) {
      return fallback;
    }
    return clampVoiceAssistantPosition({
      x: Number(parsed.x),
      y: Number(parsed.y),
    }, viewport);
  } catch {
    return fallback;
  }
}

function writeVoiceAssistantPosition(position: VoiceAssistantPosition) {
  if (typeof window === 'undefined') {
    return;
  }
  window.localStorage.setItem(VOICE_POSITION_STORAGE_KEY, JSON.stringify(position));
}

function getDefaultVoiceAssistantPosition(viewport: VoiceAssistantSize): VoiceAssistantPosition {
  return clampVoiceAssistantPosition({
    x: viewport.width - VOICE_FAB_SIZE - VOICE_DEFAULT_EDGE_GAP,
    y: viewport.height - VOICE_FAB_SIZE - VOICE_DEFAULT_EDGE_GAP,
  }, viewport);
}

function clampVoiceAssistantPosition(position: VoiceAssistantPosition, viewport: VoiceAssistantSize): VoiceAssistantPosition {
  return {
    x: clamp(position.x, VOICE_EDGE_GAP, Math.max(VOICE_EDGE_GAP, viewport.width - VOICE_FAB_SIZE - VOICE_EDGE_GAP)),
    y: clamp(position.y, VOICE_EDGE_GAP, Math.max(VOICE_EDGE_GAP, viewport.height - VOICE_FAB_SIZE - VOICE_EDGE_GAP)),
  };
}

function calculateVoicePanelPosition(
  buttonPosition: VoiceAssistantPosition,
  panelSize: VoiceAssistantSize,
  viewport: VoiceAssistantSize,
): VoiceAssistantPosition {
  const fitsAbove = buttonPosition.y >= panelSize.height + VOICE_PANEL_GAP + VOICE_EDGE_GAP;
  const top = fitsAbove
    ? buttonPosition.y - panelSize.height - VOICE_PANEL_GAP
    : buttonPosition.y + VOICE_FAB_SIZE + VOICE_PANEL_GAP;
  return {
    x: clamp(buttonPosition.x + VOICE_FAB_SIZE - panelSize.width, VOICE_EDGE_GAP, Math.max(VOICE_EDGE_GAP, viewport.width - panelSize.width - VOICE_EDGE_GAP)),
    y: clamp(top, VOICE_EDGE_GAP, Math.max(VOICE_EDGE_GAP, viewport.height - panelSize.height - VOICE_EDGE_GAP)),
  };
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

function positionsEqual(left: VoiceAssistantPosition, right: VoiceAssistantPosition): boolean {
  return Math.round(left.x) === Math.round(right.x) && Math.round(left.y) === Math.round(right.y);
}

function readVoiceHistory(): VoiceHistoryItem[] {
  if (typeof window === 'undefined') {
    return [];
  }
  try {
    const raw = window.localStorage.getItem(VOICE_HISTORY_STORAGE_KEY);
    if (!raw) {
      return [];
    }
    const parsed = JSON.parse(raw) as VoiceHistoryItem[];
    return Array.isArray(parsed)
      ? parsed.filter((item) => item && typeof item.text === 'string' && item.text.trim()).slice(0, MAX_VOICE_HISTORY_ITEMS)
      : [];
  } catch {
    return [];
  }
}

function writeVoiceHistory(items: VoiceHistoryItem[]) {
  if (typeof window === 'undefined') {
    return;
  }
  window.localStorage.setItem(VOICE_HISTORY_STORAGE_KEY, JSON.stringify(items.slice(0, MAX_VOICE_HISTORY_ITEMS)));
}
