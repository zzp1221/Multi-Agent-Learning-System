import { memo, useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type ClipboardEvent, type DragEvent } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { ArrowDown, BrainCircuit, FileImage, Globe2, LoaderCircle, Paperclip, SendHorizontal, Sparkles, X, XCircle } from 'lucide-react';
import MarkdownRenderer from '../components/MarkdownRenderer';
import { normalizeCopyText } from './LearningStudioDemoPage.utils';
import type { ChatMessage, PendingChatImage } from './LearningStudioDemoPage.types';

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
  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25 }}
      className={`flex ${message.role === 'user' ? 'justify-end' : 'justify-start'}`}
    >
      <div className={`max-w-[90%] md:max-w-[82%] ${message.role === 'user' ? '' : 'w-full md:w-auto'}`}>
        {message.role === 'user' ? (
          <div className="rounded-2xl rounded-br-md bg-primary-600 px-4 py-2.5 text-[15px] leading-7 text-white shadow-sm">
            {message.imageUrls?.length ? (
              <div className="mb-3 grid grid-cols-2 gap-2 sm:grid-cols-3">
                {message.imageUrls.map((imageUrl, index) => (
                  <button
                    key={`${message.id}-image-${index}`}
                    type="button"
                    className="overflow-hidden rounded-xl border border-white/20 bg-white/10"
                    onClick={() => onPreviewImage(imageUrl)}
                  >
                    <img src={imageUrl} alt={`上传图片 ${index + 1}`} className="h-24 w-full object-cover" />
                  </button>
                ))}
              </div>
            ) : null}
            {message.webSearchEnabled ? (
              <div className="mb-2 inline-flex items-center gap-1 rounded-full bg-white/15 px-2 py-1 text-xs font-medium text-white/90 ring-1 ring-white/20">
                <Globe2 className="h-3 w-3" />
                联网搜索
              </div>
            ) : null}
            {message.content ? <div>{message.content}</div> : <div className="text-white/80">[图片提问]</div>}
          </div>
        ) : (
          <div className="rounded-2xl rounded-bl-md border border-slate-200 bg-white px-4 py-3 text-slate-700 shadow-sm dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300">
            {message.content ? (
              <MarkdownRenderer content={message.content} isStreaming={isStreaming} />
            ) : (
              <MarkdownRenderer content="" isStreaming={true} />
            )}
          </div>
        )}
        {message.content ? (
          <div className={`mt-1.5 flex items-center gap-2 text-xs ${message.role === 'user' ? 'justify-end' : ''}`}>
            {message.role === 'assistant' ? (
              <span className="text-slate-400 dark:text-slate-500">
                <Sparkles className="inline h-3 w-3" /> 智学引擎
              </span>
            ) : null}
            <button
              type="button"
              onClick={() => onCopy(message)}
              className="rounded-md px-1.5 py-0.5 text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-600 dark:text-slate-500 dark:hover:bg-slate-800 dark:hover:text-slate-300"
            >
              {copiedMessageId === message.id ? '已复制' : '复制'}
            </button>
          </div>
        ) : null}
      </div>
    </motion.div>
  );
});

export const ChatPanel = memo(function ChatPanel({ messages }: { messages: ChatMessage[] }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const contentRef = useRef<HTMLDivElement | null>(null);
  const autoFollowRef = useRef(true);
  const [copiedMessageId, setCopiedMessageId] = useState<string | null>(null);
  const [autoFollow, setAutoFollow] = useState(true);
  const [previewImage, setPreviewImage] = useState<string | null>(null);

  const lastMessage = messages[messages.length - 1];
  const isStreaming = Boolean(lastMessage && lastMessage.role === 'assistant' && !lastMessage.content);
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
      <div ref={containerRef} onScroll={handleScroll} className="h-full overflow-y-auto px-2 py-4 scrollbar-thin md:px-8">
        <div ref={contentRef} className="space-y-6 md:space-y-8">
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
          className="absolute bottom-4 left-1/2 -translate-x-1/2 rounded-full border border-slate-200 bg-white px-4 py-2 text-xs font-medium text-slate-600 shadow-lg transition-all hover:shadow-md dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300"
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
                <div key={image.id} className="group relative overflow-hidden rounded-2xl border border-slate-200 bg-slate-50 dark:border-slate-700 dark:bg-slate-800">
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
                  title="开启后本轮对话会使用问题分析、分步推理、自我批判、最终回答的深度推理链"
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
