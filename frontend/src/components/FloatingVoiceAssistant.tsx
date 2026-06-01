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
import { voiceApi } from '../api/voice';
import { readConversationChunk } from '../pages/LearningStudioDemoPage.utils';

type VoiceState = 'idle' | 'recording' | 'transcribing' | 'ready' | 'chatting' | 'speaking' | 'error';

interface FloatingVoiceAssistantProps {
  isAuthenticated: boolean;
  openAuthModal: (tab?: 'login' | 'register', hint?: string) => void;
}

const TARGET_SAMPLE_RATE = 16000;
const MAX_RECORDING_MS = 60_000;

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
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const sourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const chunksRef = useRef<Int16Array[]>([]);
  const recordStartedAtRef = useRef(0);
  const recordingTimerRef = useRef<number | null>(null);
  const chatAbortRef = useRef<AbortController | null>(null);
  const ttsAbortRef = useRef<AbortController | null>(null);
  const playbackContextRef = useRef<AudioContext | null>(null);
  const playbackTimeRef = useRef(0);

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
    chunksRef.current = [];
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      const audioContext = new AudioContext();
      const source = audioContext.createMediaStreamSource(stream);
      const processor = audioContext.createScriptProcessor(4096, 1, 1);
      processor.onaudioprocess = (event) => {
        const input = event.inputBuffer.getChannelData(0);
        chunksRef.current.push(convertFloatToPcm16(input, audioContext.sampleRate, TARGET_SAMPLE_RATE));
      };
      source.connect(processor);
      processor.connect(audioContext.destination);
      mediaStreamRef.current = stream;
      audioContextRef.current = audioContext;
      processorRef.current = processor;
      sourceRef.current = source;
      recordStartedAtRef.current = Date.now();
      setRecordingMs(0);
      setVoiceState('recording');
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
    const pcm = mergePcmChunks(chunksRef.current);
    stopRecordingResources();
    if (pcm.byteLength === 0) {
      setVoiceState('error');
      setErrorMessage('没有录到有效语音');
      return;
    }
    setVoiceState('transcribing');
    try {
      const response = await voiceApi.transcribePcm(new Blob([pcm], { type: 'audio/pcm' }));
      setRecognizedText(response.text);
      setVoiceState(response.text.trim() ? 'ready' : 'error');
      if (!response.text.trim()) {
        setErrorMessage('没有识别到文字，请重试');
      }
    } catch (error) {
      setVoiceState('error');
      setErrorMessage(getErrorMessage(error));
    }
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
      stopRecordingResources();
    }
    chatAbortRef.current?.abort();
    stopSpeaking();
    setVoiceState('idle');
  };

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
              {recognizedText || voiceState === 'ready' ? (
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
                  disabled={voiceState === 'transcribing' || voiceState === 'chatting' || voiceState === 'speaking'}
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
    processorRef.current?.disconnect();
    sourceRef.current?.disconnect();
    audioContextRef.current?.close().catch(() => undefined);
    mediaStreamRef.current?.getTracks().forEach((track) => track.stop());
    processorRef.current = null;
    sourceRef.current = null;
    audioContextRef.current = null;
    mediaStreamRef.current = null;
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

function convertFloatToPcm16(input: Float32Array, inputSampleRate: number, targetSampleRate: number): Int16Array {
  const ratio = inputSampleRate / targetSampleRate;
  const outputLength = Math.floor(input.length / ratio);
  const output = new Int16Array(outputLength);
  for (let index = 0; index < outputLength; index += 1) {
    const sourceIndex = Math.floor(index * ratio);
    const value = Math.max(-1, Math.min(1, input[sourceIndex] ?? 0));
    output[index] = value < 0 ? value * 0x8000 : value * 0x7fff;
  }
  return output;
}

function mergePcmChunks(chunks: Int16Array[]): ArrayBuffer {
  const totalLength = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
  const merged = new Int16Array(totalLength);
  let offset = 0;
  chunks.forEach((chunk) => {
    merged.set(chunk, offset);
    offset += chunk.length;
  });
  return merged.buffer;
}
