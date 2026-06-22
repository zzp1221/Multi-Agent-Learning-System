import { memo, useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type ClipboardEvent, type DragEvent } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { ArrowDown, Bot, BrainCircuit, ChevronDown, FileCode2, FileImage, FileText, Globe2, GraduationCap, Layers3, ListChecks, Paperclip, Presentation, Route, SendHorizontal, ShieldCheck, Square, Video, X, XCircle } from 'lucide-react';
import MarkdownRenderer from '../components/MarkdownRenderer';
import { normalizeCopyMarkdown } from '../utils/markdownSanitizer';
import type { AgentCollaborationTraceItem, ChatMessage, PendingChatImage } from './LearningStudioDemoPage.types';

interface ChatMessageBubbleProps {
  message: ChatMessage;
  isStreaming: boolean;
  onPreviewImage: (imageUrl: string) => void;
  onCopy: (message: ChatMessage) => void;
  copiedMessageId: string | null;
}

const ChatMessageBubble = memo(function ChatMessageBubble({
  message,
  isStreaming,
  onPreviewImage,
  onCopy,
  copiedMessageId,
}: ChatMessageBubbleProps) {
  const assistantIsPending = message.role === 'assistant' && !message.content.trim();

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25 }}
      className={`qna-message-row ${message.role === 'user' ? 'is-user' : 'is-assistant'}`}
    >
      <div className={`qna-message-wrap ${message.role === 'user' ? 'is-user' : 'is-assistant'}`}>
        {message.role === 'user' ? (
          <div className="qna-user-bubble">
            {message.imageUrls?.length ? (
              <div className="qna-message-image-grid">
                {message.imageUrls.map((imageUrl, index) => (
                  <button
                    key={`${message.id}-image-${index}`}
                    type="button"
                    className="qna-message-image-button"
                    onClick={() => onPreviewImage(imageUrl)}
                  >
                    <img src={imageUrl} alt={`上传图片 ${index + 1}`} className="h-24 w-full object-cover" />
                  </button>
                ))}
              </div>
            ) : null}
            {message.webSearchEnabled ? (
              <div className="qna-user-chip">
                <Globe2 className="h-3 w-3" />
                联网搜索
              </div>
            ) : null}
            {message.content ? <div>{message.content}</div> : <div className="text-white/80">[图片提问]</div>}
          </div>
        ) : (
          <div className={`qna-assistant-message ${assistantIsPending ? 'is-pending' : ''} ${isStreaming ? 'is-streaming' : ''}`}>
            <AgentCollaborationPanel
              content={message.reasoningContent ?? ''}
              reasoningState={message.reasoningState}
              traceItems={message.agentTraceItems ?? []}
              collaborationState={message.collaborationState}
              isStreaming={isStreaming}
            />
            {message.content ? (
              <MarkdownRenderer content={message.content} isStreaming={isStreaming} />
            ) : (
              <MarkdownRenderer content="" isStreaming={true} />
            )}
          </div>
        )}
        {message.content.trim() ? (
          <div className={`qna-message-actions ${message.role === 'user' ? 'is-user' : 'is-assistant'}`}>
            <button
              type="button"
              onClick={() => onCopy(message)}
              className="qna-message-action-button"
            >
              {copiedMessageId === message.id ? '已复制' : '复制'}
            </button>
          </div>
        ) : null}
      </div>
    </motion.div>
  );
});

const AgentCollaborationPanel = memo(function AgentCollaborationPanel({
  content,
  reasoningState,
  traceItems,
  collaborationState,
  isStreaming,
}: {
  content: string;
  reasoningState?: ChatMessage['reasoningState'];
  traceItems: AgentCollaborationTraceItem[];
  collaborationState?: ChatMessage['collaborationState'];
  isStreaming: boolean;
}) {
  const hasContent = Boolean(content.trim());
  const hasTrace = traceItems.length > 0;
  const [expanded, setExpanded] = useState(isStreaming || hasTrace);

  useEffect(() => {
    if (hasTrace || (isStreaming && hasContent)) {
      setExpanded(true);
      return;
    }
    if (!isStreaming && reasoningState && hasContent) {
      setExpanded(false);
    }
  }, [hasContent, hasTrace, isStreaming, reasoningState]);

  if (!hasContent && !hasTrace) {
    return null;
  }

  const hasPartialCompletion = traceItems.some((item) => item.status === 'PARTIAL_FAILED');
  const title = hasTrace
    ? collaborationState === 'stopped'
      ? '协作已中止'
      : isStreaming || collaborationState === 'streaming'
        ? '多 Agent 协作中'
        : '多 Agent 协作已完成'
    : reasoningState === 'stopped'
      ? '深度思考已停止'
      : isStreaming || reasoningState === 'streaming'
        ? '深度思考中'
        : '深度思考已完成';

  return (
    <div className={`qna-reasoning-panel ${expanded ? 'is-expanded' : 'is-collapsed'}`}>
      <button
        type="button"
        className="qna-reasoning-toggle"
        aria-expanded={expanded}
        onClick={() => setExpanded((prev) => !prev)}
      >
        {hasTrace ? <Route className="h-4 w-4" /> : <BrainCircuit className="h-4 w-4" />}
        <span>{title}</span>
        <ChevronDown className="qna-reasoning-chevron h-4 w-4" />
      </button>
      {expanded ? (
        <div className={`qna-reasoning-content ${hasTrace ? 'has-agent-trace' : ''}`}>
          {hasTrace ? <AgentTraceTimeline items={traceItems} collaborationState={collaborationState} /> : null}
          {hasTrace && !isStreaming && collaborationState === 'done' ? (
            <div className="qna-agent-trace-complete">
              {hasPartialCompletion ? '部分资源已完成生成' : '资源已完成生成'}
            </div>
          ) : null}
          {hasContent ? (
            <div className={hasTrace ? 'qna-reasoning-legacy' : undefined}>
              <MarkdownRenderer content={content} isStreaming={isStreaming} />
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
});

const AgentTraceTimeline = memo(function AgentTraceTimeline({
  items,
  collaborationState,
}: {
  items: AgentCollaborationTraceItem[];
  collaborationState?: ChatMessage['collaborationState'];
}) {
  return (
    <div className="qna-agent-trace-list">
      {items.map((item) => {
        const Icon = agentTraceIcon(item);
        const status = item.status;
        const displayStatus = collaborationState === 'done' && status === 'RUNNING' ? 'SUCCESS' : status;
        const showStatus = displayStatus && !(item.phase === 'failed' && displayStatus === 'FAILED');
        const displayPercent = collaborationState === 'done' && displayStatus === 'SUCCESS' ? 100 : item.percent;
        const phaseText = phaseLabel(item.phase);
        return (
          <div key={item.id} className={`qna-agent-trace-item is-${(displayStatus ?? 'RUNNING').toLowerCase()}`}>
            <div className="qna-agent-trace-avatar">
              <Icon className="h-3.5 w-3.5" />
            </div>
            <div className="qna-agent-trace-body">
              <div className="qna-agent-trace-head">
                <span className="qna-agent-trace-agent">{item.agentName}</span>
                <span className="qna-agent-trace-phase">{phaseText}</span>
                {showStatus ? <span className="qna-agent-trace-status">{statusLabel(displayStatus)}</span> : null}
              </div>
              <div className="qna-agent-trace-text">{item.text}</div>
              {typeof displayPercent === 'number' ? (
                <div
                  className="qna-agent-progress"
                  role="progressbar"
                  aria-label={`${item.agentName} ${phaseText}进度`}
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-valuenow={displayPercent}
                >
                  <span style={{ width: `${displayPercent}%` }} />
                </div>
              ) : null}
            </div>
          </div>
        );
      })}
    </div>
  );
});

function agentTraceIcon(item: AgentCollaborationTraceItem) {
  const key = `${item.agentName} ${item.artifactType ?? ''}`.toLowerCase();
  if (key.includes('tutor')) return GraduationCap;
  if (key.includes('retrieval')) return Globe2;
  if (key.includes('bundle')) return Layers3;
  if (key.includes('slide')) return Presentation;
  if (key.includes('document')) return FileText;
  if (key.includes('mind')) return Route;
  if (key.includes('code')) return FileCode2;
  if (key.includes('practice') || key.includes('quiz')) return ListChecks;
  if (key.includes('video')) return Video;
  if (key.includes('safety')) return ShieldCheck;
  return Bot;
}

function phaseLabel(phase: AgentCollaborationTraceItem['phase']): string {
  return {
    intent: '意图',
    rewrite: '改写',
    retrieve: '检索',
    select: '选择',
    generate: '生成',
    review: '复核',
    safety: '安全',
    publish: '发布',
    done: '完成',
    failed: '失败',
  }[phase];
}

function statusLabel(status: NonNullable<AgentCollaborationTraceItem['status']>): string {
  return {
    RUNNING: '进行中',
    SUCCESS: '完成',
    FAILED: '失败',
    PARTIAL_FAILED: '部分完成',
  }[status];
}

export const ChatPanel = memo(function ChatPanel({
  busy,
  messages,
}: {
  busy: boolean;
  messages: ChatMessage[];
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const contentRef = useRef<HTMLDivElement | null>(null);
  const autoFollowRef = useRef(true);
  const [copiedMessageId, setCopiedMessageId] = useState<string | null>(null);
  const [autoFollow, setAutoFollow] = useState(true);
  const [previewImage, setPreviewImage] = useState<string | null>(null);
  const previousMessageListKeyRef = useRef('');

  const lastMessage = messages[messages.length - 1];
  const isStreaming = Boolean(busy && lastMessage && lastMessage.role === 'assistant');
  const messageListKey = messages.map((message) => message.id).join('\u0001');
  const scrollToBottom = useCallback(() => {
    const container = containerRef.current;
    if (!container) return;
    container.scrollTop = container.scrollHeight;
    requestAnimationFrame(() => {
      container.scrollTop = container.scrollHeight;
      requestAnimationFrame(() => {
        container.scrollTop = container.scrollHeight;
      });
    });
  }, []);

  useEffect(() => {
    autoFollowRef.current = autoFollow;
  }, [autoFollow]);

  useLayoutEffect(() => {
    const previousMessageListKey = previousMessageListKeyRef.current;
    previousMessageListKeyRef.current = messageListKey;
    if (!messageListKey) {
      return;
    }
    const shouldForceFollow = !previousMessageListKey || lastMessage?.role === 'user';
    if (shouldForceFollow) {
      autoFollowRef.current = true;
      setAutoFollow(true);
      scrollToBottom();
      return;
    }
    if (autoFollowRef.current) {
      scrollToBottom();
    }
  }, [lastMessage?.role, messageListKey, scrollToBottom]);

  useEffect(() => {
    if (!autoFollow) return;
    scrollToBottom();
  }, [autoFollow, messages, scrollToBottom]);

  useEffect(() => {
    const content = contentRef.current;
    if (!content || typeof ResizeObserver === 'undefined') {
      return;
    }
    const observer = new ResizeObserver(() => {
      if (autoFollowRef.current) {
        scrollToBottom();
      }
    });
    observer.observe(content);
    return () => observer.disconnect();
  }, [scrollToBottom]);

  const handleScroll = () => {
    const container = containerRef.current;
    if (!container) {
      return;
    }
    const distanceToBottom = container.scrollHeight - container.scrollTop - container.clientHeight;
    const shouldFollow = distanceToBottom < 64;
    autoFollowRef.current = shouldFollow;
    setAutoFollow(shouldFollow);
  };

  const handleCopy = async (message: ChatMessage) => {
    try {
      await navigator.clipboard.writeText(normalizeCopyMarkdown(message.content));
      setCopiedMessageId(message.id);
      window.setTimeout(() => {
        setCopiedMessageId((prev) => (prev === message.id ? null : prev));
      }, 1200);
    } catch {
      // 忽略剪贴板错误
    }
  };

  return (
    <div className="relative min-h-0 flex-1">
      <div ref={containerRef} onScroll={handleScroll} className="qna-chat-scroll scrollbar-thin">
        <div ref={contentRef} className="qna-chat-content">
          <AnimatePresence>
            {messages.map((message) => (
              <ChatMessageBubble
                key={message.id}
                message={message}
                isStreaming={message.id === lastMessage?.id && isStreaming}
                onPreviewImage={setPreviewImage}
                onCopy={handleCopy}
                copiedMessageId={copiedMessageId}
              />
            ))}
          </AnimatePresence>
        </div>
      </div>
      {!autoFollow ? (
        <motion.button
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: 10 }}
          type="button"
          onClick={() => {
            const container = containerRef.current;
            if (!container) {
              return;
            }
            container.scrollTop = container.scrollHeight;
            setAutoFollow(true);
          }}
          className="qna-scroll-bottom-button"
        >
          <ArrowDown className="mr-1.5 inline h-3.5 w-3.5" />
          回到底部
        </motion.button>
      ) : null}
      {previewImage ? (
        <button
          type="button"
          onClick={() => setPreviewImage(null)}
          className="absolute inset-0 z-20 flex items-center justify-center bg-slate-950/80 p-4"
        >
          <img src={previewImage} alt="图片预览" className="max-h-[90%] max-w-[90%] rounded-2xl object-contain shadow-2xl" />
        </button>
      ) : null}
    </div>
  );
});

export function InputPanel(props: {
  value: string;
  busy: boolean;
  placeholder: string;
  pendingImages: PendingChatImage[];
  errorMessage?: string;
  onChange: (value: string) => void;
  onSend: () => void;
  onStop?: () => void;
  onPickImages: (files: File[]) => void;
  onRemoveImage: (id: string) => void;
  deepReasoningEnabled?: boolean;
  onToggleDeepReasoning?: () => void;
  webSearchEnabled: boolean;
  onToggleWebSearch: () => void;
  variant?: 'landing' | 'chat';
}) {
  const isLanding = props.variant === 'landing';
  const fileInputId = useId();
  const [isDragActive, setIsDragActive] = useState(false);

  const pickFiles = (files: FileList | null) => {
    if (!files || files.length === 0) {
      return;
    }
    props.onPickImages(Array.from(files));
  };

  const handleDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setIsDragActive(false);
    pickFiles(event.dataTransfer.files);
  };

  const handlePaste = (event: ClipboardEvent<HTMLTextAreaElement>) => {
    const imageFiles = Array.from(event.clipboardData.files).filter((file) => file.type.startsWith('image/'));
    if (!imageFiles.length) {
      return;
    }
    event.preventDefault();
    props.onPickImages(imageFiles);
  };

  return (
    <div className={`qna-input-frame ${isLanding ? 'qna-input-frame-landing' : 'qna-input-frame-chat'}`}>
      <div className={`qna-composer ${isLanding ? 'qna-composer-landing' : 'qna-composer-chat'}`}>
        <div
          className={`qna-composer-drop ${isDragActive ? 'is-drag-active' : ''}`}
          onDragOver={(event) => {
            event.preventDefault();
            setIsDragActive(true);
          }}
          onDragLeave={(event) => {
            event.preventDefault();
            setIsDragActive(false);
          }}
          onDrop={handleDrop}
        >
          {props.pendingImages.length ? (
            <div className="qna-pending-images">
              {props.pendingImages.map((image) => (
                <div key={image.id} className="group relative overflow-hidden rounded-2xl bg-slate-50 shadow-sm shadow-slate-200/70 dark:bg-slate-800 dark:shadow-none">
                  <img src={image.previewUrl} alt="待上传图片" className="h-24 w-24 object-cover" />
                  <button
                    type="button"
                    onClick={() => props.onRemoveImage(image.id)}
                    className="absolute right-2 top-2 rounded-full bg-slate-900/70 p-1 text-white opacity-0 transition-opacity group-hover:opacity-100"
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                  <div className="absolute inset-x-0 bottom-0 bg-slate-950/70 px-2 py-1 text-[11px] text-white">
                    {image.uploadStatus === 'failed'
                      ? '上传失败'
                      : image.uploadStatus === 'uploaded'
                        ? '上传完成'
                        : `上传中 ${image.uploadProgress}%`}
                  </div>
                </div>
              ))}
            </div>
          ) : null}
          <div className="qna-composer-main">
            <input
              id={fileInputId}
              type="file"
              accept="image/png,image/jpeg,image/webp"
              multiple
              disabled={props.busy}
              className="hidden"
              onChange={(event) => {
                pickFiles(event.target.files);
                event.currentTarget.value = '';
              }}
            />
            <textarea
              value={props.value}
              onChange={(event) => props.onChange(event.target.value)}
              onPaste={handlePaste}
              rows={isLanding ? 3 : 2}
              placeholder={props.placeholder}
              disabled={props.busy}
              className="qna-composer-textarea"
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !event.shiftKey) {
                  event.preventDefault();
                  props.onSend();
                }
              }}
            />
            {props.errorMessage ? (
              <div className="qna-composer-error">
                <XCircle className="h-3.5 w-3.5" />
                <span>{props.errorMessage}</span>
              </div>
            ) : null}
            <div className="qna-composer-toolbar">
              <div className="qna-composer-actions">
                <label
                  htmlFor={fileInputId}
                  aria-disabled={props.busy}
                  className="qna-icon-button"
                >
                  <Paperclip className="h-[18px] w-[18px]" />
                </label>
                <button
                  type="button"
                  onClick={props.onToggleDeepReasoning}
                  disabled={props.busy}
                  aria-pressed={Boolean(props.deepReasoningEnabled)}
                  title="开启后更适合复杂问题和多步骤解答"
                  className={`qna-mode-button ${props.deepReasoningEnabled ? 'is-active' : ''}`}
                >
                  <BrainCircuit className="h-4 w-4" />
                  深度思考
                </button>
                <button
                  type="button"
                  onClick={props.onToggleWebSearch}
                  disabled={props.busy}
                  aria-pressed={props.webSearchEnabled}
                  className={`qna-mode-button ${props.webSearchEnabled ? 'is-active' : ''}`}
                >
                  <Globe2 className="h-4 w-4" />
                  联网搜索
                </button>
                <span className="qna-upload-hint">
                  <FileImage className="h-3.5 w-3.5" />
                  支持图片
                </span>
              </div>
              {props.busy ? (
                <button
                  type="button"
                  onClick={props.onStop}
                  className="qna-stop-button"
                  title="停止生成"
                  aria-label="停止生成"
                >
                  <Square className="h-4 w-4" />
                  <span>停止</span>
                </button>
              ) : (
                <button
                  type="button"
                  onClick={() => props.onSend()}
                  disabled={!props.value.trim() && props.pendingImages.every((item) => !item.uploadedUrl)}
                  className="qna-send-button"
                  title="发送"
                  aria-label="发送"
                >
                  <SendHorizontal className="h-5 w-5" />
                </button>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
