import { Suspense, lazy, useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate, useOutletContext } from 'react-router-dom';
import { CheckCircle2, FileText, GraduationCap, Sparkles, Target, X } from 'lucide-react';
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

const serviceDescriptions: Record<EngineService, { summary: string; detail: string; accent: string; stage: string; action: string }> = {
  resource: {
    summary: '把当前知识点补齐为可直接学习的材料包',
    detail: '填写课程与知识点后，系统会生成可阅读、可下载的学习内容。',
    accent: 'from-cyan-500 to-emerald-400',
    stage: '资源补齐',
    action: '生成学习资源',
  },
  personalized: {
    summary: '综合画像、练习和错题，生成下一阶段学习路径',
    detail: '系统会读取学习画像、进度和练习记录，生成清晰的学习路径。',
    accent: 'from-indigo-500 to-cyan-400',
    stage: '路径规划',
    action: '生成个性化方案',
  },
  path: {
    summary: '结合掌握情况，生成可执行学习路径',
    detail: '系统会根据当前学习状态给出阶段安排、检查点和重点知识。',
    accent: 'from-indigo-500 to-blue-400',
    stage: '路径规划',
    action: '规划阶段路线',
  },
  push: {
    summary: '按当前薄弱点推荐合适资源',
    detail: '系统会优先推荐与当前阶段匹配的讲解、案例和拓展内容。',
    accent: 'from-cyan-500 to-emerald-400',
    stage: '资源推送',
    action: '刷新推荐资源',
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
      <div className="learning-service-page mx-auto max-w-[1180px] space-y-6 px-0 pb-8 sm:space-y-7 sm:pb-10 md:px-0">
        <section className="learning-service-hero">
          <div className="learning-service-topline">
            <div className="learning-service-badge">
              <GraduationCap className="h-4 w-4" />
              <span>学习编排中心</span>
            </div>
            <button
              type="button"
              onClick={() => navigate('/chat')}
              className="learning-service-close"
              aria-label="返回对话"
            >
              <X className="h-5 w-5" />
            </button>
          </div>

          <div className="learning-service-grid">
            <div className="learning-service-copy">
              <div className="qna-eyebrow">学习调度</div>
              <h1>把学习过程拆成可执行的下一步</h1>
              <p>
                从画像和薄弱点出发，先生成阶段路径，再补齐资源、安排练习，最终回到错题和笔记复盘。
              </p>
              <div className="learning-service-map" aria-label="学习过程">
                {['画像', '路径', '资源', '练习', '复盘'].map((item, index) => (
                  <span key={item} className={index < 2 ? 'is-active' : ''}>
                    {item}
                  </span>
                ))}
              </div>

              <div className="learning-service-actions">
                {serviceButtons.map((item) => {
                  const active = selectedService === item.id;
                  const description = serviceDescriptions[item.id];
                  return (
                    <button
                      key={item.id}
                      type="button"
                      onClick={() => withAuth(() => handleSelectService(item.id))}
                      className={`learning-service-card ${active ? 'is-active' : ''}`}
                    >
                      <span className="learning-service-card-stage">{description.stage}</span>
                      <span className={`learning-service-card-icon bg-gradient-to-br ${description.accent}`}>
                        <item.icon className="h-5 w-5" />
                      </span>
                      <span className="learning-service-card-copy">
                        <strong>{description.action}</strong>
                        <small>{description.summary}</small>
                      </span>
                      {active ? (
                        <span className="learning-service-card-check">
                          <CheckCircle2 className="h-4 w-4" />
                        </span>
                      ) : null}
                    </button>
                  );
                })}
              </div>
            </div>

            <div className="learning-service-visual-shell">
              <ServiceHeroVisual />
            </div>
          </div>
        </section>

        <div className="learning-service-workbench">
          <section className="learning-service-form-panel">
            <EngineSectionHeader
              icon={<FileText className="h-4 w-4" />}
              title={selectedService === 'personalized' ? '输入范围：系统自动读取' : (selectedServiceButton ? `${selectedServiceButton.label}参数` : '先选择学习动作')}
              subtitle={selectedServiceDescription?.detail ?? '选择一个学习动作后，系统会把输入、生成进度和结果串在同一条流程里。'}
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
            phaseIcon={selectedService === 'resource' ? <Sparkles className="h-4 w-4" /> : <Target className="h-4 w-4" />}
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
