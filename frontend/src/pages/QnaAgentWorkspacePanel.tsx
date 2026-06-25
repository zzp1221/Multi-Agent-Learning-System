import { lazy, Suspense, useEffect, useMemo, useState, type ComponentType } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  AlertTriangle,
  BookOpen,
  CheckCircle2,
  ChevronDown,
  Code2,
  Download,
  ExternalLink,
  FileText,
  Film,
  Layers3,
  ListChecks,
  Loader2,
  Network,
  PanelRightClose,
  PanelRightOpen,
  Presentation,
} from 'lucide-react';
import MarkdownRenderer from '../components/MarkdownRenderer';
import VideoCard from '../components/VideoCard';
import { downloadAuthenticatedFile, isInternalArtifactDownloadUrl } from '../utils/authenticatedDownload';
import { ACTIVE_CONVERSATION_ID_STORAGE_KEY } from './LearningStudioDemoPage.model';
import { setPracticeSessionOpen } from './practiceSessionStore';
import {
  RESOURCE_GENERATION_UPDATED_EVENT,
  loadResourceGenerationSession,
  resourceLabel,
  type GeneratedResourceType,
  type ResourceGenerationQuizSummary,
  type ResourceGenerationResource,
  type ResourceGenerationSession,
} from './resourceGenerationStore';
import type { InlineResourceView } from './LearningStudioDemoPage.types';

const MermaidDiagram = lazy(() => import('../components/MermaidDiagram'));

const RESOURCE_META: Record<GeneratedResourceType, { icon: ComponentType<{ className?: string }>; className: string }> = {
  DOCUMENT: { icon: FileText, className: 'is-document' },
  SLIDES: { icon: Presentation, className: 'is-slides' },
  MINDMAP: { icon: Network, className: 'is-mindmap' },
  QUIZ: { icon: ListChecks, className: 'is-quiz' },
  READING: { icon: BookOpen, className: 'is-reading' },
  VIDEO: { icon: Film, className: 'is-video' },
  CODE: { icon: Code2, className: 'is-code' },
};

interface QnaAgentWorkspacePanelProps {
  conversationId: string;
  hasStartedConversation: boolean;
}

export default function QnaAgentWorkspacePanel({
  conversationId,
  hasStartedConversation,
}: QnaAgentWorkspacePanelProps) {
  const navigate = useNavigate();
  const [session, setSession] = useState<ResourceGenerationSession>(() =>
    loadResourceGenerationSession(readActiveConversationId(conversationId)),
  );
  const [expanded, setExpanded] = useState(() =>
    typeof window === 'undefined' || typeof window.matchMedia !== 'function'
      ? true
      : !window.matchMedia('(max-width: 900px)').matches,
  );

  useEffect(() => {
    if (!hasStartedConversation) {
      return;
    }
    setSession(loadResourceGenerationSession(readActiveConversationId(conversationId)));
  }, [conversationId, hasStartedConversation]);

  useEffect(() => {
    if (typeof window === 'undefined') {
      return;
    }

    const refresh = (nextConversationId = readActiveConversationId(conversationId)) => {
      setSession(loadResourceGenerationSession(nextConversationId));
    };
    const handleResourceUpdate = (event: Event) => {
      const detail = (event as CustomEvent<{ conversationId?: string }>).detail;
      refresh(detail?.conversationId?.trim() || readActiveConversationId(conversationId));
    };
    const handleActiveConversationChanged = (event: Event) => {
      const detail = (event as CustomEvent<{ conversationId?: string }>).detail;
      refresh(detail?.conversationId?.trim() ?? '');
    };

    window.addEventListener(RESOURCE_GENERATION_UPDATED_EVENT, handleResourceUpdate as EventListener);
    window.addEventListener('app:active-conversation-changed', handleActiveConversationChanged as EventListener);
    return () => {
      window.removeEventListener(RESOURCE_GENERATION_UPDATED_EVENT, handleResourceUpdate as EventListener);
      window.removeEventListener('app:active-conversation-changed', handleActiveConversationChanged as EventListener);
    };
  }, [conversationId]);

  const visible = hasStartedConversation && isSessionVisible(session);
  const resources = session.resources;
  const readyCount = resources.filter((item) => item.status === 'ready').length;
  const failedCount = resources.filter((item) => item.status === 'failed').length;
  const generatingCount = resources.filter((item) => item.status === 'generating').length;
  const latestResource = resources[resources.length - 1] ?? null;
  const statusTone = workspaceStatusTone(session.taskStatus);

  if (!visible) {
    return null;
  }

  const toggleWorkspace = () => {
    setExpanded((value) => !value);
  };

  return (
    <aside
      className={`qna-agent-workspace ${expanded ? 'is-expanded' : 'is-collapsed'}`}
      data-state={expanded ? 'expanded' : 'collapsed'}
      aria-label="资源工作区"
    >
      <div className="qna-workspace-header">
        <div className="qna-workspace-title-group">
          <span className={`qna-workspace-state ${statusTone.className}`}>
            {statusTone.icon}
            {statusTone.label}
          </span>
          <h2>资源工作区</h2>
          <p>{workspaceSubtitle(session, latestResource)}</p>
        </div>
        <button
          type="button"
          className="qna-workspace-icon-button"
          onClick={toggleWorkspace}
          title={expanded ? '收起资源工作区' : '展开资源工作区'}
          aria-label={expanded ? '收起资源工作区' : '展开资源工作区'}
          aria-expanded={expanded}
        >
          {expanded ? <PanelRightClose className="h-4 w-4" /> : <PanelRightOpen className="h-4 w-4" />}
        </button>
      </div>

      {expanded ? (
        <div className="qna-workspace-body">
          <div className="qna-workspace-progress">
            <div className="qna-workspace-progress-head">
              <span>{session.statusText || statusTone.label}</span>
              <strong>{session.progress}%</strong>
            </div>
            <div className="qna-workspace-progress-bar" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={session.progress}>
              <span style={{ width: `${session.progress}%` }} />
            </div>
          </div>

          <div className="qna-workspace-metrics" aria-label="资源统计">
            <WorkspaceMetric label="就绪" value={readyCount} />
            <WorkspaceMetric label="生成中" value={generatingCount} />
            <WorkspaceMetric label="异常" value={failedCount} />
          </div>

          {resources.length ? (
            <div className="qna-workspace-resource-list">
              {resources.map((resource) => (
                <WorkspaceResourceCard key={resource.id} resource={resource} />
              ))}
            </div>
          ) : (
            <div className="qna-workspace-empty">
              <Layers3 className="h-5 w-5" />
              <span>多 Agent 正在规划资源，产物会在这里出现。</span>
            </div>
          )}

          <button
            type="button"
            className="qna-workspace-open-page"
            onClick={() => navigate('/resources/generation')}
          >
            打开完整资源页
            <ExternalLink className="h-3.5 w-3.5" />
          </button>
        </div>
      ) : null}
    </aside>
  );
}

function WorkspaceMetric({ label, value }: { label: string; value: number }) {
  return (
    <div className="qna-workspace-metric">
      <strong>{value}</strong>
      <span>{label}</span>
    </div>
  );
}

function WorkspaceResourceCard({ resource }: { resource: ResourceGenerationResource }) {
  const [open, setOpen] = useState(false);
  const status = resourceStatusMeta(resource);
  const Icon = RESOURCE_META[resource.type]?.icon ?? FileText;
  const typeClassName = RESOURCE_META[resource.type]?.className ?? 'is-document';
  const canPreview = Boolean(resource.inline || resource.quiz || resource.video || resource.pptistSlides || resource.download);

  useEffect(() => {
    if (resource.status === 'ready' && canPreview) {
      setOpen(true);
    }
  }, [canPreview, resource.status]);

  return (
    <article className={`qna-workspace-resource ${typeClassName}`}>
      <button
        type="button"
        className="qna-workspace-resource-main"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
      >
        <span className="qna-workspace-resource-icon">
          <Icon className="h-4 w-4" />
        </span>
        <span className="qna-workspace-resource-copy">
          <span className="qna-workspace-resource-title">{resource.title}</span>
          <span className="qna-workspace-resource-meta">
            {resourceLabel(resource.type)} · {resourceSubtitle(resource)}
          </span>
        </span>
        <span className={`qna-workspace-resource-status ${status.className}`}>
          {status.icon}
          {status.label}
        </span>
        <ChevronDown className={`qna-workspace-resource-chevron h-4 w-4 ${open ? 'is-open' : ''}`} />
      </button>
      {resource.summary ? (
        <p className="qna-workspace-resource-summary">{resource.summary}</p>
      ) : null}
      {open ? (
        <div className="qna-workspace-resource-preview">
          <WorkspaceResourcePreview resource={resource} />
        </div>
      ) : null}
    </article>
  );
}

function WorkspaceResourcePreview({ resource }: { resource: ResourceGenerationResource }) {
  if (resource.video) {
    return <VideoCard {...resource.video} />;
  }

  if (resource.quiz) {
    return <WorkspaceQuizPreview batch={resource.quiz} />;
  }

  if (resource.inline) {
    return <WorkspaceInlinePreview inline={resource.inline} />;
  }

  if (resource.pptistSlides) {
    return <WorkspacePptPreview slidesJson={resource.pptistSlides} />;
  }

  if (resource.download) {
    return <WorkspaceDownloadPreview resource={resource} />;
  }

  return (
    <div className="qna-workspace-pending-preview">
      {resource.statusText || resource.failureReason || '资源正在整理，完成后会自动更新。'}
    </div>
  );
}

function WorkspaceInlinePreview({ inline }: { inline: InlineResourceView }) {
  if (inline.kind === 'mermaid') {
    return (
      <Suspense fallback={<div className="qna-workspace-pending-preview">图表加载中...</div>}>
        <MermaidDiagram chart={inline.content} />
      </Suspense>
    );
  }

  if (inline.kind === 'code') {
    return (
      <div className="qna-workspace-code-preview">
        <div className="qna-workspace-code-head">
          <span>{inline.language || 'text'}</span>
          <Code2 className="h-3.5 w-3.5" />
        </div>
        <pre>
          <code>{inline.content}</code>
        </pre>
        {inline.explanation ? (
          <div className="qna-workspace-code-note">{inline.explanation}</div>
        ) : null}
      </div>
    );
  }

  return (
    <div className="qna-workspace-markdown-preview">
      <MarkdownRenderer content={inline.content} />
    </div>
  );
}

function WorkspaceQuizPreview({ batch }: { batch: ResourceGenerationQuizSummary }) {
  return (
    <div className="qna-workspace-quiz-preview">
      <div>
        <strong>{batch.title || '练习题'}</strong>
        <span>{batch.topic ? `${batch.topic} · ` : ''}{batch.questionCount} 道题</span>
      </div>
      <button type="button" onClick={() => setPracticeSessionOpen(true)}>
        打开练习助手
      </button>
      {batch.description ? <p>{batch.description}</p> : null}
    </div>
  );
}

function WorkspacePptPreview({ slidesJson }: { slidesJson: string }) {
  const slideCount = useMemo(() => {
    try {
      const parsed = JSON.parse(slidesJson) as { slides?: unknown[] };
      return Array.isArray(parsed.slides) ? parsed.slides.length : 0;
    } catch {
      return 0;
    }
  }, [slidesJson]);

  return (
    <div className="qna-workspace-ppt-preview">
      <Presentation className="h-7 w-7" />
      <span>{slideCount > 0 ? `${slideCount} 页可编辑幻灯片` : 'PPT 课件已生成'}</span>
      <small>完整编辑器在资源生成页打开。</small>
    </div>
  );
}

function WorkspaceDownloadPreview({ resource }: { resource: ResourceGenerationResource }) {
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState('');
  const download = resource.download;

  if (!download) {
    return null;
  }

  const internalDownload = isInternalArtifactDownloadUrl(download.url);
  const handleDownload = async () => {
    if (downloading) {
      return;
    }
    setDownloading(true);
    setError('');
    try {
      await downloadAuthenticatedFile({
        url: download.url,
        fileName: download.fileName,
        title: resource.title,
      });
    } catch (downloadError) {
      setError(downloadError instanceof Error ? downloadError.message : '下载失败，请稍后重试');
    } finally {
      setDownloading(false);
    }
  };

  return (
    <div className="qna-workspace-download-preview">
      <FileText className="h-5 w-5" />
      <div>
        <strong>{download.fileName || resource.title}</strong>
        <span>{download.expiresHint || resource.expiresAt || '临时下载链接'}</span>
      </div>
      <button type="button" disabled={downloading} onClick={() => { void handleDownload(); }}>
        {downloading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
        下载
        {internalDownload ? null : <ExternalLink className="h-3.5 w-3.5" />}
      </button>
      {error ? <p>{error}</p> : null}
    </div>
  );
}

function readActiveConversationId(conversationId: string): string {
  if (conversationId.trim()) {
    return conversationId.trim();
  }
  if (typeof window === 'undefined') {
    return '';
  }
  return window.sessionStorage.getItem(ACTIVE_CONVERSATION_ID_STORAGE_KEY)?.trim() ?? '';
}

function isSessionVisible(session: ResourceGenerationSession): boolean {
  return Boolean(session.conversationTriggered || session.resources.length || session.taskStatus === 'running');
}

function workspaceSubtitle(
  session: ResourceGenerationSession,
  latestResource: ResourceGenerationResource | null,
): string {
  if (latestResource) {
    return `最新：${latestResource.title}`;
  }
  if (session.taskStatus === 'running') {
    return '多 Agent 正在规划学习资源';
  }
  return session.topic || session.title || '当前对话的资源产物';
}

function workspaceStatusTone(status: ResourceGenerationSession['taskStatus']) {
  if (status === 'completed') {
    return { label: '已完成', className: 'is-success', icon: <CheckCircle2 className="h-3.5 w-3.5" /> };
  }
  if (status === 'partial_failed') {
    return { label: '部分完成', className: 'is-warning', icon: <AlertTriangle className="h-3.5 w-3.5" /> };
  }
  if (status === 'failed') {
    return { label: '失败', className: 'is-danger', icon: <AlertTriangle className="h-3.5 w-3.5" /> };
  }
  return { label: status === 'running' ? '生成中' : '待开始', className: 'is-running', icon: <Loader2 className="h-3.5 w-3.5" /> };
}

function resourceStatusMeta(resource: ResourceGenerationResource) {
  if (resource.status === 'ready') {
    return { label: '就绪', className: 'is-success', icon: <CheckCircle2 className="h-3 w-3" /> };
  }
  if (resource.status === 'failed') {
    return { label: '异常', className: 'is-danger', icon: <AlertTriangle className="h-3 w-3" /> };
  }
  return { label: '生成中', className: 'is-running', icon: <Loader2 className="h-3 w-3" /> };
}

function resourceSubtitle(resource: ResourceGenerationResource): string {
  if (resource.status === 'failed') {
    return resource.failureReason || '生成失败';
  }
  if (resource.status === 'generating') {
    return resource.statusText || '正在生成';
  }
  if (resource.pptistSlides) {
    return '可编辑';
  }
  if (resource.download) {
    return '可下载';
  }
  if (resource.inline || resource.quiz || resource.video) {
    return '可预览';
  }
  return '已整理';
}
