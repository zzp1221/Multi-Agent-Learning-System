import { useEffect, useMemo, useState, type ComponentType } from 'react';
import {
  AlertTriangle,
  BookOpen,
  Bot,
  CheckCircle2,
  Code2,
  Download,
  ExternalLink,
  FileText,
  Film,
  Layers3,
  ListChecks,
  Loader2,
  MessageSquareText,
  Network,
  Presentation,
  TimerReset,
  Sparkles,
} from 'lucide-react';
import MarkdownRenderer from '../components/MarkdownRenderer';
import MermaidDiagram from '../components/MermaidDiagram';
import VideoCard from '../components/VideoCard';
import { renderTalkingVideoInBrowser } from '../utils/browserVideoRenderer';
import { ACTIVE_CONVERSATION_ID_STORAGE_KEY } from './LearningStudioDemoPage.model';
import type { InlineResourceView } from './LearningStudioDemoPage.types';
import {
  RESOURCE_GENERATION_UPDATED_EVENT,
  loadResourceGenerationSession,
  resourceLabel,
  updateResourceVideoRenderResult,
  type GeneratedResourceType,
  type ResourceGenerationQuizSummary,
  type ResourceGenerationResource,
  type ResourceGenerationSession,
} from './resourceGenerationStore';
import {
  getPracticeSessionState,
  setPracticeSessionOpen,
  subscribePracticeSession,
} from './practiceSessionStore';

const RESOURCE_TYPES: GeneratedResourceType[] = ['DOCUMENT', 'SLIDES', 'MINDMAP', 'QUIZ', 'VIDEO', 'CODE'];

const RESOURCE_META: Record<GeneratedResourceType, { icon: ComponentType<{ className?: string }>; accent: string }> = {
  DOCUMENT: { icon: FileText, accent: 'bg-sky-50 text-sky-700 ring-sky-100 dark:bg-sky-500/10 dark:text-sky-300 dark:ring-sky-500/20' },
  SLIDES: { icon: Presentation, accent: 'bg-indigo-50 text-indigo-700 ring-indigo-100 dark:bg-indigo-500/10 dark:text-indigo-300 dark:ring-indigo-500/20' },
  MINDMAP: { icon: Network, accent: 'bg-teal-50 text-teal-700 ring-teal-100 dark:bg-teal-500/10 dark:text-teal-300 dark:ring-teal-500/20' },
  QUIZ: { icon: ListChecks, accent: 'bg-amber-50 text-amber-700 ring-amber-100 dark:bg-amber-500/10 dark:text-amber-300 dark:ring-amber-500/20' },
  READING: { icon: BookOpen, accent: 'bg-emerald-50 text-emerald-700 ring-emerald-100 dark:bg-emerald-500/10 dark:text-emerald-300 dark:ring-emerald-500/20' },
  VIDEO: { icon: Film, accent: 'bg-rose-50 text-rose-700 ring-rose-100 dark:bg-rose-500/10 dark:text-rose-300 dark:ring-rose-500/20' },
  CODE: { icon: Code2, accent: 'bg-slate-100 text-slate-700 ring-slate-200 dark:bg-slate-700/60 dark:text-slate-200 dark:ring-slate-600' },
};

const AGENT_STEPS = [
  { label: '需求识别', description: '解析对话中的资源目标' },
  { label: '资源编排', description: '拆分文档、PPT、练习与媒体任务' },
  { label: '内容生成', description: '多智能体并行产出学习资源' },
  { label: '结果收束', description: '汇总下载、预览与练习入口' },
];

type ResourceFilter = GeneratedResourceType | 'ALL';

function getActiveConversationId(): string {
  if (typeof window === 'undefined') {
    return '';
  }
  return window.sessionStorage.getItem(ACTIVE_CONVERSATION_ID_STORAGE_KEY)?.trim() ?? '';
}

export default function MultiAgentResourceGenerationPage() {
  const [conversationId, setConversationId] = useState(() => getActiveConversationId());
  const [session, setSession] = useState<ResourceGenerationSession>(() => loadResourceGenerationSession(getActiveConversationId()));
  const [selectedType, setSelectedType] = useState<ResourceFilter>('ALL');
  const [renderingVideoIds, setRenderingVideoIds] = useState<Set<string>>(() => new Set());

  useEffect(() => {
    if (typeof window === 'undefined') {
      return;
    }

    const refresh = (nextConversationId = getActiveConversationId()) => {
      setConversationId(nextConversationId);
      setSession(loadResourceGenerationSession(nextConversationId));
    };

    const handleResourceUpdate = (event: Event) => {
      const detail = (event as CustomEvent<{ conversationId?: string }>).detail;
      const targetConversationId = detail?.conversationId?.trim() || getActiveConversationId();
      refresh(targetConversationId);
    };
    const handleActiveConversationChanged = (event: Event) => {
      const detail = (event as CustomEvent<{ conversationId?: string }>).detail;
      refresh(detail?.conversationId?.trim() ?? '');
    };

    refresh();
    window.addEventListener(RESOURCE_GENERATION_UPDATED_EVENT, handleResourceUpdate as EventListener);
    window.addEventListener('app:active-conversation-changed', handleActiveConversationChanged as EventListener);
    return () => {
      window.removeEventListener(RESOURCE_GENERATION_UPDATED_EVENT, handleResourceUpdate as EventListener);
      window.removeEventListener('app:active-conversation-changed', handleActiveConversationChanged as EventListener);
    };
  }, []);

  useEffect(() => {
    const pendingVideos = session.resources.filter((resource) =>
      resource.type === 'VIDEO'
      && resource.video?.renderStatus === 'rendering'
      && Boolean(resource.video.audioBase64)
      && !renderingVideoIds.has(resource.id)
    );
    if (!pendingVideos.length) {
      return;
    }
    pendingVideos.forEach((resource) => {
      const video = resource.video;
      if (!video?.audioBase64) {
        return;
      }
      setRenderingVideoIds((current) => new Set(current).add(resource.id));
      void renderTalkingVideoInBrowser(
        {
          taskId: video.renderTaskId || resource.id,
          audioBase64: video.audioBase64,
          title: video.title,
          durationSeconds: video.duration,
          knowledgePoint: video.knowledgePoint,
          style: video.style,
        },
        {
          onProgress: (_percent, message) => {
            updateResourceVideoRenderResult(
              session.conversationId,
              resource.id,
              {
                renderStatus: 'rendering',
                renderMessage: message || '浏览器本地渲染中',
              },
              message,
            );
          },
        },
      ).then((result) => {
        updateResourceVideoRenderResult(
          session.conversationId,
          resource.id,
          {
            videoUrl: result.videoUrl,
            thumbnailUrl: result.thumbnailUrl || video.thumbnailUrl,
            duration: result.duration ?? video.duration,
            fileName: result.fileName || video.fileName || `${video.title || resource.title}.webm`,
            renderStatus: 'ready',
            renderMessage: '视频已完成本地渲染，可播放或下载',
          },
          '视频已完成本地渲染',
        );
      }).catch((error: unknown) => {
        updateResourceVideoRenderResult(
          session.conversationId,
          resource.id,
          {
            renderStatus: 'failed',
            renderMessage: error instanceof Error && error.message ? error.message : '浏览器本地渲染失败',
          },
          '视频本地渲染失败',
        );
      }).finally(() => {
        setRenderingVideoIds((current) => {
          const next = new Set(current);
          next.delete(resource.id);
          return next;
        });
      });
    });
  }, [renderingVideoIds, session.conversationId, session.resources]);

  const resourceCounts = useMemo(() => countResources(session.resources), [session.resources]);
  const visibleResources = useMemo(() => {
    if (selectedType === 'ALL') {
      return session.resources;
    }
    return session.resources.filter((resource) => resource.type === selectedType);
  }, [selectedType, session.resources]);
  const completedCount = session.resources.length;
  const statusTone = getStatusTone(session.taskStatus);
  const remainingHint = estimateRemainingTime(session);

  return (
    <div className="mx-auto max-w-[1200px] px-4 py-6 sm:px-6 sm:py-8">
      <section className="overflow-hidden rounded-[28px] bg-white/72 shadow-[0_18px_56px_rgba(59,97,155,0.10)] ring-1 ring-white/80 backdrop-blur-xl dark:bg-slate-900/68 dark:ring-slate-800/70 dark:shadow-slate-950/20">
        <div className="grid gap-5 px-5 py-5 md:grid-cols-[minmax(0,1fr)_280px] md:px-7">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-2xl bg-primary-600 text-white shadow-lg shadow-primary-500/20">
                <Layers3 className="h-5 w-5" />
              </div>
              <div className="min-w-0">
                <h1 className="text-xl font-bold tracking-tight text-slate-950 dark:text-white sm:text-2xl">
                  多智能体协同资源生成
                </h1>
                <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
                  当前会话触发的文档、PPT、思维导图、练习题、短视频和代码案例总览
                </p>
              </div>
            </div>
            <div className="mt-4 flex flex-wrap items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
              <span className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 font-semibold ring-1 ${statusTone.className}`}>
                {statusTone.icon}
                {statusTone.label}
              </span>
              <span className="rounded-full bg-slate-100 px-3 py-1.5 dark:bg-slate-800">
                会话 {conversationId ? conversationId.slice(0, 8) : '未开始'}
              </span>
              {session.topic ? (
                <span className="max-w-full truncate rounded-full bg-primary-50 px-3 py-1.5 text-primary-700 dark:bg-primary-500/10 dark:text-primary-300">
                  主题：{session.topic}
                </span>
              ) : null}
            </div>
          </div>
          <div className="rounded-2xl bg-blue-50/70 p-4 ring-1 ring-blue-100/80 dark:bg-slate-950/40 dark:ring-slate-800/80">
            <div className="flex items-center justify-between text-sm">
              <span className="font-semibold text-slate-700 dark:text-slate-200">总进度</span>
              <span className="font-bold text-primary-600 dark:text-primary-300">{session.progress}%</span>
            </div>
            <div className="mt-3 h-2 overflow-hidden rounded-full bg-white shadow-inner dark:bg-slate-800">
              <div
                className="h-full rounded-full bg-primary-600 transition-all duration-500"
                style={{ width: `${session.progress}%` }}
              />
            </div>
            <p className="mt-3 text-sm leading-6 text-slate-500 dark:text-slate-400">{session.statusText}</p>
            <p className="mt-1 text-xs text-slate-400 dark:text-slate-500">{remainingHint}</p>
          </div>
        </div>

        <div className="px-5 py-5 md:px-7">
          <div className="grid gap-3 md:grid-cols-4">
            {AGENT_STEPS.map((step, index) => (
              <AgentStepCard
                key={step.label}
                index={index}
                label={step.label}
                description={step.description}
                activeIndex={activeAgentStepIndex(session)}
                completed={isAgentStepCompleted(index, session)}
              />
            ))}
          </div>
        </div>
      </section>

      <section className="mt-6">
        <div className="flex flex-col gap-4 px-1 pb-4 md:flex-row md:items-end md:justify-between">
          <div>
            <h2 className="text-base font-bold text-slate-900 dark:text-white">资源列表</h2>
            <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
              已记录 {completedCount} 个资源状态；生成过程中会自动刷新
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <FilterButton selected={selectedType === 'ALL'} onClick={() => setSelectedType('ALL')}>
              全部 {completedCount}
            </FilterButton>
            {RESOURCE_TYPES.map((type) => (
              <FilterButton key={type} selected={selectedType === type} onClick={() => setSelectedType(type)}>
                {resourceLabel(type)} {resourceCounts[type] || 0}
              </FilterButton>
            ))}
          </div>
        </div>

        <div>
          {visibleResources.length > 0 ? (
            <div className="grid gap-4 lg:grid-cols-2">
              {visibleResources.map((resource) => (
                <ResourceCard key={resource.id} resource={resource} />
              ))}
            </div>
          ) : (
            <EmptyState selectedType={selectedType} conversationId={conversationId} />
          )}

          {session.taskStatus === 'completed' || session.taskStatus === 'partial_failed' ? (
            <div className="mt-5 rounded-2xl bg-emerald-50/70 px-4 py-3 text-sm text-emerald-800 ring-1 ring-emerald-100/80 dark:bg-emerald-500/10 dark:text-emerald-200 dark:ring-emerald-500/20">
              <div className="flex items-start gap-2">
                <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
                <span>
                  {session.taskStatus === 'partial_failed'
                    ? '资源已部分完成，可先使用已生成内容。'
                    : '本轮协同资源生成已完成，可在下方预览或下载资源。'}
                </span>
              </div>
            </div>
          ) : null}
        </div>
      </section>
    </div>
  );
}

function AgentStepCard({
  index,
  label,
  description,
  activeIndex,
  completed,
}: {
  index: number;
  label: string;
  description: string;
  activeIndex: number;
  completed: boolean;
}) {
  const active = activeIndex === index;
  return (
    <div className={`rounded-2xl p-4 ring-1 transition-all ${
      active
        ? 'bg-primary-50/80 shadow-sm shadow-primary-100 ring-primary-200 dark:bg-primary-500/10 dark:shadow-none dark:ring-primary-500/30'
        : completed
          ? 'bg-emerald-50/50 ring-emerald-100 dark:bg-emerald-500/10 dark:ring-emerald-500/20'
          : 'bg-slate-50/70 ring-slate-200/80 dark:bg-slate-950/35 dark:ring-slate-800'
    }`}
    >
      <div className="flex items-center gap-3">
        <div className={`flex h-9 w-9 items-center justify-center rounded-full ${
          completed
            ? 'bg-emerald-500 text-white'
            : active
              ? 'bg-primary-600 text-white'
              : 'bg-white text-slate-400 ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-700'
        }`}
        >
          {completed ? <CheckCircle2 className="h-4 w-4" /> : active ? <Loader2 className="h-4 w-4 animate-spin" /> : <Bot className="h-4 w-4" />}
        </div>
        <div className="min-w-0">
          <div className="text-sm font-semibold text-slate-900 dark:text-white">{label}</div>
          <div className="mt-0.5 text-xs leading-5 text-slate-500 dark:text-slate-400">{description}</div>
        </div>
      </div>
    </div>
  );
}

function ResourceCard({ resource }: { resource: ResourceGenerationResource }) {
  const meta = RESOURCE_META[resource.type];
  const Icon = meta.icon;
  const downloadable = Boolean(resource.download);
  const statusMeta = getResourceStatusMeta(resource);
  return (
    <article className="flex min-h-[260px] flex-col rounded-2xl bg-white/88 p-4 shadow-sm shadow-blue-100/35 ring-1 ring-blue-100/80 transition hover:-translate-y-0.5 hover:shadow-lg hover:shadow-blue-100/45 dark:bg-slate-950/35 dark:ring-slate-800 dark:shadow-none">
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-3">
          <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl ring-1 ${meta.accent}`}>
            <Icon className="h-5 w-5" />
          </div>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="truncate text-base font-bold text-slate-900 dark:text-white">{resource.title}</h3>
              <span className="rounded-full bg-slate-100 px-2.5 py-1 text-xs font-semibold text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                {resourceLabel(resource.type)}
              </span>
              <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-semibold ring-1 ${statusMeta.className}`}>
                {statusMeta.icon}
                {statusMeta.label}
              </span>
            </div>
            {resource.summary ? (
              <p className="mt-1 line-clamp-2 text-sm leading-6 text-slate-500 dark:text-slate-400">{resource.summary}</p>
            ) : null}
          </div>
        </div>
        {downloadable ? <ResourceDownloadButton resource={resource} iconOnly /> : null}
      </div>

      <div className="mt-4 flex-1">
        {renderResourcePreview(resource)}
      </div>

      <div className="mt-4 flex items-center justify-between gap-3 pt-3 text-xs text-slate-400 shadow-[inset_0_1px_0_rgba(226,232,240,0.72)] dark:text-slate-500 dark:shadow-[inset_0_1px_0_rgba(30,41,59,0.82)]">
        <span>{new Date(resource.updatedAt).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })} 更新</span>
        <span className="truncate">
          {resource.sourceAgent ? `来源：${resource.sourceAgent}` : resource.statusText || downloadUnavailableReason(resource)}
        </span>
      </div>
      <div className="mt-3">
        {downloadable ? (
          <ResourceDownloadButton resource={resource} />
        ) : (
          <div className="rounded-xl bg-slate-50 px-3 py-2 text-xs text-slate-500 ring-1 ring-slate-200 dark:bg-slate-900 dark:text-slate-400 dark:ring-slate-800">
            {downloadUnavailableReason(resource)}
          </div>
        )}
      </div>
    </article>
  );
}

function ResourceDownloadButton({
  resource,
  iconOnly = false,
}: {
  resource: ResourceGenerationResource;
  iconOnly?: boolean;
}) {
  if (resource.download) {
    return (
      <a
        href={resource.download.url}
        target="_blank"
        rel="noreferrer"
        download={resource.download.fileName}
        className={iconOnly
          ? 'inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-xl text-primary-600 ring-1 ring-blue-100 transition hover:bg-primary-50 dark:text-primary-300 dark:ring-slate-700 dark:hover:bg-primary-500/10'
          : 'inline-flex h-9 w-full items-center justify-center gap-2 rounded-xl bg-primary-50 px-3 text-sm font-semibold text-primary-700 ring-1 ring-blue-100 transition hover:bg-primary-100 dark:bg-primary-500/10 dark:text-primary-200 dark:ring-primary-500/20 dark:hover:bg-primary-500/20'}
        aria-label={`下载${resource.title}`}
        title={`下载${resource.title}`}
      >
        <Download className="h-4 w-4" />
        {iconOnly ? null : '下载资源'}
        {iconOnly ? null : <ExternalLink className="h-3.5 w-3.5" />}
      </a>
    );
  }

  return null;
}

function renderResourcePreview(resource: ResourceGenerationResource) {
  if (resource.video) {
    return <VideoCard {...resource.video} />;
  }
  if (resource.quiz) {
    return <QuizPreview batch={resource.quiz} />;
  }
  if (resource.inline) {
    return <InlinePreview inline={resource.inline} />;
  }
  if (resource.download) {
    return (
      <div className="flex h-full min-h-[150px] items-center justify-center rounded-2xl bg-blue-50/40 px-4 text-center ring-1 ring-blue-100/70 dark:bg-slate-900/40 dark:ring-slate-700/70">
        <div>
          <FileText className="mx-auto h-8 w-8 text-primary-500" />
          <p className="mt-3 text-sm font-semibold text-slate-700 dark:text-slate-200">{resource.download.fileName || resource.title}</p>
          <p className="mt-1 text-xs leading-5 text-slate-500 dark:text-slate-400">{resource.download.expiresHint}</p>
        </div>
      </div>
    );
  }
  return (
    <div className="flex h-full min-h-[150px] items-center justify-center rounded-2xl bg-slate-50 px-4 text-sm text-slate-500 dark:bg-slate-900/60 dark:text-slate-400">
      {resource.statusText || '资源信息已接收，等待内容明细同步'}
    </div>
  );
}

function InlinePreview({ inline }: { inline: InlineResourceView }) {
  if (inline.kind === 'mermaid') {
    return <MermaidDiagram chart={inline.content} />;
  }
  if (inline.kind === 'code') {
    const language = inline.language || 'text';
    return (
      <div className="overflow-hidden rounded-2xl bg-slate-950 ring-1 ring-slate-200/70 dark:ring-slate-700/70">
        <div className="flex items-center justify-between px-4 py-2 text-xs text-slate-300 shadow-[inset_0_-1px_0_rgba(255,255,255,0.10)]">
          <span>{language}</span>
          <Code2 className="h-3.5 w-3.5" />
        </div>
        <pre className="max-h-[300px] overflow-auto p-4 text-xs leading-6 text-slate-100">
          <code>{inline.content}</code>
        </pre>
        {inline.explanation ? (
          <div className="bg-slate-900 px-4 py-3 text-xs leading-5 text-slate-300 shadow-[inset_0_1px_0_rgba(255,255,255,0.10)]">
            {inline.explanation}
          </div>
        ) : null}
      </div>
    );
  }
  return (
    <div className="max-h-[340px] overflow-auto rounded-2xl bg-slate-50/70 p-4 ring-1 ring-slate-200/70 dark:bg-slate-900/50 dark:ring-slate-800/70">
      <MarkdownRenderer content={inline.content} />
    </div>
  );
}

function QuizPreview({ batch }: { batch: ResourceGenerationQuizSummary }) {
  const [hasPracticeSession, setHasPracticeSession] = useState(() => Boolean(getPracticeSessionState().batch));
  useEffect(() => subscribePracticeSession((state) => setHasPracticeSession(Boolean(state.batch))), []);
  return (
    <div className="rounded-2xl bg-amber-50/50 p-4 ring-1 ring-amber-100/80 dark:bg-amber-500/10 dark:ring-amber-500/20">
      <div className="flex items-start gap-3">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-amber-500 text-white">
          <ListChecks className="h-5 w-5" />
        </div>
        <div className="min-w-0">
          <div className="text-sm font-semibold text-amber-900 dark:text-amber-100">练习题已生成</div>
          <p className="mt-1 text-sm leading-6 text-amber-800/85 dark:text-amber-100/85">
            共 {batch.questionCount} 道题，可打开浮动练习助手逐题作答和查看解析。
          </p>
        </div>
      </div>
      <button
        type="button"
        disabled={!hasPracticeSession}
        onClick={() => setPracticeSessionOpen(true)}
        className="mt-3 inline-flex h-9 w-full items-center justify-center gap-2 rounded-xl bg-amber-500 px-3 text-sm font-semibold text-white transition hover:bg-amber-600 disabled:cursor-not-allowed disabled:opacity-50"
      >
        <ListChecks className="h-4 w-4" />
        打开练习助手
      </button>
      {batch.description ? (
        <p className="mt-3 rounded-xl bg-white/65 px-3 py-2 text-xs leading-5 text-amber-800 dark:bg-slate-950/40 dark:text-amber-100">
          {batch.description}
        </p>
      ) : null}
    </div>
  );
}

function FilterButton({
  selected,
  onClick,
  children,
}: {
  selected: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`h-9 rounded-xl px-3 text-xs font-semibold transition ${
        selected
          ? 'bg-primary-600 text-white shadow-lg shadow-primary-500/20'
          : 'bg-slate-100 text-slate-600 hover:bg-primary-50 hover:text-primary-700 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-primary-500/10 dark:hover:text-primary-300'
      }`}
    >
      {children}
    </button>
  );
}

function EmptyState({ selectedType, conversationId }: { selectedType: ResourceFilter; conversationId: string }) {
  return (
    <div className="flex min-h-[260px] items-center justify-center rounded-2xl bg-blue-50/40 px-5 py-10 text-center ring-1 ring-blue-100/70 dark:bg-slate-950/35 dark:ring-slate-700/70">
      <div className="max-w-[520px]">
        <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-2xl bg-white text-primary-600 shadow-sm dark:bg-slate-900 dark:text-primary-300">
          <MessageSquareText className="h-6 w-6" />
        </div>
        <h3 className="mt-4 text-base font-bold text-slate-900 dark:text-white">
          {selectedType === 'ALL' ? '暂无会话资源' : `暂无${resourceLabel(selectedType)}资源`}
        </h3>
        <p className="mt-2 text-sm leading-6 text-slate-500 dark:text-slate-400">
          {conversationId
            ? '在当前对话中直接表达“生成一套学习资源”“做一个 PPT”或“出几道练习题”，这里会自动显示协同生成进度和结果。'
            : '先进入新对话并提出资源生成需求，总览页会同步展示当前会话的生成状态。'}
        </p>
      </div>
    </div>
  );
}

function countResources(resources: ResourceGenerationResource[]): Record<GeneratedResourceType, number> {
  return RESOURCE_TYPES.reduce((accumulator, type) => {
    accumulator[type] = resources.filter((resource) => resource.type === type).length;
    return accumulator;
  }, {} as Record<GeneratedResourceType, number>);
}

function getStatusTone(status: ResourceGenerationSession['taskStatus']) {
  if (status === 'running') {
    return {
      label: '协同生成中',
      className: 'bg-primary-50 text-primary-700 ring-primary-100 dark:bg-primary-500/10 dark:text-primary-300 dark:ring-primary-500/20',
      icon: <Loader2 className="h-3.5 w-3.5 animate-spin" />,
    };
  }
  if (status === 'waiting_confirmation') {
    return {
      label: '等待确认',
      className: 'bg-indigo-50 text-indigo-700 ring-indigo-100 dark:bg-indigo-500/10 dark:text-indigo-300 dark:ring-indigo-500/20',
      icon: <TimerReset className="h-3.5 w-3.5" />,
    };
  }
  if (status === 'completed') {
    return {
      label: '已完成',
      className: 'bg-emerald-50 text-emerald-700 ring-emerald-100 dark:bg-emerald-500/10 dark:text-emerald-300 dark:ring-emerald-500/20',
      icon: <CheckCircle2 className="h-3.5 w-3.5" />,
    };
  }
  if (status === 'failed' || status === 'partial_failed') {
    return {
      label: status === 'failed' ? '生成失败' : '部分完成',
      className: 'bg-amber-50 text-amber-700 ring-amber-100 dark:bg-amber-500/10 dark:text-amber-300 dark:ring-amber-500/20',
      icon: <AlertTriangle className="h-3.5 w-3.5" />,
    };
  }
  return {
    label: '等待对话触发',
    className: 'bg-slate-100 text-slate-600 ring-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:ring-slate-700',
    icon: <Sparkles className="h-3.5 w-3.5" />,
  };
}

function activeAgentStepIndex(session: ResourceGenerationSession): number {
  if (session.taskStatus === 'completed' || session.taskStatus === 'partial_failed') {
    return 3;
  }
  if (session.taskStatus === 'failed') {
    return Math.min(3, Math.max(0, Math.floor(session.progress / 30)));
  }
  if (session.progress >= 70 || session.resources.length > 0) {
    return 2;
  }
  if (session.progress >= 25) {
    return 1;
  }
  if (session.taskStatus === 'running' || session.taskStatus === 'waiting_confirmation') {
    return 0;
  }
  return -1;
}

function isAgentStepCompleted(index: number, session: ResourceGenerationSession): boolean {
  if (session.taskStatus === 'completed' || session.taskStatus === 'partial_failed') {
    return true;
  }
  return index < activeAgentStepIndex(session);
}

function getResourceStatusMeta(resource: ResourceGenerationResource) {
  if (resource.status === 'ready') {
    return {
      label: '已就绪',
      className: 'bg-emerald-50 text-emerald-700 ring-emerald-100 dark:bg-emerald-500/10 dark:text-emerald-300 dark:ring-emerald-500/20',
      icon: <CheckCircle2 className="h-3.5 w-3.5" />,
    };
  }
  if (resource.status === 'waiting_confirmation') {
    return {
      label: '等待确认',
      className: 'bg-indigo-50 text-indigo-700 ring-indigo-100 dark:bg-indigo-500/10 dark:text-indigo-300 dark:ring-indigo-500/20',
      icon: <TimerReset className="h-3.5 w-3.5" />,
    };
  }
  if (resource.status === 'failed') {
    return {
      label: '失败',
      className: 'bg-rose-50 text-rose-700 ring-rose-100 dark:bg-rose-500/10 dark:text-rose-300 dark:ring-rose-500/20',
      icon: <AlertTriangle className="h-3.5 w-3.5" />,
    };
  }
  if (resource.status === 'not_confirmed') {
    return {
      label: '未生成',
      className: 'bg-slate-100 text-slate-600 ring-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:ring-slate-700',
      icon: <AlertTriangle className="h-3.5 w-3.5" />,
    };
  }
  return {
    label: '生成中',
    className: 'bg-primary-50 text-primary-700 ring-primary-100 dark:bg-primary-500/10 dark:text-primary-300 dark:ring-primary-500/20',
    icon: <Loader2 className="h-3.5 w-3.5 animate-spin" />,
  };
}

function estimateRemainingTime(session: ResourceGenerationSession): string {
  if (session.taskStatus === 'idle') {
    return '在对话中提出资源需求后开始。';
  }
  if (session.taskStatus === 'completed') {
    return '已完成。';
  }
  if (session.taskStatus === 'failed') {
    return '已停止，查看资源卡状态原因。';
  }
  if (session.taskStatus === 'waiting_confirmation') {
    return '等待你在对话中确认 PPT 大纲。';
  }
  return session.progress > 0 ? '等待后续生成事件更新。' : '等待后端确认生成进度。';
}

function downloadUnavailableReason(resource: ResourceGenerationResource): string {
  if (resource.status === 'waiting_confirmation') {
    return 'PPT 大纲已生成，确认前不生成演示文稿文件。';
  }
  if (resource.status === 'not_confirmed') {
    return '未确认，未生成 PPT 文件。';
  }
  if (resource.status === 'failed') {
    return resource.failureReason || resource.statusText || '资源生成失败，未产生可下载文件。';
  }
  if (resource.quiz) {
    return '练习题已进入答题弹窗，当前不提供文件下载。';
  }
  if (resource.video?.renderStatus === 'rendering') {
    return resource.video.renderMessage || '视频正在本地渲染，完成后可播放或下载。';
  }
  if (resource.video?.renderStatus === 'failed') {
    return resource.video.renderMessage || '视频生成失败，未产生可下载文件。';
  }
  if (resource.status === 'generating') {
    return resource.statusText || '生成中，等待后续事件同步。';
  }
  return resource.inline ? '后端未签发文件，仅提供在线预览。' : '后端未签发文件，等待后续事件同步。';
}
