import { motion } from 'framer-motion';
import {
  BookOpenCheck,
  ClipboardList,
  FileText,
  Search,
  Sparkles,
} from 'lucide-react';
import { ChatPanel, InputPanel } from './LearningStudioDemoPage.qna-components';
import type { ChatMessage, PendingChatImage } from './LearningStudioDemoPage.types';

interface QnaChatViewProps {
  hasStartedConversation: boolean;
  qnaInput: string;
  qnaBusy: boolean;
  qnaMessages: ChatMessage[];
  pendingImages?: PendingChatImage[];
  imageErrorMessage?: string;
  deepReasoningEnabled?: boolean;
  webSearchEnabled: boolean;
  onChange: (value: string) => void;
  onSend: () => void;
  onToggleDeepReasoning?: () => void;
  onToggleWebSearch: () => void;
  onPickImages?: (files: File[]) => void;
  onRemoveImage?: (id: string) => void;
  onConfirmSlideOutline?: (message: ChatMessage) => void;
  onRejectSlideOutline?: (message: ChatMessage) => void;
}

const suggestionChips = [
  { label: '解释概念', prompt: '请用清晰的结构解释这个概念，并给一个例子', icon: BookOpenCheck },
  { label: '解一道题', prompt: '请帮我解答这道题，并把关键步骤讲清楚', icon: Search },
  { label: '整理文章', prompt: '请提炼这篇文章的核心观点、论据和结论', icon: FileText },
  { label: '生成资源', prompt: '请根据我当前学习阶段生成一套学习资源，包括文档、PPT、思维导图、练习题、短视频和代码案例', icon: Sparkles },
  { label: '制定计划', prompt: '请帮我制定一份适合我的学习计划', icon: ClipboardList },
];

export default function QnaChatView(props: QnaChatViewProps) {
  if (!props.hasStartedConversation) {
    return (
      <div className="qna-landing-shell">
        <motion.div
          initial={{ opacity: 0, y: 18 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5 }}
          className="qna-hero-panel"
        >
          <h1 className="qna-hero-title">
            智学引擎
          </h1>
          <p className="qna-hero-subtitle">
            把问题讲清楚，把答案写明白。
          </p>
        </motion.div>
        <div className="qna-landing-composer">
          <InputPanel
            value={props.qnaInput}
            busy={props.qnaBusy}
            placeholder="输入学习问题，或描述你想解决的内容"
            pendingImages={props.pendingImages ?? []}
            errorMessage={props.imageErrorMessage}
            deepReasoningEnabled={props.deepReasoningEnabled}
            webSearchEnabled={props.webSearchEnabled}
            onChange={props.onChange}
            onSend={props.onSend}
            onToggleDeepReasoning={props.onToggleDeepReasoning}
            onToggleWebSearch={props.onToggleWebSearch}
            onPickImages={props.onPickImages ?? (() => undefined)}
            onRemoveImage={props.onRemoveImage ?? (() => undefined)}
            variant="landing"
          />
        </div>
        <div className="qna-suggestion-row">
          {suggestionChips.map((item) => (
            <button
              key={item.label}
              type="button"
              onClick={() => props.onChange(item.prompt)}
              className="qna-suggestion-chip"
            >
              <item.icon className="h-4 w-4" />
              {item.label}
            </button>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="qna-chat-shell">
      <ChatPanel
        busy={props.qnaBusy}
        messages={props.qnaMessages}
        onConfirmSlideOutline={props.onConfirmSlideOutline}
        onRejectSlideOutline={props.onRejectSlideOutline}
      />
      <InputPanel
        value={props.qnaInput}
        busy={props.qnaBusy}
        placeholder="向智学引擎提问"
        pendingImages={props.pendingImages ?? []}
        errorMessage={props.imageErrorMessage}
        deepReasoningEnabled={props.deepReasoningEnabled}
        webSearchEnabled={props.webSearchEnabled}
        onChange={props.onChange}
        onSend={props.onSend}
        onToggleDeepReasoning={props.onToggleDeepReasoning}
        onToggleWebSearch={props.onToggleWebSearch}
        onPickImages={props.onPickImages ?? (() => undefined)}
        onRemoveImage={props.onRemoveImage ?? (() => undefined)}
        variant="chat"
      />
    </div>
  );
}
