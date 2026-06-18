import { useCallback, useEffect, useMemo, type ReactNode, useState } from 'react';
import { useOutletContext, useSearchParams } from 'react-router-dom';
import {
  GitBranch,
  LoaderCircle,
  Lock,
  Network,
  Play,
  RefreshCw,
  Search,
} from 'lucide-react';
import KnowledgeGraphCanvas, {
  ALL_EDGE_TYPES,
  ALL_NODE_STATUSES,
  EDGE_TYPE_LABELS,
  STATUS_LABELS,
  countEdgesByType,
  type GraphLayoutMode,
  type NeighborhoodDepth,
} from '../components/KnowledgeGraphCanvas';
import { conversationApi } from '../api/conversation';
import { getErrorMessage } from '../api/request';
import { readStreamPayload } from '../api/sse';
import {
  smartEngineApi,
  type KnowledgeGraphEdge,
  type KnowledgeGraphNode,
  type KnowledgeGraphResponse,
} from '../api/smartEngine';
import { studyWorkbenchApi, type KnowledgeNodeDetailResponse } from '../api/studyWorkbench';
import type { LayoutOutletContext } from '../components/Layout';
import type { PracticeQuestionBatch } from './LearningStudioDemoPage.types';
import { readPracticeQuestionBatch } from './LearningStudioDemoPage.utils';
import { openPracticeSession } from './practiceSessionStore';

type NodeStatusCounts = Record<KnowledgeGraphNode['status'], number>;

export default function KnowledgeGraphPage() {
  const { isAuthenticated, currentUser, openAuthModal } = useOutletContext<LayoutOutletContext>();
  const [searchParams, setSearchParams] = useSearchParams();
  const [knowledgeGraph, setKnowledgeGraph] = useState<KnowledgeGraphResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [selectedNodeKey, setSelectedNodeKey] = useState(searchParams.get('node') ?? '');
  const [nodeDetail, setNodeDetail] = useState<KnowledgeNodeDetailResponse | null>(null);
  const [nodeDetailLoading, setNodeDetailLoading] = useState(false);
  const [nodeDetailError, setNodeDetailError] = useState('');
  const [practiceGeneratingKey, setPracticeGeneratingKey] = useState('');
  const [viewMode, setViewMode] = useState<'graph' | 'list'>('graph');
  const [layoutMode, setLayoutMode] = useState<GraphLayoutMode>('mindmap');
  const [neighborhoodDepth, setNeighborhoodDepth] = useState<NeighborhoodDepth>(1);
  const [searchQuery, setSearchQuery] = useState('');
  const [highlightRecommendedPath, setHighlightRecommendedPath] = useState(true);
  const [statusFilter, setStatusFilter] = useState<Set<KnowledgeGraphNode['status']>>(
    () => new Set(ALL_NODE_STATUSES),
  );
  const [edgeTypeFilter, setEdgeTypeFilter] = useState<Set<KnowledgeGraphEdge['type']>>(
    () => new Set(ALL_EDGE_TYPES),
  );

  const nodes = knowledgeGraph?.nodes ?? [];
  const edges = knowledgeGraph?.edges ?? [];
  const nextRecommended = knowledgeGraph?.nextRecommended ?? [];
  const graphMetadata = knowledgeGraph?.metadata;
  const selectedNode = nodes.find((node) => node.key === selectedNodeKey) ?? nodes[0] ?? null;
  const statusCounts = useMemo(() => countNodesByStatus(nodes), [nodes]);
  const edgeCounts = useMemo(() => countEdgesByType(edges), [edges]);

  const loadKnowledgeGraph = useCallback(async () => {
    if (!isAuthenticated || !currentUser) {
      setKnowledgeGraph(null);
      setError('');
      return;
    }
    setLoading(true);
    setError('');
    try {
      setKnowledgeGraph(await smartEngineApi.getKnowledgeGraph(String(currentUser.id)));
    } catch (loadError) {
      setKnowledgeGraph(null);
      setError(getErrorMessage(loadError));
    } finally {
      setLoading(false);
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
    void loadKnowledgeGraph();
  }, [loadKnowledgeGraph]);

  useEffect(() => {
    const handleProfileUpdated = () => {
      void loadKnowledgeGraph();
    };
    window.addEventListener('app:profile-updated', handleProfileUpdated);
    return () => window.removeEventListener('app:profile-updated', handleProfileUpdated);
  }, [loadKnowledgeGraph]);

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

  useEffect(() => {
    if (selectedNodeKey || !nodes.length) {
      return;
    }
    const rootKey = graphMetadata?.rootKey && nodes.some((node) => node.key === graphMetadata.rootKey)
      ? graphMetadata.rootKey
      : nextRecommended.find((key) => nodes.some((node) => node.key === key)) ?? nodes[0]?.key;
    if (rootKey) {
      selectKnowledgeNode(rootKey);
    }
  }, [graphMetadata?.rootKey, nextRecommended, nodes, selectedNodeKey]);

  const selectKnowledgeNode = (nodeKey: string) => {
    const next = new URLSearchParams(searchParams);
    if (nodeKey) {
      next.set('node', nodeKey);
    } else {
      next.delete('node');
    }
    setSearchParams(next, { replace: true });
  };

  const resetGraphControls = () => {
    setLayoutMode('mindmap');
    setNeighborhoodDepth(1);
    setSearchQuery('');
    setHighlightRecommendedPath(true);
    setStatusFilter(new Set(ALL_NODE_STATUSES));
    setEdgeTypeFilter(new Set(ALL_EDGE_TYPES));
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
      const response = await smartEngineApi.submit({
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
      await smartEngineApi.streamTask(response.taskId, {
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
        const task = await smartEngineApi.getTask(response.taskId, { dedupe: false, retry: 2 });
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
      <GraphPageShell>
        <GraphAccessState
          icon={<Lock className="h-6 w-6" />}
          title="登录后查看知识图谱"
          description="知识图谱会汇总画像、练习和学习路径中的知识点关系。"
          actionLabel="去登录"
          onAction={() => openAuthModal('login', '登录后查看知识图谱')}
        />
      </GraphPageShell>
    );
  }

  return (
    <GraphPageShell>
      <header className="mb-5 flex flex-col gap-4 px-1 md:flex-row md:items-end md:justify-between">
        <div className="min-w-0">
          <div className="inline-flex items-center gap-2 rounded-full bg-primary-50 px-3 py-1 text-xs font-semibold text-primary-700 dark:bg-primary-500/10 dark:text-primary-300">
            <Network className="h-3.5 w-3.5" />
            知识图谱
          </div>
          <h1 className="mt-3 text-2xl font-semibold tracking-normal text-slate-950 dark:text-white md:text-3xl">
            知识关系网络
          </h1>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-500 dark:text-slate-400">
            单独查看知识点之间的前置、相关和归属关系，快速定位薄弱节点和下一步学习路径。
          </p>
        </div>
        <button
          type="button"
          onClick={() => void loadKnowledgeGraph()}
          disabled={loading}
          className="inline-flex h-10 w-fit items-center justify-center gap-2 rounded-xl bg-primary-600 px-4 text-sm font-medium text-white shadow-sm shadow-primary-500/20 outline-none transition-all hover:bg-primary-700 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {loading ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
          刷新图谱
        </button>
      </header>

      <div className="space-y-5">
        <KnowledgeGraphToolbar
          nodes={nodes}
          edges={edges}
          statusCounts={statusCounts}
          edgeCounts={edgeCounts}
          loading={loading}
          metadata={graphMetadata}
          layoutMode={layoutMode}
          neighborhoodDepth={neighborhoodDepth}
          searchQuery={searchQuery}
          highlightRecommendedPath={highlightRecommendedPath}
          statusFilter={statusFilter}
          edgeTypeFilter={edgeTypeFilter}
          onRetry={() => void loadKnowledgeGraph()}
          onLayoutModeChange={setLayoutMode}
          onNeighborhoodDepthChange={setNeighborhoodDepth}
          onSearchQueryChange={setSearchQuery}
          onHighlightRecommendedPathChange={setHighlightRecommendedPath}
          onToggleStatus={(status) => setStatusFilter((current) => toggleFilterValue(current, status))}
          onToggleEdgeType={(type) => setEdgeTypeFilter((current) => toggleFilterValue(current, type))}
        />

        <main className="min-w-0">
          <section className="rounded-[24px] bg-white/72 p-4 shadow-[0_14px_40px_rgba(59,97,155,0.08)] backdrop-blur dark:bg-slate-900/62 dark:shadow-slate-950/20">
            <div className="mb-3 flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
              <div>
                <h2 className="text-base font-semibold text-slate-950 dark:text-white">图谱画布</h2>
                <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                  显示 {nodes.length} 个知识点，{edges.length} 条真实关系
                </p>
              </div>
              <div className="flex w-fit rounded-xl bg-slate-100 p-1 dark:bg-slate-900">
                <GraphModeButton active={viewMode === 'graph'} label="图谱视图" onClick={() => setViewMode('graph')} />
                <GraphModeButton active={viewMode === 'list'} label="列表视图" onClick={() => setViewMode('list')} />
              </div>
            </div>

            {loading && !nodes.length ? (
              <GraphStateMessage text="正在读取知识图谱" />
            ) : error ? (
              <div className="rounded-2xl bg-red-50/80 p-4 text-sm text-red-700 dark:bg-red-950/30 dark:text-red-200">
                {error}
              </div>
            ) : nodes.length ? (
              viewMode === 'graph' ? (
                <KnowledgeGraphCanvas
                  nodes={nodes}
                  edges={edges}
                  selectedNodeKey={selectedNodeKey}
                  nextRecommended={nextRecommended}
                  statusFilter={statusFilter}
                  edgeTypeFilter={edgeTypeFilter}
                  layoutMode={layoutMode}
                  neighborhoodDepth={neighborhoodDepth}
                  searchQuery={searchQuery}
                  highlightRecommendedPath={highlightRecommendedPath}
                  metadata={graphMetadata}
                  onSelectNode={selectKnowledgeNode}
                  onResetView={resetGraphControls}
                />
              ) : (
                <KnowledgeGraphNodeList
                  nodes={nodes}
                  selectedNodeKey={selectedNodeKey}
                  onSelectNode={selectKnowledgeNode}
                />
              )
            ) : (
              <GraphEmptyInline text="暂无知识图谱数据。完成练习、阶段测试或画像生成后会自动沉淀知识点。" />
            )}
          </section>

          <KnowledgeNodeDetailCard
            detail={nodeDetail}
            loading={nodeDetailLoading}
            error={nodeDetailError}
            fallbackNode={selectedNode}
            practiceGeneratingKey={practiceGeneratingKey}
            onStartPractice={startNodePractice}
          />
        </main>
      </div>
    </GraphPageShell>
  );
}

function KnowledgeGraphToolbar(props: {
  nodes: KnowledgeGraphNode[];
  edges: KnowledgeGraphEdge[];
  statusCounts: NodeStatusCounts;
  edgeCounts: Record<KnowledgeGraphEdge['type'], number>;
  loading: boolean;
  metadata?: KnowledgeGraphResponse['metadata'];
  layoutMode: GraphLayoutMode;
  neighborhoodDepth: NeighborhoodDepth;
  searchQuery: string;
  highlightRecommendedPath: boolean;
  statusFilter: Set<KnowledgeGraphNode['status']>;
  edgeTypeFilter: Set<KnowledgeGraphEdge['type']>;
  onRetry: () => void;
  onLayoutModeChange: (mode: GraphLayoutMode) => void;
  onNeighborhoodDepthChange: (depth: NeighborhoodDepth) => void;
  onSearchQueryChange: (query: string) => void;
  onHighlightRecommendedPathChange: (enabled: boolean) => void;
  onToggleStatus: (status: KnowledgeGraphNode['status']) => void;
  onToggleEdgeType: (type: KnowledgeGraphEdge['type']) => void;
}) {
  return (
    <section className="rounded-[20px] bg-white/76 p-4 shadow-[0_14px_40px_rgba(59,97,155,0.08)] backdrop-blur dark:bg-slate-900/62 dark:shadow-slate-950/20">
      <div className="flex flex-wrap items-center gap-3">
        <div className="mr-auto grid min-w-[220px] grid-cols-2 gap-2 sm:grid-cols-4">
          <MetricRow label="知识点" value={props.nodes.length} />
          <MetricRow label="关系" value={props.edges.length} />
          <MetricRow label="薄弱" value={props.statusCounts.WEAK} tone="text-amber-600 dark:text-amber-300" />
          <MetricRow label="孤立" value={props.metadata?.orphanNodeCount ?? 0} tone="text-slate-600 dark:text-slate-300" />
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={props.onRetry}
            disabled={props.loading}
            className="inline-flex h-8 w-8 items-center justify-center rounded-lg bg-slate-100 text-slate-600 transition hover:bg-primary-50 hover:text-primary-700 disabled:cursor-not-allowed disabled:opacity-60 dark:bg-slate-900 dark:text-slate-300"
            aria-label="刷新图谱"
          >
            {props.loading ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
          </button>
        </div>
        <label className="min-w-[220px] flex-1 lg:max-w-xs">
          <div className="flex h-10 items-center rounded-xl border border-slate-200 bg-white px-3 focus-within:border-primary-300 focus-within:ring-2 focus-within:ring-primary-100 dark:border-slate-800 dark:bg-slate-950 dark:focus-within:border-primary-500 dark:focus-within:ring-primary-500/20">
            <Search className="mr-2 h-4 w-4 shrink-0 text-slate-400" />
            <input
              value={props.searchQuery}
              onChange={(event) => props.onSearchQueryChange(event.target.value)}
              placeholder="知识点/来源/key"
              className="min-w-0 flex-1 bg-transparent text-sm text-slate-700 outline-none placeholder:text-slate-400 dark:text-slate-200 dark:placeholder:text-slate-500"
            />
          </div>
        </label>
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <GraphSegmentedControl
          label="布局"
          options={[
            { value: 'mindmap', label: '心智图' },
            { value: 'radial', label: '辐射' },
            { value: 'path', label: '路径' },
            { value: 'network', label: '状态网' },
          ]}
          value={props.layoutMode}
          onChange={props.onLayoutModeChange}
        />
        <GraphSegmentedControl
          label="邻域"
          options={[
            { value: 0, label: '全图' },
            { value: 1, label: '一跳' },
            { value: 2, label: '二跳' },
          ]}
          value={props.neighborhoodDepth}
          onChange={props.onNeighborhoodDepthChange}
        />
        <div className="flex flex-wrap gap-2">
          {ALL_EDGE_TYPES.map((type) => (
            <GraphFilterChip
              key={type}
              label={`${EDGE_TYPE_LABELS[type]} ${props.edgeCounts[type]}`}
              active={props.edgeTypeFilter.has(type)}
              onClick={() => props.onToggleEdgeType(type)}
            />
          ))}
        </div>
        <div className="flex flex-wrap gap-2">
          {ALL_NODE_STATUSES.map((status) => (
            <GraphFilterChip
              key={status}
              label={`${STATUS_LABELS[status]} ${props.statusCounts[status]}`}
              active={props.statusFilter.has(status)}
              onClick={() => props.onToggleStatus(status)}
            />
          ))}
        </div>
        <label className="inline-flex h-9 cursor-pointer items-center gap-2 rounded-full bg-slate-50 px-3 text-xs font-semibold text-slate-600 transition hover:bg-primary-50 hover:text-primary-700 dark:bg-slate-900/80 dark:text-slate-300 dark:hover:bg-primary-500/10 dark:hover:text-primary-200">
          <input
            type="checkbox"
            checked={props.highlightRecommendedPath}
            onChange={(event) => props.onHighlightRecommendedPathChange(event.target.checked)}
            className="h-3.5 w-3.5 rounded border-slate-300 text-primary-600 focus:ring-primary-400"
          />
          高亮推荐路径
        </label>
      </div>
      {props.metadata?.sparseState ? (
        <p className="mt-3 text-xs font-medium text-amber-700 dark:text-amber-300">
          当前知识点已沉淀，关系仍在补全。画布只展示已验证关系，孤立知识点可在列表视图查看。
        </p>
      ) : null}
    </section>
  );
}

function MetricRow({ label, value, tone = 'text-slate-950 dark:text-white' }: { label: string; value: number; tone?: string }) {
  return (
    <div className="flex items-center justify-between rounded-xl bg-slate-50 px-3 py-2 dark:bg-slate-900/70">
      <span className="text-xs text-slate-500 dark:text-slate-400">{label}</span>
      <span className={`text-sm font-semibold ${tone}`}>{value}</span>
    </div>
  );
}

function GraphSegmentedControl<T extends string | number>(props: {
  label: string;
  options: Array<{ value: T; label: string }>;
  value: T;
  onChange: (value: T) => void;
}) {
  return (
    <div className="mt-4">
      <div className="text-xs font-semibold text-slate-500 dark:text-slate-400">{props.label}</div>
      <div className="mt-1 flex rounded-xl bg-slate-100 p-1 dark:bg-slate-900">
        {props.options.map((option) => (
          <button
            key={String(option.value)}
            type="button"
            onClick={() => props.onChange(option.value)}
            className={`h-8 flex-1 rounded-lg px-2 text-xs font-semibold transition ${
              option.value === props.value
                ? 'bg-white text-primary-700 shadow-sm shadow-slate-200/70 dark:bg-slate-800 dark:text-primary-200 dark:shadow-none'
                : 'text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200'
            }`}
          >
            {option.label}
          </button>
        ))}
      </div>
    </div>
  );
}

function GraphModeButton(props: { active: boolean; label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={props.onClick}
      className={`h-8 rounded-lg px-3 text-xs font-semibold transition ${
        props.active
          ? 'bg-white text-primary-700 shadow-sm shadow-slate-200/70 dark:bg-slate-800 dark:text-primary-200 dark:shadow-none'
          : 'text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200'
      }`}
    >
      {props.label}
    </button>
  );
}

function GraphFilterChip(props: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={props.onClick}
      className={`h-8 rounded-full px-3 text-xs font-semibold transition ${
        props.active
          ? 'bg-primary-50 text-primary-700 ring-1 ring-primary-100 dark:bg-primary-500/10 dark:text-primary-200 dark:ring-primary-500/20'
          : 'bg-slate-50 text-slate-500 hover:bg-slate-100 dark:bg-slate-900/70 dark:text-slate-400 dark:hover:bg-slate-800'
      }`}
    >
      {props.label}
    </button>
  );
}

function KnowledgeGraphNodeList(props: {
  nodes: KnowledgeGraphNode[];
  selectedNodeKey: string;
  onSelectNode: (nodeKey: string) => void;
}) {
  return (
    <div className="grid max-h-[720px] gap-3 overflow-auto rounded-2xl border border-slate-100 bg-white/80 p-3 shadow-sm shadow-slate-200/50 dark:border-slate-800 dark:bg-slate-950/30 dark:shadow-none md:grid-cols-2">
      {props.nodes.map((node) => (
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
    return <GraphStateMessage text="正在读取知识点详情" />;
  }
  if (props.error) {
    return (
      <div className="mt-4 rounded-2xl bg-red-50/80 p-4 text-sm text-red-700 dark:bg-red-950/30 dark:text-red-200">
        {props.error}
      </div>
    );
  }
  const detail = props.detail;
  const node = detail?.node ?? props.fallbackNode;
  if (!node) {
    return <GraphEmptyInline text="请选择一个知识点查看详情。" />;
  }
  const busy = props.practiceGeneratingKey === node.key;
  return (
    <article className="mt-4 rounded-[24px] bg-white/72 p-5 shadow-[0_14px_40px_rgba(59,97,155,0.08)] backdrop-blur dark:bg-slate-900/62 dark:shadow-slate-950/20">
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

function GraphAccessState(props: {
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
          className="mt-5 inline-flex h-10 items-center justify-center rounded-xl bg-primary-600 px-4 text-sm font-medium text-white shadow-sm shadow-primary-500/20 outline-none transition-all hover:bg-primary-700"
        >
          {props.actionLabel}
        </button>
      </div>
    </div>
  );
}

function GraphStateMessage({ text }: { text: string }) {
  return (
    <div className="flex min-h-[230px] items-center justify-center rounded-2xl bg-slate-50 text-sm text-slate-500 dark:bg-slate-950/40 dark:text-slate-400">
      <LoaderCircle className="mr-2 h-4 w-4 animate-spin text-primary-500" />
      {text}
    </div>
  );
}

function GraphEmptyInline({ text }: { text: string }) {
  return (
    <div className="mt-4 flex min-h-[160px] items-center justify-center rounded-2xl bg-slate-50/70 px-4 py-8 text-center text-sm text-slate-500 dark:bg-slate-950/30 dark:text-slate-400">
      {text}
    </div>
  );
}

function GraphPageShell({ children }: { children: ReactNode }) {
  return (
    <div className="knowledge-graph-page mx-auto w-full max-w-[1440px] min-w-0 px-1 pb-10">
      {children}
    </div>
  );
}

function countNodesByStatus(nodes: KnowledgeGraphNode[]): NodeStatusCounts {
  return nodes.reduce<NodeStatusCounts>(
    (counts, node) => ({
      ...counts,
      [node.status]: counts[node.status] + 1,
    }),
    {
      WEAK: 0,
      IN_PROGRESS: 0,
      MASTERED: 0,
      NOT_STARTED: 0,
    },
  );
}

function toggleFilterValue<T>(current: Set<T>, value: T): Set<T> {
  const next = new Set(current);
  if (next.has(value)) {
    next.delete(value);
  } else {
    next.add(value);
  }
  return next;
}

function knowledgeStatusLabel(status: string): string {
  return {
    WEAK: '薄弱',
    IN_PROGRESS: '学习中',
    NOT_STARTED: '未开始',
    MASTERED: '已掌握',
  }[status] ?? status;
}
