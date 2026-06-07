import { useCallback, useEffect, useMemo, useState } from 'react';
import { useOutletContext } from 'react-router-dom';
import {
  BookOpen,
  Check,
  ClipboardCheck,
  Code2,
  ExternalLink,
  FileText,
  Loader2,
  Play,
  RefreshCw,
  Route,
  RotateCw,
  SlidersHorizontal,
  X,
} from 'lucide-react';
import type { LayoutOutletContext } from '../components/Layout';
import { conversationApi } from '../api/conversation';
import { learningPathApi, smartEngineApi, type LearningPathCurrentResponse, type SmartEngineTaskResponse } from '../api/smartEngine';
import { downloadAuthenticatedFile, isInternalArtifactDownloadUrl } from '../utils/authenticatedDownload';
import type { PracticeQuestionBatch } from './LearningStudioDemoPage.types';
import { openStageTestSession } from './stageTestSessionStore';

type PhaseStatus = 'completed' | 'active' | 'pending';

interface LearningPhase {
  stepId: string;
  title: string;
  order: number;
  status: PhaseStatus;
  prerequisites: string[];
  targetKnowledgePoints: string[];
  checkpoint: string;
  progress: number;
  estimatedMinutes?: number;
}

interface StepResource {
  title: string;
  resourceType: string;
  summaryText: string;
  downloadUrl?: string;
  sourceName?: string;
  stepId?: string;
}

type StageTestStatus = 'idle' | 'generating' | 'error';

interface StageTestState {
  status: StageTestStatus;
  phase: LearningPhase | null;
  error: string;
}

const emptyStageTestState: StageTestState = {
  status: 'idle',
  phase: null,
  error: '',
};

const LEARNING_PATH_EMPTY_RECHECK_INTERVAL_MS = 2500;
const LEARNING_PATH_EMPTY_RECHECK_MAX_ATTEMPTS = 12;

export default function PersonalizedLearningPathPage() {
  const { isAuthenticated, openAuthModal } = useOutletContext<LayoutOutletContext>();
  const [data, setData] = useState<LearningPathCurrentResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [adjusting, setAdjusting] = useState(false);
  const [adjustmentIntent, setAdjustmentIntent] = useState('');
  const [adjustSubmiting, setAdjustSubmiting] = useState(false);
  const [pathRetrying, setPathRetrying] = useState(false);
  const [resourceRefreshing, setResourceRefreshing] = useState(false);
  const [resourceRefreshTask, setResourceRefreshTask] = useState<SmartEngineTaskResponse | null>(null);
  const [stageTest, setStageTest] = useState<StageTestState>(emptyStageTestState);
  const [generationWatchAttempts, setGenerationWatchAttempts] = useState(0);

  const loadCurrent = useCallback(async () => {
    if (!isAuthenticated) {
      setData(null);
      setResourceRefreshTask(null);
      setGenerationWatchAttempts(0);
      return;
    }
    setLoading(true);
    setError('');
    try {
      const response = await learningPathApi.current();
      setData(response);
      setResourceRefreshTask(response.resourceRefreshTask ?? null);
      if (hasLearningPathSteps(response) || isLiveTask(response.refreshTask?.status)) {
        setGenerationWatchAttempts(0);
      }
    } catch (err) {
      console.error('Failed to load learning path:', err);
      setError('学习路径读取失败，请稍后重试');
    } finally {
      setLoading(false);
    }
  }, [isAuthenticated]);

  useEffect(() => {
    void loadCurrent();
  }, [loadCurrent]);

  useEffect(() => {
    if (!isAuthenticated || typeof window === 'undefined') {
      return;
    }
    const handleProfileUpdated = () => {
      setGenerationWatchAttempts(0);
      void loadCurrent();
    };
    window.addEventListener('app:profile-updated', handleProfileUpdated);
    return () => window.removeEventListener('app:profile-updated', handleProfileUpdated);
  }, [isAuthenticated, loadCurrent]);

  useEffect(() => {
    const taskId = data?.refreshTask?.taskId;
    const status = data?.refreshTask?.status;
    if (!taskId || !isLiveTask(status)) {
      return;
    }
    const timer = window.setInterval(() => {
      smartEngineApi.getTask(taskId, { dedupe: false, retry: 0 })
        .then((task) => {
          setData((current) => current ? { ...current, refreshTask: task } : current);
          void loadCurrent();
        })
        .catch((err) => console.error('Failed to poll learning path task:', err));
    }, 3500);
    return () => window.clearInterval(timer);
  }, [data?.refreshTask?.taskId, data?.refreshTask?.status, loadCurrent]);

  useEffect(() => {
    if (!isAuthenticated || loading || hasLearningPathSteps(data) || isLiveTask(data?.refreshTask?.status) || isFailedTask(data?.refreshTask?.status)) {
      return;
    }
    if (generationWatchAttempts >= LEARNING_PATH_EMPTY_RECHECK_MAX_ATTEMPTS) {
      return;
    }
    const timer = window.setTimeout(() => {
      setGenerationWatchAttempts((attempts) => attempts + 1);
      void loadCurrent();
    }, LEARNING_PATH_EMPTY_RECHECK_INTERVAL_MS);
    return () => window.clearTimeout(timer);
  }, [data, generationWatchAttempts, isAuthenticated, loadCurrent, loading]);

  useEffect(() => {
    const taskId = resourceRefreshTask?.taskId;
    const status = resourceRefreshTask?.status;
    if (!taskId || !isLiveTask(status)) {
      return;
    }
    const timer = window.setInterval(() => {
      smartEngineApi.getTask(taskId, { dedupe: false, retry: 0 })
        .then((task) => {
          setResourceRefreshTask(task);
          if (isFailedTask(task.status)) {
            setError(task.errorMessage || readString(task.responseSummary?.summary) || '推荐资源刷新失败，请稍后重试');
            return;
          }
          if (!isLiveTask(task.status)) {
            setData((current) => mergeResourceRefreshTaskResult(current, task));
            void loadCurrent();
          }
        })
        .catch((err) => console.error('Failed to poll resource refresh task:', err));
    }, 3500);
    return () => window.clearInterval(timer);
  }, [resourceRefreshTask?.taskId, resourceRefreshTask?.status, loadCurrent]);

  const phases = useMemo(() => buildLearningPhases(data), [data]);
  const resourcesByStep = useMemo(() => buildResourcesByStep(data), [data]);
  const activePhase = phases.find((phase) => phase.status === 'active') ?? null;
  const refreshTask = data?.refreshTask ?? null;
  const waitingForLearningPathGeneration = !phases.length
    && !isFailedTask(refreshTask?.status)
    && (isLiveTask(refreshTask?.status) || generationWatchAttempts < LEARNING_PATH_EMPTY_RECHECK_MAX_ATTEMPTS);
  const completedCount = phases.filter((phase) => phase.status === 'completed').length;
  const overallProgress = phases.length ? Math.round((completedCount / phases.length) * 100) : 0;
  const resourceTaskRefreshing = isLiveTask(resourceRefreshTask?.status);
  const activePhaseResources = activePhase ? resourcesByStep.get(activePhase.stepId) ?? [] : [];
  const visibleResources = activePhase
    ? activePhaseResources.length ? activePhaseResources : flattenResources(resourcesByStep)
    : flattenResources(resourcesByStep);

  const submitAdjustment = async () => {
    if (!adjustmentIntent.trim()) {
      return;
    }
    setAdjustSubmiting(true);
    try {
      const response = await learningPathApi.adjust({ adjustmentIntent: adjustmentIntent.trim() });
      setData((current) => current
        ? { ...current, refreshTask: { taskId: response.taskId, status: response.status } }
        : current);
      setAdjustmentIntent('');
      setAdjusting(false);
      await loadCurrent();
    } catch (err) {
      console.error('Failed to adjust learning path:', err);
      setError('路径调整提交失败，请稍后重试');
    } finally {
      setAdjustSubmiting(false);
    }
  };

  const retryLearningPathGeneration = async () => {
    if (pathRetrying || isLiveTask(refreshTask?.status)) {
      return;
    }
    setPathRetrying(true);
    setError('');
    try {
      const response = await learningPathApi.adjust({ adjustmentIntent: '重新生成首版个性化学习路径' });
      const task = { taskId: response.taskId, status: response.status, serviceType: 'PERSONALIZED_LEARNING' };
      setGenerationWatchAttempts(0);
      setData((current) => current
        ? { ...current, refreshTask: task }
        : {
            userId: data?.userId ?? '',
            status: 'EMPTY',
            learningPath: {},
            refreshTask: task,
          });
      await loadCurrent();
    } catch (err) {
      console.error('Failed to retry learning path generation:', err);
      setError(getLocalErrorMessage(err, '学习路径重新生成失败，请稍后重试'));
    } finally {
      setPathRetrying(false);
    }
  };

  const refreshRecommendedResources = async () => {
    setResourceRefreshing(true);
    setError('');
    try {
      const phaseText = activePhase
        ? `当前阶段是「${activePhase.title}」，stepId=${activePhase.stepId}。`
        : '当前暂无明确活跃阶段。';
      const response = await learningPathApi.refreshResources({
        adjustmentIntent: `${phaseText} 请只刷新推荐资源，优先补充现成外部资源链接，尽量保持当前学习路径阶段结构不变。`,
      });
      const task = { taskId: response.taskId, status: response.status, serviceType: 'RESOURCE_PUSH' };
      setResourceRefreshTask(task);
      setData((current) => current ? { ...current, resourceRefreshTask: task } : current);
    } catch (err) {
      console.error('Failed to refresh recommended resources:', err);
      setError('推荐资源刷新提交失败，请稍后重试');
    } finally {
      setResourceRefreshing(false);
    }
  };

  const startStageTest = async (phase: LearningPhase) => {
    if (stageTest.status === 'generating') {
      return;
    }
    setStageTest({
      ...emptyStageTestState,
      status: 'generating',
      phase,
    });
    setError('');
    let receivedBatch: PracticeQuestionBatch | null = null;
    try {
      const conversation = await conversationApi.createConversation();
      const submitResp = await smartEngineApi.submit({
        conversationId: conversation.conversationId,
        serviceType: 'PRACTICE_JUDGE',
        params: buildStageTestParams(phase),
      });
      let streamError = '';
      await smartEngineApi.streamTask(submitResp.taskId, {
        onEvent: (event) => {
          if (event.event === 'question_batch') {
            const batch = readPracticeQuestionBatch(parseTaskStreamPayload(event.data));
            if (batch) {
              receivedBatch = batch;
              openStageTestSession({
                batch,
                phaseId: phase.stepId,
                phaseTitle: phase.title,
                conversationId: conversation.conversationId,
                taskId: submitResp.taskId,
              });
              setStageTest(emptyStageTestState);
            }
          }
          if (event.event === 'error') {
            const payload = parseTaskStreamPayload(event.data);
            streamError = readPayloadMessage(payload) || '阶段测试生成失败';
          }
        },
        onDone: () => undefined,
        onError: (err) => {
          streamError = err.message;
        },
      });
      if (streamError) {
        throw new Error(streamError);
      }
      if (!receivedBatch) {
        const task = await smartEngineApi.getTask(submitResp.taskId, { dedupe: false, retry: 2 });
        receivedBatch = readPracticeQuestionBatch(task.responseSummary);
      }
      if (!receivedBatch) {
        throw new Error('未收到阶段测试题目');
      }
      openStageTestSession({
        batch: receivedBatch,
        phaseId: phase.stepId,
        phaseTitle: phase.title,
        conversationId: conversation.conversationId,
        taskId: submitResp.taskId,
      });
      setStageTest(emptyStageTestState);
    } catch (err) {
      console.error('Failed to start stage test:', err);
      setStageTest((current) => ({
        ...current,
        status: 'error',
        error: getLocalErrorMessage(err, '阶段测试生成失败，请稍后重试'),
      }));
    }
  };

  if (!isAuthenticated) {
    return (
      <div className="mx-auto flex min-h-[calc(100dvh-78px)] max-w-[980px] items-center justify-center px-5">
        <div className="w-full max-w-[520px] px-6 py-8 text-center">
          <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-2xl bg-primary-50 text-primary-600 dark:bg-primary-500/15 dark:text-primary-300">
            <Route className="h-6 w-6" />
          </div>
          <h1 className="text-xl font-bold text-slate-900 dark:text-white">个性化学习路径</h1>
          <p className="mt-2 text-sm leading-6 text-slate-500 dark:text-slate-400">登录后查看你的阶段目标、学习进度和推荐资源。</p>
          <button
            type="button"
            onClick={() => openAuthModal('login', '登录后查看个性化学习路径')}
            className="mt-6 inline-flex h-11 items-center justify-center rounded-2xl bg-primary-600 px-5 text-sm font-semibold text-white shadow-lg shadow-primary-500/20 transition hover:bg-primary-700"
          >
            登录查看
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-[1180px] px-4 py-6 sm:px-6 sm:py-8">
      <section className="mb-5 overflow-hidden rounded-[28px] bg-white/76 shadow-[0_18px_56px_rgba(59,97,155,0.09)] backdrop-blur-xl dark:bg-slate-900/68 dark:shadow-slate-950/20">
        <div className="flex flex-col gap-4 px-5 py-5 md:flex-row md:items-center md:justify-between md:px-7">
          <div className="min-w-0">
            <div className="flex items-center gap-3">
              <div className="flex h-9 w-9 items-center justify-center rounded-full bg-primary-600 text-sm font-bold text-white">3</div>
              <h1 className="text-xl font-bold tracking-tight text-slate-950 dark:text-white sm:text-2xl">个性化学习路径</h1>
            </div>
            <p className="mt-2 max-w-[760px] text-sm leading-6 text-slate-500 dark:text-slate-400">
              系统会根据学习画像、练习表现和掌握情况，持续更新阶段目标与资源推荐。
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => void loadCurrent()}
              disabled={loading}
              className="inline-flex h-10 items-center gap-2 rounded-2xl bg-white px-4 text-sm font-semibold text-slate-600 shadow-sm shadow-blue-100/30 transition hover:text-primary-700 disabled:cursor-not-allowed disabled:opacity-60 dark:bg-slate-900 dark:text-slate-300"
            >
              <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
              刷新
            </button>
            <button
              type="button"
              onClick={() => setAdjusting((value) => !value)}
              className="inline-flex h-10 items-center gap-2 rounded-2xl bg-primary-50 px-4 text-sm font-semibold text-primary-700 shadow-sm shadow-primary-100/35 transition hover:bg-primary-100 dark:bg-primary-500/10 dark:text-primary-300"
            >
              <SlidersHorizontal className="h-4 w-4" />
              调整路径
            </button>
          </div>
        </div>

        {adjusting ? (
          <div className="bg-blue-50/42 px-5 py-4 dark:bg-primary-500/5 md:px-7">
            <div className="flex flex-col gap-3 md:flex-row">
              <input
                value={adjustmentIntent}
                onChange={(event) => setAdjustmentIntent(event.target.value)}
                placeholder="例如：我想先补数据库索引，再做更多项目实战"
                className="min-h-11 flex-1 rounded-2xl bg-white/88 px-4 text-sm text-slate-700 outline-none shadow-sm shadow-blue-100/30 transition focus:bg-white focus:shadow-md focus:shadow-primary-100/35 dark:bg-slate-900/72 dark:text-slate-200 dark:shadow-none dark:focus:bg-slate-900"
              />
              <button
                type="button"
                onClick={() => void submitAdjustment()}
                disabled={adjustSubmiting || !adjustmentIntent.trim()}
                className="inline-flex h-11 items-center justify-center gap-2 rounded-2xl bg-primary-600 px-5 text-sm font-semibold text-white shadow-lg shadow-primary-500/20 transition hover:bg-primary-700 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {adjustSubmiting ? <Loader2 className="h-4 w-4 animate-spin" /> : <RotateCw className="h-4 w-4" />}
                提交调整
              </button>
            </div>
          </div>
        ) : null}

        <div className="grid gap-5 p-5 md:grid-cols-[minmax(0,1fr)_320px] md:p-7 lg:grid-cols-[minmax(0,1fr)_360px]">
          <div className="min-w-0">
            <StatusLegend />
            {error ? <div className="mb-4 rounded-2xl bg-red-50 px-4 py-3 text-sm text-red-600 dark:bg-red-500/10 dark:text-red-300">{error}</div> : null}
            <StageTestPanel
              state={stageTest}
              onClose={() => setStageTest(emptyStageTestState)}
            />
            {loading && !data ? (
              <div className="flex min-h-[360px] items-center justify-center rounded-[20px] bg-slate-50/70 dark:bg-slate-950/30">
                <Loader2 className="mr-2 h-5 w-5 animate-spin text-primary-500" />
                <span className="text-sm text-slate-500 dark:text-slate-400">正在读取学习路径</span>
              </div>
            ) : phases.length ? (
              <div className="relative mt-5">
                {phases.map((phase, index) => (
                  <PhaseCard
                    key={phase.stepId || index}
                    phase={phase}
                    isLast={index === phases.length - 1}
                    onStartStageTest={() => void startStageTest(phase)}
                    stageTestBusy={stageTest.status === 'generating'}
                  />
                ))}
              </div>
            ) : (
              <EmptyPath
                refreshTask={refreshTask}
                pendingGeneration={waitingForLearningPathGeneration}
                retrying={pathRetrying}
                onRetry={() => void retryLearningPathGeneration()}
              />
            )}
          </div>

          <aside className="min-w-0 rounded-[22px] bg-white/62 p-4 shadow-sm shadow-blue-100/24 dark:bg-slate-950/30">
            <div className="mb-4 flex items-center justify-between gap-3">
              <h2 className="text-base font-bold text-slate-900 dark:text-white">推荐资源</h2>
              <div className="flex shrink-0 items-center gap-2">
                <RefreshBadge task={resourceRefreshTask} />
                <button
                  type="button"
                  onClick={() => void refreshRecommendedResources()}
                  disabled={resourceRefreshing || resourceTaskRefreshing}
                  title="刷新推荐资源"
                  className="inline-flex h-8 items-center gap-1.5 rounded-full bg-white px-2.5 text-xs font-semibold text-slate-600 shadow-sm shadow-blue-100/25 transition hover:text-primary-700 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-slate-900 dark:text-slate-300"
                >
                  <RefreshCw className={`h-3.5 w-3.5 ${resourceRefreshing || resourceTaskRefreshing ? 'animate-spin' : ''}`} />
                  刷新
                </button>
              </div>
            </div>
            {activePhase ? (
              <div className="mb-4 rounded-2xl bg-blue-50/70 px-4 py-3 dark:bg-primary-500/10">
                <div className="text-xs font-semibold text-primary-600 dark:text-primary-300">当前阶段</div>
                <div className="mt-1 text-sm font-bold text-slate-800 dark:text-slate-100">{activePhase.title}</div>
                <div className="mt-3 h-2 rounded-full bg-slate-200 dark:bg-slate-800">
                  <div className="h-full rounded-full bg-primary-600" style={{ width: `${Math.max(8, activePhase.progress)}%` }} />
                </div>
                <div className="mt-2 text-xs text-slate-500 dark:text-slate-400">阶段进度 {activePhase.progress}% · 总进度 {overallProgress}%</div>
              </div>
            ) : null}
            <div className="space-y-3">
              {visibleResources.slice(0, 5).map((resource, index) => (
                <ResourceCard key={`${resource.title}-${index}`} resource={resource} />
              ))}
              {!visibleResources.length ? (
                <div className="rounded-2xl bg-slate-50/60 px-4 py-8 text-center text-sm text-slate-400 dark:bg-slate-900/45">
                  当前阶段暂无合适资源，稍后刷新会继续补齐。
                </div>
              ) : null}
            </div>
          </aside>
        </div>
      </section>
    </div>
  );
}

function StatusLegend() {
  return (
    <div className="mb-4 flex flex-wrap items-center gap-5 text-xs font-semibold text-slate-500 dark:text-slate-400">
      <LegendDot color="bg-emerald-500" label="已完成" />
      <LegendDot color="bg-primary-600" label="进行中" />
      <LegendDot color="bg-slate-300" label="待开始" />
    </div>
  );
}

function LegendDot({ color, label }: { color: string; label: string }) {
  return <span className="inline-flex items-center gap-2"><span className={`h-2.5 w-2.5 rounded-full ${color}`} />{label}</span>;
}

function PhaseCard({
  phase,
  isLast,
  onStartStageTest,
  stageTestBusy,
}: {
  phase: LearningPhase;
  isLast: boolean;
  onStartStageTest: () => void;
  stageTestBusy: boolean;
}) {
  const active = phase.status === 'active';
  const completed = phase.status === 'completed';
  return (
    <div className="relative grid grid-cols-[34px_minmax(0,1fr)] gap-3 pb-5">
      {!isLast ? <div className="absolute left-[16px] top-9 h-[calc(100%-14px)] w-px bg-[repeating-linear-gradient(to_bottom,rgba(203,213,225,0.9)_0_6px,transparent_6px_12px)] dark:bg-[repeating-linear-gradient(to_bottom,rgba(51,65,85,0.9)_0_6px,transparent_6px_12px)]" /> : null}
      <div className={`relative z-10 flex h-8 w-8 items-center justify-center rounded-full text-xs font-bold ${completed ? 'bg-emerald-500 text-white' : active ? 'bg-primary-600 text-white' : 'bg-slate-200 text-slate-400 dark:bg-slate-800'}`}>
        {completed ? <Check className="h-4 w-4" /> : phase.order}
      </div>
      <div className={`rounded-[20px] p-4 transition ${active ? 'bg-blue-50/70 shadow-lg shadow-primary-100/42 dark:bg-primary-500/10 dark:shadow-none' : 'bg-white/86 shadow-sm shadow-blue-100/20 dark:bg-slate-900/70 dark:shadow-none'}`}>
        <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <div className={`text-sm font-bold ${active ? 'text-primary-700 dark:text-primary-300' : 'text-slate-800 dark:text-slate-100'}`}>阶段{phase.order}：{phase.title}</div>
            {phase.prerequisites.length ? (
              <div className="mt-2 text-xs leading-5 text-slate-500 dark:text-slate-400">先修知识：{phase.prerequisites.join('、')}</div>
            ) : null}
          </div>
          <span className={`w-fit rounded-full px-2.5 py-1 text-xs font-semibold ${completed ? 'bg-emerald-50 text-emerald-600 dark:bg-emerald-500/10 dark:text-emerald-300' : active ? 'bg-primary-100 text-primary-700 dark:bg-primary-500/15 dark:text-primary-300' : 'bg-slate-100 text-slate-400 dark:bg-slate-800 dark:text-slate-500'}`}>
            {completed ? '已完成' : active ? '进行中' : '待开始'}
          </span>
        </div>
        {active ? (
          <div className="mt-4 rounded-2xl bg-white/85 p-4 shadow-sm shadow-blue-100/25 dark:bg-slate-950/40 dark:shadow-none">
            <div className="text-sm font-bold text-slate-700 dark:text-slate-200">当前重点：{phase.checkpoint || phase.targetKnowledgePoints[0] || phase.title}</div>
            <div className="mt-3 h-2 rounded-full bg-slate-200 dark:bg-slate-800">
              <div className="h-full rounded-full bg-primary-600" style={{ width: `${Math.max(8, phase.progress)}%` }} />
            </div>
            <div className="mt-3 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div className="flex flex-wrap gap-4 text-xs text-slate-500 dark:text-slate-400">
                <span>进度 {phase.progress}%</span>
                {phase.estimatedMinutes ? <span>预计 {phase.estimatedMinutes} 分钟</span> : null}
              </div>
              <button
                type="button"
                onClick={onStartStageTest}
                disabled={stageTestBusy}
                className="inline-flex h-9 w-fit items-center justify-center gap-2 rounded-2xl bg-primary-600 px-4 text-xs font-semibold text-white shadow-md shadow-primary-500/20 transition hover:bg-primary-700 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {stageTestBusy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ClipboardCheck className="h-3.5 w-3.5" />}
                阶段测试
              </button>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function StageTestPanel({
  state,
  onClose,
}: {
  state: StageTestState;
  onClose: () => void;
}) {
  if (state.status === 'idle') {
    return null;
  }

  return (
    <div className="mb-5 rounded-[20px] bg-white/86 p-4 shadow-lg shadow-blue-100/36 dark:bg-slate-950/50">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-sm font-bold text-slate-900 dark:text-white">
            <ClipboardCheck className="h-4 w-4 text-primary-600 dark:text-primary-300" />
            {state.phase ? `${state.phase.title} · 阶段测试` : '阶段测试'}
          </div>
          <div className="mt-1 text-xs text-slate-500 dark:text-slate-400">
            题目生成后会进入独立答题页，提交后统一批改
          </div>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="inline-flex h-8 w-8 items-center justify-center rounded-full text-slate-400 transition hover:bg-slate-100 hover:text-slate-700 dark:hover:bg-slate-800 dark:hover:text-slate-200"
          title="关闭"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {state.status === 'generating' ? (
        <div className="mt-4 flex min-h-[120px] items-center justify-center rounded-2xl bg-blue-50/60 text-sm text-primary-700 dark:bg-primary-500/10 dark:text-primary-300">
          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          正在生成阶段测试
        </div>
      ) : null}

      {state.status === 'error' ? (
        <div className="mt-4 rounded-2xl bg-red-50 px-4 py-3 text-sm text-red-600 dark:bg-red-500/10 dark:text-red-300">
          {state.error || '阶段测试失败，请稍后重试'}
        </div>
      ) : null}
    </div>
  );
}

function ResourceCard({ resource }: { resource: StepResource }) {
  const Icon = resourceIcon(resource.resourceType);
  const internalDownload = resource.downloadUrl ? isInternalArtifactDownloadUrl(resource.downloadUrl) : false;
  const handleDownload = async () => {
    if (!resource.downloadUrl) {
      return;
    }
    try {
      await downloadAuthenticatedFile({
        url: resource.downloadUrl,
        title: resource.title,
      });
    } catch (error) {
      window.alert(error instanceof Error ? error.message : '下载失败，请稍后重试');
    }
  };
  const content = (
    <>
      <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-slate-100 text-primary-600 dark:bg-slate-800 dark:text-primary-300">
        <Icon className="h-5 w-5" />
      </div>
      <div className="min-w-0">
        <div className="truncate text-sm font-bold text-slate-800 dark:text-slate-100">{resource.title}</div>
        <div className="mt-1 line-clamp-2 text-xs leading-5 text-slate-500 dark:text-slate-400">{resource.summaryText || '适合当前阶段的学习资源'}</div>
      </div>
      {resource.downloadUrl ? <ExternalLink className="ml-auto h-4 w-4 shrink-0 text-slate-300" /> : null}
    </>
  );
  if (resource.downloadUrl && internalDownload) {
    return (
      <button
        type="button"
        onClick={() => { void handleDownload(); }}
        className="flex min-h-[72px] w-full items-center gap-3 rounded-2xl bg-white/88 px-3 py-2 text-left transition hover:bg-primary-50/55 hover:shadow-md hover:shadow-blue-100/40 dark:bg-slate-900/80 dark:hover:bg-primary-500/10"
      >
        {content}
      </button>
    );
  }
  if (resource.downloadUrl) {
    return (
      <a href={resource.downloadUrl} target="_blank" rel="noreferrer" className="flex min-h-[72px] items-center gap-3 rounded-2xl bg-white/88 px-3 py-2 transition hover:bg-primary-50/55 hover:shadow-md hover:shadow-blue-100/40 dark:bg-slate-900/80 dark:hover:bg-primary-500/10">
        {content}
      </a>
    );
  }
  return <div className="flex min-h-[72px] items-center gap-3 rounded-2xl bg-white/88 px-3 py-2 dark:bg-slate-900/80">{content}</div>;
}

function RefreshBadge({ task }: { task: SmartEngineTaskResponse | null }) {
  if (!task?.status) {
    return <span className="rounded-full bg-slate-100 px-2.5 py-1 text-xs font-semibold text-slate-500 dark:bg-slate-800">可刷新</span>;
  }
  if (isLiveTask(task.status)) {
    return <span className="inline-flex items-center gap-1 rounded-full bg-primary-50 px-2.5 py-1 text-xs font-semibold text-primary-700 dark:bg-primary-500/10 dark:text-primary-300"><Loader2 className="h-3 w-3 animate-spin" />同步中</span>;
  }
  if (isFailedTask(task.status)) {
    return <span className="rounded-full bg-red-50 px-2.5 py-1 text-xs font-semibold text-red-600 dark:bg-red-500/10 dark:text-red-300">刷新失败</span>;
  }
  return <span className="rounded-full bg-emerald-50 px-2.5 py-1 text-xs font-semibold text-emerald-600 dark:bg-emerald-500/10 dark:text-emerald-300">已更新</span>;
}

function EmptyPath({
  refreshTask,
  pendingGeneration,
  retrying,
  onRetry,
}: {
  refreshTask: SmartEngineTaskResponse | null;
  pendingGeneration: boolean;
  retrying: boolean;
  onRetry: () => void;
}) {
  const failed = isFailedTask(refreshTask?.status);
  return (
    <div className="flex min-h-[360px] flex-col items-center justify-center rounded-[20px] bg-slate-50/70 px-6 text-center dark:bg-slate-950/30">
      {pendingGeneration ? (
        <Loader2 className="mb-3 h-10 w-10 animate-spin text-primary-500" />
      ) : (
        <Route className="mb-3 h-10 w-10 text-primary-500" />
      )}
      <div className="text-base font-bold text-slate-800 dark:text-slate-100">
        {pendingGeneration ? '正在准备学习路径' : '暂无学习路径'}
      </div>
      <p className="mt-2 max-w-[420px] text-sm leading-6 text-slate-500 dark:text-slate-400">
        {pendingGeneration
          ? '画像保存后会自动准备首版路径，完成后页面会更新。'
          : failed
            ? '上一次生成失败，可以重新发起。'
            : '完善学习画像后，系统会为你准备首版阶段路径。'}
      </p>
      <div className="mt-4 flex flex-col items-center gap-3">
        <RefreshBadge task={refreshTask} />
        {failed ? (
          <button
            type="button"
            onClick={onRetry}
            disabled={retrying}
            className="inline-flex h-10 items-center justify-center gap-2 rounded-2xl bg-primary-600 px-4 text-sm font-semibold text-white shadow-lg shadow-primary-500/20 transition hover:bg-primary-700 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {retrying ? <Loader2 className="h-4 w-4 animate-spin" /> : <RotateCw className="h-4 w-4" />}
            重新生成
          </button>
        ) : null}
      </div>
    </div>
  );
}

function buildLearningPhases(data: LearningPathCurrentResponse | null): LearningPhase[] {
  const plan = data?.learningPath;
  const rawSteps = Array.isArray(plan?.steps) ? plan.steps : [];
  const activeStepId = readString(data?.activeStep?.stepId);
  return rawSteps
    .filter((item): item is Record<string, unknown> => Boolean(item && typeof item === 'object' && !Array.isArray(item)))
    .map((step, index) => {
      const stepId = readString(step.stepId) || `step-${index + 1}`;
      const explicitStatus = readString(step.status).toUpperCase();
      const normalizedStatus = explicitStatus.replace(/[^A-Z0-9]+/g, '_').replace(/^_+|_+$/g, '');
      const active = (activeStepId && stepId === activeStepId) || isActivePhaseStatus(normalizedStatus);
      const status: PhaseStatus = normalizedStatus.includes('COMPLETE') || normalizedStatus.includes('MASTER')
        ? 'completed'
        : active
          ? 'active'
          : 'pending';
      return {
        stepId,
        title: readString(step.title) || readString(step.intent) || `阶段 ${index + 1}`,
        order: readNumber(step.order) || index + 1,
        status,
        prerequisites: readStringArray(step.prerequisites).length ? readStringArray(step.prerequisites) : readStringArray(step.targetKnowledgePoints).slice(0, 3),
        targetKnowledgePoints: readStringArray(step.targetKnowledgePoints),
        checkpoint: readString(step.checkpoint) || readString(step.successCriteria) || readString(step.objective),
        progress: clampProgress(readNumber(step.progress) ?? readNumber(step.progressPercent) ?? (status === 'completed' ? 100 : 0)),
        estimatedMinutes: readNumber(step.estimatedMinutes),
      };
    })
    .sort((left, right) => left.order - right.order);
}

function hasLearningPathSteps(data: LearningPathCurrentResponse | null): boolean {
  const steps = data?.learningPath?.steps;
  return Array.isArray(steps) && steps.length > 0;
}

function isActivePhaseStatus(status: string): boolean {
  if (!status || status.startsWith('NOT_')) {
    return false;
  }
  if (['INACTIVE', 'PENDING', 'COMPLETED', 'DONE'].includes(status)) {
    return false;
  }
  return status.includes('RUN') || status.includes('PROGRESS') || status === 'ACTIVE';
}

function buildResourcesByStep(data: LearningPathCurrentResponse | null): Map<string, StepResource[]> {
  const result = new Map<string, StepResource[]>();
  const plan = data?.resourcePushPlan;
  const rawStepResources = Array.isArray(plan?.stepResources) ? plan.stepResources : [];
  for (const rawStep of rawStepResources) {
    if (!rawStep || typeof rawStep !== 'object' || Array.isArray(rawStep)) {
      continue;
    }
    const step = rawStep as Record<string, unknown>;
    const stepId = readString(step.stepId);
    const resources = Array.isArray(step.resources) ? step.resources : [];
    result.set(stepId, resources
      .filter((item): item is Record<string, unknown> => Boolean(item && typeof item === 'object' && !Array.isArray(item)))
      .map((resource) => ({
        stepId,
        title: readString(resource.title) || '学习资源',
        resourceType: readString(resource.resourceType) || readString(resource.type) || 'DOCUMENT',
        summaryText: readString(resource.summaryText) || readString(resource.matchReason),
        downloadUrl: readString(resource.downloadUrl) || readString(resource.url),
        sourceName: readString(resource.sourceName) || readString(resource.source),
      })));
  }
  if (!result.size) {
    const fallbackStepId = firstLearningStepId(data) || 'all';
    const resources = readRecordArray(data?.pushedResources).map((resource) => ({
      stepId: readString(resource.stepId) || fallbackStepId,
      title: readString(resource.title) || '学习资源',
      resourceType: readString(resource.resourceType) || readString(resource.type) || 'DOCUMENT',
      summaryText: readString(resource.summaryText) || readString(resource.matchReason),
      downloadUrl: readString(resource.downloadUrl) || readString(resource.url),
      sourceName: readString(resource.sourceName) || readString(resource.source),
    }));
    for (const resource of resources) {
      const stepId = resource.stepId || fallbackStepId;
      const stepResources = result.get(stepId) ?? [];
      stepResources.push(resource);
      result.set(stepId, stepResources);
    }
  }
  return result;
}

function flattenResources(resourcesByStep: Map<string, StepResource[]>): StepResource[] {
  return Array.from(resourcesByStep.values()).flat();
}

function buildStageTestParams(phase: LearningPhase): Record<string, unknown> {
  return {
    purpose: 'STAGE_TEST',
    topic: phase.title,
    query: `${phase.title} 阶段测试`,
    count: 10,
    questionCount: 10,
    learningContext: {
      activeLearningStepId: phase.stepId,
      activeLearningStepTitle: phase.title,
      chapter: phase.title,
      questionCount: 10,
    },
  };
}

function parseTaskStreamPayload(raw: string): Record<string, unknown> | undefined {
  try {
    const parsed = JSON.parse(raw) as { payload?: Record<string, unknown> };
    return parsed.payload;
  } catch {
    return {
      message: raw,
    };
  }
}

function readPracticeQuestionBatch(payload: Record<string, unknown> | undefined): PracticeQuestionBatch | null {
  const record = readRecord(payload);
  const source = readRecord(record?.practiceQuestionBatch)
    ?? readRecord(record?.questionBatch)
    ?? record;
  const questions = Array.isArray(source?.questions) ? source.questions : null;
  if (!source || !questions) {
    return null;
  }
  return {
    title: readString(source.title) || '阶段测试',
    topic: readString(source.topic),
    difficulty: readString(source.difficulty),
    description: readString(source.description),
    assessmentDimension: readString(source.assessmentDimension),
    submitLabel: readString(source.submitLabel),
    generatedBy: readString(source.generatedBy),
    contentOrigin: readString(source.contentOrigin),
    provider: readString(source.provider),
    model: readString(source.model),
    agentName: readString(source.agentName),
    evidenceIds: readStringArray(source.evidenceIds),
    fallback: typeof source.fallback === 'boolean' ? source.fallback : undefined,
    fromCache: typeof source.fromCache === 'boolean' ? source.fromCache : undefined,
    questions: questions
      .map((item) => readRecord(item))
      .filter((item): item is Record<string, unknown> => Boolean(item))
      .map((item, index) => ({
        questionId: readString(item.questionId) || `question-${index + 1}`,
        questionType: readString(item.questionType) || 'SHORT_ANSWER',
        stem: readString(item.stem),
        options: readStringArray(item.options),
        answer: readString(item.answer),
        knowledgeTags: readStringArray(item.knowledgeTags),
        difficultyLevel: readString(item.difficultyLevel),
        explanation: readString(item.explanation),
      }))
      .filter((question) => question.stem),
  };
}

function readPayloadMessage(payload: Record<string, unknown> | undefined): string {
  if (!payload) {
    return '';
  }
  return readString(payload.message) || readString(payload.text) || readString(payload.summary);
}

function getLocalErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof Error && error.message.trim()) {
    return error.message.trim();
  }
  return fallback;
}

function resourceIcon(type: string) {
  const normalized = type.toUpperCase();
  if (normalized.includes('VIDEO')) {
    return Play;
  }
  if (normalized.includes('CODE') || normalized.includes('CASE')) {
    return Code2;
  }
  if (normalized.includes('QUIZ')) {
    return BookOpen;
  }
  return FileText;
}

function isLiveTask(status?: string | null): boolean {
  const normalized = String(status || '').toUpperCase();
  return normalized === 'PENDING' || normalized === 'RUNNING';
}

function isFailedTask(status?: string | null): boolean {
  const normalized = String(status || '').toUpperCase();
  return normalized === 'FAILED' || normalized === 'CANCELLED' || normalized === 'TIMEOUT';
}

function mergeResourceRefreshTaskResult(
  current: LearningPathCurrentResponse | null,
  task: SmartEngineTaskResponse,
): LearningPathCurrentResponse | null {
  if (!current) {
    return current;
  }
  const summary = task.responseSummary;
  const resourcePushPlan = readRecord(summary?.resourcePushPlan);
  const pushedResources = readRecordArray(summary?.pushedResources);
  return {
    ...current,
    resourcePushPlan: resourcePushPlan ?? current.resourcePushPlan,
    pushedResources: pushedResources.length ? pushedResources : current.pushedResources,
    resourceRefreshTask: task,
  };
}

function firstLearningStepId(data: LearningPathCurrentResponse | null): string {
  const steps = data?.learningPath?.steps;
  if (!Array.isArray(steps)) {
    return '';
  }
  for (const step of steps) {
    const record = readRecord(step);
    const stepId = readString(record?.stepId);
    if (stepId) {
      return stepId;
    }
  }
  return '';
}

function readRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

function readRecordArray(value: unknown): Array<Record<string, unknown>> {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.filter((item): item is Record<string, unknown> => Boolean(item && typeof item === 'object' && !Array.isArray(item)));
}

function readString(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function readNumber(value: unknown): number | undefined {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : undefined;
  }
  return undefined;
}

function readStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.map((item) => String(item).trim()).filter(Boolean);
}

function clampProgress(value: number): number {
  return Math.max(0, Math.min(100, Math.round(value)));
}
