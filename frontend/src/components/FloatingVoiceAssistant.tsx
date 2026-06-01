import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import {
  LoaderCircle,
  Mic,
  MicOff,
  Pause,
  SendHorizontal,
  Square,
  Volume2,
  VolumeX,
  X,
} from 'lucide-react';
import { conversationApi, type ConversationStreamEvent } from '../api/conversation';
import { getErrorMessage } from '../api/request';
import { voiceApi, type VoiceRealtimeEvent } from '../api/voice';
import { readConversationChunk } from '../pages/LearningStudioDemoPage.utils';

type VoiceState = 'idle' | 'recording' | 'transcribing' | 'ready' | 'chatting' | 'speaking' | 'error';

interface FloatingVoiceAssistantProps {
  isAuthenticated: boolean;
  openAuthModal: (tab?: 'login' | 'register', hint?: string) => void;
}

const TARGET_SAMPLE_RATE = 16000;
const MAX_RECORDING_MS = 60_000;
const VOICE_WORKLET_PATH = '/audio-worklet/voice-pcm-processor.js';

export default function FloatingVoiceAssistant({ isAuthenticated, openAuthModal }: FloatingVoiceAssistantProps) {
  const [open, setOpen] = useState(false);
  const [voiceState, setVoiceState] = useState<VoiceState>('idle');
  const [recognizedText, setRecognizedText] = useState('');
  const [assistantText, setAssistantText] = useState('');
  const [errorMessage, setErrorMessage] = useState('');
  const [autoSpeak, setAutoSpeak] = useState(false);
  const [recordingMs, setRecordingMs] = useState(0);

  const voiceStateRef = useRef<VoiceState>('idle');
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const workletNodeRef = useRef<AudioWorkletNode | null>(null);
  const sourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const realtimeSocketRef = useRef<WebSocket | null>(null);
  const currentTurnIdRef = useRef('');
  const expectedRealtimeCloseRef = useRef(false);
  const recognizedTextRef = useRef('');
  const recordStartedAtRef = useRef(0);
  const recordingTimerRef = useRef<number | null>(null);
  const chatAbortRef = useRef<AbortController | null>(null);
  const ttsAbortRef = useRef<AbortController | null>(null);
  const playbackContextRef = useRef<AudioContext | null>(null);
  const playbackTimeRef = useRef(0);
  const realtimeReadyRef = useRef(false);

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
        return '朗读中';
      case 'error':
        return '出错了';
      default:
        return '待机';
    }
  }, [recordingMs, voiceState]);

  useEffect(() => () => {
    stopRecordingResources();
    stopSpeaking();
    chatAbortRef.current?.abort();
  }, []);

  useEffect(() => {
    voiceStateRef.current = voiceState;
  }, [voiceState]);

  useEffect(() => {
    recognizedTextRef.current = recognizedText;
  }, [recognizedText]);

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
    setAssistantText('');
    setRecognizedText('');
    recognizedTextRef.current = '';
    interruptCurrentTurn();
    realtimeReadyRef.current = false;
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
    setVoiceState('transcribing');
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: 'commit', turnId: currentTurnIdRef.current }));
      window.setTimeout(() => {
        if (voiceStateRef.current === 'transcribing' && !recognizedTextRef.current.trim()) {
      setVoiceState('error');
      setErrorMessage('没有识别到文字，请重试');
      expectedRealtimeCloseRef.current = true;
      socket.close();
    }
      }, 3000);
      return;
    }
    setVoiceState('error');
    setErrorMessage('实时语音连接已断开，请重试');
  }, []);

  const sendRecognizedText = useCallback(async () => {
    const text = recognizedText.trim();
    if (!text || !requireLogin()) {
      return;
    }
    try {
      const command = await voiceApi.parseCommand(text);
      if (command.intent === 'STOP_SPEAKING') {
        stopSpeaking();
        setAssistantText('');
        setVoiceState('idle');
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
    setVoiceState('chatting');
    try {
      const conversationId = (await conversationApi.createConversation()).conversationId;
      await conversationApi.streamMessage(
        conversationId,
        {
          message: text,
          serviceType: 'TUTORING',
          webSearchEnabled: false,
          reasoningMode: 'NORMAL',
        },
        {
          onEvent: (event: ConversationStreamEvent) => {
            const chunk = readConversationChunk(event.data, event.event);
            if (!chunk) {
              return;
            }
            setAssistantText((prev) => prev + chunk);
          },
          onDone: () => {
            chatAbortRef.current = null;
            setVoiceState(autoSpeak ? 'speaking' : 'idle');
            window.dispatchEvent(new Event('app:conversation-updated'));
          },
          onError: (error) => {
            chatAbortRef.current = null;
            setVoiceState('error');
            setErrorMessage(getErrorMessage(error));
          },
        },
        abortController.signal,
      );
    } catch (error) {
      chatAbortRef.current = null;
      setVoiceState('error');
      setErrorMessage(getErrorMessage(error));
    }
  }, [autoSpeak, recognizedText, requireLogin]);

  useEffect(() => {
    if (!autoSpeak || voiceState !== 'speaking' || !assistantText.trim()) {
      return;
    }
    const abortController = new AbortController();
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
          setVoiceState('idle');
        },
        onError: (error) => {
          ttsAbortRef.current = null;
          setVoiceState('error');
          setErrorMessage(getErrorMessage(error));
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
    <div className="voice-assistant-root">
      <AnimatePresence>
        {open ? (
          <motion.section
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
                  onClick={() => setAutoSpeak((prev) => !prev)}
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
                  <Pause className="h-4 w-4" />
                  停止当前
                </button>
              ) : null}
            </div>
          </motion.section>
        ) : null}
      </AnimatePresence>

      <button
        type="button"
        className={`voice-assistant-fab ${voiceState === 'recording' ? 'is-recording' : ''}`}
        onClick={() => {
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
      return;
    }
    if (event.type === 'asr_ready') {
      realtimeReadyRef.current = true;
      return;
    }
    if (event.type === 'asr_partial' && event.text) {
      setRecognizedText(event.text);
      recognizedTextRef.current = event.text;
      return;
    }
    if (event.type === 'asr_final') {
      const text = event.text?.trim() ?? '';
      setRecognizedText(text);
      recognizedTextRef.current = text;
      const nextState = text ? 'ready' : 'error';
      voiceStateRef.current = nextState;
      setVoiceState(nextState);
      if (!text) {
        setErrorMessage('没有识别到文字，请重试');
      }
      expectedRealtimeCloseRef.current = true;
      realtimeSocketRef.current?.close();
      return;
    }
    if (event.type === 'cancelled') {
      currentTurnIdRef.current = event.turnId ?? '';
      realtimeReadyRef.current = true;
      setRecognizedText('');
      return;
    }
    if (event.type === 'error') {
      setVoiceState('error');
      setErrorMessage(event.message || '语音识别失败，请重试');
      expectedRealtimeCloseRef.current = true;
      realtimeSocketRef.current?.close();
    }
  }

  function stopSpeaking() {
    ttsAbortRef.current?.abort();
    ttsAbortRef.current = null;
    playbackContextRef.current?.close().catch(() => undefined);
    playbackContextRef.current = null;
    playbackTimeRef.current = 0;
  }

  async function playPcmBase64(base64: string, sampleRate: number) {
    const bytes = Uint8Array.from(atob(base64), (char) => char.charCodeAt(0));
    const samples = new Int16Array(bytes.buffer);
    const audioContext = playbackContextRef.current ?? new AudioContext({ sampleRate });
    playbackContextRef.current = audioContext;
    const buffer = audioContext.createBuffer(1, samples.length, sampleRate);
    const output = buffer.getChannelData(0);
    for (let index = 0; index < samples.length; index += 1) {
      output[index] = Math.max(-1, Math.min(1, samples[index] / 32768));
    }
    const source = audioContext.createBufferSource();
    source.buffer = buffer;
    source.connect(audioContext.destination);
    const startAt = Math.max(audioContext.currentTime, playbackTimeRef.current);
    source.start(startAt);
    playbackTimeRef.current = startAt + buffer.duration;
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
