import { Suspense, lazy, useCallback, useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { useNavigate, useOutletContext } from 'react-router-dom';
import { BrainCircuit, CheckCircle2, FileText, GraduationCap, Send, Square, TrendingUp, X } from 'lucide-react';
import { conversationApi, type ConversationMessageItem } from '../api/conversation';
import { smartEngineApi } from '../api/smartEngine';
import { getErrorMessage } from '../api/request';
import type { LayoutOutletContext } from '../components/Layout';
import QnaChatView from './QnaChatView';
import {
  QNA_GREETING,
  defaultAssessmentDimensions,
  defaultResourceForm,
  serviceButtons,
  serviceTypeMap,
  type AssessmentForm,
  type ChatMessage,
  type CompletedResourceView,
  type EngineService,
  type EngineState,
  type EngineTaskSnapshot,
  type EngineTaskResultRecord,
  type InlineResourceView,
  type PathForm,
  type PendingChatImage,
  type PracticeQuestionBatch,
  type PushForm,
  type QnaState,
  type ResourceForm,
} from './LearningStudioDemoPage.types';
import {
  buildServiceParams,
  cleanupStreamSchedulers,
  readConversationChunk,
  runByApiTask,
  sanitizeConversationMessageContent,
  toUiTaskStatus,
} from './LearningStudioDemoPage.utils';

const ServiceDynamicForm = lazy(() =>
  import('./LearningStudioDemoPage.components').then((module) => ({ default: module.ServiceDynamicForm }))
);
const TaskResultPanel = lazy(() =>
  import('./LearningStudioDemoPage.components').then((module) => ({ default: module.TaskResultPanel }))
);

const serviceDescriptions: Record<EngineService, { summary: string; detail: string; accent: string }> = {
  resource: {
    summary: '基于当前任务输入生成学习资源',
    detail: '提交后展示真实生成结果、下载链接或内联内容。',
    accent: 'from-blue-500 to-sky-400',
  },
  path: {
    summary: '结合目标周期和当前进度规划路径',
    detail: '只展示任务返回的真实路径建议。',
    accent: 'from-indigo-500 to-blue-400',
  },
  push: {
    summary: '依据学习上下文推送资源',
    detail: '未返回推送结果前不展示预置推荐。',
    accent: 'from-cyan-500 to-emerald-400',
  },
  assessment: {
    summary: '围绕选定维度生成评估任务',
    detail: '练习与判题结果均来自任务接口。',
    accent: 'from-violet-500 to-blue-400',
  },
};

const ENGINE_TASK_STORAGE_KEY = 'learning_studio_engine_tasks';
const QNA_SNAPSHOT_STORAGE_KEY = 'learning_studio_qna_snapshot';
const QNA_CONVERSATION_CACHE_STORAGE_KEY = 'learning_studio_qna_cache';
const SELECTED_CONVERSATION_STORAGE_KEY = 'learning_studio_selected_conversation';
const ACTIVE_CONVERSATION_ID_STORAGE_KEY = 'learning_studio_active_conversation_id';
const DEFAULT_ENGINE_SERVICE: EngineService = 'push';

interface SelectedConversationSnapshot {
  conversationId: string;
  title?: string;
  lastMessagePreview?: string;
}

function mapConversationHistory(history: ConversationMessageItem[]): ChatMessage[] {
  const messages: ChatMessage[] = history
    .map((item, index) => ({
      id: item.messageId || `history-${index}`,
      role: (item.role === 'user' ? 'user' : 'assistant') as ChatMessage['role'],
      content: item.role === 'user'
        ? item.content?.trim() ?? ''
        : sanitizeConversationMessageContent(item.content ?? ''),
    }))
    .filter((item) => item.content);

  return messages.length > 0
    ? messages
    : [{ id: 'qna-greeting', role: 'assistant', content: QNA_GREETING }];
}

interface PersistedEngineTaskSnapshot {
  selectedService: EngineService | null;
  conversationId: string;
  snapshots: Record<EngineService, EngineTaskSnapshot>;
}

interface PersistedQnaSnapshot {
  conversationId: string;
  qnaInput: string;
  qnaState: QnaState;
  qnaMessages: ChatMessage[];
}

interface PersistedConversationViewSnapshot {
  qnaInput: string;
  qnaMessages: ChatMessage[];
  qnaState?: QnaState;
}

type QnaDrafts = Record<string, string>;
type PersistedQnaConversationCache = Record<string, PersistedConversationViewSnapshot>;

function conversationCacheKey(conversationId: string): string {
  const normalized = conversationId.trim();
  return normalized || '__new__';
}

function pickPreferredConversationMessages(
  cachedMessages: ChatMessage[] | undefined,
  fetchedMessages: ChatMessage[],
): ChatMessage[] {
  if (!cachedMessages || cachedMessages.length === 0) {
    return fetchedMessages;
  }
  const cachedTextLength = cachedMessages.reduce((sum, item) => sum + item.content.length, 0);
  const fetchedTextLength = fetchedMessages.reduce((sum, item) => sum + item.content.length, 0);
  if (cachedMessages.length > fetchedMessages.length || cachedTextLength > fetchedTextLength) {
    return cachedMessages;
  }
  return fetchedMessages;
}

function isProcessingOnlyAssistantContent(content: string): boolean {
  const lines = content
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean);
  return lines.length > 0 && lines.every((line) => line.startsWith('[处理中]'));
}

function hasPendingAssistantResponse(messages?: ChatMessage[]): boolean {
  const lastMessage = messages?.[messages.length - 1];
  if (!lastMessage || lastMessage.role !== 'assistant') {
    return false;
  }
  const content = lastMessage.content.trim();
  return !content || isProcessingOnlyAssistantContent(content);
}

function hasResolvedAssistantResponse(messages: ChatMessage[]): boolean {
  const lastMessage = messages[messages.length - 1];
  return Boolean(
    lastMessage
      && lastMessage.role === 'assistant'
      && lastMessage.content.trim()
      && !isProcessingOnlyAssistantContent(lastMessage.content),
  );
}

function EngineSectionHeader(props: {
  icon: ReactNode;
  title: string;
  subtitle: string;
}) {
  return (
    <div className="flex items-start gap-3">
      <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl bg-blue-50 text-primary-600 ring-1 ring-blue-100 dark:bg-primary-500/10 dark:text-primary-300 dark:ring-primary-500/20">
        {props.icon}
      </div>
      <div className="min-w-0">
        <h2 className="text-lg font-semibold text-slate-900 dark:text-white">{props.title}</h2>
        <p className="mt-1 text-sm leading-6 text-slate-500 dark:text-slate-400">{props.subtitle}</p>
      </div>
    </div>
  );
}

function ServiceHeroVisual() {
  return (
    <div className="relative hidden min-h-[420px] overflow-hidden lg:block">
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_52%_43%,rgba(59,130,246,0.24),transparent_32%),radial-gradient(circle_at_35%_22%,rgba(125,211,252,0.24),transparent_17%)]" />
      <div className="absolute left-1/2 top-[61%] h-20 w-[330px] -translate-x-1/2 rounded-[50%] border border-blue-200/80 bg-blue-100/42 shadow-[0_24px_56px_rgba(61,116,239,0.2)]" />
      <div className="absolute left-1/2 top-[58%] h-12 w-[260px] -translate-x-1/2 rounded-[50%] border border-cyan-100/80 bg-white/48 shadow-[0_18px_42px_rgba(14,165,233,0.15)]" />
      <div className="absolute left-1/2 top-[45%] h-48 w-48 -translate-x-1/2 -translate-y-1/2 rounded-[34px] border border-blue-200/80 bg-white/35 shadow-[0_36px_90px_rgba(64,111,214,0.18)] backdrop-blur-md" />
      <div className="absolute left-1/2 top-[42%] flex h-[136px] w-[136px] -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-[30px] border border-blue-200/80 bg-gradient-to-br from-blue-400 via-blue-600 to-cyan-400 text-5xl font-black text-white shadow-[0_28px_70px_rgba(37,99,235,0.36)]">
        AI
      </div>
      <div className="absolute left-[17%] top-[30%] h-3 w-3 rounded-full bg-cyan-300 shadow-[0_0_22px_rgba(34,211,238,0.9)]" />
      <div className="absolute right-[20%] top-[34%] h-3 w-3 rounded-full bg-blue-400 shadow-[0_0_22px_rgba(59,130,246,0.75)]" />
      <div className="absolute left-[34%] top-[18%] h-2 w-2 rounded-full bg-emerald-300 shadow-[0_0_18px_rgba(52,211,153,0.75)]" />
      <div className="absolute right-[23%] bottom-[25%] h-2.5 w-2.5 rounded-full bg-cyan-300 shadow-[0_0_18px_rgba(34,211,238,0.8)]" />
      <div className="absolute left-[19%] top-[31%] h-px w-[62%] rotate-[28deg] bg-gradient-to-r from-transparent via-blue-300/80 to-transparent" />
      <div className="absolute left-[23%] top-[57%] h-px w-[58%] -rotate-[20deg] bg-gradient-to-r from-transparent via-cyan-300/70 to-transparent" />
      <div className="absolute left-[25%] top-[22%] h-64 w-64 rounded-full border border-blue-200/50" />
      <div className="absolute left-[22%] top-[28%] h-52 w-72 rotate-12 rounded-[50%] border border-cyan-200/55" />
      <span className="absolute right-9 top-20 rounded-xl bg-blue-50/90 px-3 py-1.5 text-sm font-semibold text-primary-600 shadow-sm shadow-blue-100/80 ring-1 ring-blue-100">
        智能分析
      </span>
      <span className="absolute left-14 top-36 rounded-xl bg-cyan-50/90 px-3 py-1.5 text-sm font-semibold text-cyan-600 shadow-sm shadow-cyan-100/80 ring-1 ring-cyan-100">
        精准推荐
      </span>
      <span className="absolute bottom-28 right-5 rounded-xl bg-emerald-50/90 px-3 py-1.5 text-sm font-semibold text-emerald-600 shadow-sm shadow-emerald-100/80 ring-1 ring-emerald-100">
        学习进化
      </span>
    </div>
  );
}

function LearningEffectPreview(props: {
  selectedServiceLabel: string;
  taskId: string;
  taskProgress: number;
  taskStatus: string;
  resultLineCount: number;
  downloadCount: number;
}) {
  const hasTask = Boolean(props.taskId);
  const progressLabel = hasTask ? `${Math.round(props.taskProgress)}%` : '待提交';
  const linePercent = Math.min(100, props.resultLineCount * 12);
  const assetPercent = Math.min(100, props.downloadCount * 25);

  return (
    <section className="h-full rounded-[22px] border border-blue-100/80 bg-white/88 p-4 shadow-sm shadow-blue-100/50 dark:border-slate-800 dark:bg-slate-900/80 sm:rounded-[24px] sm:p-6">
      <div className="flex items-start gap-3 sm:items-center">
        <div className="flex h-10 w-10 items-center justify-center rounded-2xl bg-blue-50 text-primary-600 ring-1 ring-blue-100 dark:bg-primary-500/10 dark:text-primary-300 dark:ring-primary-500/20">
          <TrendingUp className="h-4 w-4" />
        </div>
        <div>
          <h2 className="text-lg font-semibold text-slate-900 dark:text-white">学习效果预览</h2>
          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">只展示真实任务状态，不展示预测分假数据。</p>
        </div>
      </div>

      {hasTask ? (
        <div className="mt-6 grid gap-5 md:grid-cols-[160px_minmax(0,1fr)] md:items-center sm:mt-8 sm:gap-6">
          <div className="mx-auto flex h-32 w-32 items-center justify-center rounded-full bg-[conic-gradient(#3b82f6_var(--progress),#e8eef7_0)] p-3 sm:h-36 sm:w-36" style={{ '--progress': `${Math.max(1, Math.min(100, props.taskProgress))}%` } as CSSProperties}>
            <div className="flex h-full w-full flex-col items-center justify-center rounded-full bg-white text-center shadow-inner dark:bg-slate-950">
              <span className="text-2xl font-bold text-primary-600 dark:text-primary-300">{progressLabel}</span>
              <span className="mt-1 text-xs text-slate-400">任务进度</span>
            </div>
          </div>

          <div className="space-y-5">
            <PreviewBar label="任务进度" value={`${Math.round(props.taskProgress)}%`} percent={props.taskProgress} color="bg-primary-500" />
            <PreviewBar label="结果片段" value={`${props.resultLineCount}条`} percent={linePercent} color="bg-cyan-500" />
            <PreviewBar label="资源产物" value={`${props.downloadCount}个`} percent={assetPercent} color="bg-violet-500" />
          </div>
        </div>
      ) : (
        <div className="mt-6 grid gap-5 md:grid-cols-[160px_minmax(0,1fr)] md:items-center sm:mt-8 sm:gap-6">
          <div className="mx-auto flex h-32 w-32 items-center justify-center rounded-full border border-dashed border-blue-200 bg-blue-50/60 text-center dark:border-slate-700 dark:bg-slate-950/40 sm:h-36 sm:w-36">
            <div>
              <div className="text-xl font-bold text-primary-600 dark:text-primary-300">待提交</div>
              <div className="mt-1 text-xs text-slate-400">暂无真实任务</div>
            </div>
          </div>
          <div className="rounded-2xl border border-dashed border-blue-100 bg-slate-50/70 px-4 py-5 text-sm leading-7 text-slate-500 dark:border-slate-700 dark:bg-slate-950/40 dark:text-slate-400 sm:px-5 sm:py-6">
            提交任务后，这里只显示后端任务返回的进度、结果片段和资源产物数量。
          </div>
        </div>
      )}

      <div className="mt-6 rounded-2xl border border-blue-100 bg-slate-50/60 p-4 dark:border-slate-800 dark:bg-slate-950/40 sm:mt-8">
        <div className="text-sm font-semibold text-slate-700 dark:text-slate-200">学习效果趋势预测</div>
        <div className="mt-3 flex min-h-24 items-center justify-center rounded-xl border border-dashed border-blue-100 bg-white/70 px-4 py-5 text-center text-sm leading-6 text-slate-500 dark:border-slate-700 dark:bg-slate-900/60 dark:text-slate-400 sm:h-28 sm:py-0">
          当前没有真实预测接口，已隐藏预测曲线和固定百分比。
        </div>
        <div className="mt-3 text-xs text-slate-400">
          当前服务：{props.selectedServiceLabel || '未选择'}{hasTask ? ` · ${props.taskStatus}` : ''}
        </div>
      </div>
    </section>
  );
}

function PreviewBar(props: {
  label: string;
  value: string;
  percent: number;
  color: string;
}) {
  return (
    <div>
      <div className="mb-2 flex items-center justify-between text-sm">
        <span className="font-medium text-slate-600 dark:text-slate-300">{props.label}</span>
        <span className="font-semibold text-slate-700 dark:text-slate-200">{props.value}</span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-800">
        <div className={`h-full rounded-full ${props.color} transition-[width] duration-300`} style={{ width: `${Math.max(0, Math.min(100, props.percent))}%` }} />
      </div>
    </div>
  );
}

function AssistantActionBar(props: {
  selectedServiceLabel: string;
  disabled: boolean;
  canStop: boolean;
  busy: boolean;
  status: string;
  onSubmit: () => void;
  onStop: () => void;
}) {
  return (
    <section className="rounded-[24px] border border-blue-100/80 bg-white/90 p-4 shadow-sm shadow-blue-100/50 dark:border-slate-800 dark:bg-slate-900/80 sm:p-5">
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(260px,0.75fr)_180px] lg:items-center">
        <div className="flex items-start gap-3 sm:items-center sm:gap-4">
          <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-blue-50 ring-1 ring-blue-100 sm:h-14 sm:w-14">
            <div className="flex h-9 w-9 items-center justify-center rounded-full bg-gradient-to-br from-blue-500 to-cyan-400 text-white shadow-md shadow-blue-300/50 sm:h-10 sm:w-10">
              <BrainCircuit className="h-5 w-5" />
            </div>
          </div>
          <div className="min-w-0">
            <div className="text-base font-semibold text-slate-900 dark:text-white">智学助手</div>
            <p className="mt-1 text-sm leading-6 text-slate-500 dark:text-slate-400">
              {props.busy ? props.status : props.selectedServiceLabel ? `已选择 ${props.selectedServiceLabel}，提交后开始执行真实服务任务。` : '请选择一项服务后提交任务。'}
            </p>
          </div>
        </div>

        <button
          type="button"
          onClick={props.onSubmit}
          disabled={props.disabled}
          className="inline-flex h-14 items-center justify-center gap-3 rounded-2xl bg-gradient-to-r from-blue-600 to-primary-500 px-5 text-base font-semibold text-white shadow-lg shadow-blue-500/24 transition-all hover:-translate-y-0.5 hover:shadow-xl hover:shadow-blue-500/28 disabled:cursor-not-allowed disabled:opacity-45 disabled:shadow-none disabled:hover:translate-y-0 sm:h-16 sm:px-6 sm:text-lg"
        >
          <Send className="h-6 w-6" />
          {props.busy ? '提交中...' : '提交任务'}
        </button>

        <button
          type="button"
          onClick={props.onStop}
          disabled={!props.canStop}
          className="inline-flex h-12 items-center justify-center gap-2 rounded-2xl border border-blue-100 bg-white px-5 text-sm font-semibold text-slate-600 shadow-sm shadow-blue-100/60 transition-all hover:border-primary-200 hover:text-primary-600 disabled:cursor-not-allowed disabled:opacity-45 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-300 sm:h-14"
        >
          <Square className="h-4 w-4" />
          停止任务
        </button>
      </div>
    </section>
  );
}

function createEmptyEngineTaskSnapshot(baseState: EngineState = 'ENGINE_IDLE'): EngineTaskSnapshot {
  return {
    engineState: baseState,
    taskId: '',
    taskProgress: 0,
    taskStatus: '未提交',
    taskSummary: '',
    serviceResultLines: [],
    downloadLinks: [],
    videoResult: null,
    inlineResource: null,
    inlineResources: [],
    practiceBatch: null,
    completedResources: [],
    judgeResult: null,
    resultHistory: [],
    selectedResultTaskId: '',
  };
}

function createInitialEngineSnapshots(): Record<EngineService, EngineTaskSnapshot> {
  return {
    resource: createEmptyEngineTaskSnapshot(),
    path: createEmptyEngineTaskSnapshot(),
    push: createEmptyEngineTaskSnapshot(),
    assessment: createEmptyEngineTaskSnapshot(),
  };
}

function hasLockedTask(snapshot: EngineTaskSnapshot): boolean {
  return snapshot.engineState === 'ENGINE_SUBMITTING' || snapshot.engineState === 'ENGINE_RUNNING';
}

function dedupeResultLines(lines: string[]): string[] {
  const seen = new Set<string>();
  const normalized: string[] = [];
  for (const raw of lines) {
    const line = String(raw).trim();
    if (!line || seen.has(line)) {
      continue;
    }
    seen.add(line);
    normalized.push(line);
  }
  return normalized;
}

function getInlineResourcesFromSnapshot(snapshot: Partial<EngineTaskSnapshot>): InlineResourceView[] {
  const resources = Array.isArray(snapshot.inlineResources) ? snapshot.inlineResources.filter(Boolean) : [];
  if (snapshot.inlineResource && !resources.some((item) => item.kind === snapshot.inlineResource?.kind && item.title === snapshot.inlineResource?.title)) {
    resources.push(snapshot.inlineResource);
  }
  return resources;
}

function createCompletedResourcesFromSnapshot(snapshot: Partial<EngineTaskSnapshot>): CompletedResourceView[] {
  if (Array.isArray(snapshot.completedResources) && snapshot.completedResources.length) {
    return snapshot.completedResources.filter(Boolean);
  }
  const completedResources: CompletedResourceView[] = getInlineResourcesFromSnapshot(snapshot).map((resource) => ({
    kind: 'inline',
    key: `inline:${resource.kind}:${resource.title}`,
    resource,
  }));
  if (snapshot.practiceBatch) {
    completedResources.push({
      kind: 'question_batch',
      key: `question_batch:${snapshot.practiceBatch.title}:${snapshot.practiceBatch.topic}`,
      batch: snapshot.practiceBatch,
    });
  }
  return completedResources;
}

function normalizeTaskResultRecord(record: Partial<EngineTaskResultRecord>): EngineTaskResultRecord | null {
  if (!record.taskId) {
    return null;
  }
  const now = Date.now();
  return {
    taskId: record.taskId,
    title: record.title || `任务 ${record.taskId.slice(0, 8)}`,
    taskStatus: record.taskStatus || '任务结果',
    engineState: record.engineState || 'ENGINE_COMPLETED',
    taskSummary: record.taskSummary || '',
    serviceResultLines: Array.isArray(record.serviceResultLines) ? record.serviceResultLines : [],
    downloadLinks: Array.isArray(record.downloadLinks) ? record.downloadLinks : [],
    videoResult: record.videoResult ?? null,
    inlineResources: Array.isArray(record.inlineResources) ? record.inlineResources : [],
    practiceBatch: record.practiceBatch ?? null,
    completedResources: createCompletedResourcesFromSnapshot(record),
    judgeResult: record.judgeResult ?? null,
    createdAt: typeof record.createdAt === 'number' ? record.createdAt : now,
    updatedAt: typeof record.updatedAt === 'number' ? record.updatedAt : now,
  };
}

function createTaskResultRecord(
  taskId: string,
  snapshot: EngineTaskSnapshot,
  overrides: Partial<EngineTaskResultRecord> = {},
): EngineTaskResultRecord {
  const now = Date.now();
  return {
    taskId,
    title: overrides.title || snapshot.taskSummary || snapshot.taskStatus || `任务 ${taskId.slice(0, 8)}`,
    taskStatus: overrides.taskStatus ?? snapshot.taskStatus,
    engineState: overrides.engineState ?? snapshot.engineState,
    taskSummary: overrides.taskSummary ?? snapshot.taskSummary,
    serviceResultLines: overrides.serviceResultLines ?? snapshot.serviceResultLines,
    downloadLinks: overrides.downloadLinks ?? snapshot.downloadLinks,
    videoResult: overrides.videoResult ?? snapshot.videoResult,
    inlineResources: overrides.inlineResources ?? getInlineResourcesFromSnapshot(snapshot),
    practiceBatch: overrides.practiceBatch ?? snapshot.practiceBatch,
    completedResources: overrides.completedResources ?? createCompletedResourcesFromSnapshot(snapshot),
    judgeResult: overrides.judgeResult ?? snapshot.judgeResult,
    createdAt: overrides.createdAt ?? now,
    updatedAt: overrides.updatedAt ?? now,
  };
}

function upsertTaskResultRecord(
  history: EngineTaskResultRecord[],
  taskId: string,
  snapshot: EngineTaskSnapshot,
  overrides: Partial<EngineTaskResultRecord> = {},
): EngineTaskResultRecord[] {
  if (!taskId) {
    return history;
  }
  const index = history.findIndex((item) => item.taskId === taskId);
  if (index < 0) {
    return [createTaskResultRecord(taskId, snapshot, overrides), ...history].slice(0, 8);
  }
  const current = history[index];
  const next: EngineTaskResultRecord = {
    ...current,
    ...overrides,
    taskId,
    title: overrides.title || current.title || snapshot.taskSummary || snapshot.taskStatus || `任务 ${taskId.slice(0, 8)}`,
    taskStatus: overrides.taskStatus ?? snapshot.taskStatus,
    engineState: overrides.engineState ?? snapshot.engineState,
    taskSummary: overrides.taskSummary ?? snapshot.taskSummary,
    serviceResultLines: overrides.serviceResultLines ?? snapshot.serviceResultLines,
    downloadLinks: overrides.downloadLinks ?? snapshot.downloadLinks,
    videoResult: overrides.videoResult ?? snapshot.videoResult,
    inlineResources: overrides.inlineResources ?? getInlineResourcesFromSnapshot(snapshot),
    practiceBatch: overrides.practiceBatch ?? snapshot.practiceBatch,
    completedResources: overrides.completedResources ?? createCompletedResourcesFromSnapshot(snapshot),
    judgeResult: overrides.judgeResult ?? snapshot.judgeResult,
    createdAt: current.createdAt,
    updatedAt: Date.now(),
  };
  return [next, ...history.slice(0, index), ...history.slice(index + 1)].slice(0, 8);
}

function syncSnapshotResultRecord(snapshot: EngineTaskSnapshot): EngineTaskSnapshot {
  if (!snapshot.taskId) {
    return snapshot;
  }
  return {
    ...snapshot,
    resultHistory: upsertTaskResultRecord(snapshot.resultHistory, snapshot.taskId, snapshot),
    selectedResultTaskId: snapshot.selectedResultTaskId || snapshot.taskId,
  };
}

function sanitizeEngineSnapshot(snapshot: EngineTaskSnapshot): EngineTaskSnapshot {
  const normalized: EngineTaskSnapshot = {
    ...snapshot,
    serviceResultLines: dedupeResultLines(snapshot.serviceResultLines),
    inlineResources: getInlineResourcesFromSnapshot(snapshot),
    completedResources: createCompletedResourcesFromSnapshot(snapshot),
    resultHistory: Array.isArray(snapshot.resultHistory)
      ? snapshot.resultHistory.map(normalizeTaskResultRecord).filter((item): item is EngineTaskResultRecord => Boolean(item))
      : [],
    selectedResultTaskId: snapshot.selectedResultTaskId || snapshot.taskId || '',
  };
  if (normalized.engineState === 'ENGINE_SUBMITTING' && !normalized.taskId) {
    return createEmptyEngineTaskSnapshot('ENGINE_FORM_EDITING');
  }
  if (normalized.taskStatus === '任务已取消') {
    normalized.engineState = 'ENGINE_FAILED';
  }
  if (normalized.taskStatus === '任务完成') {
    normalized.engineState = 'ENGINE_COMPLETED';
  }
  return syncSnapshotResultRecord(normalized);
}

function buildPersistedEngineSnapshots(
  selectedService: EngineService | null,
  snapshots: Record<EngineService, EngineTaskSnapshot>,
): Record<EngineService, EngineTaskSnapshot> {
  const next = createInitialEngineSnapshots();
  (Object.entries(snapshots) as Array<[EngineService, EngineTaskSnapshot]>).forEach(([service, snapshot]) => {
    const normalized = sanitizeEngineSnapshot(snapshot);
    const shouldPersist = service === selectedService || hasLockedTask(normalized);
    next[service] = shouldPersist ? normalized : createEmptyEngineTaskSnapshot();
  });
  return next;
}

function normalizeRestoredQnaMessages(snapshot: PersistedQnaSnapshot): ChatMessage[] {
  if (!Array.isArray(snapshot.qnaMessages) || snapshot.qnaMessages.length === 0) {
    return [{ id: 'qna-greeting', role: 'assistant', content: QNA_GREETING }];
  }
  if (snapshot.qnaState !== 'QNA_STREAMING') {
    return snapshot.qnaMessages;
  }

  const normalized = [...snapshot.qnaMessages];
  const lastAssistantIndex = [...normalized]
    .map((item, index) => ({ item, index }))
    .reverse()
    .find(({ item }) => item.role === 'assistant' && !item.content.trim())?.index;

  if (lastAssistantIndex === undefined) {
    return normalized;
  }

  normalized[lastAssistantIndex] = {
    ...normalized[lastAssistantIndex],
    content: '上一条回复未完整加载，你可以继续提问，或重新发送上一条问题。',
  };
  return normalized;
}

function buildConversationSyncSignature(messages: ChatMessage[]): string {
  return messages.map((item) => `${item.role}:${item.content}`).join('\u0001');
}

export default function LearningStudioDemoPage({ mode }: { mode: 'qna' | 'engine' }) {
  const { isAuthenticated, openAuthModal } = useOutletContext<LayoutOutletContext>();
  const navigate = useNavigate();
  const pendingActionRef = useRef<null | (() => void)>(null);
  const qnaStreamControllersRef = useRef<Record<string, AbortController>>({});
  const taskMonitorRefsRef = useRef<Record<EngineService, {
    taskStreamAbortRef: { current: AbortController | null };
    streamQueueRef: { current: string[] };
    streamFlushTimerRef: { current: number | null };
    streamRafRef: { current: number | null };
  }>>({
    resource: {
      taskStreamAbortRef: { current: null },
      streamQueueRef: { current: [] },
      streamFlushTimerRef: { current: null },
      streamRafRef: { current: null },
    },
    path: {
      taskStreamAbortRef: { current: null },
      streamQueueRef: { current: [] },
      streamFlushTimerRef: { current: null },
      streamRafRef: { current: null },
    },
    push: {
      taskStreamAbortRef: { current: null },
      streamQueueRef: { current: [] },
      streamFlushTimerRef: { current: null },
      streamRafRef: { current: null },
    },
    assessment: {
      taskStreamAbortRef: { current: null },
      streamQueueRef: { current: [] },
      streamFlushTimerRef: { current: null },
      streamRafRef: { current: null },
    },
  });
  const activeTaskMonitorsRef = useRef<Partial<Record<EngineService, string>>>({});
  const qnaMessagesRef = useRef<ChatMessage[]>([{ id: 'qna-greeting', role: 'assistant', content: QNA_GREETING }]);
  const qnaInputRef = useRef('');
  const qnaStateRef = useRef<QnaState>('QNA_IDLE');
  const conversationIdRef = useRef('');
  const mountedRef = useRef(true);
  const engineSnapshotHydratedRef = useRef(false);
  const qnaSnapshotHydratedRef = useRef(false);
  const loadingConversationIdRef = useRef('');
  const qnaDraftsRef = useRef<QnaDrafts>({});
  const qnaConversationCacheRef = useRef<PersistedQnaConversationCache>({});
  const qnaStreamTokensRef = useRef<Record<string, string>>({});
  const qnaRequestVersionRef = useRef(0);
  const engineSubmitVersionRef = useRef(0);
  const previousModeRef = useRef(mode);

  const [qnaState, setQnaState] = useState<QnaState>('QNA_IDLE');
  const [qnaMessages, setQnaMessages] = useState<ChatMessage[]>([{ id: 'qna-greeting', role: 'assistant', content: QNA_GREETING }]);
  const [qnaInput, setQnaInput] = useState('');
  const [pendingQnaImages, setPendingQnaImages] = useState<PendingChatImage[]>([]);
  const [qnaImageError, setQnaImageError] = useState('');
  const [qnaWebSearchEnabled, setQnaWebSearchEnabled] = useState(false);
  const [deepReasoningEnabled, setDeepReasoningEnabled] = useState(false);
  const [conversationId, setConversationId] = useState('');

  const [selectedService, setSelectedService] = useState<EngineService | null>(DEFAULT_ENGINE_SERVICE);
  const [serviceSnapshots, setServiceSnapshots] = useState<Record<EngineService, EngineTaskSnapshot>>(createInitialEngineSnapshots);
  const [resourceForm, setResourceForm] = useState<ResourceForm>(defaultResourceForm);
  const [pathForm, setPathForm] = useState<PathForm>({
    targetPeriod: '',
    weeklyHours: '',
    currentProgress: '',
  });
  const [pushForm, setPushForm] = useState<PushForm>({
    preferredType: 'CODE_CASE',
  });
  const [assessmentForm, setAssessmentForm] = useState<AssessmentForm>({
    dimensions: defaultAssessmentDimensions,
  });

  const qnaBusy = qnaState === 'QNA_STREAMING';
  const hasStartedConversation = Boolean(conversationId) || qnaMessages.length > 1 || qnaMessages.some((item) => item.role === 'user');
  const activeEngineSnapshot = selectedService ? serviceSnapshots[selectedService] : createEmptyEngineTaskSnapshot();
  const engineBusy = selectedService ? hasLockedTask(activeEngineSnapshot) : false;
  const taskId = activeEngineSnapshot.taskId;
  const taskProgress = activeEngineSnapshot.taskProgress;
  const taskStatus = activeEngineSnapshot.taskStatus;
  const taskSummary = activeEngineSnapshot.taskSummary;
  const serviceResultLines = activeEngineSnapshot.serviceResultLines;
  const downloadLinks = activeEngineSnapshot.downloadLinks;
  const videoResult = activeEngineSnapshot.videoResult;
  const inlineResources = getInlineResourcesFromSnapshot(activeEngineSnapshot);
  const completedResources = createCompletedResourcesFromSnapshot(activeEngineSnapshot);
  const selectedServiceButton = selectedService ? serviceButtons.find((item) => item.id === selectedService) ?? null : null;
  const selectedServiceDescription = selectedService ? serviceDescriptions[selectedService] : null;

  const clearPersistedEngineSnapshot = useCallback(() => {
    if (typeof window === 'undefined') {
      return;
    }
    window.sessionStorage.removeItem(ENGINE_TASK_STORAGE_KEY);
  }, []);

  const clearPersistedQnaSnapshot = useCallback(() => {
    if (typeof window === 'undefined') {
      return;
    }
    window.sessionStorage.removeItem(QNA_SNAPSHOT_STORAGE_KEY);
  }, []);

  const persistQnaConversationCache = useCallback(() => {
    if (typeof window === 'undefined') {
      return;
    }
    window.sessionStorage.setItem(
      QNA_CONVERSATION_CACHE_STORAGE_KEY,
      JSON.stringify(qnaConversationCacheRef.current),
    );
  }, []);

  const cacheConversationView = useCallback((
    targetConversationId: string,
    snapshot: PersistedConversationViewSnapshot,
  ) => {
    qnaConversationCacheRef.current[conversationCacheKey(targetConversationId)] = snapshot;
    persistQnaConversationCache();
  }, [persistQnaConversationCache]);

  const setQnaStateView = useCallback((nextState: QnaState) => {
    qnaStateRef.current = nextState;
    setQnaState(nextState);
  }, []);

  const updateQnaConversationMessages = useCallback((
    targetConversationId: string,
    updater: (messages: ChatMessage[]) => ChatMessage[],
    options: { qnaInput?: string; qnaState?: QnaState } = {},
  ) => {
    const normalizedConversationId = targetConversationId.trim();
    const cacheKey = conversationCacheKey(normalizedConversationId);
    const cachedSnapshot = qnaConversationCacheRef.current[cacheKey];
    const isVisibleConversation = conversationIdRef.current === normalizedConversationId;
    const sourceMessages = isVisibleConversation
      ? qnaMessagesRef.current
      : cachedSnapshot?.qnaMessages ?? [];
    const nextMessages = updater(sourceMessages);
    const nextSnapshot: PersistedConversationViewSnapshot = {
      qnaInput: options.qnaInput ?? cachedSnapshot?.qnaInput ?? (isVisibleConversation ? qnaInputRef.current : ''),
      qnaMessages: nextMessages,
      qnaState: options.qnaState ?? cachedSnapshot?.qnaState ?? (isVisibleConversation ? qnaStateRef.current : 'QNA_IDLE'),
    };

    cacheConversationView(normalizedConversationId, nextSnapshot);

    if (!isVisibleConversation || !mountedRef.current) {
      return;
    }
    qnaMessagesRef.current = nextMessages;
    setQnaMessages(nextMessages);
    if (options.qnaInput !== undefined) {
      qnaInputRef.current = options.qnaInput;
      setQnaInput(options.qnaInput);
    }
    if (options.qnaState !== undefined) {
      setQnaStateView(options.qnaState);
    }
  }, [cacheConversationView, setQnaStateView]);

  const updateServiceSnapshot = useCallback(
    (
      service: EngineService,
      updater: EngineTaskSnapshot | ((current: EngineTaskSnapshot) => EngineTaskSnapshot),
    ) => {
      setServiceSnapshots((prev) => {
        const current = prev[service];
        const next = typeof updater === 'function' ? updater(current) : updater;
        return {
          ...prev,
          [service]: syncSnapshotResultRecord(next),
        };
      });
    },
    [],
  );

  const resetQnaConversation = useCallback(() => {
    qnaRequestVersionRef.current += 1;
    cacheConversationView(conversationIdRef.current, {
      qnaInput: qnaInputRef.current,
      qnaMessages: qnaMessagesRef.current,
      qnaState: qnaStateRef.current,
    });
    const nextMessages: ChatMessage[] = [{ id: 'qna-greeting', role: 'assistant', content: QNA_GREETING }];
    const nextInput = qnaDraftsRef.current.__new__ ?? '';
    conversationIdRef.current = '';
    qnaMessagesRef.current = nextMessages;
    qnaInputRef.current = nextInput;
    setConversationId('');
    setQnaMessages(nextMessages);
    setQnaInput(nextInput);
    setQnaWebSearchEnabled(false);
    setQnaStateView('QNA_IDLE');
    clearPersistedQnaSnapshot();
    if (typeof window !== 'undefined') {
      window.sessionStorage.removeItem(ACTIVE_CONVERSATION_ID_STORAGE_KEY);
    }
    window.dispatchEvent(new CustomEvent('app:active-conversation-changed', { detail: { conversationId: '' } }));
  }, [cacheConversationView, clearPersistedQnaSnapshot]);

  const resetEngineView = useCallback(() => {
    engineSubmitVersionRef.current += 1;
    Object.values(taskMonitorRefsRef.current).forEach((refs) => {
      refs.taskStreamAbortRef.current?.abort();
      refs.taskStreamAbortRef.current = null;
      refs.streamQueueRef.current = [];
      cleanupStreamSchedulers(refs.streamFlushTimerRef, refs.streamRafRef);
    });
    activeTaskMonitorsRef.current = {};
    setConversationId('');
    setSelectedService(DEFAULT_ENGINE_SERVICE);
    setServiceSnapshots(createInitialEngineSnapshots());
    clearPersistedEngineSnapshot();
  }, [clearPersistedEngineSnapshot]);

  useEffect(() => {
    qnaMessagesRef.current = qnaMessages;
  }, [qnaMessages]);

  useEffect(() => {
    qnaInputRef.current = qnaInput;
    qnaDraftsRef.current[conversationIdRef.current || '__new__'] = qnaInput;
  }, [qnaInput]);

  useEffect(() => {
    qnaStateRef.current = qnaState;
  }, [qnaState]);

  useEffect(() => {
    if (mode !== 'qna' || typeof window === 'undefined') {
      return;
    }
    const raw = window.sessionStorage.getItem(QNA_CONVERSATION_CACHE_STORAGE_KEY);
    if (!raw) {
      return;
    }
    try {
      qnaConversationCacheRef.current = JSON.parse(raw) as PersistedQnaConversationCache;
    } catch {
      qnaConversationCacheRef.current = {};
      window.sessionStorage.removeItem(QNA_CONVERSATION_CACHE_STORAGE_KEY);
    }
  }, [mode]);

  useEffect(() => {
    conversationIdRef.current = conversationId;
    if (typeof window !== 'undefined') {
      if (conversationId) {
        window.sessionStorage.setItem(ACTIVE_CONVERSATION_ID_STORAGE_KEY, conversationId);
      } else {
        window.sessionStorage.removeItem(ACTIVE_CONVERSATION_ID_STORAGE_KEY);
      }
    }
    window.dispatchEvent(new CustomEvent('app:active-conversation-changed', { detail: { conversationId } }));
  }, [conversationId]);

  const syncConversationHistory = useCallback(async ({
    targetConversationId,
    requestVersion,
    cachedMessages,
    nextInput,
    expectStreaming = false,
  }: {
    targetConversationId: string;
    requestVersion?: number;
    cachedMessages?: ChatMessage[];
    nextInput?: string;
    expectStreaming?: boolean;
  }): Promise<boolean> => {
    const normalizedConversationId = targetConversationId.trim();
    if (!normalizedConversationId) {
      return false;
    }

    let latestMessages = cachedMessages;
    let previousSignature = latestMessages ? buildConversationSyncSignature(latestMessages) : '';
    let unchangedPolls = 0;
    const maxAttempts = expectStreaming ? 360 : 1;

    for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
      const history = await conversationApi.getConversationMessages(normalizedConversationId, {
        dedupe: false,
        retry: 1,
      });
      if (!mountedRef.current || conversationIdRef.current !== normalizedConversationId) {
        return Boolean(latestMessages?.length);
      }
      if (requestVersion !== undefined && qnaRequestVersionRef.current !== requestVersion) {
        return Boolean(latestMessages?.length);
      }

      const mapped = mapConversationHistory(history);
      if (mapped.length > 0) {
        const mappedHasResolvedAssistant = hasResolvedAssistantResponse(mapped);
        const preferredMessages = mappedHasResolvedAssistant
          ? mapped
          : pickPreferredConversationMessages(latestMessages, mapped);
        const nextState: QnaState =
          expectStreaming && hasPendingAssistantResponse(preferredMessages) && !mappedHasResolvedAssistant
            ? 'QNA_STREAMING'
            : 'QNA_IDLE';
        latestMessages = preferredMessages;
        qnaMessagesRef.current = preferredMessages;
        setQnaMessages(preferredMessages);
        if (expectStreaming) {
          setQnaStateView(nextState);
        }
        cacheConversationView(normalizedConversationId, {
          qnaInput: nextInput ?? qnaInputRef.current,
          qnaMessages: preferredMessages,
          qnaState: nextState,
        });

        const currentSignature = buildConversationSyncSignature(preferredMessages);
        if (currentSignature === previousSignature) {
          unchangedPolls += 1;
        } else {
          previousSignature = currentSignature;
          unchangedPolls = 0;
        }

        if (
          !expectStreaming
          || mappedHasResolvedAssistant
          || (attempt >= 2 && unchangedPolls >= 2 && !hasPendingAssistantResponse(preferredMessages))
        ) {
          return true;
        }
      }

      if (attempt < maxAttempts - 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 2000));
      }
    }

    return Boolean(latestMessages?.length);
  }, [cacheConversationView, setQnaStateView]);

  const openExistingConversation = useCallback(async (payload: SelectedConversationSnapshot) => {
    const nextConversationId = payload.conversationId?.trim();
    if (!nextConversationId) {
      return;
    }
    if (loadingConversationIdRef.current === nextConversationId) {
      return;
    }
    qnaRequestVersionRef.current += 1;
    const requestVersion = qnaRequestVersionRef.current;
    loadingConversationIdRef.current = nextConversationId;
    qnaDraftsRef.current[conversationIdRef.current || '__new__'] = qnaInputRef.current;
    cacheConversationView(conversationIdRef.current, {
      qnaInput: qnaInputRef.current,
      qnaMessages: qnaMessagesRef.current,
      qnaState: qnaStateRef.current,
    });
    const cachedSnapshot = qnaConversationCacheRef.current[conversationCacheKey(nextConversationId)];
    const shouldResumeStreaming =
      cachedSnapshot?.qnaState === 'QNA_STREAMING'
      || Boolean(qnaStreamTokensRef.current[nextConversationId])
      || hasPendingAssistantResponse(cachedSnapshot?.qnaMessages);
    const nextInput = cachedSnapshot?.qnaInput ?? qnaDraftsRef.current[nextConversationId] ?? '';
    const nextMessages: ChatMessage[] = cachedSnapshot?.qnaMessages?.length
      ? cachedSnapshot.qnaMessages
      : [
        { id: 'qna-greeting', role: 'assistant', content: QNA_GREETING },
        { id: `qna-loading-${nextConversationId}`, role: 'assistant', content: '正在加载历史对话...' },
      ];

    conversationIdRef.current = nextConversationId;
    qnaInputRef.current = nextInput;
    qnaMessagesRef.current = nextMessages;
    setConversationId(nextConversationId);
    setQnaInput(nextInput);
    setQnaStateView(shouldResumeStreaming ? 'QNA_STREAMING' : 'QNA_IDLE');
    setQnaMessages(cachedSnapshot?.qnaMessages?.length
      ? cachedSnapshot.qnaMessages
      : [
        { id: 'qna-greeting', role: 'assistant', content: QNA_GREETING },
        { id: `qna-loading-${nextConversationId}`, role: 'assistant', content: '正在加载历史对话...' },
      ]);

    try {
      const synced = await syncConversationHistory({
        targetConversationId: nextConversationId,
        requestVersion,
        cachedMessages: cachedSnapshot?.qnaMessages,
        nextInput: cachedSnapshot?.qnaInput ?? qnaDraftsRef.current[nextConversationId] ?? '',
        expectStreaming: shouldResumeStreaming,
      });
      if (synced) {
        return;
      }
      setQnaMessages([
        { id: 'qna-greeting', role: 'assistant', content: QNA_GREETING },
        {
          id: `qna-history-${nextConversationId}`,
          role: 'assistant',
          content: payload.lastMessagePreview?.trim()
            ? `已进入历史对话“${payload.title || '历史对话'}”。\n上次对话摘要：${payload.lastMessagePreview}\n你可以继续追问。`
            : `已进入历史对话“${payload.title || '历史对话'}”。你可以继续追问。`,
        },
      ]);
    } catch (error) {
      if (conversationIdRef.current !== nextConversationId || qnaRequestVersionRef.current !== requestVersion) {
        return;
      }
      const message = getErrorMessage(error);
      setQnaMessages([
        { id: 'qna-greeting', role: 'assistant', content: QNA_GREETING },
        {
          id: `qna-history-error-${nextConversationId}`,
          role: 'assistant',
          content:
            message.includes('(429)')
              ? '历史对话加载过于频繁，请稍等一两秒后重试。当前会话已选中，但消息列表还未重新拉取完成。'
              : `历史对话加载失败：${message}`,
        },
      ]);
    } finally {
      loadingConversationIdRef.current = '';
    }
  }, [cacheConversationView, setQnaStateView, syncConversationHistory]);

  useEffect(() => {
    return () => {
      mountedRef.current = false;
      Object.values(taskMonitorRefsRef.current).forEach((refs) => {
        refs.taskStreamAbortRef.current?.abort();
        cleanupStreamSchedulers(refs.streamFlushTimerRef, refs.streamRafRef);
      });
      Object.values(qnaStreamControllersRef.current).forEach((controller) => controller.abort());
      qnaStreamControllersRef.current = {};
    };
  }, []);

  useEffect(() => {
    mountedRef.current = true;
  }, []);

  useEffect(() => {
    if (mode !== 'qna' || qnaSnapshotHydratedRef.current || typeof window === 'undefined') {
      return;
    }

    qnaSnapshotHydratedRef.current = true;
    const raw = window.sessionStorage.getItem(QNA_SNAPSHOT_STORAGE_KEY);
    if (!raw) {
      return;
    }
    try {
      const snapshot = JSON.parse(raw) as PersistedQnaSnapshot;
      const restoredConversationId = snapshot.conversationId ?? '';
      const restoredInput = snapshot.qnaInput ?? '';
      const restoredMessages = normalizeRestoredQnaMessages(snapshot);
      cacheConversationView(snapshot.conversationId ?? '', {
        qnaInput: restoredInput,
        qnaMessages: restoredMessages,
        qnaState: snapshot.qnaState === 'QNA_STREAMING' ? 'QNA_STREAMING' : 'QNA_IDLE',
      });
      conversationIdRef.current = restoredConversationId;
      qnaInputRef.current = restoredInput;
      qnaMessagesRef.current = restoredMessages;
      setConversationId(restoredConversationId);
      setQnaInput(restoredInput);
      setQnaStateView(snapshot.qnaState === 'QNA_STREAMING' ? 'QNA_STREAMING' : 'QNA_IDLE');
      setQnaMessages(restoredMessages);
      if (snapshot.qnaState === 'QNA_STREAMING' && snapshot.conversationId?.trim()) {
        const restoredConversationId = snapshot.conversationId.trim();
        void syncConversationHistory({
          targetConversationId: restoredConversationId,
          cachedMessages: normalizeRestoredQnaMessages(snapshot),
          nextInput: snapshot.qnaInput ?? '',
          expectStreaming: true,
        }).catch(() => undefined);
      }
    } catch {
      clearPersistedQnaSnapshot();
    }
  }, [cacheConversationView, clearPersistedQnaSnapshot, mode, setQnaStateView, syncConversationHistory]);

  useEffect(() => {
    const previousMode = previousModeRef.current;
    previousModeRef.current = mode;
    if (mode !== 'qna' || previousMode === 'qna') {
      return;
    }
    const restoredConversationId = conversationIdRef.current.trim();
    if (!restoredConversationId) {
      return;
    }
    void syncConversationHistory({
      targetConversationId: restoredConversationId,
      cachedMessages: qnaMessagesRef.current,
      nextInput: qnaInputRef.current,
      expectStreaming: qnaStateRef.current === 'QNA_STREAMING',
    }).catch(() => undefined);
  }, [mode, syncConversationHistory]);

  useEffect(() => {
    if (mode !== 'qna' || !qnaSnapshotHydratedRef.current || typeof window === 'undefined') {
      return;
    }

    cacheConversationView(conversationId, {
      qnaInput,
      qnaMessages,
      qnaState,
    });

    const snapshot: PersistedQnaSnapshot = {
      conversationId,
      qnaInput,
      qnaState,
      qnaMessages,
    };
    const isEmpty =
      !snapshot.conversationId &&
      !snapshot.qnaInput &&
      snapshot.qnaState === 'QNA_IDLE' &&
      snapshot.qnaMessages.length === 1 &&
      snapshot.qnaMessages[0]?.id === 'qna-greeting';

    if (isEmpty) {
      clearPersistedQnaSnapshot();
      return;
    }
    window.sessionStorage.setItem(QNA_SNAPSHOT_STORAGE_KEY, JSON.stringify(snapshot));
  }, [cacheConversationView, clearPersistedQnaSnapshot, conversationId, mode, qnaInput, qnaMessages, qnaState]);

  useEffect(() => {
    const onNewChat = () => {
      if (mode === 'qna') {
        resetQnaConversation();
        return;
      }
      resetEngineView();
    };
    window.addEventListener('app:new-chat', onNewChat);
    return () => {
      window.removeEventListener('app:new-chat', onNewChat);
    };
  }, [mode, resetEngineView, resetQnaConversation]);

  useEffect(() => {
    const restoreSelectedConversation = (payload?: SelectedConversationSnapshot) => {
      if (mode !== 'qna') {
        return;
      }
      if (payload?.conversationId) {
        openExistingConversation(payload);
        return;
      }
      if (typeof window === 'undefined') {
        return;
      }
      const raw = window.sessionStorage.getItem(SELECTED_CONVERSATION_STORAGE_KEY);
      if (!raw) {
        return;
      }
      try {
        const parsed = JSON.parse(raw) as SelectedConversationSnapshot;
        openExistingConversation(parsed);
      } catch {
        window.sessionStorage.removeItem(SELECTED_CONVERSATION_STORAGE_KEY);
      }
    };

    const onOpenConversation = (event: Event) => {
      const customEvent = event as CustomEvent<SelectedConversationSnapshot>;
      restoreSelectedConversation(customEvent.detail);
    };

    restoreSelectedConversation();
    window.addEventListener('app:open-conversation', onOpenConversation as EventListener);
    return () => {
      window.removeEventListener('app:open-conversation', onOpenConversation as EventListener);
    };
  }, [mode, openExistingConversation]);

  useEffect(() => {
    if (mode !== 'engine' || conversationId || typeof window === 'undefined') {
      return;
    }
    const activeConversationId = window.sessionStorage.getItem(ACTIVE_CONVERSATION_ID_STORAGE_KEY)?.trim() ?? '';
    if (activeConversationId) {
      setConversationId(activeConversationId);
    }
  }, [conversationId, mode]);

  useEffect(() => {
    if (isAuthenticated && pendingActionRef.current) {
      const action = pendingActionRef.current;
      pendingActionRef.current = null;
      action();
    }
  }, [isAuthenticated]);

  const withAuth = (action: () => void) => {
    if (isAuthenticated) {
      action();
      return;
    }
    pendingActionRef.current = action;
    openAuthModal('login', '请先登录');
  };

  const markFormEditing = () => {
    if (!selectedService) {
      return;
    }
    updateServiceSnapshot(selectedService, (current) => {
      if (hasLockedTask(current)) {
        return current;
      }
      return {
        ...current,
        engineState: 'ENGINE_FORM_EDITING',
      };
    });
  };

  const handleSelectService = (service: EngineService) => {
    setSelectedService(service);
    updateServiceSnapshot(service, (current) => {
      if (current.engineState !== 'ENGINE_IDLE' || current.taskId) {
        return current;
      }
      return {
        ...current,
        engineState: 'ENGINE_SERVICE_SELECTED',
      };
    });
  };

  const monitorTask = useCallback(
    async (service: EngineService, currentTaskId: string) => {
      if (activeTaskMonitorsRef.current[service] === currentTaskId) {
        return;
      }
      activeTaskMonitorsRef.current[service] = currentTaskId;
      const refs = taskMonitorRefsRef.current[service];
      const outcome = await runByApiTask({
        service,
        currentTaskId,
        streamQueueRef: refs.streamQueueRef,
        streamFlushTimerRef: refs.streamFlushTimerRef,
        streamRafRef: refs.streamRafRef,
        setServiceResultLines: (value) => {
          updateServiceSnapshot(service, (current) => ({
            ...current,
            serviceResultLines: typeof value === 'function' ? value(current.serviceResultLines) : value,
          }));
        },
        setTaskProgress: (value) => {
          updateServiceSnapshot(service, (current) => ({
            ...current,
            taskProgress: typeof value === 'function' ? value(current.taskProgress) : value,
          }));
        },
        setTaskStatus: (value) => {
          updateServiceSnapshot(service, (current) => ({
            ...current,
            taskStatus: typeof value === 'function' ? value(current.taskStatus) : value,
          }));
        },
        setTaskSummary: (value) => {
          updateServiceSnapshot(service, (current) => ({
            ...current,
            taskSummary: typeof value === 'function' ? value(current.taskSummary) : value,
          }));
        },
        setDownloadLinks: (value) => {
          updateServiceSnapshot(service, (current) => ({
            ...current,
            downloadLinks: typeof value === 'function' ? value(current.downloadLinks) : value,
          }));
        },
        setVideoResult: (value) => {
          updateServiceSnapshot(service, (current) => ({
            ...current,
            videoResult: typeof value === 'function' ? value(current.videoResult) : value,
          }));
        },
        setInlineResource: (value) => {
          updateServiceSnapshot(service, (current) => ({
            ...current,
            inlineResource: typeof value === 'function' ? value(current.inlineResource) : value,
          }));
        },
        setInlineResources: (value) => {
          updateServiceSnapshot(service, (current) => ({
            ...current,
            inlineResources: typeof value === 'function' ? value(getInlineResourcesFromSnapshot(current)) : value,
          }));
        },
        setCompletedResources: (value) => {
          updateServiceSnapshot(service, (current) => ({
            ...current,
            completedResources: typeof value === 'function' ? value(createCompletedResourcesFromSnapshot(current)) : value,
          }));
        },
        setPracticeBatch: (value) => {
          updateServiceSnapshot(service, (current) => ({
            ...current,
            practiceBatch: typeof value === 'function' ? value(current.practiceBatch) : value,
          }));
        },
        setJudgeResult: (value) => {
          updateServiceSnapshot(service, (current) => ({
            ...current,
            judgeResult: typeof value === 'function' ? value(current.judgeResult) : value,
          }));
        },
        taskStreamAbortRef: refs.taskStreamAbortRef,
      });

      const monitorStillCurrent = activeTaskMonitorsRef.current[service] === currentTaskId;
      if (monitorStillCurrent) {
        delete activeTaskMonitorsRef.current[service];
      }
      if (!monitorStillCurrent || !mountedRef.current) {
        return;
      }

      if (outcome === 'completed') {
        updateServiceSnapshot(service, (current) => ({
          ...current,
          engineState: 'ENGINE_COMPLETED',
          taskStatus: '任务完成',
        }));
        return;
      }

      if (outcome === 'failed') {
        updateServiceSnapshot(service, (current) => ({
          ...current,
          engineState: 'ENGINE_FAILED',
          taskStatus: '任务失败',
        }));
        return;
      }

      if (outcome === 'aborted') {
        updateServiceSnapshot(service, (current) => ({
          ...current,
          engineState: 'ENGINE_FAILED',
          taskStatus: current.taskStatus === '任务已取消' ? current.taskStatus : '连接中断，请重试',
        }));
        return;
      }

      if (outcome === 'running') {
        updateServiceSnapshot(service, (current) => ({
          ...current,
          engineState: 'ENGINE_RUNNING',
          taskStatus: '后台运行中',
          serviceResultLines: current.serviceResultLines.includes('任务仍在后台执行，可切换页面，稍后返回继续查看结果。')
            ? current.serviceResultLines
            : [...current.serviceResultLines, '任务仍在后台执行，可切换页面，稍后返回继续查看结果。'],
        }));
        return;
      }

      if (outcome === 'unauthorized') {
        updateServiceSnapshot(service, (current) => ({
          ...current,
          engineState: 'ENGINE_RUNNING',
          taskStatus: '登录失效，待重新登录',
        }));
        openAuthModal('login', '登录状态已失效，重新登录后可继续查看任务结果');
      }
    },
    [openAuthModal, updateServiceSnapshot],
  );

  const handleQnaSend = async () => {
    const text = qnaInput.trim();
    const uploadedImageUrls = pendingQnaImages.filter((item) => item.uploadStatus === 'uploaded' && item.uploadedUrl).map((item) => item.uploadedUrl as string);
    if ((!text && uploadedImageUrls.length === 0) || qnaBusy) {
      return;
    }
    if (!isAuthenticated) {
      openAuthModal('login', '请先登录');
      return;
    }

    const assistantMessageId = `qna-assistant-${Date.now()}`;
    const userMessageId = `qna-user-${Date.now()}`;
    const pendingPreviewUrls = pendingQnaImages.map((item) => item.previewUrl);
    const useWebSearch = qnaWebSearchEnabled;
    const useDeepReasoning = deepReasoningEnabled;
    const pendingMessages: ChatMessage[] = [
      ...qnaMessagesRef.current,
      {
        id: userMessageId,
        role: 'user',
        content: text,
        imageUrls: uploadedImageUrls,
        localImagePreviews: pendingPreviewUrls,
        webSearchEnabled: useWebSearch,
        deepReasoningEnabled: useDeepReasoning,
      },
      { id: assistantMessageId, role: 'assistant', content: '' },
    ];
    qnaInputRef.current = '';
    qnaMessagesRef.current = pendingMessages;
    setQnaInput('');
    setQnaWebSearchEnabled(false);
    setQnaImageError('');
    setQnaMessages(pendingMessages);
    setPendingQnaImages([]);
    setQnaStateView('QNA_STREAMING');
    cacheConversationView(conversationIdRef.current, {
      qnaInput: '',
      qnaMessages: pendingMessages,
      qnaState: 'QNA_STREAMING',
    });

    qnaRequestVersionRef.current += 1;
    const abortController = new AbortController();
    const originConversationId = conversationIdRef.current.trim();
    let streamConversationId = originConversationId;

    try {
      const currentConversationId = conversationId || (await conversationApi.createConversation()).conversationId;
      streamConversationId = currentConversationId;
      if (abortController.signal.aborted || !mountedRef.current) {
        return;
      }
      const stillViewingOrigin = conversationIdRef.current === originConversationId;
      if (!conversationId && stillViewingOrigin) {
        conversationIdRef.current = currentConversationId;
        setConversationId(currentConversationId);
        qnaDraftsRef.current.__new__ = '';
        window.dispatchEvent(new Event('app:conversation-updated'));
      }
      cacheConversationView(currentConversationId, {
        qnaInput: '',
        qnaMessages: pendingMessages,
        qnaState: 'QNA_STREAMING',
      });
      const streamToken = `${currentConversationId}:${assistantMessageId}`;
      qnaStreamControllersRef.current[currentConversationId]?.abort();
      qnaStreamControllersRef.current[currentConversationId] = abortController;
      qnaStreamTokensRef.current[currentConversationId] = streamToken;

      await conversationApi.streamMessage(
        currentConversationId,
        {
          message: text,
          imageUrls: uploadedImageUrls,
          serviceType: 'TUTORING',
          webSearchEnabled: useWebSearch,
          reasoningMode: useDeepReasoning ? 'DEEP' : 'NORMAL',
        },
        {
          onOpen: () => {
            if (qnaStreamTokensRef.current[currentConversationId] !== streamToken) {
              return;
            }
            window.dispatchEvent(new Event('app:conversation-updated'));
          },
          onEvent: (event) => {
            if (qnaStreamTokensRef.current[currentConversationId] !== streamToken) {
              return;
            }
            const chunk = readConversationChunk(event.data, event.event);
            if (!chunk) {
              return;
            }
            updateQnaConversationMessages(
              currentConversationId,
              (messages) => {
                let updatedAssistant = false;
                const nextMessagesForChunk = messages.map((item) => {
                  if (item.id !== assistantMessageId) {
                    return item;
                  }
                  updatedAssistant = true;
                  return { ...item, content: (item.content ?? '') + chunk };
                });
                return updatedAssistant
                  ? nextMessagesForChunk
                  : [...messages, { id: assistantMessageId, role: 'assistant', content: chunk }];
              },
              { qnaState: 'QNA_STREAMING' },
            );
          },
          onDone: () => {
            if (qnaStreamTokensRef.current[currentConversationId] !== streamToken) {
              return;
            }
            delete qnaStreamTokensRef.current[currentConversationId];
            if (qnaStreamControllersRef.current[currentConversationId] === abortController) {
              delete qnaStreamControllersRef.current[currentConversationId];
            }
            updateQnaConversationMessages(currentConversationId, (messages) => messages, { qnaState: 'QNA_IDLE' });
            window.dispatchEvent(new Event('app:conversation-updated'));
          },
          onError: (error) => {
            if (qnaStreamTokensRef.current[currentConversationId] !== streamToken) {
              return;
            }
            delete qnaStreamTokensRef.current[currentConversationId];
            if (qnaStreamControllersRef.current[currentConversationId] === abortController) {
              delete qnaStreamControllersRef.current[currentConversationId];
            }
            const message = getErrorMessage(error);
            updateQnaConversationMessages(
              currentConversationId,
              (messages) =>
                messages.map((item) =>
                  item.id === assistantMessageId
                    ? {
                      ...item,
                      content: item.content && !isProcessingOnlyAssistantContent(item.content)
                        ? item.content
                        : `会话失败：${message}`,
                    }
                    : item,
                ),
              { qnaState: 'QNA_IDLE' },
            );
            if (conversationIdRef.current !== currentConversationId) {
              window.dispatchEvent(new Event('app:conversation-updated'));
              return;
            }
            setQnaMessages((prev) =>
              prev.map((item) =>
                item.id === assistantMessageId ? { ...item, content: item.content || `会话失败：${message}` } : item,
              ),
            );
            setQnaStateView('QNA_IDLE');
            window.dispatchEvent(new Event('app:conversation-updated'));
          },
        },
        abortController.signal,
      );
    } catch (error) {
      const message = getErrorMessage(error);
      const targetConversationId = streamConversationId || (
        conversationIdRef.current === originConversationId
          ? conversationIdRef.current
          : originConversationId
      );
      if (targetConversationId) {
        delete qnaStreamTokensRef.current[targetConversationId];
        if (qnaStreamControllersRef.current[targetConversationId] === abortController) {
          delete qnaStreamControllersRef.current[targetConversationId];
        }
        updateQnaConversationMessages(
          targetConversationId,
          (messages) =>
            messages.map((item) =>
              item.id === assistantMessageId
                ? { ...item, content: item.content || `会话失败：${message}` }
                : item,
            ),
          { qnaState: 'QNA_IDLE' },
        );
        return;
      }
      if (conversationIdRef.current !== originConversationId) {
        return;
      }
      setQnaMessages((prev) =>
        prev.map((item) => (item.id === assistantMessageId ? { ...item, content: `会话失败：${message}` } : item)),
      );
      setQnaStateView('QNA_IDLE');
    }
  };

  const revokePendingImage = useCallback((image: PendingChatImage) => {
    if (image.previewUrl.startsWith('blob:')) {
      URL.revokeObjectURL(image.previewUrl);
    }
  }, []);

  const validateImageFile = useCallback((file: File): string => {
    const allowedTypes = new Set(['image/jpeg', 'image/png', 'image/webp']);
    if (!allowedTypes.has(file.type)) {
      return '仅支持 jpg、png、webp 图片';
    }
    if (file.size > 10 * 1024 * 1024) {
      return '图片不能超过 10MB';
    }
    return '';
  }, []);

  const handlePickQnaImages = useCallback(async (files: File[]) => {
    if (!files.length) {
      return;
    }
    setQnaImageError('');
    for (const file of files) {
      const validationMessage = validateImageFile(file);
      if (validationMessage) {
        setQnaImageError(validationMessage);
        continue;
      }
      const imageId = `pending-image-${Date.now()}-${Math.random().toString(16).slice(2)}`;
      const previewUrl = URL.createObjectURL(file);
      const pendingImage: PendingChatImage = {
        id: imageId,
        file,
        previewUrl,
        uploadStatus: 'uploading',
        uploadProgress: 0,
      };
      setPendingQnaImages((prev) => [...prev, pendingImage]);
      try {
        const uploaded = await conversationApi.uploadImage(file, (percent) => {
          setPendingQnaImages((prev) =>
            prev.map((item) => (item.id === imageId ? { ...item, uploadProgress: percent, uploadStatus: 'uploading' } : item)),
          );
        });
        setPendingQnaImages((prev) =>
          prev.map((item) =>
            item.id === imageId
              ? { ...item, uploadProgress: 100, uploadStatus: 'uploaded', uploadedUrl: uploaded.imageUrl }
              : item,
          ),
        );
      } catch (error) {
        const message = getErrorMessage(error);
        setPendingQnaImages((prev) =>
          prev.map((item) =>
            item.id === imageId
              ? { ...item, uploadStatus: 'failed', errorMessage: message }
              : item,
          ),
        );
        setQnaImageError(message);
      }
    }
  }, [validateImageFile]);

  const handleRemovePendingQnaImage = useCallback((id: string) => {
    setPendingQnaImages((prev) => {
      const target = prev.find((item) => item.id === id);
      if (target) {
        revokePendingImage(target);
      }
      return prev.filter((item) => item.id !== id);
    });
  }, [revokePendingImage]);

  useEffect(() => () => {
    pendingQnaImages.forEach(revokePendingImage);
  }, [pendingQnaImages, revokePendingImage]);

  const ensureEngineConversationId = useCallback(async () => {
    const currentConversationId = conversationIdRef.current.trim();
    if (currentConversationId) {
      return currentConversationId;
    }

    if (typeof window !== 'undefined') {
      const activeConversationId = window.sessionStorage.getItem(ACTIVE_CONVERSATION_ID_STORAGE_KEY)?.trim() ?? '';
      if (activeConversationId) {
        setConversationId(activeConversationId);
        window.dispatchEvent(new Event('app:conversation-updated'));
        return activeConversationId;
      }
    }

    const recentConversations = await conversationApi.listRecentConversations();
    const latestConversationId = recentConversations[0]?.conversationId?.trim() ?? '';
    if (latestConversationId) {
      setConversationId(latestConversationId);
      window.dispatchEvent(new Event('app:conversation-updated'));
      return latestConversationId;
    }

    const createdConversationId = (await conversationApi.createConversation()).conversationId;
    setConversationId(createdConversationId);
    window.dispatchEvent(new Event('app:conversation-updated'));
    return createdConversationId;
  }, []);

  const handleSubmitService = async () => {
    if (!isAuthenticated) {
      openAuthModal('login', '请先登录');
      return;
    }
    if (!selectedService || engineBusy || hasLockedTask(serviceSnapshots[selectedService])) {
      return;
    }

    const refs = taskMonitorRefsRef.current[selectedService];
    refs.taskStreamAbortRef.current?.abort();
    refs.taskStreamAbortRef.current = null;
    refs.streamQueueRef.current = [];
    cleanupStreamSchedulers(refs.streamFlushTimerRef, refs.streamRafRef);
    updateServiceSnapshot(selectedService, {
      engineState: 'ENGINE_SUBMITTING',
      taskId: '',
      taskProgress: 8,
      taskStatus: '已提交，等待受理',
      taskSummary: '',
      serviceResultLines: [],
      downloadLinks: [],
      videoResult: null,
      inlineResource: null,
      inlineResources: [],
      practiceBatch: null,
      completedResources: [],
      judgeResult: null,
      resultHistory: serviceSnapshots[selectedService].resultHistory,
      selectedResultTaskId: serviceSnapshots[selectedService].selectedResultTaskId,
    });

    const params = buildServiceParams(selectedService, { resourceForm, pathForm, pushForm, assessmentForm });

    try {
      engineSubmitVersionRef.current += 1;
      const submitVersion = engineSubmitVersionRef.current;
      const ensuredConversationId = await ensureEngineConversationId();
      if (engineSubmitVersionRef.current !== submitVersion) {
        return;
      }
      const submitResp = await smartEngineApi.submit({
        conversationId: ensuredConversationId,
        serviceType: serviceTypeMap[selectedService],
        params,
      });

      updateServiceSnapshot(selectedService, (current) => ({
        ...current,
        taskId: submitResp.taskId,
        engineState: 'ENGINE_RUNNING',
        taskStatus: toUiTaskStatus(submitResp.status),
        selectedResultTaskId: submitResp.taskId,
      }));
      void monitorTask(selectedService, submitResp.taskId);
    } catch (error) {
      const message = getErrorMessage(error);
      updateServiceSnapshot(selectedService, (current) => ({
        ...current,
        engineState: 'ENGINE_FAILED',
        taskStatus: '任务失败',
        serviceResultLines: [...current.serviceResultLines, `接口失败：${message}`],
      }));
    }
  };

  const handleSubmitPracticeAnswers = async (batch: PracticeQuestionBatch, answers: Record<string, string>) => {
    if (!isAuthenticated) {
      openAuthModal('login', '请先登录');
      return;
    }
    const targetService: EngineService = selectedService === 'assessment' ? 'assessment' : 'resource';
    if (hasLockedTask(serviceSnapshots[targetService])) {
      return;
    }

    const refs = taskMonitorRefsRef.current[targetService];
    refs.taskStreamAbortRef.current?.abort();
    refs.taskStreamAbortRef.current = null;
    refs.streamQueueRef.current = [];
    cleanupStreamSchedulers(refs.streamFlushTimerRef, refs.streamRafRef);
    updateServiceSnapshot(targetService, (current) => ({
      ...current,
      engineState: 'ENGINE_SUBMITTING',
      taskId: '',
      taskProgress: 12,
      taskStatus: '已提交判题任务',
      taskSummary: '',
      serviceResultLines: [],
      downloadLinks: [],
      videoResult: null,
      inlineResource: null,
      inlineResources: [],
      completedResources: [],
      judgeResult: null,
      resultHistory: current.resultHistory,
      selectedResultTaskId: current.selectedResultTaskId,
    }));

    try {
      const ensuredConversationId = await ensureEngineConversationId();
      const assessmentDimension = batch.assessmentDimension || (targetService === 'assessment' ? assessmentForm.dimensions[0] : '');
      const batchTopic = batch.topic || resourceForm.keyPoints || resourceForm.course;
      const judgeQuery = targetService === 'assessment'
        ? `${assessmentDimension || '专项评估'} ${batchTopic} 判题`
        : `${resourceForm.course} ${batchTopic} 练习题判题`;
      const submitResp = await smartEngineApi.submit({
        conversationId: ensuredConversationId,
        serviceType: 'PRACTICE_JUDGE',
        params: {
          topic: targetService === 'assessment' && assessmentDimension
            ? `${assessmentDimension}：${batchTopic}`
            : batchTopic,
          query: judgeQuery,
          practiceQuestionBatch: batch,
          practiceQuestions: batch.questions,
          answers,
          assessmentDimension,
          learningContext: {
            course: resourceForm.course,
            chapter: batchTopic,
          },
        },
      });
      updateServiceSnapshot(targetService, (current) => ({
        ...current,
        taskId: submitResp.taskId,
        engineState: 'ENGINE_RUNNING',
        taskStatus: toUiTaskStatus(submitResp.status),
        selectedResultTaskId: submitResp.taskId,
      }));
      void monitorTask(targetService, submitResp.taskId);
    } catch (error) {
      const message = getErrorMessage(error);
      updateServiceSnapshot(targetService, (current) => ({
        ...current,
        engineState: 'ENGINE_FAILED',
        taskStatus: '判题失败',
        serviceResultLines: [...current.serviceResultLines, `判题失败：${message}`],
      }));
    }
  };

  const handleStopService = async () => {
    if (!selectedService) {
      return;
    }
    const service = selectedService;
    const currentTaskId = serviceSnapshots[service].taskId;
    const refs = taskMonitorRefsRef.current[selectedService];
    refs.taskStreamAbortRef.current?.abort();
    refs.taskStreamAbortRef.current = null;
    activeTaskMonitorsRef.current[service] = '';
    cleanupStreamSchedulers(refs.streamFlushTimerRef, refs.streamRafRef);
    updateServiceSnapshot(service, (current) => ({
      ...current,
      engineState: 'ENGINE_FAILED',
      taskStatus: currentTaskId ? '正在取消任务' : '已停止实时接收',
      serviceResultLines: current.serviceResultLines.includes('已向后端发送取消请求，正在等待确认。')
        ? current.serviceResultLines
        : [
          ...current.serviceResultLines,
          currentTaskId ? '已向后端发送取消请求，正在等待确认。' : '已停止当前页面的实时接收。',
        ],
    }));
    if (!currentTaskId) {
      return;
    }
    try {
      await smartEngineApi.cancelTask(currentTaskId);
      updateServiceSnapshot(service, (current) => ({
        ...current,
        engineState: 'ENGINE_FAILED',
        taskStatus: '任务已取消',
        serviceResultLines: current.serviceResultLines.includes('后端任务已取消。')
          ? current.serviceResultLines
          : [...current.serviceResultLines, '后端任务已取消。'],
      }));
    } catch (error) {
      const message = getErrorMessage(error);
      updateServiceSnapshot(service, (current) => ({
        ...current,
        taskStatus: '取消失败',
        serviceResultLines: [...current.serviceResultLines, `取消失败：${message}`],
      }));
    }
  };

  const handleSelectResultTask = (resultTaskId: string) => {
    if (!selectedService || !resultTaskId) {
      return;
    }
    updateServiceSnapshot(selectedService, (current) => ({
      ...current,
      selectedResultTaskId: resultTaskId,
    }));
  };

  useEffect(() => {
    if (mode !== 'engine' || engineSnapshotHydratedRef.current) {
      return;
    }

    engineSnapshotHydratedRef.current = true;
    if (typeof window === 'undefined') {
      return;
    }

    const raw = window.sessionStorage.getItem(ENGINE_TASK_STORAGE_KEY);
    if (!raw) {
      return;
    }

    try {
      const snapshot = JSON.parse(raw) as PersistedEngineTaskSnapshot;
      const persistedSnapshots = buildPersistedEngineSnapshots(
        snapshot.selectedService ?? null,
        snapshot.snapshots ?? createInitialEngineSnapshots(),
      );
      setSelectedService(snapshot.selectedService ?? DEFAULT_ENGINE_SERVICE);
      setConversationId(snapshot.conversationId ?? window.sessionStorage.getItem(ACTIVE_CONVERSATION_ID_STORAGE_KEY) ?? '');
      setServiceSnapshots({
        ...createInitialEngineSnapshots(),
        ...persistedSnapshots,
      });

      (Object.entries(persistedSnapshots) as Array<[EngineService, EngineTaskSnapshot]>).forEach(([service, item]) => {
        if (item.taskId && (item.engineState === 'ENGINE_RUNNING' || item.engineState === 'ENGINE_SUBMITTING')) {
          void monitorTask(service, item.taskId);
        }
      });
    } catch {
      clearPersistedEngineSnapshot();
    }
  }, [clearPersistedEngineSnapshot, mode, monitorTask]);

  useEffect(() => {
    if (mode !== 'engine' || !engineSnapshotHydratedRef.current || typeof window === 'undefined') {
      return;
    }

    const snapshot: PersistedEngineTaskSnapshot = {
      selectedService,
      conversationId,
      snapshots: buildPersistedEngineSnapshots(selectedService, serviceSnapshots),
    };

    const isEmptySnapshot =
      (!selectedService || selectedService === DEFAULT_ENGINE_SERVICE) &&
      !conversationId &&
      Object.values(serviceSnapshots).every((item) => !item.taskId && item.engineState === 'ENGINE_IDLE');

    if (isEmptySnapshot) {
      clearPersistedEngineSnapshot();
      return;
    }

    window.sessionStorage.setItem(ENGINE_TASK_STORAGE_KEY, JSON.stringify(snapshot));
  }, [
    clearPersistedEngineSnapshot,
    conversationId,
    mode,
    selectedService,
    serviceSnapshots,
  ]);

  if (mode === 'qna') {
    return (
      <QnaChatView
        hasStartedConversation={hasStartedConversation}
        qnaInput={qnaInput}
        qnaBusy={qnaBusy}
        qnaMessages={qnaMessages}
        pendingImages={pendingQnaImages}
        imageErrorMessage={qnaImageError}
        deepReasoningEnabled={deepReasoningEnabled}
        webSearchEnabled={qnaWebSearchEnabled}
        onChange={setQnaInput}
        onSend={handleQnaSend}
        onToggleDeepReasoning={() => setDeepReasoningEnabled((prev) => !prev)}
        onToggleWebSearch={() => setQnaWebSearchEnabled((prev) => !prev)}
        onPickImages={handlePickQnaImages}
        onRemoveImage={handleRemovePendingQnaImage}
      />
    );
  }

  return (
    <Suspense fallback={<div className="mx-auto max-w-[1180px] rounded-[28px] border border-blue-100 bg-white/85 px-6 py-10 text-center text-sm text-slate-500 shadow-sm shadow-blue-100/60">正在加载学习服务...</div>}>
      <div className="mx-auto max-w-[1120px] space-y-5 px-0 pb-8 sm:space-y-7 sm:pb-10 md:px-0">
        <section className="overflow-hidden rounded-[22px] border border-blue-100/80 bg-white/92 shadow-xl shadow-blue-100/55 dark:border-slate-800 dark:bg-slate-900/86 dark:shadow-slate-950/30 sm:rounded-[28px]">
          <div className="flex items-center justify-between gap-3 border-b border-blue-100/80 px-4 py-4 dark:border-slate-800 sm:px-6 sm:py-5 md:px-8">
            <div className="flex min-w-0 flex-wrap items-center gap-3 sm:gap-4">
              <div className="flex h-10 w-10 items-center justify-center rounded-2xl bg-gradient-to-br from-blue-600 to-sky-400 text-white shadow-lg shadow-blue-500/20 sm:h-11 sm:w-11">
                <GraduationCap className="h-5 w-5" />
              </div>
              <div className="text-xl font-bold tracking-tight text-slate-900 dark:text-white sm:text-2xl">学习服务</div>
              <span className="rounded-full bg-blue-50 px-3 py-1 text-xs font-semibold text-primary-600 ring-1 ring-blue-100 dark:bg-primary-500/10 dark:text-primary-300 dark:ring-primary-500/20 sm:text-sm">
                智学引擎
              </span>
            </div>
            <button
              type="button"
              onClick={() => navigate('/')}
              className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-slate-400 transition-colors hover:bg-blue-50 hover:text-primary-600 dark:text-slate-500 dark:hover:bg-slate-800 dark:hover:text-primary-300"
              aria-label="返回对话"
            >
              <X className="h-5 w-5" />
            </button>
          </div>

          <div className="relative overflow-hidden px-4 py-6 dark:bg-slate-900/40 sm:px-6 sm:py-8 md:px-8 md:py-10">
            <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_80%_20%,rgba(59,130,246,0.18),transparent_34%),linear-gradient(120deg,rgba(248,251,255,0.95),rgba(255,255,255,0.55)_48%,rgba(231,243,255,0.82))] dark:bg-[radial-gradient(circle_at_80%_20%,rgba(59,130,246,0.2),transparent_34%),linear-gradient(120deg,rgba(15,23,42,0.95),rgba(30,41,59,0.82)_52%,rgba(17,24,39,0.92))]" />
            <div className="relative grid gap-6 lg:grid-cols-[minmax(0,0.95fr)_minmax(360px,0.78fr)] lg:items-center lg:gap-8">
              <div>
                <h1 className="text-3xl font-bold leading-tight text-slate-900 dark:text-white sm:text-[34px] md:text-[46px]">
                  选择一项<span className="text-primary-600 dark:text-primary-300">智能服务</span>
                </h1>
                <p className="mt-3 text-sm leading-7 text-slate-500 dark:text-slate-400 sm:text-base">
                  智学引擎为你量身定制专属学习体验
                </p>

                <div className="mt-6 grid gap-3 sm:mt-8 sm:grid-cols-2 sm:gap-4">
                  {serviceButtons.map((item) => {
                    const active = selectedService === item.id;
                    const description = serviceDescriptions[item.id];
                    return (
                      <button
                        key={item.id}
                        type="button"
                        onClick={() => withAuth(() => handleSelectService(item.id))}
                        className={`group relative min-h-[132px] rounded-2xl border bg-white/86 p-4 text-left shadow-sm shadow-blue-100/40 transition-all duration-200 hover:-translate-y-0.5 hover:border-primary-200 hover:shadow-lg hover:shadow-blue-100/60 dark:bg-slate-950/42 dark:shadow-none sm:min-h-[148px] sm:p-6 ${
                          active
                            ? 'border-primary-400 ring-2 ring-primary-500/15 dark:border-primary-500'
                            : 'border-blue-100/80 dark:border-slate-800'
                        }`}
                      >
                        <div className="flex items-center gap-3 sm:gap-5">
                          <span className={`flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-gradient-to-br ${description.accent} text-white shadow-md shadow-blue-500/18 sm:h-14 sm:w-14`}>
                            <item.icon className="h-6 w-6" />
                          </span>
                          <span className="min-w-0">
                            <span className={`block text-base font-bold ${active ? 'text-primary-700 dark:text-primary-300' : 'text-slate-900 dark:text-white'}`}>
                              {item.label}
                            </span>
                            <span className="mt-2 block text-sm leading-6 text-slate-500 dark:text-slate-400">
                              {description.summary}
                            </span>
                          </span>
                        </div>
                        {active ? (
                          <span className="absolute right-4 top-4 flex h-6 w-6 items-center justify-center rounded-full bg-primary-600 text-white sm:right-5 sm:top-5">
                            <CheckCircle2 className="h-4 w-4" />
                          </span>
                        ) : null}
                      </button>
                    );
                  })}
                </div>
              </div>

              <ServiceHeroVisual />
            </div>
          </div>
        </section>

        <div className="grid overflow-hidden rounded-[22px] border border-blue-100/80 bg-white/90 shadow-sm shadow-blue-100/50 dark:border-slate-800 dark:bg-slate-900/80 sm:rounded-[24px] xl:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
          <section className="border-b border-blue-100/80 p-4 dark:border-slate-800 sm:p-6 xl:border-b-0 xl:border-r">
            <EngineSectionHeader
              icon={<FileText className="h-4 w-4" />}
              title={selectedServiceButton ? `${selectedServiceButton.label}参数` : '服务参数'}
              subtitle={selectedServiceDescription?.detail ?? '选择服务后填写参数，提交前不会生成任何预置推荐。'}
            />
            <div className="mt-5 sm:mt-6">
              <ServiceDynamicForm
                service={selectedService}
                resourceForm={resourceForm}
                pathForm={pathForm}
                pushForm={pushForm}
                assessmentForm={assessmentForm}
                onResourceChange={(next) => {
                  setResourceForm(next);
                  markFormEditing();
                }}
                onPathChange={(next) => {
                  setPathForm(next);
                  markFormEditing();
                }}
                onPushChange={(next) => {
                  setPushForm(next);
                  markFormEditing();
                }}
                onAssessmentChange={(next) => {
                  setAssessmentForm(next);
                  markFormEditing();
                }}
              />
            </div>
          </section>

          <LearningEffectPreview
            selectedServiceLabel={selectedServiceButton?.label ?? ''}
            taskId={taskId}
            taskProgress={taskProgress}
            taskStatus={taskStatus}
            resultLineCount={serviceResultLines.length}
            downloadCount={downloadLinks.length}
          />
        </div>

        <AssistantActionBar
          selectedServiceLabel={selectedServiceButton?.label ?? ''}
          disabled={!selectedService || engineBusy}
          canStop={engineBusy}
          busy={engineBusy}
          status={taskStatus}
          onSubmit={handleSubmitService}
          onStop={handleStopService}
        />

        <TaskResultPanel
          service={selectedService}
          taskSummary={taskSummary}
          serviceResultLines={serviceResultLines}
          downloadLinks={downloadLinks}
          videoResult={videoResult}
          inlineResource={activeEngineSnapshot.inlineResource}
          inlineResources={inlineResources}
          completedResources={completedResources}
          resultHistory={activeEngineSnapshot.resultHistory}
          selectedResultTaskId={activeEngineSnapshot.selectedResultTaskId}
          practiceBatch={activeEngineSnapshot.practiceBatch}
          judgeResult={activeEngineSnapshot.judgeResult}
          canSubmitPractice={!hasLockedTask(activeEngineSnapshot)}
          onSelectResultTask={handleSelectResultTask}
          onSubmitPracticeAnswers={handleSubmitPracticeAnswers}
        />
      </div>
    </Suspense>
  );
}
