import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Link, useNavigate, useOutletContext } from 'react-router-dom';
import {
  BookOpenCheck,
  CheckCircle2,
  ClipboardCheck,
  Clock3,
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
import {
  studyWorkbenchApi,
  type DailyExecutionPlan,
  type DailyStudyWorkbenchResponse,
  type DailyTaskItem,
  type LearningSessionStep,
  type PlanSupportItem,
} from '../api/studyWorkbench';
import { readStreamMessage, readStreamPayload } from '../api/sse';
import type { PracticeQuestionBatch } from './LearningStudioDemoPage.types';
import { readPracticeQuestionBatch } from './LearningStudioDemoPage.taskPayloadReaders';
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

  const executionPlan = useMemo(() => data ? data.executionPlan ?? buildFallbackExecutionPlan(data) : null, [data]);
  const activeStep = data?.activeStep ?? null;
  const stageTitle = readString(activeStep?.title) || '当前学习阶段';
  const targetPoints = readStringArray(activeStep?.targetKnowledgePoints);
  const currentResource = data?.recommendedResources.find((item) => !item.completed) ?? data?.recommendedResources[0] ?? null;
  const weakNodes = useMemo(() => {
    return data?.knowledgeGraph.nodes
      .filter((node) => node.status === 'WEAK' || node.status === 'IN_PROGRESS')
      .sort((left, right) => left.mastery - right.mastery)
      .slice(0, 3) ?? [];
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

  const runTaskAction = (task?: DailyTaskItem | null) => {
    if (!task) {
      return;
    }
    if (task.type === 'STAGE_TEST' && task.status === 'READY') {
      void startStageTest();
      return;
    }
    navigateFromWorkbench(task.actionRoute || '/');
  };

  const runStepAction = (step: LearningSessionStep) => {
    if (step.sourceTaskType === 'STAGE_TEST' && step.status === 'READY') {
      void startStageTest();
      return;
    }
    navigateFromWorkbench(step.actionRoute || '/');
  };

  const navigateFromWorkbench = (route: string) => {
    if (route.startsWith('/notes')) {
      navigate(route, { state: { returnTo: '/dashboard' } });
      return;
    }
    navigate(route);
  };

  if (!isAuthenticated) {
    return (
      <WorkbenchShell>
        <AccessState
          icon={<Compass className="h-6 w-6" />}
          title="登录后进入今日学习执行台"
          description="这里会把错题复习、资源补强、阶段检测和反思记录排成一轮今天能完成的学习行动。"
          actionLabel="登录查看"
          onAction={() => openAuthModal('login', '登录后查看今日学习执行台')}
        />
      </WorkbenchShell>
    );
  }

  if (loading && !data) {
    return (
      <WorkbenchShell>
        <LoadingPanel text="正在整理今日学习主线" />
      </WorkbenchShell>
    );
  }

  if (error && !data) {
    return (
      <WorkbenchShell>
        <AccessState
          icon={<TriangleAlert className="h-6 w-6" />}
          title="今日执行台读取失败"
          description={error}
          actionLabel="重新加载"
          visual={false}
          onAction={() => void loadDaily()}
        />
      </WorkbenchShell>
    );
  }

  return (
    <WorkbenchShell>
      <div className="space-y-5">
        <section className="workbench-hero no-theme-surface">
          <div className="workbench-hero-copy">
            <div className="workbench-kicker">
              <Compass className="h-3.5 w-3.5" />
              今日学习执行台
            </div>
            <h1>
              {executionPlan?.title || '今天从一个可完成的任务开始'}
            </h1>
            <p>
              {executionPlan?.subtitle || '系统会把今天最该做的一件事放到前面，其余内容只作为依据和辅助入口。'}
            </p>
            {executionPlan?.focusReason ? (
              <div className="workbench-reason">
                <span>为什么先做它</span>
                <p>{executionPlan.focusReason}</p>
              </div>
            ) : null}
            <div className="mt-5 grid gap-3 sm:grid-cols-2">
              <HeroFact
                icon={<Clock3 className="h-4 w-4" />}
                label="预计用时"
                value={`${executionPlan?.estimatedMinutes ?? 15} 分钟`}
              />
              <HeroFact
                icon={<CheckCircle2 className="h-4 w-4" />}
                label="完成标准"
                value={executionPlan?.successCriteria || '完成一次可回流的学习动作'}
              />
            </div>
            {error ? <InlineError text={error} /> : null}
            {stageStatus === 'failed' && stageError ? <InlineError text={stageError} /> : null}
            <div className="workbench-action-row">
              <button
                type="button"
                onClick={() => runTaskAction(executionPlan?.primaryTask)}
                disabled={canGenerateStageTest(executionPlan?.primaryTask) && stageStatus === 'generating'}
                className="workbench-primary-button"
              >
                {stageStatus === 'generating' && canGenerateStageTest(executionPlan?.primaryTask) ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
                {executionPlan?.primaryTask.actionLabel || '开始'}
              </button>
              <button
                type="button"
                onClick={() => void refresh()}
                disabled={refreshing}
                className="workbench-secondary-button"
              >
                {refreshing ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
                刷新计划
              </button>
            </div>
          </div>

          <div className="workbench-hero-media">
            <img src="/images/study-workspace.jpg" alt="书桌上的笔记本电脑和打开的学习笔记" />
            <div className="workbench-hero-plan-card">
              <div className="workbench-plan-head">
                <div>
                  <span>本轮完成度</span>
                  <strong>
                    {data?.summary.progressPercent ?? 0}%
                  </strong>
                </div>
                <span>
                  {executionPlan?.steps.length ?? 4} 步
                </span>
              </div>
              <div className="workbench-progress-line">
                <div className="h-full rounded-full bg-primary-500" style={{ width: `${data?.summary.progressPercent ?? 0}%` }} />
              </div>
              <div className="workbench-compact-steps">
                {(executionPlan?.steps ?? []).map((step, index) => (
                  <button key={step.id} type="button" onClick={() => runStepAction(step)}>
                    <span>
                      {index + 1}
                    </span>
                    <strong>{step.phase}</strong>
                    <small>{statusLabel(step.status)}</small>
                  </button>
                ))}
              </div>
            </div>
          </div>
        </section>

        {!data?.dataAvailable ? (
          <Panel>
            <EmptyState
              title="还没有足够的学习记录"
              description="先完成一次问答、练习或画像初始化，系统会把今天最该做的一件事排出来。"
              action={<LinkButton to="/" label="开始学习对话" />}
            />
          </Panel>
        ) : null}

        <div className="grid gap-5 xl:grid-cols-[minmax(0,1.14fr)_minmax(320px,0.86fr)]">
          <Panel>
            <SectionHeader
              icon={<Target className="h-5 w-5" />}
              title="今天按这一轮做"
              subtitle="热身先唤回记忆，补强只处理一个重点，检测负责验收，反思把结果写回学习记录。"
            />
            <div className="mt-5 space-y-3">
              {(executionPlan?.steps ?? []).map((step, index) => (
                <SessionStepCard
                  key={step.id}
                  step={step}
                  index={index}
                  busy={canGenerateStageTest(step) && stageStatus === 'generating'}
                  onAction={runStepAction}
                />
              ))}
            </div>
          </Panel>

          <div className="space-y-5">
            <Panel>
              <SectionHeader
                icon={<Layers3 className="h-5 w-5" />}
                title="推荐依据"
                subtitle="这些内容只解释为什么这样排，不再和主任务抢位置。"
              />
              <div className="mt-5 space-y-3">
                {executionPlan?.supportItems.length ? executionPlan.supportItems.map((item) => (
                  <SupportItemCard key={item.id} item={item} onNavigate={navigate} />
                )) : (
                  <EmptyState title="暂无明确依据" description="完成更多练习或资源学习后，系统会给出更具体的排序依据。" />
                )}
              </div>
            </Panel>

            <Panel>
              <SectionHeader
                icon={<Route className="h-5 w-5" />}
                title="辅助入口"
                subtitle="需要展开更多信息时再进入对应页面。"
              />
              <div className="mt-5 grid gap-3">
                <AuxiliaryLink icon={<BookOpenCheck className="h-4 w-4" />} label="错题训练营" value={`${data?.summary.dueMistakeCount ?? 0} 道到期`} to="/mistakes" />
                <AuxiliaryLink icon={<FileText className="h-4 w-4" />} label="资源库" value={`${data?.summary.recommendedResourceCount ?? 0} 个推荐`} to="/resources" />
                <AuxiliaryLink icon={<GitBranch className="h-4 w-4" />} label="学习画像" value={`${data?.summary.weakKnowledgeCount ?? 0} 个薄弱点`} to="/profile" />
              </div>
            </Panel>
          </div>
        </div>

        <Panel>
          <SectionHeader
            icon={<ClipboardCheck className="h-5 w-5" />}
            title="主任务材料"
            subtitle="只展示能直接帮助完成今天主任务的少量材料。"
          />
          <div className="mt-5 grid gap-4 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
            <div className="space-y-3">
              {data?.dueMistakes.length ? data.dueMistakes.slice(0, 2).map((item) => (
                <MistakeMiniCard key={item.id} mistake={item} />
              )) : (
                <EmptyState title="今天没有到期错题" description="新的错题会按复习计划自动进入热身环节。" />
              )}
            </div>
            <div className="space-y-3">
              {currentResource ? (
                <ResourceFocusCard
                  resource={currentResource}
                  saving={savingResourceId === currentResource.id}
                  onStart={() => void markResourceProgress(currentResource, false)}
                  onComplete={() => void markResourceProgress(currentResource, true)}
                />
              ) : (
                <EmptyState title="暂无推荐资源" description="生成学习路径或刷新资源推荐后，这里会放一个最适合今天补强的资源。" />
              )}
              {weakNodes.length ? (
                <div className="rounded-2xl bg-slate-50/78 p-4 dark:bg-slate-950/32">
                  <div className="text-xs font-semibold text-slate-500 dark:text-slate-400">薄弱点参考</div>
                  <div className="mt-3 flex flex-wrap gap-2">
                    {weakNodes.map((node) => (
                      <button
                        key={node.key}
                        type="button"
                        onClick={() => navigate(`/profile?node=${encodeURIComponent(node.key)}`)}
                        className="rounded-xl bg-white px-3 py-2 text-left text-xs font-medium text-slate-600 transition hover:bg-primary-50 hover:text-primary-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-300 dark:bg-slate-900 dark:text-slate-300 dark:hover:bg-primary-500/10"
                      >
                        {node.topic} · {Math.round(node.mastery * 100)}%
                      </button>
                    ))}
                  </div>
                </div>
              ) : null}
            </div>
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

function HeroFact({ icon, label, value }: { icon: ReactNode; label: string; value: string }) {
  return (
    <div className="rounded-2xl bg-slate-50/78 p-4 dark:bg-slate-950/32">
      <div className="flex items-center gap-2 text-xs font-semibold text-slate-500 dark:text-slate-400">
        {icon}
        {label}
      </div>
      <p className="mt-2 text-sm leading-6 text-slate-800 dark:text-slate-100">{value}</p>
    </div>
  );
}

function SessionStepCard(props: {
  step: LearningSessionStep;
  index: number;
  busy: boolean;
  onAction: (step: LearningSessionStep) => void;
}) {
  const isCompleted = props.step.status === 'COMPLETED';
  return (
    <article className={`grid gap-4 rounded-2xl p-4 transition sm:grid-cols-[44px_minmax(0,1fr)_auto] ${isCompleted ? 'bg-emerald-50/72 dark:bg-emerald-500/10' : 'bg-slate-50/78 dark:bg-slate-950/32'}`}>
      <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-white text-sm font-semibold text-primary-700 shadow-sm shadow-slate-200/60 dark:bg-slate-900 dark:text-primary-300 dark:shadow-none">
        {props.index + 1}
      </div>
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs font-semibold text-primary-600 dark:text-primary-300">{props.step.phase}</span>
          <span className="rounded-lg bg-white px-2 py-0.5 text-xs text-slate-400 dark:bg-slate-900 dark:text-slate-500">
            {statusLabel(props.step.status)}
          </span>
          {props.step.minutes ? (
            <span className="text-xs text-slate-400 dark:text-slate-500">{props.step.minutes} 分钟</span>
          ) : null}
        </div>
        <h3 className="mt-1 text-base font-semibold text-slate-950 dark:text-white">{props.step.title}</h3>
        <p className="mt-1 text-sm leading-6 text-slate-500 dark:text-slate-400">{props.step.description}</p>
      </div>
      <button
        type="button"
        onClick={() => props.onAction(props.step)}
        disabled={props.busy}
        className="inline-flex h-10 items-center justify-center gap-2 self-start rounded-xl bg-white px-3 text-sm font-medium text-slate-600 shadow-sm shadow-slate-200/60 transition hover:bg-primary-50 hover:text-primary-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-300 disabled:cursor-not-allowed disabled:opacity-60 dark:bg-slate-900 dark:text-slate-300 dark:shadow-none"
      >
        {props.busy ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <ExternalLink className="h-4 w-4" />}
        {props.step.actionLabel}
      </button>
    </article>
  );
}

function SupportItemCard({ item, onNavigate }: { item: PlanSupportItem; onNavigate: (path: string) => void }) {
  return (
    <button
      type="button"
      onClick={() => onNavigate(item.actionRoute || '/')}
      className="w-full rounded-2xl bg-slate-50/78 p-4 text-left transition hover:bg-primary-50/80 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-300 dark:bg-slate-950/32 dark:hover:bg-primary-500/10"
    >
      <div className="flex items-center justify-between gap-3">
        <span className="text-xs font-semibold text-primary-600 dark:text-primary-300">{taskTypeLabel(item.type)}</span>
        <ExternalLink className="h-3.5 w-3.5 text-slate-300 dark:text-slate-600" />
      </div>
      <h3 className="mt-2 text-sm font-semibold leading-6 text-slate-950 dark:text-white">{item.title}</h3>
      <p className="mt-1 text-xs leading-5 text-slate-500 dark:text-slate-400">{item.description}</p>
    </button>
  );
}

function AuxiliaryLink({ icon, label, value, to }: { icon: ReactNode; label: string; value: string; to: string }) {
  return (
    <Link
      to={to}
      className="flex items-center justify-between gap-3 rounded-2xl bg-slate-50/78 px-4 py-3 text-sm transition hover:bg-primary-50/80 hover:text-primary-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-300 dark:bg-slate-950/32 dark:hover:bg-primary-500/10"
    >
      <span className="flex min-w-0 items-center gap-2 font-semibold text-slate-800 dark:text-slate-100">
        {icon}
        {label}
      </span>
      <span className="shrink-0 text-xs text-slate-400 dark:text-slate-500">{value}</span>
    </Link>
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

function ResourceFocusCard(props: {
  resource: ResourceItem;
  saving: boolean;
  onStart: () => void;
  onComplete: () => void;
}) {
  const progress = clampPercent(props.resource.progress ?? 0);
  return (
    <article className="rounded-2xl bg-primary-50/70 p-4 dark:bg-primary-500/10">
      <div className="flex items-center gap-2 text-xs text-primary-700 dark:text-primary-300">
        <FileText className="h-3.5 w-3.5" />
        <span>{resourceTypeLabel(props.resource.displayType || props.resource.resourceType)}</span>
      </div>
      <h3 className="mt-2 text-base font-semibold leading-6 text-slate-950 dark:text-white">{props.resource.title}</h3>
      <p className="mt-1 line-clamp-2 text-sm leading-6 text-slate-500 dark:text-slate-400">{props.resource.summaryText || '适合作为今天补强环节的输入材料。'}</p>
      <div className="mt-3 h-2 overflow-hidden rounded-full bg-white dark:bg-slate-800">
        <div className="h-full rounded-full bg-emerald-500" style={{ width: `${progress}%` }} />
      </div>
      <div className="mt-4 flex flex-wrap gap-2">
        <button
          type="button"
          onClick={props.onStart}
          disabled={props.saving}
          className="inline-flex h-9 items-center gap-2 rounded-xl bg-white px-3 text-xs font-semibold text-slate-600 shadow-sm shadow-slate-200/60 transition hover:bg-primary-50 hover:text-primary-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-300 disabled:cursor-not-allowed disabled:opacity-60 dark:bg-slate-900 dark:text-slate-300 dark:shadow-none"
        >
          {props.saving ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
          开始
        </button>
        <button
          type="button"
          onClick={props.onComplete}
          disabled={props.saving || props.resource.completed}
          className="inline-flex h-9 items-center gap-2 rounded-xl bg-emerald-600 px-3 text-xs font-semibold text-white transition hover:bg-emerald-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-emerald-300 disabled:cursor-not-allowed disabled:opacity-60"
        >
          <CheckCircle2 className="h-3.5 w-3.5" />
          完成
        </button>
      </div>
    </article>
  );
}

function AccessState(props: { icon: ReactNode; title: string; description: string; actionLabel: string; visual?: boolean; onAction: () => void }) {
  return (
    <div className={`workbench-access-state no-theme-surface ${props.visual === false ? 'is-compact' : ''}`}>
      <div className="workbench-access-copy">
        <div className="workbench-access-icon">
          {props.icon}
        </div>
        <h1>{props.title}</h1>
        <p>{props.description}</p>
        <button
          type="button"
          onClick={props.onAction}
          className="workbench-primary-button"
        >
          {props.actionLabel}
        </button>
      </div>
      {props.visual !== false ? (
        <div className="workbench-access-media" aria-hidden="true">
          <img src="/images/study-workspace.jpg" alt="" />
        </div>
      ) : null}
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
      className="inline-flex h-10 items-center justify-center rounded-xl bg-primary-600 px-4 text-sm font-semibold text-white shadow-sm shadow-primary-500/20 transition hover:bg-primary-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-400"
    >
      {label}
    </Link>
  );
}

function InlineError({ text }: { text: string }) {
  return (
    <div className="mt-4 rounded-2xl bg-red-50/86 px-4 py-3 text-sm text-red-700 dark:bg-red-950/30 dark:text-red-200">
      {text}
    </div>
  );
}

function buildFallbackExecutionPlan(data: DailyStudyWorkbenchResponse): DailyExecutionPlan {
  const primaryTask = choosePrimaryTask(data.tasks);
  const weakNode = data.knowledgeGraph.nodes
    .filter((node) => node.status === 'WEAK' || node.status === 'IN_PROGRESS')
    .sort((left, right) => left.mastery - right.mastery)[0];
  const resourceTask = data.tasks.find((task) => task.type === 'RESOURCE');
  const stageTestTask = data.tasks.find((task) => task.type === 'STAGE_TEST' && task.status === 'READY');
  const mistakeTask = data.tasks.find((task) => task.type === 'MISTAKE_REVIEW');
  const steps: LearningSessionStep[] = [
    fallbackStep('warmup', '热身', mistakeTask, '先复习到期错题', '用主动回忆清掉遗忘风险最高的内容。', '开始复习', '/mistakes', 6),
    fallbackStep('strengthen', '补强', resourceTask, '完成一个推荐资源', '只处理一个最贴近当前阶段的输入材料。', '查看资源', '/resources', 12),
    fallbackStep('check', '检测', stageTestTask, '准备阶段检测', '先完成补强材料，进度达标后再开始阶段检测。', '继续补强', '/engine', 10),
    {
      id: 'reflect',
      phase: '反思',
      title: '记录今天的变化',
      description: '把错因、掌握度变化和下一次复习点沉淀到笔记或画像里。',
      status: 'PENDING',
      minutes: 4,
      actionLabel: '写复盘',
      actionRoute: '/notes',
      sourceTaskId: null,
      sourceTaskType: null,
    },
  ];
  const supportItems: PlanSupportItem[] = [];
  if (data.activeStep) {
    supportItems.push({
      id: 'active-step',
      type: 'STAGE',
      title: `当前阶段：${readString(data.activeStep.title) || '未命名阶段'}`,
      description: readString(data.activeStep.checkpoint) || '阶段进度会由资源学习、检测和错题回流共同推动。',
      actionRoute: '/engine',
    });
  }
  if (data.dueMistakes.length) {
    supportItems.push({
      id: 'due-mistakes',
      type: 'MISTAKE_REVIEW',
      title: `到期错题 ${data.dueMistakes.length} 道`,
      description: '先用提取练习处理今天最容易遗忘的内容。',
      actionRoute: '/mistakes',
    });
  }
  if (data.recommendedResources.length) {
    supportItems.push({
      id: `resource:${data.recommendedResources[0].id}`,
      type: 'RESOURCE',
      title: `推荐资源：${data.recommendedResources[0].title}`,
      description: '可作为今天补强环节的输入材料。',
      actionRoute: '/resources',
    });
  }
  if (weakNode) {
    supportItems.push({
      id: `knowledge:${weakNode.key}`,
      type: 'KNOWLEDGE',
      title: `薄弱点：${weakNode.topic}`,
      description: `当前掌握度约 ${Math.round(weakNode.mastery * 100)}%，适合作为补强依据。`,
      actionRoute: `/profile?node=${weakNode.key}`,
    });
  }
  return {
    title: primaryTask.title,
    subtitle: primarySubtitle(primaryTask.type),
    focusReason: focusReason(primaryTask, data),
    successCriteria: successCriteria(primaryTask.type),
    estimatedMinutes: estimateMinutes(primaryTask.type),
    primaryTask,
    steps,
    supportItems,
  };
}

function choosePrimaryTask(tasks: DailyTaskItem[]): DailyTaskItem {
  return tasks.find((task) => task.type === 'MISTAKE_REVIEW' && task.status === 'READY')
    ?? tasks.find((task) => task.type === 'STAGE_TEST' && task.status === 'READY')
    ?? tasks.find((task) => task.type === 'RESOURCE' && task.status === 'READY')
    ?? tasks.find((task) => task.type === 'STAGE' && task.status === 'IN_PROGRESS')
    ?? tasks.find((task) => task.type === 'KNOWLEDGE' && task.status === 'READY')
    ?? tasks.find((task) => task.status !== 'COMPLETED')
    ?? tasks[0]
    ?? {
      id: 'onboarding',
      type: 'ONBOARDING',
      title: '先完成一次学习对话',
      description: '用一次问答、练习或画像初始化建立今日学习记录。',
      status: 'READY',
      progress: null,
      actionLabel: '开始学习',
      actionRoute: '/',
      actionPayload: {},
      dueAt: null,
    };
}

function fallbackStep(
  id: string,
  phase: string,
  task: DailyTaskItem | undefined,
  fallbackTitle: string,
  fallbackDescription: string,
  fallbackActionLabel: string,
  fallbackActionRoute: string,
  minutes: number,
): LearningSessionStep {
  return {
    id,
    phase,
    title: task?.title ?? fallbackTitle,
    description: task?.description ?? fallbackDescription,
    status: task?.status ?? 'PENDING',
    minutes,
    actionLabel: task?.actionLabel ?? fallbackActionLabel,
    actionRoute: task?.actionRoute ?? fallbackActionRoute,
    sourceTaskId: task?.id ?? null,
    sourceTaskType: task?.type ?? null,
  };
}

function canGenerateStageTest(item?: { sourceTaskType?: string | null; type?: string | null; status?: string | null } | null): boolean {
  return (item?.sourceTaskType === 'STAGE_TEST' || item?.type === 'STAGE_TEST') && item.status === 'READY';
}

function primarySubtitle(type: string): string {
  return {
    MISTAKE_REVIEW: '先做提取练习，再决定今天补什么。',
    STAGE_TEST: '当前阶段已到检测点，先验证能否进入下一阶段。',
    RESOURCE: '先补齐输入材料，再用练习检查是否真的会用。',
    STAGE: '把当前学习路径推进成一轮可完成的行动。',
    KNOWLEDGE: '围绕薄弱点做一次定向补强。',
  }[type] ?? '从一个可完成的小任务开始建立学习记录。';
}

function focusReason(task: DailyTaskItem, data: DailyStudyWorkbenchResponse): string {
  if (task.type === 'MISTAKE_REVIEW') {
    return `有 ${data.dueMistakes.length} 道错题进入复习窗口，先清掉遗忘风险最高的内容。`;
  }
  if (task.type === 'RESOURCE') {
    return `有 ${data.recommendedResources.length} 个资源匹配当前阶段，优先完成一个未学资源。`;
  }
  return task.description;
}

function successCriteria(type: string): string {
  return {
    MISTAKE_REVIEW: '完成到期错题复习，并记录至少一个错因。',
    STAGE_TEST: '完成 10 题阶段检测，结果能回流到画像或路径。',
    RESOURCE: '学习一个推荐资源并把进度标记为完成。',
    STAGE: '推进当前阶段，并明确下一次检测条件。',
    KNOWLEDGE: '完成薄弱点查看和一次针对练习。',
  }[type] ?? '完成一次问答、练习或资源学习，生成可追踪记录。';
}

function estimateMinutes(type: string): number {
  return {
    MISTAKE_REVIEW: 18,
    STAGE_TEST: 16,
    RESOURCE: 22,
    STAGE: 25,
    KNOWLEDGE: 20,
  }[type] ?? 12;
}

function readString(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
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
    ONBOARDING: '学习记录',
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
