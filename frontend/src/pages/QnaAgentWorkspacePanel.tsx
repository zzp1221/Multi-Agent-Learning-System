import { useEffect, useState, type ComponentType } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  AlertTriangle,
  BookOpen,
  CheckCircle2,
  Code2,
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
import { ACTIVE_CONVERSATION_ID_STORAGE_KEY } from './LearningStudioDemoPage.model';
import {
  RESOURCE_GENERATION_UPDATED_EVENT,
  loadResourceGenerationSession,
  resourceLabel,
  type GeneratedResourceType,
  type ResourceGenerationResource,
  type ResourceGenerationSession,
} from './resourceGenerationStore';

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
  const recentResources = resources.slice(-3).reverse();
  const hiddenResourceCount = Math.max(0, resources.length - recentResources.length);
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
      aria-label="本轮产物"
    >
      <div className="qna-workspace-header">
        <div className="qna-workspace-title-group">
          <span className={`qna-workspace-state ${statusTone.className}`}>
            {statusTone.icon}
            {statusTone.label}
          </span>
          <h2>本轮产物</h2>
          <p>{workspaceSubtitle(session, latestResource)}</p>
        </div>
        <button
          type="button"
          className="qna-workspace-icon-button"
          onClick={toggleWorkspace}
          title={expanded ? '收起本轮产物' : '展开本轮产物'}
          aria-label={expanded ? '收起本轮产物' : '展开本轮产物'}
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

          {recentResources.length ? (
            <div className="qna-workspace-resource-list">
              {recentResources.map((resource) => (
                <WorkspaceResourceCard key={resource.id} resource={resource} />
              ))}
              {hiddenResourceCount > 0 ? (
                <div className="qna-workspace-more-hint">
                  还有 {hiddenResourceCount} 个产物，进入资源包查看完整结果。
                </div>
              ) : null}
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
            查看资源包
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
  const status = resourceStatusMeta(resource);
  const Icon = RESOURCE_META[resource.type]?.icon ?? FileText;
  const typeClassName = RESOURCE_META[resource.type]?.className ?? 'is-document';

  return (
    <article className={`qna-workspace-resource ${typeClassName}`}>
      <div className="qna-workspace-resource-main">
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
      </div>
      {resource.summary ? (
        <p className="qna-workspace-resource-summary">{resource.summary}</p>
      ) : null}
    </article>
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
