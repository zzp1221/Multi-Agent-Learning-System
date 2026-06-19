import { useCallback, useEffect, useRef, useState, type Dispatch, type MutableRefObject, type SetStateAction } from 'react';
import { conversationApi } from '../api/conversation';
import { getErrorMessage } from '../api/request';
import { smartEngineApi } from '../api/smartEngine';
import type { LayoutOutletContext } from '../components/Layout';
import {
  defaultResourceForm,
  serviceTypeMap,
  type EngineService,
  type EngineTaskSnapshot,
  type PathForm,
  type PushForm,
  type ResourceForm,
} from './LearningStudioDemoPage.types';
import {
  runByApiTask,
  toUiTaskStatus,
} from './LearningStudioDemoPage.utils';
import { buildServiceParams } from './LearningStudioDemoPage.serviceParams';
import { cleanupStreamSchedulers } from './LearningStudioDemoPage.streamBuffer';
import {
  ACTIVE_CONVERSATION_ID_STORAGE_KEY,
  DEFAULT_ENGINE_SERVICE,
  ENGINE_TASK_STORAGE_KEY,
  buildPersistedEngineSnapshots,
  createCompletedResourcesFromSnapshot,
  createEmptyEngineTaskSnapshot,
  createInitialEngineSnapshots,
  getInlineResourcesFromSnapshot,
  hasLockedTask,
  syncSnapshotResultRecord,
  type PersistedEngineTaskSnapshot,
} from './LearningStudioDemoPage.model';
import { recordConversationResourceEvent } from './resourceGenerationStore';

interface TaskMonitorRefs {
  taskStreamAbortRef: { current: AbortController | null };
  streamQueueRef: { current: string[] };
  streamFlushTimerRef: { current: number | null };
  streamRafRef: { current: number | null };
}

interface UseLearningStudioEngineOptions {
  mode: 'qna' | 'engine';
  isAuthenticated: boolean;
  openAuthModal: LayoutOutletContext['openAuthModal'];
  conversationId: string;
  setConversationId: Dispatch<SetStateAction<string>>;
  conversationIdRef: MutableRefObject<string>;
  mountedRef: MutableRefObject<boolean>;
}

function createTaskMonitorRefs(): Record<EngineService, TaskMonitorRefs> {
  return {
    resource: {
      taskStreamAbortRef: { current: null },
      streamQueueRef: { current: [] },
      streamFlushTimerRef: { current: null },
      streamRafRef: { current: null },
    },
    personalized: {
      taskStreamAbortRef: { current: null },
      streamQueueRef: { current: [] },
      streamFlushTimerRef: { current: null },
      streamRafRef: { current: null },
    },
    path: {
      taskStreamAbortRef: { current: null },
      streamQueueRef: { current: [] },
      streamFlushTimerRef: { current: null },
      streamRafRef: { current: null },
    },
    push: {
      taskStreamAbortRef: { current: null },
      streamQueueRef: { current: [] },
      streamFlushTimerRef: { current: null },
      streamRafRef: { current: null },
    },
  };
}

type ResourceFieldErrors = Partial<Record<'course' | 'keyPoints', string>>;

function validateResourceForm(resourceForm: ResourceForm): ResourceFieldErrors {
  const errors: ResourceFieldErrors = {};
  if (!resourceForm.course.trim()) {
    errors.course = '请填写课程名称';
  }
  if (!resourceForm.keyPoints.trim()) {
    errors.keyPoints = '请填写重点知识点';
  }
  return errors;
}

export function useLearningStudioEngine({
  mode,
  isAuthenticated,
  openAuthModal,
  conversationId,
  setConversationId,
  conversationIdRef,
  mountedRef,
}: UseLearningStudioEngineOptions) {
  const taskMonitorRefsRef = useRef<Record<EngineService, TaskMonitorRefs>>(createTaskMonitorRefs());
  const activeTaskMonitorsRef = useRef<Partial<Record<EngineService, string>>>({});
  const engineSnapshotHydratedRef = useRef(false);
  const engineSubmitVersionRef = useRef(0);

  const [selectedService, setSelectedService] = useState<EngineService | null>(DEFAULT_ENGINE_SERVICE);
  const [serviceSnapshots, setServiceSnapshots] = useState<Record<EngineService, EngineTaskSnapshot>>(createInitialEngineSnapshots);
  const [resourceForm, setResourceForm] = useState<ResourceForm>(defaultResourceForm);
  const [resourceFieldErrors, setResourceFieldErrors] = useState<ResourceFieldErrors>({});
  const [pathForm, setPathForm] = useState<PathForm>({
    targetPeriod: '',
    weeklyHours: '',
    currentProgress: '',
  });
  const [pushForm, setPushForm] = useState<PushForm>({
    preferredType: 'CODE_CASE',
  });

  const activeEngineSnapshot = selectedService ? serviceSnapshots[selectedService] : createEmptyEngineTaskSnapshot();
  const engineBusy = selectedService ? hasLockedTask(activeEngineSnapshot) : false;
  const inlineResources = getInlineResourcesFromSnapshot(activeEngineSnapshot);
  const completedResources = createCompletedResourcesFromSnapshot(activeEngineSnapshot);

  const clearPersistedEngineSnapshot = useCallback(() => {
    if (typeof window === 'undefined') {
      return;
    }
    window.sessionStorage.removeItem(ENGINE_TASK_STORAGE_KEY);
  }, []);

  const abortEngineTasks = useCallback(() => {
    Object.values(taskMonitorRefsRef.current).forEach((refs) => {
      refs.taskStreamAbortRef.current?.abort();
      refs.taskStreamAbortRef.current = null;
      refs.streamQueueRef.current = [];
      cleanupStreamSchedulers(refs.streamFlushTimerRef, refs.streamRafRef);
    });
    activeTaskMonitorsRef.current = {};
  }, []);

  const updateServiceSnapshot = useCallback(
    (
      service: EngineService,
      updater: EngineTaskSnapshot | ((current: EngineTaskSnapshot) => EngineTaskSnapshot),
    ) => {
      setServiceSnapshots((prev) => {
        const current = prev[service];
        const next = typeof updater === 'function' ? updater(current) : updater;
        return {
          ...prev,
          [service]: syncSnapshotResultRecord(next),
        };
      });
    },
    [],
  );

  const resetEngineView = useCallback(() => {
    engineSubmitVersionRef.current += 1;
    abortEngineTasks();
    setConversationId('');
    setSelectedService(DEFAULT_ENGINE_SERVICE);
    setResourceFieldErrors({});
    setServiceSnapshots(createInitialEngineSnapshots());
    clearPersistedEngineSnapshot();
  }, [abortEngineTasks, clearPersistedEngineSnapshot, setConversationId]);

  const updateResourceForm = useCallback((next: ResourceForm) => {
    setResourceForm(next);
    setResourceFieldErrors((current) => {
      const nextErrors = { ...current };
      if (next.course.trim()) {
        delete nextErrors.course;
      }
      if (next.keyPoints.trim()) {
        delete nextErrors.keyPoints;
      }
      return nextErrors;
    });
  }, []);

  const markFormEditing = useCallback(() => {
    if (!selectedService) {
      return;
    }
    updateServiceSnapshot(selectedService, (current) => {
      if (hasLockedTask(current)) {
        return current;
      }
      return {
        ...current,
        engineState: 'ENGINE_FORM_EDITING',
      };
    });
  }, [selectedService, updateServiceSnapshot]);

  const handleSelectService = useCallback((service: EngineService) => {
    setSelectedService(service);
    updateServiceSnapshot(service, (current) => {
      if (current.engineState !== 'ENGINE_IDLE' || current.taskId) {
        return current;
      }
      return {
        ...current,
        engineState: 'ENGINE_SERVICE_SELECTED',
      };
    });
  }, [updateServiceSnapshot]);

  const monitorTask = useCallback(
    async (service: EngineService, currentTaskId: string) => {
      if (activeTaskMonitorsRef.current[service] === currentTaskId) {
        return;
      }
      activeTaskMonitorsRef.current[service] = currentTaskId;
      const refs = taskMonitorRefsRef.current[service];
      const snapshotSetter = <K extends keyof EngineTaskSnapshot>(
        key: K,
        currentValue?: (snapshot: EngineTaskSnapshot) => EngineTaskSnapshot[K],
      ) => (value: SetStateAction<EngineTaskSnapshot[K]>) => {
        updateServiceSnapshot(service, (current) => {
          const previousValue = currentValue ? currentValue(current) : current[key];
          const nextValue = typeof value === 'function'
            ? (value as (previous: EngineTaskSnapshot[K]) => EngineTaskSnapshot[K])(previousValue)
            : value;
          return {
            ...current,
            [key]: nextValue,
          };
        });
      };
      const outcome = await runByApiTask({
        service,
        currentTaskId,
        streamQueueRef: refs.streamQueueRef,
        streamFlushTimerRef: refs.streamFlushTimerRef,
        streamRafRef: refs.streamRafRef,
        setServiceResultLines: snapshotSetter('serviceResultLines'),
        setTaskProgress: snapshotSetter('taskProgress'),
        setTaskStatus: snapshotSetter('taskStatus'),
        setTaskSummary: snapshotSetter('taskSummary'),
        setDownloadLinks: snapshotSetter('downloadLinks'),
        setVideoResult: snapshotSetter('videoResult'),
        setInlineResource: snapshotSetter('inlineResource'),
        setInlineResources: snapshotSetter('inlineResources', getInlineResourcesFromSnapshot),
        setCompletedResources: snapshotSetter('completedResources', createCompletedResourcesFromSnapshot),
        setPracticeBatch: snapshotSetter('practiceBatch'),
        setJudgeResult: snapshotSetter('judgeResult'),
        setMasteryDiagnosis: snapshotSetter('masteryDiagnosis'),
        setLearningPlan: snapshotSetter('learningPlan'),
        setResourcePushPlan: snapshotSetter('resourcePushPlan'),
        setCriticReview: snapshotSetter('criticReview'),
        setAgentTrace: snapshotSetter('agentTrace'),
        taskStreamAbortRef: refs.taskStreamAbortRef,
        onResourceEvent: (eventName, event) => {
          const activeConversationId = conversationIdRef.current.trim()
            || (typeof window !== 'undefined'
              ? window.sessionStorage.getItem(ACTIVE_CONVERSATION_ID_STORAGE_KEY)?.trim() ?? ''
              : '');
          if (!activeConversationId) {
            return;
          }
          recordConversationResourceEvent(activeConversationId, eventName, event);
        },
      });

      const monitorStillCurrent = activeTaskMonitorsRef.current[service] === currentTaskId;
      if (monitorStillCurrent) {
        delete activeTaskMonitorsRef.current[service];
      }
      if (!monitorStillCurrent || !mountedRef.current) {
        return;
      }

      if (outcome === 'completed') {
        updateServiceSnapshot(service, (current) => ({
          ...current,
          engineState: 'ENGINE_COMPLETED',
          taskStatus: '任务完成',
        }));
        return;
      }

      if (outcome === 'failed') {
        updateServiceSnapshot(service, (current) => ({
          ...current,
          engineState: 'ENGINE_FAILED',
          taskStatus: '任务失败',
        }));
        return;
      }

      if (outcome === 'aborted') {
        updateServiceSnapshot(service, (current) => ({
          ...current,
          engineState: 'ENGINE_FAILED',
          taskStatus: current.taskStatus === '任务已取消' ? current.taskStatus : '连接中断，请重试',
        }));
        return;
      }

      if (outcome === 'running') {
        updateServiceSnapshot(service, (current) => ({
          ...current,
          engineState: 'ENGINE_RUNNING',
          taskStatus: '后台运行中',
          serviceResultLines: current.serviceResultLines.includes('任务仍在后台执行，可切换页面，稍后返回继续查看结果。')
            ? current.serviceResultLines
            : [...current.serviceResultLines, '任务仍在后台执行，可切换页面，稍后返回继续查看结果。'],
        }));
        return;
      }

      if (outcome === 'unauthorized') {
        updateServiceSnapshot(service, (current) => ({
          ...current,
          engineState: 'ENGINE_RUNNING',
          taskStatus: '登录失效，待重新登录',
        }));
        openAuthModal('login', '登录状态已失效，重新登录后可继续查看任务结果');
      }
    },
    [mountedRef, openAuthModal, updateServiceSnapshot],
  );

  const ensureEngineConversationId = useCallback(async () => {
    const currentConversationId = conversationIdRef.current.trim();
    if (currentConversationId) {
      return currentConversationId;
    }

    if (typeof window !== 'undefined') {
      const activeConversationId = window.sessionStorage.getItem(ACTIVE_CONVERSATION_ID_STORAGE_KEY)?.trim() ?? '';
      if (activeConversationId) {
        setConversationId(activeConversationId);
        window.dispatchEvent(new Event('app:conversation-updated'));
        return activeConversationId;
      }
    }

    const recentConversations = await conversationApi.listRecentConversations();
    const latestConversationId = recentConversations[0]?.conversationId?.trim() ?? '';
    if (latestConversationId) {
      setConversationId(latestConversationId);
      window.dispatchEvent(new Event('app:conversation-updated'));
      return latestConversationId;
    }

    const createdConversationId = (await conversationApi.createConversation()).conversationId;
    setConversationId(createdConversationId);
    window.dispatchEvent(new Event('app:conversation-updated'));
    return createdConversationId;
  }, [conversationIdRef, setConversationId]);

  const handleSubmitService = async () => {
    if (!isAuthenticated) {
      openAuthModal('login', '请先登录');
      return;
    }
    if (!selectedService || engineBusy || hasLockedTask(serviceSnapshots[selectedService])) {
      return;
    }
    if (selectedService === 'resource') {
      const errors = validateResourceForm(resourceForm);
      if (Object.keys(errors).length > 0) {
        setResourceFieldErrors(errors);
        updateServiceSnapshot(selectedService, (current) => ({
          ...current,
          engineState: 'ENGINE_FORM_EDITING',
          taskStatus: '请先补全必填项',
          serviceResultLines: ['课程名称和重点知识点为必填项，补全后再提交。'],
        }));
        return;
      }
    }

    const refs = taskMonitorRefsRef.current[selectedService];
    refs.taskStreamAbortRef.current?.abort();
    refs.taskStreamAbortRef.current = null;
    refs.streamQueueRef.current = [];
    cleanupStreamSchedulers(refs.streamFlushTimerRef, refs.streamRafRef);
    updateServiceSnapshot(selectedService, {
      engineState: 'ENGINE_SUBMITTING',
      taskId: '',
      taskProgress: 8,
      taskStatus: '已提交，等待受理',
      taskSummary: '',
      serviceResultLines: [],
      downloadLinks: [],
      videoResult: null,
      inlineResource: null,
      inlineResources: [],
      practiceBatch: null,
      completedResources: [],
      judgeResult: null,
      masteryDiagnosis: null,
      learningPlan: null,
      resourcePushPlan: null,
      criticReview: null,
      agentTrace: [],
      resultHistory: serviceSnapshots[selectedService].resultHistory,
      selectedResultTaskId: serviceSnapshots[selectedService].selectedResultTaskId,
    });

    const params = buildServiceParams(selectedService, { resourceForm, pathForm, pushForm });

    try {
      engineSubmitVersionRef.current += 1;
      const submitVersion = engineSubmitVersionRef.current;
      const ensuredConversationId = await ensureEngineConversationId();
      if (engineSubmitVersionRef.current !== submitVersion) {
        return;
      }
      const submitResp = await smartEngineApi.submit({
        conversationId: ensuredConversationId,
        serviceType: serviceTypeMap[selectedService],
        params,
      });

      updateServiceSnapshot(selectedService, (current) => ({
        ...current,
        taskId: submitResp.taskId,
        engineState: 'ENGINE_RUNNING',
        taskStatus: toUiTaskStatus(submitResp.status),
        selectedResultTaskId: submitResp.taskId,
      }));
      void monitorTask(selectedService, submitResp.taskId);
    } catch (error) {
      const message = getErrorMessage(error);
      updateServiceSnapshot(selectedService, (current) => ({
        ...current,
        engineState: 'ENGINE_FAILED',
        taskStatus: '任务失败',
        serviceResultLines: [...current.serviceResultLines, `服务请求失败：${message}`],
      }));
    }
  };

  const handleStopService = async () => {
    if (!selectedService) {
      return;
    }
    const service = selectedService;
    const currentTaskId = serviceSnapshots[service].taskId;
    const refs = taskMonitorRefsRef.current[selectedService];
    refs.taskStreamAbortRef.current?.abort();
    refs.taskStreamAbortRef.current = null;
    activeTaskMonitorsRef.current[service] = '';
    cleanupStreamSchedulers(refs.streamFlushTimerRef, refs.streamRafRef);
    updateServiceSnapshot(service, (current) => ({
      ...current,
      engineState: 'ENGINE_FAILED',
      taskStatus: currentTaskId ? '正在取消任务' : '已停止实时接收',
      serviceResultLines: current.serviceResultLines.includes('已发送取消请求，正在等待确认。')
        ? current.serviceResultLines
        : [
          ...current.serviceResultLines,
          currentTaskId ? '已发送取消请求，正在等待确认。' : '已停止当前页面的实时接收。',
        ],
    }));
    if (!currentTaskId) {
      return;
    }
    try {
      await smartEngineApi.cancelTask(currentTaskId);
      updateServiceSnapshot(service, (current) => ({
        ...current,
        engineState: 'ENGINE_FAILED',
        taskStatus: '任务已取消',
        serviceResultLines: current.serviceResultLines.includes('任务已取消。')
          ? current.serviceResultLines
          : [...current.serviceResultLines, '任务已取消。'],
      }));
    } catch (error) {
      const message = getErrorMessage(error);
      updateServiceSnapshot(service, (current) => ({
        ...current,
        taskStatus: '停止失败',
        serviceResultLines: [...current.serviceResultLines, `停止任务失败：${message}`],
      }));
    }
  };

  const handleSelectResultTask = useCallback((resultTaskId: string) => {
    if (!selectedService || !resultTaskId) {
      return;
    }
    updateServiceSnapshot(selectedService, (current) => ({
      ...current,
      selectedResultTaskId: resultTaskId,
    }));
  }, [selectedService, updateServiceSnapshot]);

  useEffect(() => {
    if (mode !== 'engine' || conversationId || typeof window === 'undefined') {
      return;
    }
    const activeConversationId = window.sessionStorage.getItem(ACTIVE_CONVERSATION_ID_STORAGE_KEY)?.trim() ?? '';
    if (activeConversationId) {
      setConversationId(activeConversationId);
    }
  }, [conversationId, mode, setConversationId]);

  useEffect(() => {
    if (mode !== 'engine' || engineSnapshotHydratedRef.current) {
      return;
    }

    engineSnapshotHydratedRef.current = true;
    if (typeof window === 'undefined') {
      return;
    }

    const raw = window.sessionStorage.getItem(ENGINE_TASK_STORAGE_KEY);
    if (!raw) {
      return;
    }

    try {
      const snapshot = JSON.parse(raw) as PersistedEngineTaskSnapshot;
      const persistedSnapshots = buildPersistedEngineSnapshots(
        snapshot.selectedService ?? null,
        snapshot.snapshots ?? createInitialEngineSnapshots(),
      );
      setSelectedService(snapshot.selectedService ?? DEFAULT_ENGINE_SERVICE);
      setConversationId(snapshot.conversationId ?? window.sessionStorage.getItem(ACTIVE_CONVERSATION_ID_STORAGE_KEY) ?? '');
      setServiceSnapshots({
        ...createInitialEngineSnapshots(),
        ...persistedSnapshots,
      });

      (Object.entries(persistedSnapshots) as Array<[EngineService, EngineTaskSnapshot]>).forEach(([service, item]) => {
        if (item.taskId && (item.engineState === 'ENGINE_RUNNING' || item.engineState === 'ENGINE_SUBMITTING')) {
          void monitorTask(service, item.taskId);
        }
      });
    } catch {
      clearPersistedEngineSnapshot();
    }
  }, [clearPersistedEngineSnapshot, mode, monitorTask, setConversationId]);

  useEffect(() => {
    if (mode !== 'engine' || !engineSnapshotHydratedRef.current || typeof window === 'undefined') {
      return;
    }

    const snapshot: PersistedEngineTaskSnapshot = {
      selectedService,
      conversationId,
      snapshots: buildPersistedEngineSnapshots(selectedService, serviceSnapshots),
    };

    const isEmptySnapshot =
      (!selectedService || selectedService === DEFAULT_ENGINE_SERVICE) &&
      !conversationId &&
      Object.values(serviceSnapshots).every((item) => !item.taskId && item.engineState === 'ENGINE_IDLE');

    if (isEmptySnapshot) {
      clearPersistedEngineSnapshot();
      return;
    }

    window.sessionStorage.setItem(ENGINE_TASK_STORAGE_KEY, JSON.stringify(snapshot));
  }, [
    clearPersistedEngineSnapshot,
    conversationId,
    mode,
    selectedService,
    serviceSnapshots,
  ]);

  return {
    selectedService,
    serviceSnapshots,
    resourceForm,
    pathForm,
    pushForm,
    activeEngineSnapshot,
    engineBusy,
    taskId: activeEngineSnapshot.taskId,
    taskProgress: activeEngineSnapshot.taskProgress,
    taskStatus: activeEngineSnapshot.taskStatus,
    taskSummary: activeEngineSnapshot.taskSummary,
    serviceResultLines: activeEngineSnapshot.serviceResultLines,
    downloadLinks: activeEngineSnapshot.downloadLinks,
    videoResult: activeEngineSnapshot.videoResult,
    inlineResources,
    completedResources,
    resourceFieldErrors,
    setResourceForm: updateResourceForm,
    setPathForm,
    setPushForm,
    markFormEditing,
    handleSelectService,
    handleSubmitService,
    handleStopService,
    handleSelectResultTask,
    resetEngineView,
    abortEngineTasks,
  };
}
