import { lazy, Suspense, useEffect, useMemo, useState, type ComponentType } from 'react';
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
  RotateCw,
  Sparkles,
} from 'lucide-react';
import { smartEngineApi } from '../api/smartEngine';
import MarkdownRenderer from '../components/MarkdownRenderer';
import MermaidDiagram from '../components/MermaidDiagram';
import VideoCard from '../components/VideoCard';
import { downloadAuthenticatedFile, isInternalArtifactDownloadUrl } from '../utils/authenticatedDownload';
import { renderTalkingVideoInBrowser } from '../utils/browserVideoRenderer';
import { ACTIVE_CONVERSATION_ID_STORAGE_KEY } from './LearningStudioDemoPage.model';
const PPTistEditor = lazy(() => import('../components/PPTistEditor'));
import type { InlineResourceView } from './LearningStudioDemoPage.types';
import {
  RESOURCE_GENERATION_UPDATED_EVENT,
  loadResourceGenerationSession,
  resourceLabel,
  updateResourceVideoRenderResult,
  recordConversationResourceEvent,
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
  DOCUMENT: { icon: FileText, accent: 'bg-sky-50 text-sky-700 dark:bg-sky-500/10 dark:text-sky-300' },
  SLIDES: { icon: Presentation, accent: 'bg-indigo-50 text-indigo-700 dark:bg-indigo-500/10 dark:text-indigo-300' },
  MINDMAP: { icon: Network, accent: 'bg-teal-50 text-teal-700 dark:bg-teal-500/10 dark:text-teal-300' },
  QUIZ: { icon: ListChecks, accent: 'bg-amber-50 text-amber-700 dark:bg-amber-500/10 dark:text-amber-300' },
  READING: { icon: BookOpen, accent: 'bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300' },
  VIDEO: { icon: Film, accent: 'bg-rose-50 text-rose-700 dark:bg-rose-500/10 dark:text-rose-300' },
  CODE: { icon: Code2, accent: 'bg-slate-100 text-slate-700 dark:bg-slate-700/60 dark:text-slate-200' },
};

const AGENT_STEPS = [
  { label: '需求识别', description: '解析对话中的资源目标' },
  { label: '资源规划', description: '规划文档、PPT、练习与视频资源' },
  { label: '内容生成', description: '并行产出学习资源' },
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
                renderMessage: message || '视频生成中',
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
            renderMessage: '视频生成完成，可播放或下载',
          },
          '视频生成完成',
        );
      }).catch(() => {
        updateResourceVideoRenderResult(
          session.conversationId,
          resource.id,
          {
            renderStatus: 'failed',
            renderMessage: '请重新生成视频后再试',
          },
          '视频生成失败',
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
    <div className="resource-generation-page mx-auto max-w-[1200px] px-4 py-6 sm:px-6 sm:py-8">
      <section className="overflow-hidden rounded-[28px] bg-white/76 shadow-[0_18px_56px_rgba(59,97,155,0.09)] backdrop-blur-xl dark:bg-slate-900/68 dark:shadow-slate-950/20">
        <div className="grid gap-5 px-5 py-5 md:grid-cols-[minmax(0,1fr)_280px] md:px-7">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-2xl bg-primary-600 text-white shadow-lg shadow-primary-500/20">
                <Layers3 className="h-5 w-5" />
              </div>
              <div className="min-w-0">
                <h1 className="text-xl font-bold tracking-tight text-slate-950 dark:text-white sm:text-2xl">
                  学习资源工作台
                </h1>
                <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">集中查看对话中生成的学习资源</p>
              </div>
            </div>
            <div className="mt-4 flex flex-wrap items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
              <span className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 font-semibold ${statusTone.className}`}>
                {statusTone.icon}
                {statusTone.label}
              </span>
              {session.topic ? (
                <span className="max-w-full truncate rounded-full bg-primary-50 px-3 py-1.5 text-primary-700 dark:bg-primary-500/10 dark:text-primary-300">
                  主题：{session.topic}
                </span>
              ) : null}
            </div>
          </div>
          <div className="rounded-2xl bg-blue-50/58 p-4 dark:bg-slate-950/34">
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
              已生成 {completedCount} 个资源，生成过程中会自动刷新
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
                <ResourceCard key={resource.id} resource={resource} session={session} />
              ))}
            </div>
          ) : (
            <EmptyState selectedType={selectedType} conversationId={conversationId} session={session} />
          )}

          {session.taskStatus === 'completed' || session.taskStatus === 'partial_failed' ? (
            <div className="mt-5 rounded-2xl bg-emerald-50/70 px-4 py-3 text-sm text-emerald-800 dark:bg-emerald-500/10 dark:text-emerald-200">
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
    <div className={`rounded-2xl p-4 transition-all ${
      active
        ? 'bg-primary-50/80 shadow-sm shadow-primary-100 dark:bg-primary-500/10 dark:shadow-none'
        : completed
          ? 'bg-emerald-50/50 dark:bg-emerald-500/10'
          : 'bg-slate-50/62 dark:bg-slate-950/35'
    }`}
    >
      <div className="flex items-center gap-3">
        <div className={`flex h-9 w-9 items-center justify-center rounded-full ${
          completed
            ? 'bg-emerald-500 text-white'
            : active
              ? 'bg-primary-600 text-white'
              : 'bg-white text-slate-400 dark:bg-slate-900'
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

function ResourceCard({ resource, session }: { resource: ResourceGenerationResource; session: ResourceGenerationSession }) {
  const meta = RESOURCE_META[resource.type];
  const Icon = meta.icon;
  const downloadable = Boolean(resource.download);
  const statusMeta = getResourceStatusMeta(resource);
  return (
    <article className="flex min-h-[260px] flex-col rounded-2xl bg-white/82 p-4 shadow-sm shadow-blue-100/24 transition hover:-translate-y-0.5 hover:bg-white/92 hover:shadow-lg hover:shadow-blue-100/36 dark:bg-slate-950/35 dark:shadow-none">
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-3">
          <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl ${meta.accent}`}>
            <Icon className="h-5 w-5" />
          </div>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="truncate text-base font-bold text-slate-900 dark:text-white">{resource.title}</h3>
              <span className="rounded-full bg-slate-100 px-2.5 py-1 text-xs font-semibold text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                {resourceLabel(resource.type)}
              </span>
              <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-semibold ${statusMeta.className}`}>
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

      <div className="mt-4 flex items-center justify-between gap-3 rounded-xl bg-slate-50/58 px-3 py-2 text-xs text-slate-500 dark:bg-slate-900/42 dark:text-slate-400">
        <span>{new Date(resource.updatedAt).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })} 更新</span>
        <span className="truncate">{resourceSubtitle(resource)}</span>
      </div>
      <div className="mt-3 flex gap-2">
        {resource.pptistSlides ? (
          <PPTistEditorButton resource={resource} />
        ) : downloadable ? (
          <ResourceDownloadButton resource={resource} />
        ) : (
          <div className="rounded-xl bg-slate-50/72 px-3 py-2 text-xs text-slate-500 dark:bg-slate-900/58 dark:text-slate-400">
            {downloadUnavailableReason(resource)}
          </div>
        )}
        {resource.status === 'failed' ? (
          <ResourceRetryButton resource={resource} session={session} />
        ) : null}
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
  const [downloading, setDownloading] = useState(false);

  const download = resource.download;
  if (download) {
    const internalDownload = isInternalArtifactDownloadUrl(download.url);
    const handleDownload = async () => {
      if (downloading) {
        return;
      }
      setDownloading(true);
      try {
        await downloadAuthenticatedFile({
          url: download.url,
          fileName: download.fileName,
          title: resource.title,
        });
      } catch (error) {
        window.alert(error instanceof Error ? error.message : '下载失败，请稍后重试');
      } finally {
        setDownloading(false);
      }
    };

    return (
      <button
        type="button"
        onClick={() => { void handleDownload(); }}
        disabled={downloading}
        className={iconOnly
          ? 'inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-xl text-primary-600 transition hover:bg-primary-50 disabled:cursor-wait disabled:opacity-60 dark:text-primary-300 dark:hover:bg-primary-500/10'
          : 'inline-flex h-9 w-full items-center justify-center gap-2 rounded-xl bg-primary-50 px-3 text-sm font-semibold text-primary-700 transition hover:bg-primary-100 disabled:cursor-wait disabled:opacity-60 dark:bg-primary-500/10 dark:text-primary-200 dark:hover:bg-primary-500/20'}
        aria-label={`下载${resource.title}`}
        title={`下载${resource.title}`}
      >
        {downloading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
        {iconOnly ? null : '下载资源'}
        {iconOnly || internalDownload ? null : <ExternalLink className="h-3.5 w-3.5" />}
      </button>
    );
  }

  return null;
}

function buildResourceRetryParams(
  resource: ResourceGenerationResource,
  session: ResourceGenerationSession,
): Record<string, unknown> {
  const base = session.requestParams || {};
  return {
    ...base,
    topic: readText(base.topic) || session.topic || session.title || resource.title,
    query: readText(base.query) || session.topic || resource.title,
    resourceTypes: [resource.type],
  };
}

function readText(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function ResourceRetryButton({
  resource,
  session,
}: {
  resource: ResourceGenerationResource;
  session: ResourceGenerationSession;
}) {
  const [retrying, setRetrying] = useState(false);
  const retryParams = resource.retryParams || buildResourceRetryParams(resource, session);
  const canRetry = Boolean(session.conversationId);

  const handleRetry = async () => {
    if (retrying || !canRetry) {
      return;
    }
    setRetrying(true);
    try {
      const response = await smartEngineApi.submit({
        conversationId: session.conversationId,
        serviceType: 'RESOURCE_GENERATION',
        params: retryParams,
      });
      await smartEngineApi.streamTask(response.taskId, {
        onEvent: (event) => {
          recordConversationResourceEvent(session.conversationId, event.event, {
            event: event.event,
            seq: event.envelope.seq ?? 0,
            payload: {
              ...(event.payload ?? {}),
              taskId: response.taskId,
              params: retryParams,
            },
          });
        },
        onDone: () => undefined,
        onError: (error) => {
          recordConversationResourceEvent(session.conversationId, 'error', {
            event: 'error',
            seq: 0,
            payload: {
              taskId: response.taskId,
              message: error.message,
              resourceFailures: [{
                resourceType: resource.type,
                title: resource.title,
                error: error.message,
              }],
            },
          });
        },
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : '重新生成失败';
      recordConversationResourceEvent(session.conversationId, 'error', {
        event: 'error',
        seq: 0,
        payload: {
          message,
          resourceFailures: [{
            resourceType: resource.type,
            title: resource.title,
            error: message,
          }],
        },
      });
    } finally {
      setRetrying(false);
    }
  };

  return (
    <button
      type="button"
      onClick={() => { void handleRetry(); }}
      disabled={!canRetry || retrying}
      className="inline-flex h-9 shrink-0 items-center justify-center gap-2 rounded-xl bg-rose-50 px-3 text-sm font-semibold text-rose-700 transition hover:bg-rose-100 disabled:cursor-not-allowed disabled:opacity-60 dark:bg-rose-500/10 dark:text-rose-200 dark:hover:bg-rose-500/20"
    >
      {retrying ? <Loader2 className="h-4 w-4 animate-spin" /> : <RotateCw className="h-4 w-4" />}
      重新生成
    </button>
  );
}

function PPTistEditorButton({ resource }: { resource: ResourceGenerationResource }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="inline-flex h-9 w-full items-center justify-center gap-2 rounded-xl bg-indigo-50 px-3 text-sm font-semibold text-indigo-700 transition hover:bg-indigo-100 dark:bg-indigo-500/10 dark:text-indigo-200 dark:hover:bg-indigo-500/20"
      >
        <Presentation className="h-4 w-4" />
        在浏览器中编辑
      </button>
      {open && resource.pptistSlides ? (
        <Suspense fallback={null}>
          <PPTistEditor
            slidesJson={resource.pptistSlides}
            title={resource.title}
            onClose={() => setOpen(false)}
          />
        </Suspense>
      ) : null}
    </>
  );
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
  if (resource.pptistSlides) {
    let slideCount = 0;
    try {
      slideCount = JSON.parse(resource.pptistSlides).slides?.length ?? 0;
    } catch { /* ignore */ }
    return (
      <div className="flex h-full min-h-[150px] items-center justify-center rounded-2xl bg-indigo-50/40 px-4 text-center dark:bg-slate-900/40">
        <div>
          <Presentation className="mx-auto h-12 w-12 text-indigo-500" />
          <p className="mt-3 text-sm font-semibold text-slate-700 dark:text-slate-200">
            {slideCount > 0 ? `${slideCount} 页可编辑幻灯片` : 'PPT 课件'}
          </p>
          <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">点击下方按钮进入全屏编辑器</p>
        </div>
      </div>
    );
  }
  if (resource.download) {
    return (
      <div className="flex h-full min-h-[150px] items-center justify-center rounded-2xl bg-blue-50/40 px-4 text-center dark:bg-slate-900/40">
        <div>
          <FileText className="mx-auto h-8 w-8 text-primary-500" />
          <p className="mt-3 text-sm font-semibold text-slate-700 dark:text-slate-200">{resource.download.fileName || resource.title}</p>
          <p className="mt-1 text-xs leading-5 text-slate-500 dark:text-slate-400">文件已准备好，可直接下载</p>
        </div>
      </div>
    );
  }
  return (
    <div className="flex h-full min-h-[150px] items-center justify-center rounded-2xl bg-slate-50 px-4 text-sm text-slate-500 dark:bg-slate-900/60 dark:text-slate-400">
      {resource.statusText || '资源正在整理，完成后会自动更新'}
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
      <div className="overflow-hidden rounded-2xl bg-slate-950 shadow-sm shadow-slate-900/18 dark:shadow-slate-950/40">
        <div className="flex items-center justify-between bg-white/5 px-4 py-2 text-xs text-slate-300">
          <span>{language}</span>
          <Code2 className="h-3.5 w-3.5" />
        </div>
        <pre className="max-h-[300px] overflow-auto p-4 text-xs leading-6 text-slate-100">
          <code>{inline.content}</code>
        </pre>
        {inline.explanation ? (
          <div className="mx-3 mb-3 rounded-xl bg-white/6 px-3 py-2.5 text-xs leading-5 text-slate-300">
            {inline.explanation}
          </div>
        ) : null}
      </div>
    );
  }
  return (
    <div className="max-h-[340px] overflow-auto rounded-2xl bg-slate-50/64 p-4 dark:bg-slate-900/50">
      <MarkdownRenderer content={inline.content} />
    </div>
  );
}

function QuizPreview({ batch }: { batch: ResourceGenerationQuizSummary }) {
  const [hasPracticeSession, setHasPracticeSession] = useState(() => Boolean(getPracticeSessionState().batch));
  useEffect(() => subscribePracticeSession((state) => setHasPracticeSession(Boolean(state.batch))), []);
  return (
    <div className="rounded-2xl bg-amber-50/50 p-4 dark:bg-amber-500/10">
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

function EmptyState({
  selectedType,
  conversationId,
  session,
}: {
  selectedType: ResourceFilter;
  conversationId: string;
  session: ResourceGenerationSession;
}) {
  if (session.taskStatus === 'running') {
    const activeStep = AGENT_STEPS[Math.max(0, activeAgentStepIndex(session))] ?? AGENT_STEPS[0];
    const progress = Math.min(96, Math.max(8, session.progress || 8));
    const skeletonLabels = selectedType === 'ALL'
      ? ['文档资源', '课件资源', '练习资源']
      : [`${resourceLabel(selectedType)}资源`, '内容预览', '下载链接'];
    return (
      <div className="resource-generation-stream-empty rounded-2xl bg-primary-50/40 p-5 dark:bg-primary-500/10">
        <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
          <div className="min-w-0">
            <div className="inline-flex items-center gap-2 rounded-full bg-white/80 px-3 py-1.5 text-xs font-semibold text-primary-700 shadow-sm dark:bg-slate-900/70 dark:text-primary-200">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              {activeStep.label}
            </div>
            <h3 className="mt-3 text-base font-bold text-slate-900 dark:text-white">
              多 Agent 正在生成资源
            </h3>
            <p className="mt-1 max-w-2xl text-sm leading-6 text-slate-500 dark:text-slate-400">
              {session.statusText || activeStep.description}
            </p>
          </div>
          <div className="w-full md:w-56">
            <div className="flex items-center justify-between text-xs font-semibold text-slate-500 dark:text-slate-400">
              <span>当前进度</span>
              <span className="text-primary-700 dark:text-primary-200">{progress}%</span>
            </div>
            <div className="mt-2 h-2 overflow-hidden rounded-full bg-white shadow-inner dark:bg-slate-900">
              <div className="h-full rounded-full bg-primary-600 transition-all duration-500" style={{ width: `${progress}%` }} />
            </div>
          </div>
        </div>
        <div className="mt-5 grid gap-3 md:grid-cols-3">
          {skeletonLabels.map((label, index) => (
            <div key={label} className="rounded-2xl bg-white/76 p-4 shadow-sm dark:bg-slate-950/35">
              <div className="flex items-center gap-3">
                <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary-50 text-primary-700 dark:bg-primary-500/10 dark:text-primary-200">
                  {index + 1}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="h-3 w-24 rounded-full bg-slate-200/90 dark:bg-slate-700/70" />
                  <div className="mt-2 h-2 w-32 rounded-full bg-slate-100 dark:bg-slate-800" />
                </div>
              </div>
              <p className="mt-4 text-xs font-semibold text-slate-500 dark:text-slate-400">{label}</p>
            </div>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-[260px] items-center justify-center rounded-2xl bg-blue-50/40 px-5 py-10 text-center dark:bg-slate-950/35">
      <div className="max-w-[520px]">
        <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-2xl bg-white text-primary-600 shadow-sm dark:bg-slate-900 dark:text-primary-300">
          <MessageSquareText className="h-6 w-6" />
        </div>
        <h3 className="mt-4 text-base font-bold text-slate-900 dark:text-white">
          {selectedType === 'ALL' ? '暂无学习资源' : `暂无${resourceLabel(selectedType)}资源`}
        </h3>
        <p className="mt-2 text-sm leading-6 text-slate-500 dark:text-slate-400">
          {conversationId
            ? '在当前对话中直接表达“生成一套学习资源”“做一个 PPT”或“出几道练习题”，这里会自动展示进度和结果。'
            : '先进入新对话并提出资源生成需求，这里会自动展示可预览和可下载的学习资源。'}
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
      className: 'bg-primary-50 text-primary-700 dark:bg-primary-500/10 dark:text-primary-300',
      icon: <Loader2 className="h-3.5 w-3.5 animate-spin" />,
    };
  }
  if (status === 'completed') {
    return {
      label: '已完成',
      className: 'bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300',
      icon: <CheckCircle2 className="h-3.5 w-3.5" />,
    };
  }
  if (status === 'failed' || status === 'partial_failed') {
    return {
      label: status === 'failed' ? '生成失败' : '部分完成',
      className: 'bg-amber-50 text-amber-700 dark:bg-amber-500/10 dark:text-amber-300',
      icon: <AlertTriangle className="h-3.5 w-3.5" />,
    };
  }
  return {
    label: '等待对话触发',
    className: 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300',
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
  if (session.taskStatus === 'running') {
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
      className: 'bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300',
      icon: <CheckCircle2 className="h-3.5 w-3.5" />,
    };
  }
  if (resource.status === 'failed') {
    return {
      label: '失败',
      className: 'bg-rose-50 text-rose-700 dark:bg-rose-500/10 dark:text-rose-300',
      icon: <AlertTriangle className="h-3.5 w-3.5" />,
    };
  }
  return {
    label: '生成中',
    className: 'bg-primary-50 text-primary-700 dark:bg-primary-500/10 dark:text-primary-300',
    icon: <Loader2 className="h-3.5 w-3.5 animate-spin" />,
  };
}

function resourceSubtitle(resource: ResourceGenerationResource): string {
  if (resource.status === 'failed' && resource.failureReason) {
    return resource.failureReason;
  }
  if (resource.status === 'ready') {
    return resource.pptistSlides ? '可在线编辑' : resource.download ? '可下载' : '可在线预览';
  }
  if (resource.status === 'failed') {
    return '需要重新生成';
  }
  return '正在生成';
}

function estimateRemainingTime(session: ResourceGenerationSession): string {
  if (session.taskStatus === 'idle') {
    return '在对话中提出资源需求后开始。';
  }
  if (session.taskStatus === 'completed') {
    return '已完成。';
  }
  if (session.taskStatus === 'failed') {
    return '已停止，可重新发起生成。';
  }
  return session.progress > 0 ? '正在整理生成进度。' : '等待开始生成。';
}

function downloadUnavailableReason(resource: ResourceGenerationResource): string {
  if (resource.status === 'failed' && resource.failureReason) {
    return resource.failureReason;
  }
  if (resource.status === 'failed') {
    return '资源生成失败，可在对话中重新生成。';
  }
  if (resource.quiz) {
    return '练习题已进入答题弹窗，当前不提供文件下载。';
  }
  if (resource.video?.renderStatus === 'rendering') {
    return resource.video.renderMessage || '视频正在生成，完成后可播放或下载。';
  }
  if (resource.video?.renderStatus === 'failed') {
    return resource.video.renderMessage || '视频生成失败，请重新生成后再试。';
  }
  if (resource.status === 'generating') {
    return resource.statusText || '正在生成，完成后会自动更新。';
  }
  return resource.inline ? '当前内容可在线预览。' : '资源正在整理，完成后会自动更新。';
}
