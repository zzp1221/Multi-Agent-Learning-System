import { memo, useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type ClipboardEvent, type DragEvent } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { ArrowDown, BrainCircuit, CheckCircle2, FileImage, Globe2, LoaderCircle, Paperclip, SendHorizontal, X, XCircle } from 'lucide-react';
import MarkdownRenderer from '../components/MarkdownRenderer';
import { normalizeCopyText } from './LearningStudioDemoPage.utils';
import type { ChatMessage, PendingChatImage } from './LearningStudioDemoPage.types';

interface ChatMessageBubbleProps {
  message: ChatMessage;
  isStreaming: boolean;
  onPreviewImage: (imageUrl: string) => void;
  onCopy: (message: ChatMessage) => void;
  copiedMessageId: string | null;
  onConfirmSlideOutline?: (message: ChatMessage) => void;
  onRejectSlideOutline?: (message: ChatMessage) => void;
}

const ChatMessageBubble = memo(function ChatMessageBubble({
  message,
  isStreaming,
  onPreviewImage,
  onCopy,
  copiedMessageId,
  onConfirmSlideOutline,
  onRejectSlideOutline,
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
          <div className={`qna-assistant-message ${assistantIsPending ? 'is-pending' : ''}`}>
            {message.content ? (
              <MarkdownRenderer content={message.content} isStreaming={isStreaming} />
            ) : (
              <MarkdownRenderer content="" isStreaming={true} />
            )}
            {message.slideConfirmation ? (
              <SlideOutlineConfirmationCard
                message={message}
                onConfirm={onConfirmSlideOutline}
                onReject={onRejectSlideOutline}
              />
            ) : null}
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

function SlideOutlineConfirmationCard(props: {
  message: ChatMessage;
  onConfirm?: (message: ChatMessage) => void;
  onReject?: (message: ChatMessage) => void;
}) {
  const confirmation = props.message.slideConfirmation;
  if (!confirmation) {
    return null;
  }
  const pending = confirmation.status === 'pending';
  return (
    <div className="mt-4 rounded-xl bg-blue-50/70 p-3 shadow-sm shadow-blue-100/70 dark:bg-primary-500/10 dark:shadow-none">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <div className="text-xs font-semibold text-primary-600 dark:text-primary-300">PPT 大纲待确认</div>
          <div className="mt-1 truncate text-sm font-bold text-slate-800 dark:text-slate-100">{confirmation.title}</div>
        </div>
        <span className="rounded-full bg-white/88 px-2.5 py-1 text-xs font-semibold text-slate-500 dark:bg-slate-900/80 dark:text-slate-300">
          {confirmation.status === 'confirmed' ? '已确认' : confirmation.status === 'rejected' ? '未生成' : '等待确认'}
        </span>
      </div>
      <div className="mt-3 max-h-64 overflow-auto rounded-lg bg-white/88 px-3 py-2 text-sm leading-6 text-slate-700 shadow-sm shadow-blue-100/60 dark:bg-slate-950/80 dark:text-slate-300 dark:shadow-none">
        <MarkdownRenderer content={confirmation.outline} />
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        <button
          type="button"
          disabled={!pending}
          onClick={() => props.onConfirm?.(props.message)}
          className="inline-flex h-9 items-center gap-2 rounded-lg bg-primary-600 px-3 text-sm font-semibold text-white transition hover:bg-primary-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          <CheckCircle2 className="h-4 w-4" />
          确认生成 PPT
        </button>
        <button
          type="button"
          disabled={!pending}
          onClick={() => props.onReject?.(props.message)}
          className="inline-flex h-9 items-center gap-2 rounded-lg bg-white/88 px-3 text-sm font-semibold text-slate-600 shadow-sm shadow-slate-200/60 transition hover:bg-rose-50 hover:text-rose-600 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-slate-900/82 dark:text-slate-300 dark:shadow-none dark:hover:bg-rose-500/10"
        >
          <XCircle className="h-4 w-4" />
          暂不生成
        </button>
      </div>
    </div>
  );
}

export const ChatPanel = memo(function ChatPanel({
  busy,
  messages,
  onConfirmSlideOutline,
  onRejectSlideOutline,
}: {
  busy: boolean;
  messages: ChatMessage[];
  onConfirmSlideOutline?: (message: ChatMessage) => void;
  onRejectSlideOutline?: (message: ChatMessage) => void;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const contentRef = useRef<HTMLDivElement | null>(null);
  const autoFollowRef = useRef(true);
  const [copiedMessageId, setCopiedMessageId] = useState<string | null>(null);
  const [autoFollow, setAutoFollow] = useState(true);
  const [previewImage, setPreviewImage] = useState<string | null>(null);

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
    autoFollowRef.current = true;
    setAutoFollow(true);
    scrollToBottom();
  }, [messageListKey, scrollToBottom]);

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
      await navigator.clipboard.writeText(normalizeCopyText(message.content));
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
                onConfirmSlideOutline={onConfirmSlideOutline}
                onRejectSlideOutline={onRejectSlideOutline}
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
              <button
                type="button"
                onClick={props.onSend}
                disabled={props.busy || (!props.value.trim() && props.pendingImages.every((item) => !item.uploadedUrl))}
                className="qna-send-button"
              >
                {props.busy ? <LoaderCircle className="h-5 w-5 animate-spin" /> : <SendHorizontal className="h-5 w-5" />}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
