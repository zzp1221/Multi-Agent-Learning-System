import { motion } from 'framer-motion';
import { BookOpenCheck, ClipboardList, FileText, FolderOpen, Network, Search, Sparkles } from 'lucide-react';
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
}

const suggestionChips = [
  { label: '帮我制定学习计划', prompt: '帮我制定一份适合我的学习计划', icon: ClipboardList },
  { label: '解答一道数学题', prompt: '请帮我解答一道数学题，并讲清楚思路', icon: Search },
  { label: '总结一篇文章', prompt: '请帮我总结这篇文章的核心观点', icon: FileText },
  { label: '推荐学习资料', prompt: '请根据我的目标推荐学习资料', icon: BookOpenCheck },
];

const featureCards = [
  {
    title: '智能问答',
    description: '快速解答各类学习问题',
    icon: FileText,
    tone: 'blue',
  },
  {
    title: '学习规划',
    description: '定制你的个性化学习路径',
    icon: ClipboardList,
    tone: 'violet',
  },
  {
    title: '知识图谱',
    description: '构建知识体系，理解更深刻',
    icon: Network,
    tone: 'mint',
  },
  {
    title: '错题本',
    description: '智能整理，精准攻克薄弱点',
    icon: FolderOpen,
    tone: 'orange',
  },
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
          <div className="smart-orbit-logo" aria-hidden="true">
            <span className="smart-orbit-ring" />
            <span className="smart-orbit-cube smart-orbit-cube-back" />
            <span className="smart-orbit-cube smart-orbit-cube-front">
              <Sparkles className="h-8 w-8 text-white" />
            </span>
          </div>
          <h1 className="qna-hero-title">
            你好，我是<span>智学引擎</span>
          </h1>
          <p className="qna-hero-subtitle">
            智能驱动的个性化学习助手，随时为你解答问题
          </p>
        </motion.div>
        <div className="qna-landing-composer">
          <InputPanel
            value={props.qnaInput}
            busy={props.qnaBusy}
            placeholder="请输入你的学习问题或需求，按回车发送，按住上档键再按回车换行"
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
          <span>你可以试试：</span>
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
        <div className="qna-feature-grid" aria-label="智学引擎能力">
          {featureCards.map((item) => (
            <div key={item.title} className={`qna-feature-card qna-feature-${item.tone}`}>
              <div>
                <h2>{item.title}</h2>
                <p>{item.description}</p>
              </div>
              <div className="qna-feature-visual" aria-hidden="true">
                <item.icon className="h-12 w-12" />
              </div>
            </div>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="qna-chat-shell">
      <ChatPanel messages={props.qnaMessages} />
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
