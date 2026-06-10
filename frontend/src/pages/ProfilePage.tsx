import { useCallback, useEffect, useMemo, type ReactNode, useState } from 'react';
import { useOutletContext, useSearchParams } from 'react-router-dom';
import {
  CalendarClock,
  GitBranch,
  LineChart,
  LoaderCircle,
  Lock,
  Network,
  Play,
  RefreshCw,
  Target,
  TriangleAlert,
  UserRoundSearch,
} from 'lucide-react';
import RadarChart from '../components/RadarChart';
import { getErrorMessage } from '../api/request';
import {
  smartEngineApi,
  type KnowledgeGraphNode,
  type KnowledgeGraphResponse,
  type ProfileBehaviorTrendPoint,
  type UserProfileAnalyticsResponse,
} from '../api/smartEngine';
import { conversationApi } from '../api/conversation';
import { smartEngineApi as engineApi } from '../api/smartEngine';
import { readStreamPayload } from '../api/sse';
import { studyWorkbenchApi, type KnowledgeNodeDetailResponse } from '../api/studyWorkbench';
import type { LayoutOutletContext } from '../components/Layout';
import {
  EMPTY_VALUE,
  type PracticeQuestionBatch,
  type ProfileSnapshot,
  type WeakPointRank,
} from './LearningStudioDemoPage.types';
import { mapProfileResponse, readPracticeQuestionBatch } from './LearningStudioDemoPage.utils';
import { openPracticeSession } from './practiceSessionStore';

export default function ProfilePage() {
  const { isAuthenticated, currentUser, openAuthModal } = useOutletContext<LayoutOutletContext>();
  const [searchParams, setSearchParams] = useSearchParams();
  const [profile, setProfile] = useState<ProfileSnapshot | null>(null);
  const [updatedAt, setUpdatedAt] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [analytics, setAnalytics] = useState<UserProfileAnalyticsResponse | null>(null);
  const [analyticsLoading, setAnalyticsLoading] = useState(false);
  const [analyticsError, setAnalyticsError] = useState('');
  const [knowledgeGraph, setKnowledgeGraph] = useState<KnowledgeGraphResponse | null>(null);
  const [knowledgeLoading, setKnowledgeLoading] = useState(false);
  const [knowledgeError, setKnowledgeError] = useState('');
  const [selectedNodeKey, setSelectedNodeKey] = useState(searchParams.get('node') ?? '');
  const [nodeDetail, setNodeDetail] = useState<KnowledgeNodeDetailResponse | null>(null);
  const [nodeDetailLoading, setNodeDetailLoading] = useState(false);
  const [nodeDetailError, setNodeDetailError] = useState('');
  const [practiceGeneratingKey, setPracticeGeneratingKey] = useState('');

  const loadProfile = useCallback(async () => {
    if (!isAuthenticated || !currentUser) {
      setProfile(null);
      setUpdatedAt('');
      return;
    }
    setLoading(true);
    setError('');
    try {
      const response = await smartEngineApi.getCurrentProfile(String(currentUser.id));
      const hasProfilePayload = Boolean(response.profile && Object.keys(response.profile).length > 0);
      setProfile(hasProfilePayload ? mapProfileResponse(response) : null);
      setUpdatedAt(response.updatedAt ?? '');
    } catch (loadError) {
      setProfile(null);
      setUpdatedAt('');
      setError(getErrorMessage(loadError));
    } finally {
      setLoading(false);
    }
  }, [currentUser, isAuthenticated]);

  const loadAnalytics = useCallback(async () => {
    if (!isAuthenticated || !currentUser) {
      setAnalytics(null);
      setAnalyticsError('');
      return;
    }
    setAnalyticsLoading(true);
    setAnalyticsError('');
    try {
      const response = await smartEngineApi.getProfileAnalytics(String(currentUser.id), 30);
      setAnalytics(response);
    } catch (loadError) {
      setAnalytics(null);
      setAnalyticsError(getErrorMessage(loadError));
    } finally {
      setAnalyticsLoading(false);
    }
  }, [currentUser, isAuthenticated]);

  const loadKnowledgeGraph = useCallback(async () => {
    if (!isAuthenticated || !currentUser) {
      setKnowledgeGraph(null);
      setKnowledgeError('');
      return;
    }
    setKnowledgeLoading(true);
    setKnowledgeError('');
    try {
      setKnowledgeGraph(await smartEngineApi.getKnowledgeGraph(String(currentUser.id)));
    } catch (loadError) {
      setKnowledgeGraph(null);
      setKnowledgeError(getErrorMessage(loadError));
    } finally {
      setKnowledgeLoading(false);
    }
  }, [currentUser, isAuthenticated]);

  const loadNodeDetail = useCallback(async (nodeKey: string) => {
    if (!isAuthenticated || !currentUser || !nodeKey) {
      setNodeDetail(null);
      setNodeDetailError('');
      return;
    }
    setNodeDetailLoading(true);
    setNodeDetailError('');
    try {
      setNodeDetail(await studyWorkbenchApi.knowledgeNodeDetail(String(currentUser.id), nodeKey));
    } catch (loadError) {
      setNodeDetail(null);
      setNodeDetailError(getErrorMessage(loadError));
    } finally {
      setNodeDetailLoading(false);
    }
  }, [currentUser, isAuthenticated]);

  useEffect(() => {
    void loadProfile();
    void loadAnalytics();
    void loadKnowledgeGraph();
  }, [loadAnalytics, loadKnowledgeGraph, loadProfile]);

  useEffect(() => {
    const handleProfileUpdated = () => {
      void loadProfile();
      void loadAnalytics();
      void loadKnowledgeGraph();
    };
    window.addEventListener('app:profile-updated', handleProfileUpdated);
    return () => window.removeEventListener('app:profile-updated', handleProfileUpdated);
  }, [loadAnalytics, loadKnowledgeGraph, loadProfile]);

  useEffect(() => {
    const nodeKey = searchParams.get('node') ?? '';
    setSelectedNodeKey(nodeKey);
  }, [searchParams]);

  useEffect(() => {
    if (selectedNodeKey) {
      void loadNodeDetail(selectedNodeKey);
    } else {
      setNodeDetail(null);
      setNodeDetailError('');
    }
  }, [loadNodeDetail, selectedNodeKey]);

  const displayName = currentUser?.fullName || currentUser?.loginId || currentUser?.username || '同学';
  const weakPointItems = useMemo(() => profile ? buildWeakPointItems(profile).slice(0, 3) : [], [profile]);
  const trendSummary = useMemo(() => buildTrendSummary(analytics), [analytics]);

  const selectKnowledgeNode = (nodeKey: string) => {
    const next = new URLSearchParams(searchParams);
    if (nodeKey) {
      next.set('node', nodeKey);
    } else {
      next.delete('node');
    }
    setSearchParams(next, { replace: true });
  };

  const startNodePractice = async (detail: KnowledgeNodeDetailResponse) => {
    if (!currentUser || practiceGeneratingKey) {
      return;
    }
    setPracticeGeneratingKey(detail.node.key);
    setNodeDetailError('');
    let receivedBatch: PracticeQuestionBatch | null = null;
    try {
      const conversation = await conversationApi.createConversation();
      const response = await engineApi.submit({
        conversationId: conversation.conversationId,
        serviceType: 'PRACTICE_JUDGE',
        params: {
          topic: detail.node.topic,
          query: `${detail.node.topic} 知识点针对性练习`,
          count: 5,
          questionCount: 5,
          learningContext: {
            ...detail.practiceContext,
            chapter: detail.node.topic,
            questionCount: 5,
          },
        },
      });
      await engineApi.streamTask(response.taskId, {
        onEvent: (event) => {
          if (event.event === 'question_batch') {
            const batch = readPracticeQuestionBatch(event.payload ?? readStreamPayload(event.data));
            if (batch) {
              receivedBatch = batch;
            }
          }
          if (event.event === 'error') {
            setNodeDetailError('练习生成失败，请稍后重试');
          }
        },
        onDone: () => undefined,
        onError: (streamError) => setNodeDetailError(getErrorMessage(streamError)),
      });
      if (!receivedBatch) {
        const task = await engineApi.getTask(response.taskId, { dedupe: false, retry: 2 });
        receivedBatch = readPracticeQuestionBatch(task.responseSummary);
      }
      if (!receivedBatch) {
        throw new Error('未收到完整练习题目');
      }
      openPracticeSession({
        batch: receivedBatch,
        source: 'engine',
        ownerUserId: currentUser.id,
        phaseId: detail.node.key,
        phaseTitle: detail.node.topic,
        conversationId: conversation.conversationId,
      });
    } catch (practiceError) {
      setNodeDetailError(getErrorMessage(practiceError));
    } finally {
      setPracticeGeneratingKey('');
    }
  };

  if (!isAuthenticated) {
    return (
      <ProfileShell>
        <ProfileAccessState
          icon={<Lock className="h-6 w-6" />}
          title="登录后查看学习画像"
          description="登录后查看你的学习节奏、能力维度和重点关注内容。"
          actionLabel="去登录"
          onAction={() => openAuthModal('login', '登录后查看学习画像')}
        />
      </ProfileShell>
    );
  }

  if (loading && !profile) {
    return (
      <ProfileShell>
        <div className="flex min-h-[420px] items-center justify-center rounded-[24px] bg-white/64 text-sm text-slate-500 shadow-[0_14px_40px_rgba(59,97,155,0.08)] backdrop-blur dark:bg-slate-900/60 dark:text-slate-400 dark:shadow-slate-950/20">
          <LoaderCircle className="mr-2 h-4 w-4 animate-spin text-primary-500" />
          正在整理学习画像
        </div>
      </ProfileShell>
    );
  }

  if (error) {
    return (
      <ProfileShell>
        <ProfileAccessState
          icon={<TriangleAlert className="h-6 w-6" />}
          title="画像读取失败"
          description={error}
          actionLabel="重新加载"
          onAction={() => {
            void loadProfile();
            void loadAnalytics();
          }}
        />
      </ProfileShell>
    );
  }

  if (!profile) {
    return (
      <ProfileShell>
        <ProfileAccessState
          icon={<UserRoundSearch className="h-6 w-6" />}
          title="暂无学习画像"
          description="完成对话、练习或学习服务后，系统会逐步补全画像。"
          actionLabel="刷新画像"
          onAction={() => {
            void loadProfile();
            void loadAnalytics();
          }}
        />
      </ProfileShell>
    );
  }

  return (
    <ProfileShell>
      <div className="min-w-0 space-y-5">
        <header className="rounded-[28px] bg-white/72 px-5 py-5 shadow-[0_18px_56px_rgba(59,97,155,0.10)] backdrop-blur-xl dark:bg-slate-900/68 dark:shadow-slate-950/20 md:px-6">
          <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
            <div className="min-w-0">
              <div className="inline-flex items-center gap-2 rounded-full bg-primary-50 px-3 py-1 text-xs font-semibold text-primary-700 dark:bg-primary-500/10 dark:text-primary-300">
                <UserRoundSearch className="h-3.5 w-3.5" />
                学习画像
              </div>
              <h1 className="mt-4 text-2xl font-semibold tracking-normal text-slate-950 dark:text-white md:text-3xl">
                {displayName}，这是你的学习信号面板
              </h1>
              <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-500 dark:text-slate-400">
                这里汇总近期学习趋势、能力维度和最需要关注的薄弱点，便于快速判断当前学习状态。
              </p>
            </div>
            <div className="flex flex-col items-start gap-3 sm:flex-row sm:items-center md:flex-col md:items-end">
              <button
                type="button"
                onClick={() => {
                  void loadProfile();
                  void loadAnalytics();
                  void loadKnowledgeGraph();
                }}
                className="inline-flex h-10 items-center justify-center gap-2 rounded-xl bg-primary-600 px-4 text-sm font-medium text-white shadow-sm shadow-primary-500/20 outline-none transition-all hover:bg-primary-700 focus-visible:shadow-[0_10px_24px_rgba(59,130,246,0.24)] disabled:cursor-not-allowed disabled:opacity-60 dark:focus-visible:shadow-[0_10px_24px_rgba(37,99,235,0.24)]"
                disabled={loading || analyticsLoading}
              >
                {loading || analyticsLoading ? (
                  <LoaderCircle className="h-4 w-4 animate-spin" />
                ) : (
                  <CalendarClock className="h-4 w-4" />
                )}
                刷新画像
              </button>
              <div className="text-xs text-slate-400 dark:text-slate-500">
                更新时间：{updatedAt ? new Date(updatedAt).toLocaleString('zh-CN') : EMPTY_VALUE}
              </div>
            </div>
          </div>
        </header>

        <BehaviorTrendPanel
          analytics={analytics}
          loading={analyticsLoading}
          error={analyticsError}
          summary={trendSummary}
          onRetry={() => void loadAnalytics()}
        />

        <KnowledgeGraphInteractionPanel
          graph={knowledgeGraph}
          loading={knowledgeLoading}
          error={knowledgeError}
          selectedNodeKey={selectedNodeKey}
          detail={nodeDetail}
          detailLoading={nodeDetailLoading}
          detailError={nodeDetailError}
          practiceGeneratingKey={practiceGeneratingKey}
          onSelectNode={selectKnowledgeNode}
          onRetry={() => void loadKnowledgeGraph()}
          onStartPractice={startNodePractice}
        />

        <div className="grid min-w-0 gap-5 xl:grid-cols-[minmax(0,1.08fr)_minmax(360px,0.92fr)]">
          <DimensionPanel profile={profile} />
          <WeakPointTopThree items={weakPointItems} />
        </div>
      </div>
    </ProfileShell>
  );
}

function ProfileShell({ children }: { children: ReactNode }) {
  return (
    <div className="profile-page mx-auto w-full max-w-[1280px] min-w-0 px-1 pb-10">
      {children}
    </div>
  );
}

function PanelShell({
  id,
  children,
  className = '',
}: {
  id?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      id={id}
      className={`min-w-0 rounded-[24px] bg-white/66 p-5 shadow-[0_14px_40px_rgba(59,97,155,0.08)] backdrop-blur dark:bg-slate-900/62 dark:shadow-slate-950/20 md:p-6 ${className}`}
    >
      {children}
    </section>
  );
}

function SectionTitle({ icon, title, subtitle }: { icon: ReactNode; title: string; subtitle: string }) {
  return (
    <div className="flex items-start gap-3">
      <div className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-200">
        {icon}
      </div>
      <div className="min-w-0">
        <h2 className="text-lg font-semibold text-slate-950 dark:text-white">{title}</h2>
        <p className="mt-1 text-sm leading-6 text-slate-500 dark:text-slate-400">{subtitle}</p>
      </div>
    </div>
  );
}

function KnowledgeGraphInteractionPanel(props: {
  graph: KnowledgeGraphResponse | null;
  loading: boolean;
  error: string;
  selectedNodeKey: string;
  detail: KnowledgeNodeDetailResponse | null;
  detailLoading: boolean;
  detailError: string;
  practiceGeneratingKey: string;
  onSelectNode: (nodeKey: string) => void;
  onRetry: () => void;
  onStartPractice: (detail: KnowledgeNodeDetailResponse) => void;
}) {
  const nodes = props.graph?.nodes ?? [];
  const selectedNode = nodes.find((node) => node.key === props.selectedNodeKey) ?? nodes[0] ?? null;

  return (
    <PanelShell id="knowledge-graph">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <SectionTitle
          icon={<Network className="h-5 w-5" />}
          title="知识图谱交互"
          subtitle="点击知识点查看前置/后续关系、相关错题、资源和针对性练习。"
        />
        <button
          type="button"
          onClick={props.onRetry}
          disabled={props.loading}
          className="inline-flex h-10 items-center justify-center gap-2 rounded-xl bg-white px-3 text-sm font-medium text-slate-600 shadow-sm shadow-slate-200/60 transition hover:bg-primary-50 hover:text-primary-700 disabled:cursor-not-allowed disabled:opacity-60 dark:bg-slate-950/50 dark:text-slate-300 dark:shadow-none"
        >
          {props.loading ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
          刷新图谱
        </button>
      </div>

      <div className="mt-6 min-h-[300px]">
        {props.loading && !nodes.length ? (
          <AnalyticsStateMessage text="正在读取知识图谱" />
        ) : props.error ? (
          <div className="rounded-2xl bg-red-50/80 p-4 text-sm text-red-700 dark:bg-red-950/30 dark:text-red-200">
            {props.error}
          </div>
        ) : nodes.length ? (
          <div className="grid gap-5 xl:grid-cols-[minmax(0,0.95fr)_minmax(360px,1.05fr)]">
            <div className="grid max-h-[520px] gap-3 overflow-auto pr-1 md:grid-cols-2 xl:grid-cols-1">
              {nodes.map((node) => (
                <button
                  key={node.key}
                  type="button"
                  onClick={() => props.onSelectNode(node.key)}
                  className={`rounded-2xl p-4 text-left transition ${
                    node.key === props.selectedNodeKey
                      ? 'bg-primary-50 text-primary-800 shadow-sm shadow-primary-100/60 dark:bg-primary-500/10 dark:text-primary-200 dark:shadow-none'
                      : 'bg-slate-50/78 text-slate-700 hover:bg-primary-50/60 dark:bg-slate-950/30 dark:text-slate-300 dark:hover:bg-primary-500/5'
                  }`}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <h3 className="truncate text-sm font-semibold">{node.topic}</h3>
                      <p className="mt-1 text-xs opacity-70">{knowledgeStatusLabel(node.status)} · {node.source}</p>
                    </div>
                    <span className="shrink-0 text-sm font-semibold">{Math.round(node.mastery * 100)}%</span>
                  </div>
                  <div className="mt-3 h-2 overflow-hidden rounded-full bg-white dark:bg-slate-800">
                    <div
                      className={`h-full rounded-full ${node.status === 'MASTERED' ? 'bg-emerald-500' : node.status === 'WEAK' ? 'bg-amber-500' : 'bg-primary-500'}`}
                      style={{ width: `${Math.max(2, Math.round(node.mastery * 100))}%` }}
                    />
                  </div>
                </button>
              ))}
            </div>
            <KnowledgeNodeDetailCard
              detail={props.detail}
              loading={props.detailLoading}
              error={props.detailError}
              fallbackNode={selectedNode}
              practiceGeneratingKey={props.practiceGeneratingKey}
              onStartPractice={props.onStartPractice}
            />
          </div>
        ) : (
          <EmptyInline text="暂无知识图谱数据。完成练习、阶段测试或画像生成后会自动沉淀知识点。" />
        )}
      </div>
    </PanelShell>
  );
}

function KnowledgeNodeDetailCard(props: {
  detail: KnowledgeNodeDetailResponse | null;
  loading: boolean;
  error: string;
  fallbackNode: KnowledgeGraphNode | null;
  practiceGeneratingKey: string;
  onStartPractice: (detail: KnowledgeNodeDetailResponse) => void;
}) {
  if (props.loading) {
    return <AnalyticsStateMessage text="正在读取知识点详情" />;
  }
  if (props.error) {
    return (
      <div className="rounded-2xl bg-red-50/80 p-4 text-sm text-red-700 dark:bg-red-950/30 dark:text-red-200">
        {props.error}
      </div>
    );
  }
  const detail = props.detail;
  const node = detail?.node ?? props.fallbackNode;
  if (!node) {
    return <EmptyInline text="请选择一个知识点查看详情。" />;
  }
  const busy = props.practiceGeneratingKey === node.key;
  return (
    <article className="rounded-2xl bg-slate-50/78 p-5 dark:bg-slate-950/32">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="inline-flex items-center gap-2 rounded-full bg-white px-3 py-1 text-xs font-semibold text-slate-500 shadow-sm shadow-slate-200/50 dark:bg-slate-900 dark:text-slate-300 dark:shadow-none">
            <GitBranch className="h-3.5 w-3.5" />
            {knowledgeStatusLabel(node.status)}
          </div>
          <h3 className="mt-3 text-xl font-semibold text-slate-950 dark:text-white">{node.topic}</h3>
          <p className="mt-2 text-sm text-slate-500 dark:text-slate-400">掌握度 {Math.round(node.mastery * 100)}%，来源 {node.source}</p>
        </div>
        {detail ? (
          <button
            type="button"
            onClick={() => props.onStartPractice(detail)}
            disabled={busy}
            className="inline-flex h-10 items-center justify-center gap-2 rounded-xl bg-primary-600 px-4 text-sm font-semibold text-white shadow-sm shadow-primary-500/20 transition hover:bg-primary-700 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {busy ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
            立即练习
          </button>
        ) : null}
      </div>

      {detail ? (
        <div className="mt-5 grid gap-4 lg:grid-cols-2">
          <RelationBlock title="前置知识" items={detail.prerequisites.map((item) => item.topic)} empty="暂无明确前置知识" />
          <RelationBlock title="后续知识" items={detail.nextNodes.map((item) => item.topic)} empty="暂无明确后续知识" />
          <RelationBlock title="相关知识" items={detail.relatedNodes.map((item) => item.topic)} empty="暂无相关节点" />
          <RelationBlock title="推荐动作" items={detail.recommendedNextActions} empty="暂无推荐动作" />
        </div>
      ) : null}

      {detail?.relatedMistakes.length ? (
        <div className="mt-5">
          <h4 className="text-sm font-semibold text-slate-900 dark:text-white">相关错题</h4>
          <div className="mt-3 space-y-2">
            {detail.relatedMistakes.slice(0, 3).map((mistake) => (
              <div key={mistake.id} className="rounded-xl bg-white/78 px-3 py-2 text-sm text-slate-600 shadow-sm shadow-slate-200/50 dark:bg-slate-900/70 dark:text-slate-300 dark:shadow-none">
                <div className="line-clamp-2">{mistake.stem}</div>
                <div className="mt-1 text-xs text-slate-400">错 {mistake.wrongCount} 次 · 复习 {mistake.reviewCount} 次</div>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {detail?.relatedResources.length ? (
        <div className="mt-5">
          <h4 className="text-sm font-semibold text-slate-900 dark:text-white">相关资源</h4>
          <div className="mt-3 grid gap-2 sm:grid-cols-2">
            {detail.relatedResources.slice(0, 4).map((resource) => (
              <div key={resource.id} className="rounded-xl bg-white/78 px-3 py-2 shadow-sm shadow-slate-200/50 dark:bg-slate-900/70 dark:shadow-none">
                <div className="line-clamp-2 text-sm font-medium text-slate-800 dark:text-slate-100">{resource.title}</div>
                <div className="mt-1 text-xs text-slate-400">{resource.displayType || resource.resourceType}</div>
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </article>
  );
}

function RelationBlock({ title, items, empty }: { title: string; items: string[]; empty: string }) {
  return (
    <div className="rounded-xl bg-white/76 p-3 shadow-sm shadow-slate-200/50 dark:bg-slate-900/70 dark:shadow-none">
      <h4 className="text-xs font-semibold text-slate-500 dark:text-slate-400">{title}</h4>
      <div className="mt-2 flex flex-wrap gap-2">
        {items.length ? items.map((item) => (
          <span key={item} className="rounded-full bg-slate-100 px-2.5 py-1 text-xs text-slate-600 dark:bg-slate-800 dark:text-slate-300">
            {item}
          </span>
        )) : (
          <span className="text-xs text-slate-400">{empty}</span>
        )}
      </div>
    </div>
  );
}

function knowledgeStatusLabel(status: string): string {
  return {
    WEAK: '薄弱',
    IN_PROGRESS: '学习中',
    NOT_STARTED: '未开始',
    MASTERED: '已掌握',
  }[status] ?? status;
}

function BehaviorTrendPanel(props: {
  analytics: UserProfileAnalyticsResponse | null;
  loading: boolean;
  error: string;
  summary: TrendSummary;
  onRetry: () => void;
}) {
  const trend = props.analytics?.behaviorTrend ?? [];
  const visibleTrend = trend.slice(-14);
  const hasData = visibleTrend.some((point) => sumTrendActivity(point) > 0);
  const maxActivity = Math.max(1, ...visibleTrend.map(sumTrendActivity));

  return (
    <PanelShell id="behavior">
      <div className="flex flex-col gap-5 lg:flex-row lg:items-start lg:justify-between">
        <SectionTitle
          icon={<LineChart className="h-5 w-5" />}
          title="学习行为趋势"
          subtitle="展示近 30 天学习节奏，聚焦对话、服务、练习和复盘。"
        />
        <div className="grid gap-2 sm:grid-cols-3 lg:min-w-[420px]">
          <TrendMetric label="活跃天数" value={`${props.summary.activeDays}天`} />
          <TrendMetric label="学习互动" value={`${props.summary.totalActivity}次`} />
          <TrendMetric label="练习正确率" value={formatAccuracy(props.summary.practiceAccuracy)} />
        </div>
      </div>

      <div className="mt-6 min-h-[230px]">
        {props.loading ? (
          <AnalyticsStateMessage text="正在读取行为趋势" />
        ) : props.error ? (
          <div className="rounded-2xl bg-red-50/80 p-4 text-sm text-red-700 shadow-sm shadow-red-100/70 dark:bg-red-950/30 dark:text-red-200 dark:shadow-red-950/20">
            <div>{props.error}</div>
            <button
              type="button"
              onClick={props.onRetry}
              className="mt-3 rounded-xl bg-white px-3 py-1.5 text-xs font-medium text-red-700 transition-colors hover:bg-red-100 dark:bg-red-950 dark:text-red-200 dark:hover:bg-red-900"
            >
              重新读取
            </button>
          </div>
        ) : hasData ? (
          <div className="grid min-w-0 min-h-[230px] gap-4 lg:grid-cols-[minmax(0,1fr)_220px]">
            <div className="min-w-0 overflow-x-auto rounded-2xl bg-slate-50 dark:bg-slate-950/40">
              <div className="flex min-h-[230px] min-w-[520px] items-end gap-2 px-4 pb-4 pt-5 sm:min-w-0">
              {visibleTrend.map((point) => {
                const activity = sumTrendActivity(point);
                const height = `${Math.max(8, Math.round((activity / maxActivity) * 100))}%`;
                const label = formatTrendDate(point.date);
                return (
                  <div key={point.date} className="group flex h-48 min-w-0 flex-1 flex-col items-center justify-end gap-2">
                    <div className="relative flex h-full w-full max-w-8 items-end">
                      <div
                        className="w-full rounded-t-lg bg-primary-500 transition-[height,background-color] duration-200 group-hover:bg-primary-600"
                        style={{ height }}
                      />
                      <div className="pointer-events-none absolute bottom-full left-1/2 z-10 mb-2 hidden w-40 -translate-x-1/2 rounded-xl bg-white/95 px-3 py-2 text-xs text-slate-600 shadow-lg shadow-slate-200/80 backdrop-blur group-hover:block dark:bg-slate-900/95 dark:text-slate-300 dark:shadow-slate-950/40">
                        <div className="font-semibold text-slate-900 dark:text-white">{label}</div>
                        <div className="mt-1">当天互动：{activity}</div>
                        <div>对话：{point.conversationCount}，服务：{point.serviceTaskCount}</div>
                        <div>练习：{point.practiceSubmissionCount}，复盘：{point.reviewCount}</div>
                      </div>
                    </div>
                    <span className="text-[11px] text-slate-400 dark:text-slate-500">{label}</span>
                  </div>
                );
              })}
              </div>
            </div>
            <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-1">
              <BehaviorSignal label="对话" value={props.summary.conversationCount} color="bg-primary-500" />
              <BehaviorSignal label="学习服务" value={props.summary.serviceTaskCount} color="bg-cyan-500" />
              <BehaviorSignal label="练习提交" value={props.summary.practiceSubmissionCount} color="bg-emerald-500" />
              <BehaviorSignal label="新增错题" value={props.summary.newMistakeCount} color="bg-amber-500" />
              <BehaviorSignal label="复盘" value={props.summary.reviewCount} color="bg-slate-500" />
            </div>
          </div>
        ) : (
          <EmptyInline text={props.analytics ? `近 ${props.analytics.days} 天暂无学习记录。` : '暂无行为趋势数据。'} />
        )}
      </div>
    </PanelShell>
  );
}

function DimensionPanel({ profile }: { profile: ProfileSnapshot }) {
  const dimensionScores = profile.dimensionScores;
  const hasScores = dimensionScores.length > 0;

  return (
    <PanelShell id="dimensions" className="min-h-[520px]">
      <SectionTitle
        icon={<Target className="h-5 w-5" />}
        title="画像维度可视化"
        subtitle="综合当前画像记录，快速观察掌握、目标、习惯与适配状态。"
      />

      {hasScores ? (
        <div className="mt-6 grid gap-6 lg:grid-cols-[minmax(0,0.92fr)_minmax(260px,0.8fr)]">
          <div className="rounded-2xl bg-slate-50 p-3 dark:bg-slate-950/40">
            <RadarChart
              data={dimensionScores.map((item) => ({
                subject: item.subject,
                score: item.score,
                fullMark: item.fullMark,
                description: item.description,
              }))}
              height={340}
              className="min-h-[340px]"
            />
          </div>
          <div className="space-y-3">
            {dimensionScores.map((item) => (
              <ScoreLine key={item.key} label={item.subject} detail={item.hint} score={item.score} />
            ))}
          </div>
        </div>
      ) : (
        <div className="mt-6">
          <EmptyInline text="当前画像暂无可视化维度，完成更多学习记录后会更新。" />
        </div>
      )}
    </PanelShell>
  );
}

function WeakPointTopThree({ items }: { items: WeakPointRank[] }) {
  return (
    <PanelShell id="key-weak" className="min-h-[520px]">
      <SectionTitle
        icon={<TriangleAlert className="h-5 w-5" />}
        title="关键薄弱点 Top3"
        subtitle="只保留优先级最高的三个薄弱点，避免一次承载过多信息。"
      />

      <div className="mt-6 space-y-3">
        {items.length > 0 ? items.map((item, index) => (
          <WeakPointCard key={`${item.topic}-${index}`} item={item} rank={index + 1} />
        )) : (
          <EmptyInline text="暂无明确薄弱点，继续完成练习或学习评估后会更新。" />
        )}
      </div>
    </PanelShell>
  );
}

function TrendMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-2xl bg-slate-50 px-4 py-3 dark:bg-slate-950/40">
      <div className="text-xs text-slate-400 dark:text-slate-500">{label}</div>
      <div className="mt-1 text-lg font-semibold text-slate-950 dark:text-white">{value}</div>
    </div>
  );
}

function BehaviorSignal({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div className="flex items-center justify-between rounded-2xl bg-white/72 px-3.5 py-3 shadow-sm shadow-slate-200/50 dark:bg-slate-950/30 dark:shadow-none">
      <div className="flex min-w-0 items-center gap-2.5">
        <span className={`h-2.5 w-2.5 rounded-full ${color}`} />
        <span className="truncate text-sm text-slate-600 dark:text-slate-300">{label}</span>
      </div>
      <span className="text-sm font-semibold text-slate-950 dark:text-white">{value}</span>
    </div>
  );
}

function ScoreLine({ label, detail, score }: { label: string; detail: string; score: number }) {
  const normalizedScore = Math.max(0, Math.min(100, score));
  return (
    <div className="rounded-2xl bg-white/62 p-4 shadow-sm shadow-slate-200/45 transition-colors hover:bg-primary-50/45 dark:bg-slate-950/24 dark:shadow-none dark:hover:bg-primary-500/5">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold text-slate-800 dark:text-slate-100">{label}</div>
          <div className="mt-1 line-clamp-2 text-xs leading-5 text-slate-500 dark:text-slate-400">{detail}</div>
        </div>
        <div className="shrink-0 text-lg font-semibold text-slate-950 dark:text-white">{normalizedScore}</div>
      </div>
      <div className="mt-3 h-2 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
        <div className="h-full rounded-full bg-primary-500" style={{ width: `${normalizedScore}%` }} />
      </div>
    </div>
  );
}

function WeakPointCard({ item, rank }: { item: WeakPointRank; rank: number }) {
  const severity = Math.max(0, Math.min(100, item.severity));
  const level = severity >= 80 ? '高优先级' : severity >= 60 ? '中优先级' : '待观察';

  return (
    <article className="rounded-2xl bg-slate-50/70 p-4 shadow-sm shadow-slate-200/50 transition-colors hover:bg-amber-50/60 dark:bg-slate-950/30 dark:shadow-none dark:hover:bg-amber-500/5">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="text-xs font-semibold text-amber-600 dark:text-amber-300">Top {rank}</div>
          <h3 className="mt-1 text-base font-semibold text-slate-950 dark:text-white">{item.topic}</h3>
        </div>
        <div className="shrink-0 rounded-full bg-white/86 px-3 py-1 text-xs font-medium text-slate-600 dark:bg-slate-900/70 dark:text-slate-300">
          {level}
        </div>
      </div>

      <div className="mt-4">
        <div className="flex items-center justify-between text-xs text-slate-500 dark:text-slate-400">
          <span>关注强度</span>
          <span>{severity}%</span>
        </div>
        <div className="mt-2 h-2 overflow-hidden rounded-full bg-white dark:bg-slate-800">
          <div className="h-full rounded-full bg-amber-500" style={{ width: `${severity}%` }} />
        </div>
      </div>

      <div className="mt-4 space-y-2 text-sm leading-6 text-slate-600 dark:text-slate-300">
        <p>{item.lastError || '等待更多练习或评估记录补充错因。'}</p>
        {item.errorPattern ? (
          <p className="text-xs text-slate-500 dark:text-slate-400">可能原因：{item.errorPattern}</p>
        ) : null}
        {item.severityInferred ? (
          <p className="text-xs text-slate-400 dark:text-slate-500">等待更多记录确认关注优先级</p>
        ) : null}
      </div>
    </article>
  );
}

function ProfileAccessState(props: {
  icon: ReactNode;
  title: string;
  description: string;
  actionLabel: string;
  onAction: () => void;
}) {
  return (
    <div className="flex min-h-[420px] items-center justify-center rounded-[28px] bg-white/72 p-6 text-center shadow-[0_18px_56px_rgba(59,97,155,0.10)] backdrop-blur-xl dark:bg-slate-900/68 dark:shadow-slate-950/20">
      <div className="max-w-md">
        <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-2xl bg-primary-50 text-primary-600 dark:bg-primary-500/10 dark:text-primary-300">
          {props.icon}
        </div>
        <h1 className="text-xl font-semibold text-slate-950 dark:text-white">{props.title}</h1>
        <p className="mt-2 text-sm leading-6 text-slate-500 dark:text-slate-400">{props.description}</p>
        <button
          type="button"
          onClick={props.onAction}
          className="mt-5 inline-flex h-10 items-center justify-center rounded-xl bg-primary-600 px-4 text-sm font-medium text-white shadow-sm shadow-primary-500/20 outline-none transition-all hover:bg-primary-700 focus-visible:shadow-[0_10px_24px_rgba(59,130,246,0.24)] dark:focus-visible:shadow-[0_10px_24px_rgba(37,99,235,0.24)]"
        >
          {props.actionLabel}
        </button>
      </div>
    </div>
  );
}

function AnalyticsStateMessage({ text }: { text: string }) {
  return (
    <div className="flex min-h-[230px] items-center justify-center rounded-2xl bg-slate-50 text-sm text-slate-500 dark:bg-slate-950/40 dark:text-slate-400">
      <LoaderCircle className="mr-2 h-4 w-4 animate-spin text-primary-500" />
      {text}
    </div>
  );
}

function EmptyInline({ text }: { text: string }) {
  return (
    <div className="flex min-h-[160px] items-center justify-center rounded-2xl bg-slate-50/70 px-4 py-8 text-center text-sm text-slate-500 dark:bg-slate-950/30 dark:text-slate-400">
      {text}
    </div>
  );
}

interface TrendSummary {
  activeDays: number;
  totalActivity: number;
  conversationCount: number;
  serviceTaskCount: number;
  practiceSubmissionCount: number;
  newMistakeCount: number;
  reviewCount: number;
  practiceAccuracy: number | null;
}

function buildTrendSummary(analytics: UserProfileAnalyticsResponse | null): TrendSummary {
  const trend = analytics?.behaviorTrend ?? [];
  const coverage = analytics?.systemAnalysis.coverage;
  const totals = trend.reduce(
    (summary, point) => ({
      conversationCount: summary.conversationCount + point.conversationCount,
      serviceTaskCount: summary.serviceTaskCount + point.serviceTaskCount,
      practiceSubmissionCount: summary.practiceSubmissionCount + point.practiceSubmissionCount,
      newMistakeCount: summary.newMistakeCount + point.newMistakeCount,
      reviewCount: summary.reviewCount + point.reviewCount,
    }),
    {
      conversationCount: 0,
      serviceTaskCount: 0,
      practiceSubmissionCount: 0,
      newMistakeCount: 0,
      reviewCount: 0,
    },
  );
  const practiceAccuracy = coverage && coverage.practiceSubmissionCount > 0
    ? weightedPracticeAccuracy(trend)
    : null;
  const conversationCount = coverage?.conversationCount ?? totals.conversationCount;
  const serviceTaskCount = coverage?.serviceTaskCount ?? totals.serviceTaskCount;
  const practiceSubmissionCount = coverage?.practiceSubmissionCount ?? totals.practiceSubmissionCount;
  const newMistakeCount = coverage?.newMistakeCount ?? totals.newMistakeCount;
  const reviewCount = coverage?.reviewCount ?? totals.reviewCount;
  return {
    activeDays: coverage?.activeDays ?? trend.filter((point) => sumTrendActivity(point) > 0).length,
    totalActivity: conversationCount
      + serviceTaskCount
      + practiceSubmissionCount
      + newMistakeCount
      + reviewCount,
    conversationCount,
    serviceTaskCount,
    practiceSubmissionCount,
    newMistakeCount,
    reviewCount,
    practiceAccuracy,
  };
}

function weightedPracticeAccuracy(trend: ProfileBehaviorTrendPoint[]): number | null {
  const validPoints = trend.filter((point) => point.practiceAccuracy !== null && point.practiceSubmissionCount > 0);
  const totalSubmissions = validPoints.reduce((sum, point) => sum + point.practiceSubmissionCount, 0);
  if (totalSubmissions === 0) {
    return null;
  }
  return validPoints.reduce(
    (sum, point) => sum + (point.practiceAccuracy ?? 0) * point.practiceSubmissionCount,
    0,
  ) / totalSubmissions;
}

function sumTrendActivity(point: ProfileBehaviorTrendPoint): number {
  return point.conversationCount
    + point.serviceTaskCount
    + point.practiceSubmissionCount
    + point.newMistakeCount
    + point.reviewCount;
}

function buildWeakPointItems(profile: ProfileSnapshot): WeakPointRank[] {
  if (profile.weakPointRanks.length > 0) {
    return profile.weakPointRanks;
  }
  return profile.weakPoints
    .filter((topic) => topic.trim())
    .map((topic, index) => ({
      topic,
      severity: Math.max(45, 76 - index * 10),
      lastError: '等待更多练习或评估记录补充错因。',
      severityInferred: true,
    }));
}

function formatAccuracy(value: number | null): string {
  if (value === null) {
    return EMPTY_VALUE;
  }
  return `${Math.round(value)}%`;
}

function formatTrendDate(value: string): string {
  if (!value) {
    return '--';
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value.slice(5) || value;
  }
  return `${date.getMonth() + 1}/${date.getDate()}`;
}
