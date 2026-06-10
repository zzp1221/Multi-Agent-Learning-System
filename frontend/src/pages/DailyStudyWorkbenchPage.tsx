import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Link, useNavigate, useOutletContext } from 'react-router-dom';
import {
  BookOpenCheck,
  CheckCircle2,
  ClipboardCheck,
  Compass,
  ExternalLink,
  FileText,
  GitBranch,
  Layers3,
  LoaderCircle,
  Play,
  RefreshCw,
  Route,
  Target,
  TriangleAlert,
} from 'lucide-react';
import type { LayoutOutletContext } from '../components/Layout';
import { conversationApi } from '../api/conversation';
import { getErrorMessage } from '../api/request';
import { smartEngineApi } from '../api/smartEngine';
import { resourcesApi, type ResourceItem } from '../api/resources';
import { studyWorkbenchApi, type DailyStudyWorkbenchResponse, type DailyTaskItem } from '../api/studyWorkbench';
import { readStreamMessage, readStreamPayload } from '../api/sse';
import type { PracticeQuestionBatch } from './LearningStudioDemoPage.types';
import { readPracticeQuestionBatch } from './LearningStudioDemoPage.utils';
import { openStageTestSession } from './stageTestSessionStore';

type StageGenerationStatus = 'idle' | 'generating' | 'failed';

export default function DailyStudyWorkbenchPage() {
  const { isAuthenticated, openAuthModal } = useOutletContext<LayoutOutletContext>();
  const navigate = useNavigate();
  const [data, setData] = useState<DailyStudyWorkbenchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');
  const [stageStatus, setStageStatus] = useState<StageGenerationStatus>('idle');
  const [stageError, setStageError] = useState('');
  const [savingResourceId, setSavingResourceId] = useState('');
  const abortRef = useRef<AbortController | null>(null);

  const loadDaily = useCallback(async () => {
    if (!isAuthenticated) {
      setData(null);
      setError('');
      return;
    }
    setLoading(true);
    setError('');
    try {
      setData(await studyWorkbenchApi.daily());
    } catch (loadError) {
      setError(getErrorMessage(loadError));
    } finally {
      setLoading(false);
    }
  }, [isAuthenticated]);

  useEffect(() => {
    void loadDaily();
  }, [loadDaily]);

  useEffect(() => () => abortRef.current?.abort(), []);

  const activeStep = data?.activeStep ?? null;
  const stageTitle = readString(activeStep?.title) || '当前学习阶段';
  const targetPoints = readStringArray(activeStep?.targetKnowledgePoints);
  const stageProgress = clampPercent(readNumber(activeStep?.progress));
  const currentResource = data?.recommendedResources.find((item) => !item.completed) ?? data?.recommendedResources[0] ?? null;
  const weakNodes = useMemo(() => {
    return data?.knowledgeGraph.nodes
      .filter((node) => node.status === 'WEAK' || node.status === 'IN_PROGRESS')
      .slice(0, 5) ?? [];
  }, [data]);

  const refresh = async () => {
    if (!isAuthenticated) {
      return;
    }
    setRefreshing(true);
    setError('');
    try {
      setData(await studyWorkbenchApi.refreshDaily());
    } catch (refreshError) {
      setError(getErrorMessage(refreshError));
    } finally {
      setRefreshing(false);
    }
  };

  const markResourceProgress = async (resource: ResourceItem, completed: boolean) => {
    setSavingResourceId(resource.id);
    setError('');
    try {
      const state = await resourcesApi.progress(resource.id, {
        progress: completed ? 100 : Math.max(15, resource.progress ?? 0),
        completed,
      });
      setData((current) => current ? {
        ...current,
        recommendedResources: current.recommendedResources.map((item) => item.id === state.resourceId
          ? {
              ...item,
              progress: state.progress,
              completed: state.completed,
              lastStudyAt: state.lastStudyAt ?? item.lastStudyAt,
            }
          : item),
      } : current);
    } catch (saveError) {
      setError(getErrorMessage(saveError));
    } finally {
      setSavingResourceId('');
    }
  };

  const startStageTest = async () => {
    if (!activeStep || stageStatus === 'generating') {
      return;
    }
    abortRef.current?.abort();
    const abortController = new AbortController();
    abortRef.current = abortController;
    setStageStatus('generating');
    setStageError('');
    let receivedBatch: PracticeQuestionBatch | null = null;
    try {
      const conversation = await conversationApi.createConversation();
      const submitResp = await smartEngineApi.submit({
        conversationId: conversation.conversationId,
        serviceType: 'PRACTICE_JUDGE',
        params: {
          purpose: 'STAGE_TEST',
          topic: stageTitle,
          query: `${stageTitle} 阶段检测`,
          count: 10,
          questionCount: 10,
          learningContext: {
            activeLearningStepId: readString(activeStep.stepId),
            activeLearningStepTitle: stageTitle,
            chapter: stageTitle,
            knowledgeTags: targetPoints,
            questionCount: 10,
          },
        },
      });
      let streamError = '';
      await smartEngineApi.streamTask(submitResp.taskId, {
        onEvent: (event) => {
          const payload = event.payload ?? readStreamPayload(event.data);
          if (event.event === 'question_batch') {
            const batch = readPracticeQuestionBatch(payload);
            if (batch) {
              receivedBatch = batch;
              openStageTestSession({
                batch,
                phaseId: readString(activeStep.stepId),
                phaseTitle: stageTitle,
                conversationId: conversation.conversationId,
                taskId: submitResp.taskId,
              });
              setStageStatus('idle');
            }
          }
          if (event.event === 'error') {
            streamError = readStreamMessage(payload) || '阶段检测生成失败';
          }
        },
        onDone: () => undefined,
        onError: (streamFailure) => {
          streamError = getErrorMessage(streamFailure);
        },
      }, abortController.signal);
      if (abortController.signal.aborted) {
        return;
      }
      if (streamError) {
        throw new Error(streamError);
      }
      if (!receivedBatch) {
        const task = await smartEngineApi.getTask(submitResp.taskId, { dedupe: false, retry: 2 });
        receivedBatch = readPracticeQuestionBatch(task.responseSummary);
      }
      if (!receivedBatch) {
        throw new Error('未收到完整阶段检测题目');
      }
      openStageTestSession({
        batch: receivedBatch,
        phaseId: readString(activeStep.stepId),
        phaseTitle: stageTitle,
        conversationId: conversation.conversationId,
        taskId: submitResp.taskId,
      });
      setStageStatus('idle');
    } catch (stageFailure) {
      if (!abortController.signal.aborted) {
        setStageStatus('failed');
        setStageError(getErrorMessage(stageFailure));
      }
    } finally {
      if (abortRef.current === abortController) {
        abortRef.current = null;
      }
    }
  };

  if (!isAuthenticated) {
    return (
      <WorkbenchShell>
        <AccessState
          icon={<Compass className="h-6 w-6" />}
          title="登录后进入每日学习工作台"
          description="工作台会把当前阶段、到期错题、推荐资源、画像摘要和阶段检测集中到一个学习闭环。"
          actionLabel="登录查看"
          onAction={() => openAuthModal('login', '登录后查看每日学习工作台')}
        />
      </WorkbenchShell>
    );
  }

  if (loading && !data) {
    return (
      <WorkbenchShell>
        <LoadingPanel text="正在整理今日学习任务" />
      </WorkbenchShell>
    );
  }

  if (error && !data) {
    return (
      <WorkbenchShell>
        <AccessState
          icon={<TriangleAlert className="h-6 w-6" />}
          title="每日工作台读取失败"
          description={error}
          actionLabel="重新加载"
          onAction={() => void loadDaily()}
        />
      </WorkbenchShell>
    );
  }

  return (
    <WorkbenchShell>
      <div className="space-y-5">
        <section className="overflow-hidden rounded-[28px] bg-white/76 shadow-[0_18px_56px_rgba(59,97,155,0.10)] backdrop-blur-xl dark:bg-slate-900/68 dark:shadow-slate-950/20">
          <div className="grid gap-5 p-5 md:grid-cols-[minmax(0,1fr)_300px] md:p-6">
            <div className="min-w-0">
              <div className="inline-flex items-center gap-2 rounded-full bg-primary-50 px-3 py-1 text-xs font-semibold text-primary-700 dark:bg-primary-500/10 dark:text-primary-300">
                <Compass className="h-3.5 w-3.5" />
                今日学习闭环
              </div>
              <h1 className="mt-4 text-2xl font-semibold tracking-normal text-slate-950 dark:text-white md:text-3xl">
                {data?.summary.nextAction || '今天从一个可完成的任务开始'}
              </h1>
              <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-500 dark:text-slate-400">
                工作台会随阶段测试、错题复习和资源学习记录自动刷新；页面刷新后从后端重新聚合当前状态。
              </p>
              {error ? (
                <InlineError text={error} />
              ) : null}
            </div>
            <div className="rounded-2xl bg-slate-50/82 p-4 dark:bg-slate-950/36">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <div className="text-xs text-slate-400 dark:text-slate-500">今日完成度</div>
                  <div className="mt-1 text-3xl font-semibold text-slate-950 dark:text-white">
                    {data?.summary.progressPercent ?? 0}%
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => void refresh()}
                  disabled={refreshing}
                  className="inline-flex h-10 items-center gap-2 rounded-xl bg-white px-3 text-sm font-medium text-slate-600 shadow-sm shadow-slate-200/60 transition hover:bg-primary-50 hover:text-primary-700 disabled:cursor-not-allowed disabled:opacity-60 dark:bg-slate-900 dark:text-slate-300 dark:shadow-none"
                >
                  {refreshing ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
                  刷新
                </button>
              </div>
              <div className="mt-4 h-2 overflow-hidden rounded-full bg-white dark:bg-slate-800">
                <div className="h-full rounded-full bg-primary-500" style={{ width: `${data?.summary.progressPercent ?? 0}%` }} />
              </div>
              <div className="mt-3 grid grid-cols-3 gap-2 text-center text-xs">
                <MiniStat label="到期错题" value={data?.summary.dueMistakeCount ?? 0} />
                <MiniStat label="推荐资源" value={data?.summary.recommendedResourceCount ?? 0} />
                <MiniStat label="薄弱点" value={data?.summary.weakKnowledgeCount ?? 0} />
              </div>
            </div>
          </div>
        </section>

        {!data?.dataAvailable ? (
          <Panel>
            <EmptyState
              title="还没有足够的学习记录"
              description="先完成一次问答、练习或画像初始化，系统会把路径、错题、资源和知识点串成每日任务。"
              action={<LinkButton to="/" label="开始学习对话" />}
            />
          </Panel>
        ) : null}

        <div className="grid gap-5 xl:grid-cols-[minmax(0,1.05fr)_minmax(340px,0.95fr)]">
          <Panel>
            <SectionHeader icon={<Route className="h-5 w-5" />} title="当前阶段" subtitle="阶段进度会由资源学习、阶段检测和错题回流共同推动。" />
            {activeStep ? (
              <div className="mt-5 space-y-4">
                <div className="rounded-2xl bg-slate-50/78 p-4 dark:bg-slate-950/32">
                  <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                    <div className="min-w-0">
                      <h2 className="text-lg font-semibold text-slate-950 dark:text-white">{stageTitle}</h2>
                      <p className="mt-1 text-sm leading-6 text-slate-500 dark:text-slate-400">
                        {readString(activeStep.checkpoint) || '完成本阶段学习资源后进行阶段检测，检测通过会推进到下一阶段。'}
                      </p>
                    </div>
                    <span className="shrink-0 rounded-full bg-white px-3 py-1 text-xs font-semibold text-primary-700 shadow-sm shadow-slate-200/60 dark:bg-slate-900 dark:text-primary-300 dark:shadow-none">
                      {stageProgress}%
                    </span>
                  </div>
                  <div className="mt-4 h-2 overflow-hidden rounded-full bg-white dark:bg-slate-800">
                    <div className="h-full rounded-full bg-primary-500" style={{ width: `${stageProgress}%` }} />
                  </div>
                </div>
                {targetPoints.length ? (
                  <div className="flex flex-wrap gap-2">
                    {targetPoints.map((point) => (
                      <span key={point} className="rounded-full bg-primary-50 px-3 py-1 text-xs font-medium text-primary-700 dark:bg-primary-500/10 dark:text-primary-300">
                        {point}
                      </span>
                    ))}
                  </div>
                ) : null}
                <div className="flex flex-col gap-2 sm:flex-row">
                  <button
                    type="button"
                    onClick={startStageTest}
                    disabled={stageStatus === 'generating'}
                    className="inline-flex h-11 items-center justify-center gap-2 rounded-xl bg-primary-600 px-4 text-sm font-semibold text-white shadow-sm shadow-primary-500/20 transition hover:bg-primary-700 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {stageStatus === 'generating' ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <ClipboardCheck className="h-4 w-4" />}
                    开始阶段检测
                  </button>
                  <button
                    type="button"
                    onClick={() => navigate('/engine')}
                    className="inline-flex h-11 items-center justify-center gap-2 rounded-xl bg-white px-4 text-sm font-semibold text-slate-600 shadow-sm shadow-slate-200/60 transition hover:bg-primary-50 hover:text-primary-700 dark:bg-slate-950/40 dark:text-slate-300 dark:shadow-none"
                  >
                    <ExternalLink className="h-4 w-4" />
                    查看完整路径
                  </button>
                </div>
                {stageStatus === 'failed' && stageError ? <InlineError text={stageError} /> : null}
              </div>
            ) : (
              <EmptyState title="暂无活动阶段" description="生成个性化学习路径后，这里会展示当前阶段和检测入口。" action={<LinkButton to="/engine" label="生成学习路径" />} />
            )}
          </Panel>

          <Panel>
            <SectionHeader icon={<Target className="h-5 w-5" />} title="今日任务" subtitle="任务状态来自后端聚合，不依赖前端假数据。" />
            <div className="mt-5 space-y-3">
              {data?.tasks.length ? data.tasks.map((task) => (
                <TaskCard key={task.id} task={task} onNavigate={navigate} onStartStageTest={startStageTest} stageBusy={stageStatus === 'generating'} />
              )) : (
                <EmptyState title="今日没有待办任务" description="当前学习记录较少，完成一次问答、资源学习或错题复习后会自动出现。" />
              )}
            </div>
          </Panel>
        </div>

        <div className="grid gap-5 xl:grid-cols-[minmax(0,0.95fr)_minmax(0,1.05fr)]">
          <Panel>
            <SectionHeader icon={<BookOpenCheck className="h-5 w-5" />} title="到期错题" subtitle="来自错题本的间隔复习调度。" />
            <div className="mt-5 space-y-3">
              {data?.dueMistakes.length ? data.dueMistakes.slice(0, 4).map((item) => (
                <MistakeMiniCard key={item.id} mistake={item} />
              )) : (
                <EmptyState title="今天没有到期错题" description="新的错题会按复习计划自动进入这里。" />
              )}
            </div>
            <div className="mt-4">
              <LinkButton to="/mistakes" label="进入错题训练营" />
            </div>
          </Panel>

          <Panel>
            <SectionHeader icon={<Layers3 className="h-5 w-5" />} title="推荐资源" subtitle="资源学习进度会写回后端，刷新后仍可恢复。" />
            <div className="mt-5 grid gap-3 md:grid-cols-2">
              {data?.recommendedResources.length ? data.recommendedResources.slice(0, 4).map((resource) => (
                <ResourceMiniCard
                  key={resource.id}
                  resource={resource}
                  saving={savingResourceId === resource.id}
                  primary={currentResource?.id === resource.id}
                  onStart={() => void markResourceProgress(resource, false)}
                  onComplete={() => void markResourceProgress(resource, true)}
                />
              )) : (
                <div className="md:col-span-2">
                  <EmptyState title="暂无推荐资源" description="生成学习路径或刷新资源推荐后，这里会显示当前阶段资源。" />
                </div>
              )}
            </div>
          </Panel>
        </div>

        <Panel>
          <SectionHeader icon={<GitBranch className="h-5 w-5" />} title="薄弱知识点" subtitle="点击知识点进入画像页查看前置知识、相关错题、资源和立即练习入口。" />
          <div className="mt-5 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {weakNodes.length ? weakNodes.map((node) => (
              <button
                key={node.key}
                type="button"
                onClick={() => navigate(`/profile?node=${encodeURIComponent(node.key)}`)}
                className="rounded-2xl bg-slate-50/78 p-4 text-left transition hover:bg-primary-50/80 dark:bg-slate-950/32 dark:hover:bg-primary-500/10"
              >
                <div className="flex items-center justify-between gap-3">
                  <h3 className="min-w-0 truncate text-sm font-semibold text-slate-950 dark:text-white">{node.topic}</h3>
                  <span className="shrink-0 text-xs text-slate-400 dark:text-slate-500">{Math.round(node.mastery * 100)}%</span>
                </div>
                <div className="mt-3 h-2 overflow-hidden rounded-full bg-white dark:bg-slate-800">
                  <div className="h-full rounded-full bg-amber-500" style={{ width: `${Math.round(node.mastery * 100)}%` }} />
                </div>
                <p className="mt-3 text-xs text-slate-500 dark:text-slate-400">{statusLabel(node.status)}</p>
              </button>
            )) : (
              <div className="md:col-span-2 xl:col-span-3">
                <EmptyState title="暂无明显薄弱知识点" description="完成练习和阶段测试后，图谱会自动沉淀薄弱点。" />
              </div>
            )}
          </div>
        </Panel>
      </div>
    </WorkbenchShell>
  );
}

function WorkbenchShell({ children }: { children: ReactNode }) {
  return (
    <div className="study-workbench-page mx-auto w-full max-w-[1280px] min-w-0 px-1 pb-10">
      {children}
    </div>
  );
}

function Panel({ children }: { children: ReactNode }) {
  return (
    <section className="min-w-0 rounded-[24px] bg-white/68 p-5 shadow-[0_14px_40px_rgba(59,97,155,0.08)] backdrop-blur dark:bg-slate-900/62 dark:shadow-slate-950/20 md:p-6">
      {children}
    </section>
  );
}

function SectionHeader({ icon, title, subtitle }: { icon: ReactNode; title: string; subtitle: string }) {
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

function TaskCard(props: {
  task: DailyTaskItem;
  onNavigate: (path: string) => void;
  onStartStageTest: () => void;
  stageBusy: boolean;
}) {
  const progress = props.task.progress === null || props.task.progress === undefined ? null : clampPercent(props.task.progress);
  const isStageTest = props.task.type === 'STAGE_TEST';
  return (
    <article className="rounded-2xl bg-slate-50/78 p-4 dark:bg-slate-950/32">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="text-xs font-semibold text-primary-600 dark:text-primary-300">{taskTypeLabel(props.task.type)}</div>
          <h3 className="mt-1 text-base font-semibold text-slate-950 dark:text-white">{props.task.title}</h3>
          <p className="mt-1 text-sm leading-6 text-slate-500 dark:text-slate-400">{props.task.description}</p>
        </div>
        <span className="shrink-0 rounded-full bg-white px-2.5 py-1 text-xs font-medium text-slate-500 dark:bg-slate-900 dark:text-slate-300">
          {statusLabel(props.task.status)}
        </span>
      </div>
      {progress !== null ? (
        <div className="mt-3 h-2 overflow-hidden rounded-full bg-white dark:bg-slate-800">
          <div className="h-full rounded-full bg-primary-500" style={{ width: `${progress}%` }} />
        </div>
      ) : null}
      <button
        type="button"
        onClick={() => {
          if (isStageTest) {
            props.onStartStageTest();
            return;
          }
          props.onNavigate(props.task.actionRoute || '/');
        }}
        disabled={isStageTest && props.stageBusy}
        className="mt-4 inline-flex h-9 items-center gap-2 rounded-xl bg-white px-3 text-sm font-medium text-slate-600 shadow-sm shadow-slate-200/60 transition hover:bg-primary-50 hover:text-primary-700 disabled:cursor-not-allowed disabled:opacity-60 dark:bg-slate-900 dark:text-slate-300 dark:shadow-none"
      >
        {isStageTest && props.stageBusy ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
        {props.task.actionLabel}
      </button>
    </article>
  );
}

function MistakeMiniCard({ mistake }: { mistake: import('../api/mistakes').MistakeRecordResponse }) {
  return (
    <article className="rounded-2xl bg-slate-50/78 p-4 dark:bg-slate-950/32">
      <div className="flex items-center justify-between gap-3 text-xs text-slate-400 dark:text-slate-500">
        <span>{mistake.knowledgeTags.slice(0, 2).join(' / ') || '未标注知识点'}</span>
        <span>错 {mistake.wrongCount} 次</span>
      </div>
      <h3 className="mt-2 line-clamp-2 text-sm font-semibold leading-6 text-slate-900 dark:text-white">{mistake.stem}</h3>
      <p className="mt-2 text-xs text-slate-500 dark:text-slate-400">下次复习：{formatDateTime(mistake.nextReviewAt)}</p>
    </article>
  );
}

function ResourceMiniCard(props: {
  resource: ResourceItem;
  saving: boolean;
  primary: boolean;
  onStart: () => void;
  onComplete: () => void;
}) {
  const progress = clampPercent(props.resource.progress ?? 0);
  return (
    <article className={`rounded-2xl p-4 transition ${props.primary ? 'bg-primary-50/70 dark:bg-primary-500/10' : 'bg-slate-50/78 dark:bg-slate-950/32'}`}>
      <div className="flex items-center gap-2 text-xs text-slate-400 dark:text-slate-500">
        <FileText className="h-3.5 w-3.5" />
        <span>{resourceTypeLabel(props.resource.displayType || props.resource.resourceType)}</span>
      </div>
      <h3 className="mt-2 line-clamp-2 min-h-[48px] text-sm font-semibold leading-6 text-slate-950 dark:text-white">{props.resource.title}</h3>
      <div className="mt-3 h-2 overflow-hidden rounded-full bg-white dark:bg-slate-800">
        <div className="h-full rounded-full bg-emerald-500" style={{ width: `${progress}%` }} />
      </div>
      <div className="mt-4 flex flex-wrap gap-2">
        <button
          type="button"
          onClick={props.onStart}
          disabled={props.saving}
          className="inline-flex h-9 items-center gap-2 rounded-xl bg-white px-3 text-xs font-semibold text-slate-600 shadow-sm shadow-slate-200/60 transition hover:bg-primary-50 hover:text-primary-700 disabled:cursor-not-allowed disabled:opacity-60 dark:bg-slate-900 dark:text-slate-300 dark:shadow-none"
        >
          {props.saving ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
          开始
        </button>
        <button
          type="button"
          onClick={props.onComplete}
          disabled={props.saving || props.resource.completed}
          className="inline-flex h-9 items-center gap-2 rounded-xl bg-emerald-600 px-3 text-xs font-semibold text-white transition hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-60"
        >
          <CheckCircle2 className="h-3.5 w-3.5" />
          完成
        </button>
      </div>
    </article>
  );
}

function AccessState(props: { icon: ReactNode; title: string; description: string; actionLabel: string; onAction: () => void }) {
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
          className="mt-5 inline-flex h-10 items-center justify-center rounded-xl bg-primary-600 px-4 text-sm font-medium text-white shadow-sm shadow-primary-500/20 transition hover:bg-primary-700"
        >
          {props.actionLabel}
        </button>
      </div>
    </div>
  );
}

function LoadingPanel({ text }: { text: string }) {
  return (
    <div className="flex min-h-[420px] items-center justify-center rounded-[28px] bg-white/72 text-sm text-slate-500 shadow-[0_18px_56px_rgba(59,97,155,0.10)] backdrop-blur-xl dark:bg-slate-900/68 dark:text-slate-400 dark:shadow-slate-950/20">
      <LoaderCircle className="mr-2 h-4 w-4 animate-spin text-primary-500" />
      {text}
    </div>
  );
}

function EmptyState({ title, description, action }: { title: string; description: string; action?: ReactNode }) {
  return (
    <div className="rounded-2xl bg-slate-50/72 px-4 py-8 text-center dark:bg-slate-950/30">
      <h3 className="text-sm font-semibold text-slate-900 dark:text-white">{title}</h3>
      <p className="mx-auto mt-2 max-w-md text-sm leading-6 text-slate-500 dark:text-slate-400">{description}</p>
      {action ? <div className="mt-4">{action}</div> : null}
    </div>
  );
}

function LinkButton({ to, label }: { to: string; label: string }) {
  return (
    <Link
      to={to}
      className="inline-flex h-10 items-center justify-center rounded-xl bg-primary-600 px-4 text-sm font-semibold text-white shadow-sm shadow-primary-500/20 transition hover:bg-primary-700"
    >
      {label}
    </Link>
  );
}

function MiniStat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-xl bg-white px-2 py-2 dark:bg-slate-900">
      <div className="font-semibold text-slate-950 dark:text-white">{value}</div>
      <div className="mt-0.5 text-slate-400 dark:text-slate-500">{label}</div>
    </div>
  );
}

function InlineError({ text }: { text: string }) {
  return (
    <div className="mt-4 rounded-2xl bg-red-50/86 px-4 py-3 text-sm text-red-700 dark:bg-red-950/30 dark:text-red-200">
      {text}
    </div>
  );
}

function readString(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function readNumber(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0;
}

function readStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.map((item) => readString(item)).filter(Boolean);
}

function clampPercent(value: number): number {
  return Math.max(0, Math.min(100, Math.round(value)));
}

function taskTypeLabel(type: string): string {
  return {
    STAGE: '阶段学习',
    STAGE_TEST: '阶段检测',
    MISTAKE_REVIEW: '错题复习',
    RESOURCE: '资源学习',
    KNOWLEDGE: '知识补强',
  }[type] ?? type;
}

function statusLabel(status: string): string {
  return {
    READY: '可开始',
    PENDING: '待完成',
    IN_PROGRESS: '进行中',
    COMPLETED: '已完成',
    WEAK: '薄弱',
    MASTERED: '已掌握',
    NOT_STARTED: '未开始',
  }[status] ?? status;
}

function resourceTypeLabel(type: string): string {
  return {
    COURSE: '课程',
    DOCUMENT: '文档',
    VIDEO: '视频',
    CASE: '案例',
    NOTE: '笔记',
  }[type] ?? type;
}

function formatDateTime(value?: string | null): string {
  if (!value) {
    return '--';
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}
