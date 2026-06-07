import { Suspense, lazy, useCallback, useEffect, useRef, useState } from 'react';
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
} from './LearningStudioDemoPage.model';
import {
  AssistantActionBar,
  EngineSectionHeader,
  TaskStatusPreview,
  ServiceHeroVisual,
} from './LearningStudioDemoPage.shell-components';
import { useLearningStudioEngine } from './useLearningStudioEngine';
import { useLearningStudioQna } from './useLearningStudioQna';
import {
  VOICE_PAGE_ACTION_EVENT,
  consumeQueuedVoicePageAction,
  isVoicePageActionEvent,
} from '../utils/voicePageActions';

const ServiceDynamicForm = lazy(() =>
  import('./LearningStudioDemoPage.components').then((module) => ({ default: module.ServiceDynamicForm }))
);
const TaskResultPanel = lazy(() =>
  import('./LearningStudioDemoPage.components').then((module) => ({ default: module.TaskResultPanel }))
);

const serviceDescriptions: Record<EngineService, { summary: string; detail: string; accent: string }> = {
  resource: {
    summary: '基于你的目标生成文档、课件、练习和视频',
    detail: '填写课程与知识点后，系统会生成可阅读、可下载的学习内容。',
    accent: 'from-blue-500 to-sky-400',
  },
  personalized: {
    summary: '自动规划阶段目标，并匹配下一步资源',
    detail: '系统会读取学习画像、进度和练习记录，生成清晰的学习路径。',
    accent: 'from-indigo-500 to-cyan-400',
  },
  path: {
    summary: '结合掌握情况，生成可执行学习路径',
    detail: '系统会根据当前学习状态给出阶段安排、检查点和重点知识。',
    accent: 'from-indigo-500 to-blue-400',
  },
  push: {
    summary: '按当前薄弱点推荐合适资源',
    detail: '系统会优先推荐与当前阶段匹配的讲解、案例和拓展内容。',
    accent: 'from-cyan-500 to-emerald-400',
  },
};

export default function LearningStudioDemoPage({ mode }: { mode: 'qna' | 'engine' }) {
  const { isAuthenticated, currentUser, openAuthModal } = useOutletContext<LayoutOutletContext>();
  const navigate = useNavigate();
  const pendingActionRef = useRef<null | (() => void)>(null);
  const conversationIdRef = useRef('');
  const mountedRef = useRef(true);

  const [conversationId, setConversationId] = useState('');
  const [voicePlanSubmitPending, setVoicePlanSubmitPending] = useState(false);
  const { resetQnaConversation, viewProps: qnaViewProps } = useLearningStudioQna({
    mode,
    isAuthenticated,
    currentUser,
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
    markFormEditing,
    handleSelectService,
    handleSubmitService,
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

  const withAuth = useCallback((action: () => void) => {
    if (isAuthenticated) {
      action();
      return;
    }
    pendingActionRef.current = action;
    openAuthModal('login', '请先登录');
  }, [isAuthenticated, openAuthModal]);

  const prepareVoiceStudyPlan = useCallback(() => {
    if (mode !== 'engine') {
      return;
    }
    withAuth(() => {
      handleSelectService('personalized');
      setVoicePlanSubmitPending(true);
    });
  }, [handleSelectService, mode, withAuth]);

  useEffect(() => {
    if (mode !== 'engine') {
      return;
    }
    if (consumeQueuedVoicePageAction('generate_study_plan')) {
      prepareVoiceStudyPlan();
    }
    const handleVoiceAction = (event: Event) => {
      if (isVoicePageActionEvent(event, 'generate_study_plan')) {
        prepareVoiceStudyPlan();
      }
    };
    window.addEventListener(VOICE_PAGE_ACTION_EVENT, handleVoiceAction);
    return () => {
      window.removeEventListener(VOICE_PAGE_ACTION_EVENT, handleVoiceAction);
    };
  }, [mode, prepareVoiceStudyPlan]);

  useEffect(() => {
    if (!voicePlanSubmitPending || mode !== 'engine') {
      return;
    }
    if (selectedService !== 'personalized' || engineBusy) {
      return;
    }
    setVoicePlanSubmitPending(false);
    void handleSubmitService();
  }, [engineBusy, handleSubmitService, mode, selectedService, voicePlanSubmitPending]);

  if (mode === 'qna') {
    return <QnaChatView {...qnaViewProps} />;
  }

  return (
    <Suspense fallback={<div className="mx-auto max-w-[1180px] rounded-[28px] bg-white/76 px-6 py-10 text-center text-sm text-slate-500 shadow-[0_14px_42px_rgba(59,97,155,0.08)] dark:bg-slate-900/68">正在加载学习服务...</div>}>
      <div className="mx-auto max-w-[1120px] space-y-6 px-0 pb-8 sm:space-y-7 sm:pb-10 md:px-0">
        <section className="overflow-hidden rounded-[28px] bg-white/76 shadow-[0_18px_56px_rgba(59,97,155,0.09)] backdrop-blur-xl dark:bg-slate-900/68 dark:shadow-slate-950/20">
          <div className="flex items-center justify-between gap-3 px-4 py-4 sm:px-6 sm:py-5 md:px-8">
            <div className="flex min-w-0 flex-wrap items-center gap-3 sm:gap-4">
              <div className="flex h-10 w-10 items-center justify-center rounded-2xl bg-primary-600 text-white shadow-lg shadow-blue-500/18 sm:h-11 sm:w-11">
                <GraduationCap className="h-5 w-5" />
              </div>
              <div className="text-xl font-bold tracking-tight text-slate-900 dark:text-white sm:text-2xl">智能服务</div>
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
                  按学习目标生成内容、路径和推荐，过程清晰，结果集中展示。
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
                        className={`group relative min-h-[132px] rounded-2xl bg-white/66 p-4 text-left shadow-sm shadow-blue-100/24 transition-all duration-200 hover:-translate-y-0.5 hover:bg-white/92 hover:shadow-lg hover:shadow-blue-100/36 dark:bg-slate-950/38 dark:shadow-none sm:min-h-[148px] sm:p-6 ${
                          active
                            ? 'bg-primary-50/78 shadow-primary-100/45 dark:bg-primary-500/10'
                            : ''
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

        <div className="grid gap-2 overflow-hidden rounded-[24px] bg-white/72 p-2 shadow-[0_14px_42px_rgba(43,83,145,0.08)] backdrop-blur dark:bg-slate-900/62 dark:shadow-slate-950/20 xl:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
          <section className="rounded-[20px] bg-white/44 p-4 dark:bg-slate-950/18 sm:p-6">
            <EngineSectionHeader
              icon={<FileText className="h-4 w-4" />}
              title={selectedService === 'personalized' ? '自动分析范围' : (selectedServiceButton ? `${selectedServiceButton.label}参数` : '服务参数')}
              subtitle={selectedServiceDescription?.detail ?? '选择服务后开始生成，系统会整理并展示结果。'}
            />
            <div className="mt-5 sm:mt-6">
              <ServiceDynamicForm
                service={selectedService}
                resourceForm={resourceForm}
                resourceErrors={resourceFieldErrors}
                pathForm={pathForm}
                pushForm={pushForm}
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
              />
            </div>
          </section>

          <TaskStatusPreview
            selectedServiceLabel={selectedServiceButton?.label ?? ''}
            taskId={taskId}
            taskProgress={taskProgress}
            taskStatus={taskStatus}
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
          taskId={taskId}
          taskSummary={taskSummary}
          serviceResultLines={serviceResultLines}
          downloadLinks={downloadLinks}
          videoResult={videoResult}
          inlineResource={activeEngineSnapshot.inlineResource}
          inlineResources={inlineResources}
          completedResources={completedResources}
          learningPlan={activeEngineSnapshot.learningPlan}
          resourcePushPlan={activeEngineSnapshot.resourcePushPlan}
          resultHistory={activeEngineSnapshot.resultHistory}
          selectedResultTaskId={activeEngineSnapshot.selectedResultTaskId}
          practiceBatch={activeEngineSnapshot.practiceBatch}
          onSelectResultTask={handleSelectResultTask}
        />
      </div>
    </Suspense>
  );
}
