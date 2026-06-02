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
import { readConversationChunk } from '../pages/LearningStudioDemoPage.utils';
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
  const currentTurnIdRef = useRef('');
  const expectedRealtimeCloseRef = useRef(false);
  const recordingCommitRequestedRef = useRef(false);
  const recognizedTextRef = useRef('');
  const assistantTextRef = useRef('');
  const recordStartedAtRef = useRef(0);
  const recordingTimerRef = useRef<number | null>(null);
  const chatAbortRef = useRef<AbortController | null>(null);
  const ttsAbortRef = useRef<AbortController | null>(null);
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
    stopSpeaking();
    chatAbortRef.current?.abort();
  }, []);

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
      const voiceSession = await voiceApi.createSession();
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
      realtimeSocketRef.current = socket;
      workletNode.port.onmessage = (event) => {
        const turnId = currentTurnIdRef.current;
        if (!turnId || !realtimeReadyRef.current || socket.readyState !== WebSocket.OPEN) {
          return;
        }
        socket.send(JSON.stringify({
          type: 'audio_chunk',
          turnId,
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
    try {
      const command = await voiceApi.parseCommand(text, pageContext);
      if (handleLocalVoiceCommand(command.intent)) {
        return;
      }
    } catch {
      // 指令解析失败不阻断普通问答。
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
    try {
      const conversationId = (await conversationApi.createConversation()).conversationId;
      await conversationApi.streamMessage(
        conversationId,
        {
          message: text,
          serviceType: 'TUTORING',
          webSearchEnabled: false,
          reasoningMode: 'NORMAL',
          voiceContext: pageContext,
        },
        {
          onEvent: (event: ConversationStreamEvent) => {
            const chunk = readConversationChunk(event.data, event.event);
            if (!chunk) {
              return;
            }
            setAssistantText((prev) => {
              const next = prev + chunk;
              assistantTextRef.current = next;
              return next;
            });
          },
          onDone: () => {
            chatAbortRef.current = null;
            finishHistoryTurn(assistantTextRef.current);
            setVoiceState(autoSpeak ? 'speaking' : 'idle');
            window.dispatchEvent(new Event('app:conversation-updated'));
          },
          onError: (error) => {
            chatAbortRef.current = null;
            cancelHistoryTurn();
            setVoiceState('error');
            setErrorMessage(getErrorMessage(error));
          },
        },
        abortController.signal,
      );
    } catch (error) {
      chatAbortRef.current = null;
      cancelHistoryTurn();
      setVoiceState('error');
      setErrorMessage(getErrorMessage(error));
    }
  }, [autoSpeak, pageContext, requireLogin]);

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
    if (!autoSpeak || voiceState !== 'speaking' || !assistantText.trim()) {
      return;
    }
    const abortController = new AbortController();
    const playbackGeneration = playbackGenerationRef.current + 1;
    playbackGenerationRef.current = playbackGeneration;
    playbackSourceCountRef.current = 0;
    playbackSourcesRef.current.clear();
    playbackTimeRef.current = 0;
    ttsStreamDoneRef.current = false;
    setPlaybackPaused(false);
    ttsAbortRef.current = abortController;
    void voiceApi.streamTts(
      assistantText,
      {
        onEvent: (event) => {
          if (event.event !== 'audio' || !event.payload.audio) {
            return;
          }
          void playPcmBase64(event.payload.audio, event.payload.sampleRate ?? TARGET_SAMPLE_RATE);
        },
        onDone: () => {
          ttsAbortRef.current = null;
          ttsStreamDoneRef.current = true;
          finishSpeakingIfPlaybackComplete(playbackGeneration);
        },
        onError: (error) => {
          ttsAbortRef.current = null;
          setNoticeMessage(getErrorMessage(error));
          ttsStreamDoneRef.current = true;
          setVoiceState('idle');
        },
      },
      abortController.signal,
    );
    return () => {
      abortController.abort();
    };
  }, [assistantText, autoSpeak, voiceState]);

  const cancelCurrent = () => {
    if (voiceState === 'recording') {
      realtimeSocketRef.current?.send(JSON.stringify({ type: 'cancel', turnId: currentTurnIdRef.current }));
    }
    interruptCurrentTurn();
    stopRecordingResources();
    setNoticeMessage('');
    setVoiceState('idle');
  };

  function interruptCurrentTurn() {
    realtimeSocketRef.current?.send(JSON.stringify({ type: 'cancel', turnId: currentTurnIdRef.current }));
    expectedRealtimeCloseRef.current = true;
    realtimeSocketRef.current?.close();
    realtimeSocketRef.current = null;
    realtimeReadyRef.current = false;
    chatAbortRef.current?.abort();
    stopSpeaking();
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
                <button type="button" className="voice-assistant-icon" onClick={() => setOpen(false)} title="收起">
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

function buildVoicePageContext(pathname: string): VoicePageContext {
  if (pathname.startsWith('/engine')) {
    return {
      pageType: 'learning_service',
      pageTitle: readDocumentTitle('学习服务'),
    };
  }
  if (pathname.startsWith('/mistakes')) {
    return {
      pageType: 'mistake_book',
      pageTitle: readDocumentTitle('错题本'),
    };
  }
  if (pathname.startsWith('/profile')) {
    return {
      pageType: 'learner_profile',
      pageTitle: readDocumentTitle('个人画像'),
    };
  }
  return {
    pageType: 'qna_chat',
    pageTitle: readDocumentTitle('智能对话'),
  };
}

function readDocumentTitle(fallback: string): string {
  if (typeof document === 'undefined') {
    return fallback;
  }
  return document.title?.trim() || fallback;
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
