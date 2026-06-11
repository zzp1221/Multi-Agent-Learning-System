import { motion } from 'framer-motion';
import { useNavigate } from 'react-router-dom';
import {
  ArrowUpRight,
  BookOpenCheck,
  ClipboardList,
  FileText,
  Layers3,
  NotebookPen,
  RefreshCw,
  Route,
  Search,
  Sparkles,
  UserRoundSearch,
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
  onStop?: () => void;
  onToggleDeepReasoning?: () => void;
  onToggleWebSearch: () => void;
  onPickImages?: (files: File[]) => void;
  onRemoveImage?: (id: string) => void;
}

const suggestionChips = [
  { label: '解释概念', prompt: '请用清晰的结构解释这个概念，并给一个例子', icon: BookOpenCheck },
  { label: '解一道题', prompt: '请帮我解答这道题，并把关键步骤讲清楚', icon: Search },
  { label: '整理文章', prompt: '请提炼这篇文章的核心观点、论据和结论', icon: FileText },
  { label: '生成资源', prompt: '请根据我当前学习阶段生成一套学习资源，包括文档、PPT、思维导图、练习题、短视频和代码案例', icon: Sparkles },
  { label: '制定计划', prompt: '请帮我制定一份适合我的学习计划', icon: ClipboardList },
];

const learningCycle = [
  {
    step: '01',
    title: '画像诊断',
    summary: '先明确基础、目标和学习偏好',
    icon: UserRoundSearch,
    route: '/profile',
  },
  {
    step: '02',
    title: '路径规划',
    summary: '生成阶段目标和检查点',
    icon: Route,
    route: '/engine',
  },
  {
    step: '03',
    title: '资源补齐',
    summary: '按知识点生成文档、课件、练习和视频',
    icon: Layers3,
    route: '/engine',
  },
  {
    step: '04',
    title: '练习检测',
    summary: '阶段测试后得到薄弱点',
    icon: BookOpenCheck,
    prompt: '请基于我当前阶段生成 10 道阶段检测题，并在批改后给出薄弱点',
  },
  {
    step: '05',
    title: '复盘沉淀',
    summary: '错题、笔记和下一轮路径联动',
    icon: RefreshCw,
    route: '/mistakes',
  },
  {
    step: '06',
    title: '笔记输出',
    summary: '把问答整理成可复习的学习笔记',
    icon: NotebookPen,
    route: '/notes',
  },
];

export default function QnaChatView(props: QnaChatViewProps) {
  const navigate = useNavigate();

  if (!props.hasStartedConversation) {
    return (
      <div className="qna-landing-shell">
        <div className="qna-landing-grid">
          <div className="qna-landing-primary">
            <motion.div
              initial={false}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.55, ease: [0.32, 0.72, 0, 1] }}
              className="qna-hero-panel"
            >
              <div className="qna-eyebrow">学习闭环</div>
              <h1 className="qna-hero-title">
                智学引擎
                <span>从一个问题走到一次掌握</span>
              </h1>
              <p className="qna-hero-subtitle">
                围绕学习全过程组织：先看画像与薄弱点，再给路径、资源、练习、错题复盘和笔记沉淀。
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
                onStop={props.onStop}
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

          <motion.section
            initial={false}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.7, delay: 0.08, ease: [0.32, 0.72, 0, 1] }}
            className="qna-cycle-shell"
            aria-label="学习闭环"
          >
            <div className="qna-cycle-core">
              <div className="qna-cycle-head">
                <span>闭环路径</span>
                <strong>六个学习环节</strong>
              </div>
              <div className="qna-cycle-track">
                {learningCycle.map((item) => (
                  <button
                    key={item.step}
                    type="button"
                    onClick={() => {
                      if (item.route) {
                        navigate(item.route);
                        return;
                      }
                      if (item.prompt) {
                        props.onChange(item.prompt);
                      }
                    }}
                    className="qna-cycle-step"
                  >
                    <span className="qna-cycle-step-index">{item.step}</span>
                    <span className="qna-cycle-step-icon">
                      <item.icon className="h-4 w-4" />
                    </span>
                    <span className="qna-cycle-step-copy">
                      <strong>{item.title}</strong>
                      <small>{item.summary}</small>
                    </span>
                    <ArrowUpRight className="qna-cycle-step-arrow h-4 w-4" />
                  </button>
                ))}
              </div>
            </div>
          </motion.section>
        </div>
      </div>
    );
  }

  return (
    <div className="qna-chat-shell">
      <ChatPanel
        busy={props.qnaBusy}
        messages={props.qnaMessages}
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
        onStop={props.onStop}
        onToggleDeepReasoning={props.onToggleDeepReasoning}
        onToggleWebSearch={props.onToggleWebSearch}
        onPickImages={props.onPickImages ?? (() => undefined)}
        onRemoveImage={props.onRemoveImage ?? (() => undefined)}
        variant="chat"
      />
    </div>
  );
}
