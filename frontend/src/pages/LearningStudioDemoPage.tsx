import { Suspense, lazy, useEffect, useRef, useState } from 'react';
import { useNavigate, useOutletContext } from 'react-router-dom';
import { CheckCircle2, FileText, GraduationCap, X } from 'lucide-react';
import type { LayoutOutletContext } from '../components/Layout';
import QnaChatView from './QnaChatView';
import {
  serviceButtons,
  type EngineService,
} from './LearningStudioDemoPage.types';
import {
  ACTIVE_CONVERSATION_ID_STORAGE_KEY,
  hasLockedTask,
} from './LearningStudioDemoPage.model';
import {
  AssistantActionBar,
  EngineSectionHeader,
  LearningEffectPreview,
  ServiceHeroVisual,
} from './LearningStudioDemoPage.shell-components';
import { useLearningStudioEngine } from './useLearningStudioEngine';
import { useLearningStudioQna } from './useLearningStudioQna';

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
  personalized: {
    summary: '多智能体协同生成路径并匹配资源',
    detail: '画像、评估、检索、规划、资源推荐与质量审查串联执行。',
    accent: 'from-indigo-500 to-cyan-400',
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

export default function LearningStudioDemoPage({ mode }: { mode: 'qna' | 'engine' }) {
  const { isAuthenticated, openAuthModal } = useOutletContext<LayoutOutletContext>();
  const navigate = useNavigate();
  const pendingActionRef = useRef<null | (() => void)>(null);
  const conversationIdRef = useRef('');
  const mountedRef = useRef(true);

  const [conversationId, setConversationId] = useState('');
  const { resetQnaConversation, viewProps: qnaViewProps } = useLearningStudioQna({
    mode,
    isAuthenticated,
    openAuthModal,
    conversationId,
    setConversationId,
    conversationIdRef,
    mountedRef,
  });
  const {
    selectedService,
    resourceForm,
    resourceFieldErrors,
    pathForm,
    pushForm,
    assessmentForm,
    activeEngineSnapshot,
    engineBusy,
    taskId,
    taskProgress,
    taskStatus,
    taskSummary,
    serviceResultLines,
    downloadLinks,
    videoResult,
    inlineResources,
    completedResources,
    setResourceForm,
    setPathForm,
    setPushForm,
    setAssessmentForm,
    markFormEditing,
    handleSelectService,
    handleSubmitService,
    handleSubmitPracticeAnswers,
    handleStopService,
    handleSelectResultTask,
    resetEngineView,
    abortEngineTasks,
  } = useLearningStudioEngine({
    mode,
    isAuthenticated,
    openAuthModal,
    conversationId,
    setConversationId,
    conversationIdRef,
    mountedRef,
  });
  const selectedServiceButton = selectedService ? serviceButtons.find((item) => item.id === selectedService) ?? null : null;
  const selectedServiceDescription = selectedService ? serviceDescriptions[selectedService] : null;

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

  useEffect(() => {
    return () => {
      mountedRef.current = false;
      abortEngineTasks();
    };
  }, [abortEngineTasks]);

  useEffect(() => {
    mountedRef.current = true;
  }, []);

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

  if (mode === 'qna') {
    return <QnaChatView {...qnaViewProps} />;
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
                resourceErrors={resourceFieldErrors}
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
          learningPlan={activeEngineSnapshot.learningPlan}
          criticReview={activeEngineSnapshot.criticReview}
          agentTrace={activeEngineSnapshot.agentTrace}
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
