import { getErrorMessage, isUnauthorizedError } from '../api/request';
import { smartEngineApi } from '../api/smartEngine';
import { renderTalkingVideoInBrowser } from '../utils/browserVideoRenderer';
import { sanitizeMarkdownContent } from '../utils/markdownSanitizer';
import type {
  EngineService,
  AgentTraceStepView,
  InlineResourceView,
  CriticReviewView,
  LearningPlanView,
  MasteryDiagnosisView,
  ProfileCurrentGoal,
  ProfileDimensionScore,
  ProfileHistoryPoint,
  ProfileLearningHabits,
  ProfileSnapshot,
  ProfileSkillMastery,
  PracticeJudgeResult,
  PracticeQuestionBatch,
  ResourcePushPlanView,
  ResourceType,
  RunByApiTaskArgs,
  ServiceFormsPayload,
  SmartEngineStreamEvent,
  SmartEngineTaskResponse,
  TaskRunHandlers,
  TempDownloadLink,
  UserProfileResponse,
  VideoCardStyle,
  VideoResult,
  WeakPointRank,
} from './LearningStudioDemoPage.types';

interface ProfileErrorPattern {
  pattern: string;
  examples: string[];
}

export function normalizeCopyText(input: string): string {
  return input
    .replace(/\r\n/g, '\n')
    .replace(/\n{2,}/g, '\n')
    .trim();
}

export function sanitizeConversationMessageContent(input: string): string {
  const normalized = normalizeCopyText(input);
  if (!normalized || looksLikeTutorChain(normalized)) {
    return '';
  }

  return normalized
    .split('\n')
    .map((line) => line.trimEnd())
    .filter((line) => line.trim() !== '---')
    .map((line) =>
      line
        .replace(/^\s{0,3}#{1,6}\s*/g, '')
        .replace(/^\s*>\s?/g, '')
        .replace(/\*\*(.*?)\*\*/g, '$1')
        .replace(/__(.*?)__/g, '$1')
        .replace(/`([^`]+)`/g, '$1'),
    )
    .join('\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

export async function runByApiTask({
  service,
  currentTaskId,
  streamQueueRef,
  streamFlushTimerRef,
  streamRafRef,
  setServiceResultLines,
  setTaskProgress,
  setTaskStatus,
  setTaskSummary,
  setDownloadLinks,
  setVideoResult,
  setInlineResource,
  setInlineResources,
  setCompletedResources,
  setPracticeBatch,
  setJudgeResult,
  setMasteryDiagnosis,
  setLearningPlan,
  setResourcePushPlan,
  setCriticReview,
  setAgentTrace,
  taskStreamAbortRef,
  onResourceEvent,
}: RunByApiTaskArgs): Promise<'completed' | 'running' | 'failed' | 'aborted' | 'unauthorized'> {
  const browserRenderState = {
    taskId: currentTaskId,
    started: false,
    completed: false,
    promise: null as Promise<void> | null,
    errorMessage: '',
  };
  const handlers: TaskRunHandlers = {
    onProgress: (value, statusHint, options) => {
      const nextValue = Math.max(0, Math.min(options?.maxProgress ?? 100, value));
      setTaskProgress((prev) => (options?.allowDecrease ? nextValue : Math.max(prev, nextValue)));
      if (statusHint) {
        setTaskStatus(statusHint);
      }
    },
    onLine: (line) => {
      if (!line.trim()) {
        return;
      }
      streamQueueRef.current.push(line);
      scheduleStreamFlush(streamQueueRef, streamFlushTimerRef, streamRafRef, setServiceResultLines);
    },
    onSummary: (summary) => {
      if (!summary.trim()) {
        return;
      }
      setTaskSummary(summary);
    },
    onDownload: (item) => {
      setDownloadLinks((prev) => (prev.some((existing) => existing.url === item.url) ? prev : [...prev, item]));
      if (isVideoLink(item)) {
        setVideoResult((prev) => prev ?? mapDownloadToVideoResult(item));
      }
    },
    onVideo: (item) => {
      if (item.renderStatus === 'ready' || Boolean(item.videoUrl)) {
        browserRenderState.completed = true;
      }
      setVideoResult((prev) => mergeVideoResult(prev, item));
    },
    onInlineResource: (item) => {
      setInlineResource(item);
      setInlineResources((prev) => (
        prev.some((existing) => existing.title === item.title && existing.kind === item.kind)
          ? prev
          : [...prev, item]
      ));
      setCompletedResources((prev) => {
        const key = `inline:${item.kind}:${item.title}`;
        return prev.some((existing) => existing.key === key)
          ? prev
          : [...prev, { kind: 'inline', key, resource: item }];
      });
    },
    onQuestionBatch: (item) => {
      setPracticeBatch(item);
      setCompletedResources((prev) => {
        const key = `question_batch:${item.title}:${item.topic}`;
        return prev.some((existing) => existing.key === key)
          ? prev
          : [...prev, { kind: 'question_batch', key, batch: item }];
      });
      setJudgeResult(null);
      setTaskSummary(item.title);
    },
    onJudgeResult: (item) => {
      setJudgeResult(item);
      setTaskSummary(item.summary);
    },
    onMasteryDiagnosis: (item) => {
      setMasteryDiagnosis(item);
    },
    onLearningPlan: (item) => {
      setLearningPlan(item);
    },
    onResourcePushPlan: (item) => {
      setResourcePushPlan(item);
    },
    onCriticReview: (item) => {
      setCriticReview(item);
    },
    onAgentTrace: (items) => {
      setAgentTrace(items);
    },
    onResourceEvent,
  };

  const streamAbortController = new AbortController();
  taskStreamAbortRef.current = streamAbortController;
  let streamErrorMessage = '';
  let streamDone = false;

  await smartEngineApi.streamTask(
    currentTaskId,
    {
      onEvent: (event) => {
        void consumeTaskStreamEvent(event, handlers, browserRenderState);
      },
      onDone: () => {
        streamDone = true;
      },
      onError: (error) => {
        streamErrorMessage = error.message;
      },
    },
    streamAbortController.signal,
  );

  taskStreamAbortRef.current = null;
  flushStreamQueue(streamQueueRef, streamFlushTimerRef, streamRafRef, setServiceResultLines);

  if (streamDone && !streamErrorMessage) {
    const browserRenderSucceeded = await settleBrowserRender(handlers, browserRenderState);
    if (!browserRenderSucceeded) {
      setTaskStatus('视频生成失败');
      return 'failed';
    }
    setTaskProgress(100);
    setTaskStatus('任务完成');
    return 'completed';
  }

  if (streamAbortController.signal.aborted && !streamErrorMessage) {
    return 'aborted';
  }

  if (streamErrorMessage) {
    if (isUnauthorizedError(new Error(streamErrorMessage))) {
      handlers.onLine('任务结果读取需要重新登录，任务本身可能仍在后台继续执行。');
      return 'unauthorized';
    }
    handlers.onLine('实时连接中断，正在改用轮询方式读取结果。');
  }

  try {
    for (let i = 0; i < 45; i += 1) {
      if (streamAbortController.signal.aborted) {
        return 'aborted';
      }
      const task = await smartEngineApi.getTask(currentTaskId, { dedupe: false, retry: 2 });
      await applyTaskSnapshot(task, service, handlers, browserRenderState);

      if (task.status === 'COMPLETED') {
        const browserRenderSucceeded = await settleBrowserRender(handlers, browserRenderState);
        if (!browserRenderSucceeded) {
          throw new Error(browserRenderState.errorMessage || '视频生成失败');
        }
        flushStreamQueue(streamQueueRef, streamFlushTimerRef, streamRafRef, setServiceResultLines);
        return 'completed';
      }

      if (task.status === 'FAILED' || task.status === 'CANCELLED' || task.status === 'TIMEOUT') {
        throw new Error(formatUserFacingTaskMessage(task.errorMessage || '任务失败'));
      }

      await wait(2000);
    }

    flushStreamQueue(streamQueueRef, streamFlushTimerRef, streamRafRef, setServiceResultLines);
    return 'running';
  } catch (error) {
    if (streamAbortController.signal.aborted) {
      return 'aborted';
    }
    if (isUnauthorizedError(error)) {
      handlers.onLine('任务结果读取需要重新登录，任务本身可能仍在后台继续执行。');
      flushStreamQueue(streamQueueRef, streamFlushTimerRef, streamRafRef, setServiceResultLines);
      return 'unauthorized';
    }
    handlers.onLine(formatUserFacingTaskMessage(getErrorMessage(error)));
    flushStreamQueue(streamQueueRef, streamFlushTimerRef, streamRafRef, setServiceResultLines);
    return 'failed';
  }
}

async function consumeTaskStreamEvent(
  event: SmartEngineStreamEvent,
  handlers: TaskRunHandlers,
  browserRenderState: {
    taskId: string;
    started: boolean;
    completed: boolean;
    promise: Promise<void> | null;
    errorMessage: string;
  },
): Promise<void> {
  const payload = event.payload;
  if (isResourceStoreEvent(event.event)) {
    handlers.onResourceEvent?.(event.event, event);
  }

  if (event.event === 'progress') {
    const progress = readNumeric(payload?.percent) ?? readNumeric(payload?.progress) ?? 0;
    handlers.onProgress(progress, readStatusHint(payload));
    const learningPlan = readLearningPlan(payload);
    if (learningPlan) {
      handlers.onLearningPlan(learningPlan);
    }
    const masteryDiagnosis = readMasteryDiagnosis(payload);
    if (masteryDiagnosis) {
      handlers.onMasteryDiagnosis(masteryDiagnosis);
    }
    const criticReview = readCriticReview(payload);
    if (criticReview) {
      handlers.onCriticReview(criticReview);
    }
    const resourcePushPlan = readResourcePushPlan(payload);
    if (resourcePushPlan) {
      handlers.onResourcePushPlan(resourcePushPlan);
    }
    return;
  }

  if (isVideoProgressEvent(event.event)) {
    const stage = mapVideoProgressEvent(event.event);
    const progress = readNumeric(payload?.percent) ?? readNumeric(payload?.progress) ?? stage.progress;
    handlers.onProgress(progress, stage.status);
    const line = readSummary(payload) || stage.message;
    if (line) {
      handlers.onLine(line);
    }
    if (event.event === 'video_gen:complete') {
      const summary = readSummary(payload);
      if (summary) {
        handlers.onSummary(summary);
      }
      const browserRenderedVideo = readBrowserRenderedVideoResult(payload, '视频生成中，请稍候');
      if (browserRenderedVideo) {
        handlers.onVideo(browserRenderedVideo);
      }
      await maybeStartBrowserRender(payload, handlers, browserRenderState);
      const videoResult = readVideoResult(payload);
      if (videoResult) {
        if (!ensureGeneratedPayloadProvenance(payload, handlers, '视频资源')) {
          return;
        }
        handlers.onVideo(videoResult);
      }
    }
    if (event.event === 'video_gen:speech') {
      const browserRenderedVideo = readBrowserRenderedVideoResult(payload, '已收到语音素材，正在生成视频');
      if (browserRenderedVideo) {
        handlers.onVideo(browserRenderedVideo);
      }
      await maybeStartBrowserRender(payload, handlers, browserRenderState);
    }
    return;
  }

  if (event.event === 'resource_file') {
    const title = readString(payload?.title) || readString(payload?.fileName) || '资源文件';
    const downloadUrl = readString(payload?.downloadUrl);
    const resourceType = readString(payload?.assetType);
    const fileName = readString(payload?.fileName);
    if (!ensureGeneratedPayloadProvenance(payload, handlers, '资源')) {
      return;
    }
    const inlineResource = readInlineResource(payload);
    const browserRenderedVideo = readBrowserRenderedVideoResult(payload, '视频素材已就绪，正在生成视频');
    if (browserRenderedVideo) {
      handlers.onVideo(browserRenderedVideo);
    }
    if (downloadUrl) {
      const summary = readString(payload?.summary);
      const sourceName = readString(payload?.sourceName);
      if (!isSafeRecommendationContent(title, summary, sourceName, downloadUrl)) {
        handlers.onLine('已过滤不安全外部资源');
        return;
      }
      handlers.onDownload({
        title: truncateRecommendationText(title, 20),
        url: downloadUrl,
        fileName: fileName || undefined,
        expiresHint: formatExpiresHint(payload),
        resourceType,
        mimeType: readString(payload?.mimeType),
        summary: truncateRecommendationText(summary, 20),
        sourceName,
        thumbnailUrl: readUrlField(payload, ['thumbnailUrl', 'thumbnail_url', 'posterUrl', 'coverUrl']),
        duration: readDuration(payload),
        style: readVideoStyle(payload),
        knowledgePoint: readString(payload?.knowledgePoint) || readString(payload?.topic),
      });
    }
    if (inlineResource) {
      handlers.onInlineResource(inlineResource);
    }
    handlers.onLine(`${title} 已生成`);
    return;
  }

  if (event.event === 'question_batch') {
    if (!ensureQuestionBatchProvenance(payload, handlers)) {
      return;
    }
    const batch = readPracticeQuestionBatch(payload);
    if (batch) {
      handlers.onQuestionBatch(batch);
      handlers.onLine(`${batch.title} 已生成，可直接在页面作答`);
      return;
    }
  }

  if (event.event === 'judge_result') {
    const result = readPracticeJudgeResult(payload);
    if (result) {
      handlers.onJudgeResult(result);
      handlers.onLine(result.summary);
      return;
    }
  }

  if (event.event === 'done') {
    const summary = readSummary(payload);
    const learningPlan = readLearningPlan(payload);
    if (learningPlan) {
      handlers.onLearningPlan(learningPlan);
    }
    const masteryDiagnosis = readMasteryDiagnosis(payload);
    if (masteryDiagnosis) {
      handlers.onMasteryDiagnosis(masteryDiagnosis);
    }
    const criticReview = readCriticReview(payload);
    if (criticReview) {
      handlers.onCriticReview(criticReview);
    }
    const resourcePushPlan = readResourcePushPlan(payload);
    if (resourcePushPlan) {
      handlers.onResourcePushPlan(resourcePushPlan);
    }
    const agentTrace = readAgentTrace(payload);
    if (agentTrace.length > 0) {
      handlers.onAgentTrace(agentTrace);
    }
    const status = readString(payload?.status).toUpperCase();
    const doneLabel = status === 'FAILED' || status === 'ERROR'
      ? '任务失败'
      : status === 'PARTIAL_FAILED'
        ? '部分完成'
        : '任务完成';
    handlers.onProgress(100, doneLabel);
    if (summary) {
      handlers.onSummary(formatUserFacingTaskMessage(summary));
    }
    if (doneLabel === '任务失败') {
      handlers.onLine(formatUserFacingTaskMessage(summary || '任务执行失败'));
    } else if (doneLabel === '部分完成') {
      handlers.onLine(formatUserFacingTaskMessage(summary || '任务部分完成，部分资源生成失败'));
    }
    return;
  }

  if (event.event === 'error') {
    handlers.onLine(formatUserFacingTaskMessage(readSummary(payload) || '任务执行失败'));
    return;
  }

  const summaryLine = readSummary(payload);
  const line = summaryLine
    ? summaryLine
    : shouldSuppressTaskPayload(payload)
      ? ''
      : formatUserFacingTaskMessage(stringifyCompact(payload));
  if (line) {
    handlers.onLine(line);
  }
}

async function applyTaskSnapshot(
  task: SmartEngineTaskResponse,
  service: EngineService,
  handlers: TaskRunHandlers,
  browserRenderState: {
    taskId: string;
    started: boolean;
    completed: boolean;
    promise: Promise<void> | null;
    errorMessage: string;
  },
): Promise<void> {
  const progress =
    readNumeric(task.progressPercent) ??
    readNumeric(task.progress);

  if (progress !== undefined) {
    handlers.onProgress(progress, toUiTaskStatus(task.status));
  }

  if (task.responseSummary) {
    const summary = readSummary(task.responseSummary);
    const learningPlan = readLearningPlan(task.responseSummary);
    if (learningPlan) {
      handlers.onLearningPlan(learningPlan);
    }
    const masteryDiagnosis = readMasteryDiagnosis(task.responseSummary);
    if (masteryDiagnosis) {
      handlers.onMasteryDiagnosis(masteryDiagnosis);
    }
    const criticReview = readCriticReview(task.responseSummary);
    if (criticReview) {
      handlers.onCriticReview(criticReview);
    }
    const resourcePushPlan = readResourcePushPlan(task.responseSummary);
    if (resourcePushPlan) {
      handlers.onResourcePushPlan(resourcePushPlan);
    }
    const agentTrace = readAgentTrace(task.responseSummary);
    if (agentTrace.length > 0) {
      handlers.onAgentTrace(agentTrace);
    }
    if (summary) {
      handlers.onSummary(summary);
    }
    const videoResult = readVideoResult(task.responseSummary);
    if (videoResult) {
      if (!ensureGeneratedPayloadProvenance(task.responseSummary, handlers, '视频资源')) {
        return;
      }
      handlers.onVideo(videoResult);
    }
    if (readString(task.responseSummary.audioBase64)) {
      const browserRenderedVideo = readBrowserRenderedVideoResult(task.responseSummary, '视频素材已就绪，正在恢复视频生成');
      if (browserRenderedVideo) {
        handlers.onVideo(browserRenderedVideo);
      }
      if (!ensureGeneratedPayloadProvenance(task.responseSummary, handlers, '视频资源')) {
        return;
      }
      await maybeStartBrowserRender(task.responseSummary, handlers, browserRenderState);
    }
    const inlineResource = readInlineResource(task.responseSummary);
    if (inlineResource) {
      if (!ensureGeneratedPayloadProvenance(task.responseSummary, handlers, '资源')) {
        return;
      }
      handlers.onInlineResource(inlineResource);
    }
    const batch = readPracticeQuestionBatch(task.responseSummary);
    if (batch) {
      if (!ensureQuestionBatchProvenance(task.responseSummary, handlers)) {
        return;
      }
      handlers.onQuestionBatch(batch);
    }
    const judgeResult = readPracticeJudgeResult(task.responseSummary);
    if (judgeResult) {
      handlers.onJudgeResult(judgeResult);
    }
    responseSummaryToLines(task.responseSummary, service).forEach((line) => handlers.onLine(line));
  }
}

async function maybeStartBrowserRender(
  payload: Record<string, unknown> | undefined,
  handlers: TaskRunHandlers,
  browserRenderState: {
    taskId: string;
    started: boolean;
    completed: boolean;
    promise: Promise<void> | null;
    errorMessage: string;
  },
): Promise<void> {
  if (!payload || browserRenderState.completed) {
    return;
  }
  if (browserRenderState.promise) {
    await browserRenderState.promise;
    return;
  }
  const audioBase64 = readString(payload.audioBase64);
  if (!audioBase64) {
    return;
  }
  if (!ensureGeneratedPayloadProvenance(payload, handlers, '视频资源')) {
    browserRenderState.errorMessage = '缺少有效的智能生成校验信息';
    return;
  }
  browserRenderState.started = true;
  browserRenderState.errorMessage = '';
  handlers.onProgress(78, '视频生成中');
  handlers.onLine('已收到语音素材，开始生成视频');
  browserRenderState.promise = renderTalkingVideoInBrowser(
    {
      taskId: browserRenderState.taskId,
      audioBase64,
      title: readString(payload.title) || readString(payload.topic) || '教学视频',
      durationSeconds: readDuration(payload),
      knowledgePoint: readString(payload.knowledgePoint) || readString(payload.topic),
      style: readVideoStyle(payload),
    },
    {
      onProgress: (percent, message) => {
        handlers.onProgress(Math.max(78, percent), '视频生成中');
        if (message) {
          handlers.onLine(message);
        }
      },
    },
  )
    .then((rendered) => {
      browserRenderState.completed = true;
      handlers.onSummary('视频生成完成');
      handlers.onVideo({
        title: readString(payload.title) || readString(payload.topic) || '教学视频',
        videoUrl: rendered.videoUrl,
        thumbnailUrl: rendered.thumbnailUrl,
        duration: readDuration(payload),
        style: readVideoStyle(payload),
        knowledgePoint: readString(payload.knowledgePoint) || readString(payload.topic),
        expiresHint: '视频已生成',
        fileName: rendered.fileName,
        renderStatus: 'ready',
        renderMessage: '视频生成完成',
      });
      handlers.onProgress(100, '视频生成完成');
    })
    .catch((error) => {
      browserRenderState.started = false;
      browserRenderState.errorMessage = toVideoGenerationErrorMessage(error);
      const failedVideo = readBrowserRenderedVideoResult(payload, browserRenderState.errorMessage);
      if (failedVideo) {
        handlers.onVideo({
          ...failedVideo,
          renderStatus: 'failed',
          renderMessage: browserRenderState.errorMessage,
          expiresHint: '视频生成失败',
        });
      }
      handlers.onLine(`视频生成失败：${browserRenderState.errorMessage}`);
    })
    .finally(() => {
      browserRenderState.promise = null;
    });
  await browserRenderState.promise;
}

async function settleBrowserRender(
  handlers: TaskRunHandlers,
  browserRenderState: {
    taskId: string;
    started: boolean;
    completed: boolean;
    promise: Promise<void> | null;
    errorMessage: string;
  },
): Promise<boolean> {
  if (browserRenderState.errorMessage) {
    return false;
  }
  if (!browserRenderState.started && !browserRenderState.promise) {
    return true;
  }
  if (browserRenderState.promise) {
    handlers.onProgress(88, '等待视频生成完成');
    await browserRenderState.promise;
  }
  return !browserRenderState.errorMessage;
}

function responseSummaryToLines(summary: Record<string, unknown>, service: EngineService): string[] {
  if (service === 'path' || service === 'personalized') {
    const learningPath = readRecord(summary.learningPath);
    if (learningPath) {
      const lines = formatLearningPathLines(learningPath);
      const resourcePushPlan = readRecord(summary.resourcePushPlan);
      const stepResources = Array.isArray(resourcePushPlan?.stepResources) ? resourcePushPlan.stepResources : [];
      if (stepResources.length > 0) {
        lines.push(`资源匹配：已为 ${stepResources.length} 个学习步骤绑定资源或检索证据`);
      }
      return lines;
    }
  }
  return Object.entries(summary)
    .filter(([key]) => ![
      'summary',
      'summaryText',
      'message',
      'inlineContent',
      'questions',
      'items',
      'learningPath',
      'learningPlan',
      'masteryDiagnosis',
      'resourcePushPlan',
      'pushedResources',
      'agentTrace',
      'criticReview',
      'planning',
      'checkpointActions',
      'learningLoop',
      'planningWarnings',
      'resourceFailures',
      'traceId',
      'taskId',
      'score',
      'totalScore',
      'confidence',
      'confidenceScore',
      'confidence_score',
      'qualityScore',
      'videoUrl',
      'video_url',
      'finalVideoUrl',
      'final_video_url',
      'thumbnailUrl',
      'thumbnail_url',
      'posterUrl',
      'coverUrl',
      'downloadUrl',
      'download_url',
      'resourceUrl',
      'resource_url',
      'fileUrl',
      'file_url',
      'url',
    ].includes(key))
    .map(([key, value]) => formatUserFacingTaskMessage(`${labelForSummaryKey(key, service)}：${stringifyCompact(value)}`))
    .filter((line) => !line.endsWith('：'));
}

function looksLikeTutorChain(text: string): boolean {
  const normalized = text.replace(/\n/g, ' ').trim();
  return normalized.includes('历史摘要')
    || normalized.includes('检索查询')
    || normalized.includes('来源摘要')
    || normalized.includes('查询处理')
    || normalized.includes('质量复核')
    || normalized.includes('优先参考的来源')
    || normalized.includes('建议你这样学')
    || normalized.includes('接下来请你先回答')
    || normalized.includes('未解决的问题');
}

export function mapProfileResponse(response: UserProfileResponse): ProfileSnapshot | null {
  const raw = response.profile;
  if (!raw) {
    return null;
  }

  const preferredResourceTypes = readStringArray(raw.preferredResourceTypes, raw.preferred_resource_types, raw.preference, raw.learningPreference, raw.learning_preference)
    .map(localizeResourceTypeLabel)
    .filter(Boolean);
  const rawCurrentGoal = readRecord(raw.currentGoal) ?? readRecord(raw.current_goal);
  const learningGoal = readString(raw.learningGoal)
    || readString(raw.learning_goal)
    || readString(rawCurrentGoal?.shortTerm)
    || readString(rawCurrentGoal?.short_term)
    || readString(raw.goal);
  const currentGoal = readCurrentGoal(raw);
  const learningHabits = readLearningHabits(raw);
  const skillMastery = readSkillMastery(raw);
  const knowledgeBase = localizeKnowledgeFoundation(
    readString(raw.knowledgeBase)
    || readString(raw.knowledge_base)
    || readString(raw.foundationLevel)
    || readString(raw.foundation_level)
    || readString(raw.knowledgeFoundation)
    || readString(raw.knowledge_foundation)
    || readString(raw.studentLevel)
    || readString(raw.student_level),
  );
  const confidenceScore = normalizeConfidenceScore(
    readNumeric(raw.confidenceScore)
      ?? readNumeric(raw.confidence_score)
      ?? readNumeric(raw.confidence)
      ?? readNumeric(raw.score),
  );
  const errorPatterns = readErrorPatterns(raw);
  const weakPointRanks = buildWeakPointRanks(raw, errorPatterns);
  const explanationPreference = localizeExplanationPreference(
    readString(raw.explanationPreference) || readString(raw.explanation_preference),
  );
  const learningPace = localizeLearningPace(readString(raw.learningPace) || readString(raw.learning_pace));
  const inferredRecommendations = readStringArray(raw.inferredRecommendations, raw.inferred_recommendations)
    .map(localizeNarrativeText)
    .filter(Boolean)
    .slice(0, 3);
  const dimensionScores = buildProfileDimensionScores({
    raw,
    knowledgeBase,
    learningGoal,
    confidenceScore,
    preferredResourceTypes,
    explanationPreference,
    weakPointRanks,
  });

  return {
    major: readString(raw.major)
      || readString(raw.courseFocus)
      || readString(raw.course_focus)
      || readString(raw.courseDirection)
      || readString(raw.course_direction),
    goal: learningGoal,
    knowledgeBase,
    weakPoints: readStringArray(
      raw.weakPoints,
      raw.weak_points,
      raw.knownGaps,
      raw.known_gaps,
      raw.knowledgeGaps,
      raw.knowledge_gaps,
      Array.isArray(raw.weakPointDetails)
        ? (raw.weakPointDetails as Array<Record<string, unknown>>).map((item) => item.topic)
        : Array.isArray(raw.weak_point_details)
          ? (raw.weak_point_details as Array<Record<string, unknown>>).map((item) => item.topic)
        : undefined,
    ),
    preference: preferredResourceTypes,
    cognitiveStyle: localizeCognitiveStyle(
      readString(raw.cognitiveStyle) || readString(raw.cognitive_style) || readString(raw.learningStyle),
    ),
    learningPace,
    currentGoal,
    learningHabits,
    skillMastery,
    confidenceLevel: localizeConfidenceLevel(
      readString(raw.confidenceLevel)
        || readString(raw.confidence_level)
        || readString(raw.confidence)
        || readString(raw.confidenceScore)
        || readString(raw.confidence_score),
    ),
    confidenceScore,
    explanationPreference,
    inferredRecommendations,
    dimensionScores,
    weakPointRanks,
    history: buildProfileHistory(response),
  };
}

function readCurrentGoal(raw: Record<string, unknown>): ProfileCurrentGoal {
  const currentGoal = readRecord(raw.currentGoal) ?? readRecord(raw.current_goal) ?? {};
  return {
    shortTerm: localizeNarrativeText(readString(currentGoal.shortTerm) || readString(currentGoal.short_term)),
    midTerm: localizeNarrativeText(readString(currentGoal.midTerm) || readString(currentGoal.mid_term)),
    context: localizeNarrativeText(readString(currentGoal.context)),
    urgency: readString(currentGoal.urgency),
  };
}

function readLearningHabits(raw: Record<string, unknown>): ProfileLearningHabits {
  const learningHabits = readRecord(raw.learningHabits) ?? readRecord(raw.learning_habits) ?? {};
  return {
    studyFrequency: localizeStudyFrequency(readString(learningHabits.studyFrequency) || readString(learningHabits.study_frequency)),
    preferredTime: localizeNarrativeText(readString(learningHabits.preferredTime) || readString(learningHabits.preferred_time)),
    avgSessionDuration: Math.max(0, Math.round(readNumeric(learningHabits.avgSessionDuration) ?? readNumeric(learningHabits.avg_session_duration) ?? 0)),
    noteTaking: Boolean(learningHabits.noteTaking ?? learningHabits.note_taking),
    selfTesting: Boolean(learningHabits.selfTesting ?? learningHabits.self_testing),
  };
}

function readSkillMastery(raw: Record<string, unknown>): ProfileSkillMastery[] {
  const skillMastery = readRecord(raw.skillMastery) ?? readRecord(raw.skill_mastery);
  if (!skillMastery) {
    return [];
  }
  return Object.entries(skillMastery)
    .map(([topic, value]) => ({
      topic: localizeNarrativeText(topic),
      score: normalizeConfidenceScore(readNumeric(value)),
    }))
    .filter((item) => item.topic)
    .sort((left, right) => right.score - left.score);
}

function buildProfileHistory(response: UserProfileResponse): ProfileHistoryPoint[] {
  const history = Array.isArray(response.history) ? response.history : [];
  return history
    .map((item) => {
      const profile = readRecord(item.profile) ?? {};
      const weakPoints = readStringArray(
        profile.weakPoints,
        profile.weak_points,
        profile.knowledgeGaps,
        profile.knowledge_gaps,
      );
      return {
        version: Math.max(1, Math.round(readNumeric(item.version) ?? 1)),
        updatedAt: readString(item.updatedAt),
        confidenceScore: normalizeConfidenceScore(readNumeric(item.confidence) ?? readNumeric(profile.confidenceScore)),
        knowledgeBase: localizeKnowledgeFoundation(
          readString(profile.knowledgeBase)
          || readString(profile.foundationLevel)
          || readString(profile.knowledgeFoundation)
          || readString(profile.studentLevel),
        ),
        weakPointCount: weakPoints.length,
        learningPace: localizeLearningPace(readString(profile.learningPace) || readString(profile.learning_pace)),
      };
    })
    .filter((item) => item.updatedAt || item.knowledgeBase || item.weakPointCount > 0);
}

function buildProfileDimensionScores(input: {
  raw: Record<string, unknown>;
  knowledgeBase: string;
  learningGoal: string;
  confidenceScore: number;
  preferredResourceTypes: string[];
  explanationPreference: string;
  weakPointRanks: WeakPointRank[];
}): ProfileDimensionScore[] {
  const skillMastery = readRecord(input.raw.skillMastery) ?? readRecord(input.raw.skill_mastery);
  const skillValues = skillMastery
    ? Object.values(skillMastery)
      .map((value) => readNumeric(value))
      .filter((value): value is number => value !== undefined)
      .map(normalizeConfidenceScore)
    : [];
  const learningHabits = readRecord(input.raw.learningHabits) ?? readRecord(input.raw.learning_habits);
  const currentGoal = readRecord(input.raw.currentGoal) ?? readRecord(input.raw.current_goal);
  const noteTaking = Boolean(learningHabits?.noteTaking ?? learningHabits?.note_taking);
  const selfTesting = Boolean(learningHabits?.selfTesting ?? learningHabits?.self_testing);
  const studyFrequency = localizeStudyFrequency(readString(learningHabits?.studyFrequency) || readString(learningHabits?.study_frequency));
  const avgSessionDuration = readNumeric(learningHabits?.avgSessionDuration) ?? readNumeric(learningHabits?.avg_session_duration) ?? 0;
  const cognitiveStyle = localizeCognitiveStyle(
    readString(input.raw.cognitiveStyle)
      || readString(input.raw.cognitive_style)
      || readString(input.raw.learningStyle)
      || readString(input.raw.learning_style),
  );

  const knowledgeBaseScore = normalizeToPercent(levelToScore(input.knowledgeBase));
  const skillMasteryScore = normalizeToPercent(
    skillValues.length > 0
      ? skillValues.reduce((sum, value) => sum + value, 0) / skillValues.length
      : 0,
  );
  const goalScore = normalizeToPercent(
    (input.learningGoal ? 72 : 38)
      + (readString(currentGoal?.midTerm) ? 10 : 0)
      + (readString(currentGoal?.context) ? 6 : 0)
      + (readString(currentGoal?.urgency) === 'HIGH' ? 4 : 0),
  );
  const habitScore = normalizeToPercent(
    35
      + (studyFrequency.includes('高频') ? 18 : studyFrequency ? 10 : 0)
      + Math.min(18, Math.round(avgSessionDuration / 2))
      + (noteTaking ? 12 : 0)
      + (selfTesting ? 15 : 0),
  );
  const weakPointControlScore = normalizeToPercent(
    input.weakPointRanks.length > 0
      ? 100 - (input.weakPointRanks.reduce((sum, item) => sum + item.severity, 0) / input.weakPointRanks.length) * 45
      : 82,
  );
  const cognitiveFitScore = normalizeToPercent(
    42
      + (cognitiveStyle ? 18 : 0)
      + (input.explanationPreference ? 18 : 0)
      + Math.min(22, input.preferredResourceTypes.length * 8),
  );

  return [
    {
      key: 'knowledgeBase',
      subject: '基础匹配',
      score: knowledgeBaseScore,
      fullMark: 100,
      hint: `当前基础：${input.knowledgeBase || '待分析'}`,
      description: '表示当前讲解和练习难度是否贴合你的已有基础。',
    },
    {
      key: 'skillMastery',
      subject: '技能掌握',
      score: skillMasteryScore,
      fullMark: 100,
      hint: skillValues.length > 0 ? `已识别 ${skillValues.length} 个技能掌握度` : '等待更多练习与评估数据',
      description: '表示系统从练习和对话中识别到的知识点掌握情况。',
    },
    {
      key: 'goalClarity',
      subject: '目标清晰度',
      score: goalScore,
      fullMark: 100,
      hint: input.learningGoal || '尚未形成明确目标',
      description: '表示当前学习目标是否明确，便于系统安排后续内容。',
    },
    {
      key: 'learningHabits',
      subject: '节奏稳定度',
      score: habitScore,
      fullMark: 100,
      hint: studyFrequency || '等待更多学习节奏数据',
      description: '表示系统观察到的学习频率、复盘和自测信号是否稳定。',
    },
    {
      key: 'weakPointControl',
      subject: '薄弱点可控程度',
      score: weakPointControlScore,
      fullMark: 100,
      hint: input.weakPointRanks[0]?.topic ? `当前首要薄弱点：${input.weakPointRanks[0].topic}` : '暂无明显薄弱点',
      description: '表示薄弱点是否集中、清楚，是否适合被拆成可逐步解决的小任务。',
    },
    {
      key: 'cognitiveFit',
      subject: '讲解适配度',
      score: cognitiveFitScore,
      fullMark: 100,
      hint: input.explanationPreference || cognitiveStyle || '等待画像进一步完善',
      description: '表示系统掌握的讲解偏好和资源偏好是否足够支持个性化教学。',
    },
  ];
}

function buildWeakPointRanks(raw: Record<string, unknown>, errorPatterns: ProfileErrorPattern[] = []): WeakPointRank[] {
  const details = Array.isArray(raw.weakPointDetails)
    ? raw.weakPointDetails
    : Array.isArray(raw.weak_point_details)
      ? raw.weak_point_details
      : [];
  const fromDetails = details
    .map((item) => readRecord(item))
    .filter((item): item is Record<string, unknown> => item !== null)
    .map((item) => {
      const topic = localizeNarrativeText(readString(item.topic));
      return {
        topic,
        severity: normalizeSeverityScore(readNumeric(item.severity)),
        lastError: localizeNarrativeText(readString(item.lastError) || readString(item.last_error)),
        errorPattern: findErrorPatternForTopic(topic, errorPatterns),
        severityInferred: false,
      };
    })
    .filter((item) => item.topic);
  if (fromDetails.length > 0) {
    return fromDetails.sort((left, right) => right.severity - left.severity);
  }

  const weakPoints = readStringArray(raw.weakPoints, raw.weak_points, raw.knowledgeGaps, raw.knowledge_gaps, raw.knownGaps, raw.known_gaps);
  return weakPoints.map((topic, index) => {
    const localizedTopic = localizeNarrativeText(topic);
    return {
      topic: localizedTopic,
      severity: normalizeSeverityScore(0.76 - index * 0.1),
      lastError: '',
      errorPattern: findErrorPatternForTopic(localizedTopic, errorPatterns),
      severityInferred: true,
    };
  });
}

function readErrorPatterns(raw: Record<string, unknown>): ProfileErrorPattern[] {
  const patterns = Array.isArray(raw.errorPatterns)
    ? raw.errorPatterns
    : Array.isArray(raw.error_patterns)
      ? raw.error_patterns
      : [];

  return patterns
    .map((item) => readRecord(item))
    .filter((item): item is Record<string, unknown> => item !== null)
    .map((item) => ({
      pattern: localizeErrorPattern(readString(item.pattern) || readString(item.errorPattern) || readString(item.error_pattern)),
      examples: readStringArray(item.examples, item.exampleTopics, item.example_topics),
    }))
    .filter((item) => item.pattern && item.examples.length > 0);
}

function findErrorPatternForTopic(topic: string, patterns: ProfileErrorPattern[]): string | undefined {
  const normalizedTopic = normalizePatternMatchText(topic);
  if (!normalizedTopic) {
    return undefined;
  }
  const matched = patterns.find((item) =>
    item.examples.some((example) => {
      const normalizedExample = normalizePatternMatchText(example);
      return Boolean(normalizedExample)
        && (normalizedExample === normalizedTopic
          || normalizedExample.includes(normalizedTopic)
          || normalizedTopic.includes(normalizedExample));
    }),
  );
  return matched?.pattern;
}

function normalizePatternMatchText(value: string): string {
  return value.replace(/\s+/g, '').toLowerCase();
}

function levelToScore(level: string): number {
  const normalized = level.trim().toUpperCase();
  if (['ADVANCED', '熟练', '高级'].includes(normalized)) {
    return 88;
  }
  if (['INTERMEDIATE', '中级', '进阶'].includes(normalized)) {
    return 74;
  }
  if (['BASIC', '基础'].includes(normalized)) {
    return 58;
  }
  if (['BEGINNER', '入门', '初学'].includes(normalized)) {
    return 42;
  }
  return 50;
}

function normalizeConfidenceScore(value: number | undefined): number {
  if (value === undefined) {
    return 65;
  }
  if (value <= 1) {
    return normalizeToPercent(value * 100);
  }
  return normalizeToPercent(value);
}

function normalizeSeverityScore(value: number | undefined): number {
  if (value === undefined) {
    return 70;
  }
  if (value <= 1) {
    return normalizeToPercent(value * 100);
  }
  return normalizeToPercent(value);
}

function normalizeToPercent(value: number): number {
  return Math.max(0, Math.min(100, Math.round(value)));
}

function localizeKnowledgeFoundation(value: string): string {
  const normalized = value.trim().toUpperCase();
  switch (normalized) {
    case 'ADVANCED':
      return '熟练';
    case 'INTERMEDIATE':
      return '进阶';
    case 'BASIC':
      return '基础';
    case 'BEGINNER':
      return '入门';
    case 'UNKNOWN':
      return '待分析';
    case '':
      return '';
    default:
      return value.trim();
  }
}

function localizeConfidenceLevel(value: string): string {
  const normalized = value.trim().toUpperCase();
  switch (normalized) {
    case 'HIGH':
      return '高';
    case 'MEDIUM':
      return '中';
    case 'LOW':
      return '低';
    case 'UNKNOWN':
    case '':
      return '';
    default:
      return value.trim();
  }
}

function localizeCognitiveStyle(value: string): string {
  const normalized = value.trim().toLowerCase();
  switch (normalized) {
    case 'reasoning_oriented':
      return '偏原理推导';
    case 'procedural_oriented':
      return '偏步骤实操';
    case 'mixed':
      return '混合型';
    case '':
    case 'unknown':
      return '';
    default:
      return value.trim();
  }
}

function localizeStudyFrequency(value: string): string {
  const normalized = value.trim().toLowerCase();
  switch (normalized) {
    case 'high_frequency':
      return '高频学习';
    case 'stage_based':
      return '阶段性学习';
    default:
      return value.trim();
  }
}

function localizeLearningPace(value: string): string {
  const normalized = value.trim().toLowerCase();
  switch (normalized) {
    case 'steady':
    case 'stable':
      return '稳步推进';
    case 'fast':
      return '节奏偏快';
    case 'slow':
      return '需要放慢巩固';
    case 'normal':
      return '正常节奏';
    case '':
    case 'unknown':
      return '';
    default:
      return value.trim();
  }
}

function localizeExplanationPreference(value: string): string {
  const normalized = value.trim().toLowerCase();
  switch (normalized) {
    case 'step_by_step':
      return '循序渐进';
    case 'concept_first':
      return '先讲原理';
    case 'concept_then_question':
      return '先概念后练习';
    case 'example_first':
      return '先例子后原理';
    case 'visual_first':
      return '先图示后讲解';
    default:
      return value.trim();
  }
}

function localizeErrorPattern(value: string): string {
  const normalized = value.trim().toLowerCase();
  switch (normalized) {
    case 'concept_confusion':
    case 'concept_misunderstanding':
      return '概念混淆';
    case 'condition_missing':
    case 'boundary_missing':
      return '条件遗漏';
    case 'unstable_knowledge':
    case 'knowledge_unstable':
      return '知识点掌握不稳';
    default:
      return localizeNarrativeText(value);
  }
}

function localizeResourceTypeLabel(value: string): string {
  const normalized = value.trim().toUpperCase();
  switch (normalized) {
    case 'DOCUMENT':
    case 'EXPLANATION':
      return '讲解文档';
    case 'READING':
      return '拓展阅读';
    case 'MINDMAP':
      return '思维导图';
    case 'CODE':
    case 'CODE_CASE':
      return '代码案例';
    case 'QUIZ':
      return '练习题';
    case 'VIDEO':
      return '数字人视频';
    case 'STEP_BY_STEP':
      return '循序渐进';
    case 'CONCEPT_THEN_QUESTION':
      return '先概念后练习';
    case 'EXAMPLE_FIRST':
      return '先例子后原理';
    case 'VISUAL_FIRST':
      return '先图示后讲解';
    case 'UNKNOWN':
    case '':
      return '';
    default:
      return value.trim();
  }
}

function localizeNarrativeText(value: string): string {
  if (!value.trim()) {
    return '';
  }
  const replacements: Array<[RegExp, string]> = [
    [/\bBEGINNER\b/g, '入门'],
    [/\bBASIC\b/g, '基础'],
    [/\bINTERMEDIATE\b/g, '进阶'],
    [/\bADVANCED\b/g, '熟练'],
    [/\bUNKNOWN\b/g, '待分析'],
    [/\bHIGH\b/g, '高'],
    [/\bMEDIUM\b/g, '中'],
    [/\bLOW\b/g, '低'],
    [/\bmixed\b/g, '混合型'],
    [/\breasoning_oriented\b/g, '偏原理推导'],
    [/\bprocedural_oriented\b/g, '偏步骤实操'],
    [/\bconcept_first\b/g, '先讲原理'],
    [/\bconcept_then_question\b/g, '先概念后练习'],
    [/\bstep_by_step\b/g, '循序渐进'],
    [/\bexample_first\b/g, '先例子后原理'],
    [/\bvisual_first\b/g, '先图示后讲解'],
    [/\bDOCUMENT\b/g, '讲解文档'],
    [/\bREADING\b/g, '拓展阅读'],
    [/\bMINDMAP\b/g, '思维导图'],
    [/\bCODE_CASE\b/g, '代码案例'],
    [/\bCODE\b/g, '代码案例'],
    [/\bQUIZ\b/g, '练习题'],
    [/\bVIDEO\b/g, '数字人视频'],
  ];
  let result = value.trim();
  replacements.forEach(([pattern, replacement]) => {
    result = result.replace(pattern, replacement);
  });
  result = result
    .replace(/\u7f6e\u4fe1\u5206/g, '画像可靠度')
    .replace(/(知识基础|当前知识基础为)\s*待分析/g, '$1尚待分析')
    .replace(/(置信度|画像可靠度)\s*=\s*待分析/g, '$1尚待分析')
    .replace(/\s{2,}/g, ' ')
    .trim();
  return result;
}

export function buildServiceParams(service: EngineService, payload: ServiceFormsPayload): Record<string, unknown> {
  if (service === 'resource') {
    const resourceForm = payload.resourceForm;
    const selectedResourceTypes = resolveSelectedResourceTypes(resourceForm.resourceTypes, resourceForm.resourceType);
    const normalizedResourceTypes = uniqueResourceTypes(selectedResourceTypes.map(normalizeResourceType));
    const includeVideo = normalizedResourceTypes.includes('VIDEO');
    const resourceTypeLabelText = selectedResourceTypes.map(resourceTypeLabel).join('、');
    const difficultyLabel = resourceDifficultyLabel(resourceForm.difficulty);
    const query = [
      resourceForm.course,
      resourceForm.keyPoints,
      difficultyLabel,
      resourceTypeLabelText,
    ]
      .map((item) => item?.trim())
      .filter(Boolean)
      .join(' ');
    return {
      resourceType: normalizedResourceTypes[0],
      resourceTypes: normalizedResourceTypes,
      course: resourceForm.course,
      difficulty: resourceForm.difficulty,
      keyPoints: resourceForm.keyPoints,
      query,
      topic: resourceForm.keyPoints || resourceForm.course,
      learningContext: {
        course: resourceForm.course,
        chapter: resourceForm.keyPoints,
      },
      style: includeVideo ? 'talking_head' : undefined,
      duration: includeVideo ? 60 : undefined,
    };
  }

  if (service === 'path') {
    return {
      targetPeriod: payload.pathForm.targetPeriod,
      weeklyHours: payload.pathForm.weeklyHours,
      currentProgress: payload.pathForm.currentProgress,
    };
  }

  if (service === 'personalized') {
    return {
      query: '生成我的个性化学习路径规划和资源推送方案',
      topic: '个性化学习方案',
      autoPersonalized: true,
      contextSources: [
        'learner_profile',
        'learning_progress',
        'knowledge_mastery_graph',
        'practice_and_test_results',
        'mistake_review_records',
        'resource_usage_feedback',
      ],
      requestedOutputs: [
        'learning_effect_evaluation',
        'dynamic_learning_path',
        'resource_push_strategy',
        'plan_adjustment_hints',
      ],
    };
  }

  if (service === 'push') {
    const preferredTypeLabelMap: Record<string, string> = {
      CODE_CASE: '代码案例',
      EXPLANATION: '讲解文档',
      PRACTICAL_CASE: '实操案例',
      READING: '拓展阅读',
      VIDEO: '视频',
    };
    const preferredType = payload.pushForm.preferredType;
    const composedQuery = `基于学习上下文自动推送${preferredTypeLabelMap[preferredType] ?? preferredType}`;
    return {
      resourceType: preferredType,
      query: composedQuery,
      topic: composedQuery,
    };
  }

  return {};
}

function scheduleStreamFlush(
  streamQueueRef: RunByApiTaskArgs['streamQueueRef'],
  streamFlushTimerRef: RunByApiTaskArgs['streamFlushTimerRef'],
  streamRafRef: RunByApiTaskArgs['streamRafRef'],
  setServiceResultLines: RunByApiTaskArgs['setServiceResultLines'],
): void {
  if (streamFlushTimerRef.current != null) {
    return;
  }

  streamFlushTimerRef.current = window.setTimeout(() => {
    streamFlushTimerRef.current = null;
    if (streamRafRef.current != null) {
      return;
    }
    streamRafRef.current = window.requestAnimationFrame(() => {
      streamRafRef.current = null;
      flushStreamQueue(streamQueueRef, streamFlushTimerRef, streamRafRef, setServiceResultLines);
    });
  }, 60);
}

function flushStreamQueue(
  streamQueueRef: RunByApiTaskArgs['streamQueueRef'],
  streamFlushTimerRef: RunByApiTaskArgs['streamFlushTimerRef'],
  streamRafRef: RunByApiTaskArgs['streamRafRef'],
  setServiceResultLines: RunByApiTaskArgs['setServiceResultLines'],
): void {
  cleanupStreamSchedulers(streamFlushTimerRef, streamRafRef);
  if (streamQueueRef.current.length === 0) {
    return;
  }
  const chunks = [...streamQueueRef.current];
  streamQueueRef.current = [];
  setServiceResultLines((prev) => [...prev, ...chunks]);
}

function cleanupStreamSchedulers(
  streamFlushTimerRef: RunByApiTaskArgs['streamFlushTimerRef'],
  streamRafRef: RunByApiTaskArgs['streamRafRef'],
): void {
  if (streamFlushTimerRef.current != null) {
    window.clearTimeout(streamFlushTimerRef.current);
    streamFlushTimerRef.current = null;
  }
  if (streamRafRef.current != null) {
    window.cancelAnimationFrame(streamRafRef.current);
    streamRafRef.current = null;
  }
}

function readSummary(payload: Record<string, unknown> | undefined): string {
  if (!payload) {
    return '';
  }

  const nestedLearningPath = readRecord(payload.learningPath);
  if (nestedLearningPath) {
    const learningPathMarkdown = formatLearningPathMarkdown(nestedLearningPath);
    if (learningPathMarkdown) {
      return learningPathMarkdown;
    }
  }

  return formatUserFacingTaskMessage(
    readString(payload.summaryText) || readString(payload.summary) || readString(payload.message) || readString(payload.text) || '',
  );
}

export function formatUserFacingTaskMessage(value: string): string {
  const raw = value.trim();
  if (!raw) {
    return '';
  }
  if (looksLikeInternalTaskDetail(raw)) {
    return '';
  }
  if (/ReadTimeout/i.test(raw) || /read operation timed out/i.test(raw)) {
    if (/TTS|语音|audio|speech/i.test(raw)) {
      return '视频语音生成超时，请稍后重试或先生成非视频资源。';
    }
    return '智能生成服务响应超时，请稍后重试。';
  }
  if (/Resource bundle generation failed/i.test(raw)) {
    if (/VIDEO|video_generator|Video TTS/i.test(raw)) {
      return '视频资源生成失败：语音合成服务响应超时，请稍后重试或先生成讲解文档、练习题等非视频资源。';
    }
    if (/QUIZ|practice/i.test(raw)) {
      return '练习题生成失败：智能生成结果不完整，请稍后重试。';
    }
    return '资源生成失败，请稍后重试。';
  }
  if (/RuntimeError|Traceback|ValidationError|traceId|taskId|resourceType|agentName/i.test(raw)) {
    return raw
      .replace(/RuntimeError:\s*/gi, '')
      .replace(/ValidationError:\s*/gi, '')
      .replace(/\{[^{}]*(traceId|taskId)[^{}]*\}/gi, '')
      .replace(/\[\{[^{}]*(resourceType|agentName)[^{}]*\}\]/gi, '')
      .replace(/\s{2,}/g, ' ')
      .trim() || '任务执行失败，请稍后重试。';
  }
  return raw;
}

function toVideoGenerationErrorMessage(error: unknown): string {
  const userFacingMessage = formatUserFacingTaskMessage(getErrorMessage(error));
  if (!userFacingMessage) {
    return '请重新生成视频后再试。';
  }
  if (/MediaRecorder|iframe|DH Live|浏览器本地渲染|浏览器渲染|本地渲染/i.test(userFacingMessage)) {
    return '请重新生成视频后再试。';
  }
  return userFacingMessage;
}

function looksLikeInternalTaskDetail(value: string): boolean {
  const normalized = value.trim();
  return normalized.startsWith('原始查询：')
    || normalized.startsWith('检索查询：')
    || normalized.includes('来源摘要:')
    || normalized.includes('phrase:')
    || normalized.includes('改写后:')
    || normalized.includes('关键词:');
}

function shouldSuppressTaskPayload(payload: Record<string, unknown> | undefined): boolean {
  if (!payload) {
    return false;
  }
  const stage = readString(payload.stage);
  return stage === 'query_rewrite'
    || stage === 'retrieving'
    || Boolean(payload.traceId)
    || Boolean(payload.taskId);
}

function formatLearningPathMarkdown(learningPath: Record<string, unknown>): string {
  const goal = readString(learningPath.goal);
  const duration = readString(learningPath.duration);
  const milestones = readStringArray(learningPath.milestones);
  const rawSteps = Array.isArray(learningPath.steps) ? learningPath.steps : [];
  const sections: string[] = [];

  if (goal || duration) {
    sections.push(
      [
        '## 学习路径总览',
        goal ? `- 学习目标：${goal}` : '',
        duration ? `- 规划周期：${duration}` : '',
      ].filter(Boolean).join('\n'),
    );
  }

  if (milestones.length > 0) {
    sections.push(
      [
        '## 阶段里程碑',
        ...milestones.map((milestone, index) => `${index + 1}. ${milestone}`),
      ].join('\n'),
    );
  }

  rawSteps.forEach((step, index) => {
    const record = readRecord(step);
    if (!record) {
      return;
    }
    const title = readString(record.title) || `阶段 ${index + 1}`;
    const objective = readString(record.objective);
    const activities = readStringArray(record.activities);
    const successCriteria = readString(record.successCriteria);
    sections.push(
      [
        `## ${title}`,
        objective ? `### 核心目标\n- ${objective}` : '',
        activities.length > 0
          ? ['### 必做内容', ...activities.map((activity) => `- ${activity}`)].join('\n')
          : '',
        successCriteria ? `### 完成标准\n- ${successCriteria}` : '',
      ].filter(Boolean).join('\n\n'),
    );
  });

  const summaryText = readString(learningPath.summaryText);
  if (summaryText) {
    sections.push(`## 路径说明\n${summaryText}`);
  }

  return sections.filter(Boolean).join('\n\n');
}

function formatLearningPathLines(learningPath: Record<string, unknown>): string[] {
  const lines: string[] = [];
  const goal = readString(learningPath.goal);
  const duration = readString(learningPath.duration);
  const milestones = readStringArray(learningPath.milestones);
  const rawSteps = Array.isArray(learningPath.steps) ? learningPath.steps : [];

  if (goal) {
    lines.push(`学习目标：${goal}`);
  }
  if (duration) {
    lines.push(`规划周期：${duration}`);
  }
  if (milestones.length > 0) {
    lines.push(`阶段里程碑：${milestones.join(' → ')}`);
  }

  rawSteps.forEach((step, index) => {
    const record = readRecord(step);
    if (!record) {
      return;
    }
    const title = readString(record.title) || `阶段 ${index + 1}`;
    const objective = readString(record.objective);
    const activities = readStringArray(record.activities);
    const successCriteria = readString(record.successCriteria);

    lines.push(`${index + 1}. ${title}${objective ? `：${objective}` : ''}`);
    if (activities.length > 0) {
      lines.push(`   关键活动：${activities.join('；')}`);
    }
    if (successCriteria) {
      lines.push(`   完成标准：${successCriteria}`);
    }
  });

  return lines;
}

function readStatusHint(payload: Record<string, unknown> | undefined): string | undefined {
  if (!payload) {
    return undefined;
  }
  return readString(payload.message) || readString(payload.stage) || readString(payload.status) || undefined;
}

function formatExpiresHint(payload: Record<string, unknown> | undefined): string {
  if (!payload) {
    return '下载链接已生成';
  }

  const expiresInSec = readNumeric(payload.expiresInSec);
  if (expiresInSec !== undefined) {
    return `${expiresInSec} 秒后过期`;
  }

  const expiresAt = readString(payload.expiresAt);
  if (expiresAt) {
    return `到期时间 ${new Date(expiresAt).toLocaleString('zh-CN')}`;
  }

  return '下载链接已生成';
}

function labelForSummaryKey(key: string, service: EngineService): string {
  const commonLabels: Record<string, string> = {
    goal: '目标',
    steps: '步骤',
    weakKnowledgeTags: '薄弱知识点',
    recommendations: '建议',
    score: '得分',
    accuracy: '正确率',
    currentStage: '阶段',
    duration: '视频时长',
    videoUrl: '视频地址',
    thumbnailUrl: '缩略图',
    style: '视频风格',
    topic: '知识点',
  };

  if (commonLabels[key]) {
    return commonLabels[key];
  }

  if ((service === 'path' || service === 'personalized') && key === 'plan') {
    return '学习路径';
  }
  if ((service === 'push' || service === 'personalized') && key === 'candidates') {
    return '候选资源';
  }
  if (service === 'resource' && key === 'segments') {
    return '视频脚本分段';
  }

  return key;
}

function resourceDifficultyLabel(difficulty: string): string {
  switch (difficulty) {
    case 'basic':
      return '基础';
    case 'intermediate':
      return '中等';
    case 'advanced':
      return '进阶';
    default:
      return difficulty.trim();
  }
}

function resolveSelectedResourceTypes(resourceTypes: ResourceType[] | undefined, fallback: ResourceType): ResourceType[] {
  return resourceTypes?.length ? resourceTypes : [fallback];
}

function uniqueResourceTypes(resourceTypes: string[]): string[] {
  return resourceTypes.filter((item, index) => item && resourceTypes.indexOf(item) === index);
}

function resourceTypeLabel(resourceType: string): string {
  switch (resourceType) {
    case 'EXPLANATION':
      return '讲解文档';
    case 'SLIDES':
      return '演示课件';
    case 'CODE_CASE':
      return '代码案例';
    case 'QUIZ':
      return '练习题';
    case 'MINDMAP':
      return '思维导图';
    case 'READING':
      return '拓展阅读';
    case 'VIDEO':
      return '教学视频';
    default:
      return resourceType.trim();
  }
}

function normalizeResourceType(resourceType: string): string {
  switch (resourceType) {
    case 'EXPLANATION':
      return 'DOCUMENT';
    case 'CODE_CASE':
      return 'CODE';
    case 'QUIZ':
      return 'QUIZ';
    default:
      return resourceType;
  }
}

function isVideoProgressEvent(eventType: SmartEngineStreamEvent['event']): boolean {
  return eventType.startsWith('video_gen:');
}

function isResourceStoreEvent(eventType: SmartEngineStreamEvent['event']): boolean {
  return eventType === 'progress'
    || eventType === 'resource_file'
    || eventType === 'question_batch'
    || eventType === 'done'
    || eventType === 'error'
    || eventType.startsWith('video_gen:');
}

function mapVideoProgressEvent(eventType: SmartEngineStreamEvent['event']): { progress: number; status: string; message: string } {
  switch (eventType) {
    case 'video_gen:start':
      return { progress: 10, status: '视频任务启动', message: '视频生成任务已启动' };
    case 'video_gen:script':
      return { progress: 25, status: '脚本生成完成', message: '脚本生成完成' };
    case 'video_gen:speech':
      return { progress: 50, status: '语音合成完成', message: '语音合成完成' };
    case 'video_gen:avatar':
      return { progress: 75, status: '视频生成中', message: '视频生成中...' };
    case 'video_gen:complete':
      return { progress: 100, status: '视频素材已就绪', message: '视频素材已就绪' };
    default:
      return { progress: 0, status: '执行中', message: '' };
  }
}

function readDuration(payload: Record<string, unknown> | undefined): number | undefined {
  if (!payload) {
    return undefined;
  }
  const duration = readNumericRaw(payload.duration) ?? readNumericRaw(payload.durationSeconds) ?? readNumericRaw(payload.totalDuration);
  if (duration === undefined) {
    return undefined;
  }
  return Math.max(0, duration);
}

function readVideoStyle(payload: Record<string, unknown> | undefined): VideoCardStyle | undefined {
  if (!payload) {
    return undefined;
  }
  const style = readString(payload.style) || readString(payload.videoStyle);
  if (style === 'talking_head' || style === 'animation' || style === 'hybrid') {
    return style;
  }
  return undefined;
}

function readUrlField(payload: Record<string, unknown> | undefined, keys: string[]): string {
  if (!payload) {
    return '';
  }
  for (const key of keys) {
    const value = readString(payload[key]);
    if (value) {
      return value;
    }
  }
  return '';
}

function requiresLlmProvenance(payload: Record<string, unknown> | undefined): boolean {
  if (!payload) {
    return false;
  }
  const displayMode = readString(payload.displayMode).toLowerCase();
  const sourceName = readString(payload.sourceName).toLowerCase();
  if (displayMode === 'external_link') {
    return false;
  }
  return !sourceName || sourceName === 'generated';
}

function ensureGeneratedPayloadProvenance(
  payload: Record<string, unknown> | undefined,
  handlers: TaskRunHandlers,
  label: string,
): boolean {
  if (!requiresLlmProvenance(payload)) {
    return true;
  }
  if (hasRealLlmProvenance(payload)) {
    return true;
  }
  handlers.onLine(`${label}生成失败：缺少有效的智能生成校验信息`);
  return false;
}

function ensureQuestionBatchProvenance(
  payload: Record<string, unknown> | undefined,
  handlers: TaskRunHandlers,
): boolean {
  if (hasRealLlmProvenance(payload)) {
    return true;
  }
  handlers.onLine('练习题生成失败：缺少有效的智能生成校验信息');
  return false;
}

function hasRealLlmProvenance(payload: Record<string, unknown> | undefined): boolean {
  if (!payload) {
    return false;
  }
  const evidenceIds = payload.evidenceIds;
  return readString(payload.generatedBy).toUpperCase() === 'LLM'
    && readString(payload.contentOrigin).toUpperCase() === 'LLM'
    && Boolean(readString(payload.provider))
    && Boolean(readString(payload.model))
    && Boolean(readString(payload.agentName))
    && Array.isArray(evidenceIds)
    && payload.fallback === false
    && typeof payload.fromCache === 'boolean';
}

function isVideoLink(item: TempDownloadLink): boolean {
  if (item.resourceType !== 'VIDEO') {
    return false;
  }
  const mimeType = (item.mimeType || '').toLowerCase();
  const fileName = (item.fileName || '').toLowerCase();
  const url = item.url.toLowerCase();
  if (mimeType.startsWith('video/')) {
    return true;
  }
  return ['.mp4', '.webm', '.mov', '.m4v', '.m3u8'].some((ext) => fileName.endsWith(ext) || url.includes(ext));
}

function mapDownloadToVideoResult(item: TempDownloadLink): VideoResult {
  return {
    title: item.title,
    videoUrl: item.url,
    thumbnailUrl: item.thumbnailUrl,
    duration: item.duration,
    style: item.style,
    knowledgePoint: item.knowledgePoint,
    expiresHint: item.expiresHint,
    fileName: item.fileName,
  };
}

function isSafeRecommendationContent(title: string, summary: string, sourceName: string, url: string): boolean {
  const combined = `${title} ${summary} ${sourceName} ${url}`.toLowerCase();
  const blockedTokens = [
    'china-dictatorship',
    'anti chinese',
    'anti-china',
    'anti china',
    'anti ccp',
    '反共',
    '反华',
    '政治宣传',
    '宣传库',
    'propaganda',
    'dictatorship',
    'falun',
    'falun gong',
    '法轮功',
    '六四',
    '天安门',
    '疆独',
    '港独',
    '台独',
    '邪教',
    '习近平',
    'xijinping',
    'ccp',
    '共产党',
  ];
  return !blockedTokens.some((token) => combined.includes(token));
}

function truncateRecommendationText(value: string, limit: number): string {
  const normalized = value.trim();
  if (normalized.length <= limit) {
    return normalized;
  }
  return normalized.slice(0, limit);
}

function readVideoResult(payload: Record<string, unknown> | undefined): VideoResult | null {
  if (!payload) {
    return null;
  }
  const assetType = readString(payload.assetType);
  if (assetType && assetType !== 'VIDEO') {
    return null;
  }
  const videoUrl =
    readUrlField(payload, ['videoUrl', 'finalVideoUrl', 'final_video_url', 'downloadUrl', 'resourceUrl']) ||
    readNestedVideoUrl(payload.result);
  if (!videoUrl) {
    return null;
  }
  const mimeType = readString(payload.mimeType).toLowerCase();
  const fileName = readString(payload.fileName).toLowerCase();
  const normalizedUrl = videoUrl.toLowerCase();
  const isVideoUrl = mimeType.startsWith('video/')
    || ['.mp4', '.webm', '.mov', '.m4v', '.m3u8'].some((ext) => fileName.endsWith(ext) || normalizedUrl.includes(ext));
  if (!isVideoUrl) {
    return null;
  }

  return {
    title: readString(payload.title) || readString(payload.topic) || '教学视频',
    videoUrl,
    thumbnailUrl: readUrlField(payload, ['thumbnailUrl', 'thumbnail_url', 'posterUrl', 'coverUrl']),
    duration: readDuration(payload),
    style: readVideoStyle(payload),
    knowledgePoint: readString(payload.knowledgePoint) || readString(payload.topic),
    expiresHint: formatExpiresHint(payload),
    renderStatus: 'ready',
  };
}

function mergeVideoResult(previous: VideoResult | null, next: VideoResult): VideoResult {
  if (!previous) {
    return next;
  }
  if (next.videoUrl || next.renderStatus === 'ready' || next.renderStatus === 'failed') {
    return {
      ...previous,
      ...next,
    };
  }
  return {
    ...next,
    videoUrl: previous.videoUrl || next.videoUrl,
    thumbnailUrl: previous.thumbnailUrl || next.thumbnailUrl,
    renderStatus: previous.renderStatus === 'ready' ? previous.renderStatus : next.renderStatus,
    renderMessage: next.renderMessage || previous.renderMessage,
  };
}

function readBrowserRenderedVideoResult(
  payload: Record<string, unknown> | undefined,
  renderMessage: string,
): VideoResult | null {
  if (!payload) {
    return null;
  }
  const assetType = readString(payload.assetType).toUpperCase();
  const displayMode = readString(payload.displayMode).toUpperCase();
  const mimeType = readString(payload.mimeType).toLowerCase();
  const fileName = readString(payload.fileName).toLowerCase();
  const hasAudio = Boolean(readString(payload.audioBase64));
  const isBrowserRenderedVideo =
    hasAudio
    || displayMode === 'VIDEO_PLAYER'
    || fileName === 'browser-rendered.webm'
    || (assetType === 'VIDEO' && mimeType.startsWith('video/'));
  if (!isBrowserRenderedVideo) {
    return null;
  }
  return {
    title: readString(payload.title) || readString(payload.topic) || '教学视频',
    videoUrl: '',
    thumbnailUrl: readUrlField(payload, ['thumbnailUrl', 'thumbnail_url', 'posterUrl', 'coverUrl']),
    duration: readDuration(payload),
    style: readVideoStyle(payload),
    knowledgePoint: readString(payload.knowledgePoint) || readString(payload.topic),
    expiresHint: '视频正在生成',
    fileName: readString(payload.fileName) || undefined,
    renderStatus: 'rendering',
    renderMessage,
  };
}

function readNestedVideoUrl(value: unknown): string {
  if (!value || typeof value !== 'object') {
    return '';
  }
  const payload = value as Record<string, unknown>;
  return readUrlField(payload, ['videoUrl', 'finalVideoUrl', 'final_video_url', 'downloadUrl', 'resourceUrl']);
}

function readInlineResource(payload: Record<string, unknown> | undefined): InlineResourceView | null {
  if (!payload) {
    return null;
  }
  const displayMode = readString(payload.displayMode).toUpperCase();
  const rawInlineContent = readString(payload.inlineContent);
  const inlineContent = displayMode === 'INLINE_CODE'
    ? rawInlineContent.replace(/\r\n/g, '\n')
    : sanitizeMarkdownContent(rawInlineContent);
  if (!inlineContent) {
    return null;
  }
  const title = readString(payload.title) || '内嵌资源';
  const summary = sanitizeMarkdownContent(readString(payload.summary));
  if (displayMode === 'INLINE_CODE') {
    return {
      kind: 'code',
      title,
      summary,
      content: inlineContent,
      language: readString(payload.language) || 'text',
      explanation: readString(payload.explanation),
    };
  }
  if (displayMode === 'INLINE_MERMAID') {
    return {
      kind: 'mermaid',
      title,
      summary,
      content: inlineContent,
    };
  }
  if (displayMode === 'MARKDOWN_CARD') {
    return {
      kind: 'markdown',
      title,
      summary,
      content: inlineContent,
    };
  }
  return null;
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
    title: readString(source.title) || '练习题',
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
    evidenceIds: Array.isArray(source.evidenceIds) ? source.evidenceIds.map((id) => readString(id)).filter(Boolean) : undefined,
    fallback: typeof source.fallback === 'boolean' ? source.fallback : undefined,
    fromCache: typeof source.fromCache === 'boolean' ? source.fromCache : undefined,
    questions: questions
      .map((item) => readRecord(item))
      .filter((item): item is Record<string, unknown> => Boolean(item))
      .map((item, index) => ({
        questionId: readString(item.questionId) || `question-${index + 1}`,
        questionType: readString(item.questionType) || 'SHORT_ANSWER',
        stem: readString(item.stem),
        options: Array.isArray(item.options) ? item.options.map((option) => readString(option)).filter(Boolean) : undefined,
        answer: readString(item.answer),
        knowledgeTags: Array.isArray(item.knowledgeTags) ? item.knowledgeTags.map((tag) => readString(tag)).filter(Boolean) : undefined,
        difficultyLevel: readString(item.difficultyLevel),
        explanation: readString(item.explanation),
      })),
  };
}

function readPracticeJudgeResult(payload: Record<string, unknown> | undefined): PracticeJudgeResult | null {
  const record = readRecord(payload);
  const source = readRecord(record?.judgeResult) ?? record;
  const items = Array.isArray(source?.items) ? source.items : null;
  if (!source || !items) {
    return null;
  }
  return {
    title: readString(source.title) || '判题结果',
    summary: readString(source.summary),
    totalScore: readNumericRaw(source.totalScore) ?? 0,
    accuracy: readNumericRaw(source.accuracy) ?? 0,
    weakKnowledgeTags: Array.isArray(source.weakKnowledgeTags)
      ? source.weakKnowledgeTags.map((tag) => readString(tag)).filter(Boolean)
      : undefined,
    items: items
      .map((item) => readRecord(item))
      .filter((item): item is Record<string, unknown> => Boolean(item))
      .map((item) => ({
        questionId: readString(item.questionId),
        questionType: readString(item.questionType),
        learnerAnswer: readString(item.learnerAnswer),
        correctAnswer: readString(item.correctAnswer),
        isCorrect: Boolean(item.isCorrect),
        score: readNumericRaw(item.score) ?? 0,
        knowledgeTags: Array.isArray(item.knowledgeTags) ? item.knowledgeTags.map((tag) => readString(tag)).filter(Boolean) : undefined,
        reason: readString(item.reason),
        feedback: readString(item.feedback),
      })),
  };
}

function readLearningPlan(payload: Record<string, unknown> | undefined): LearningPlanView | null {
  const record = readRecord(payload?.learningPlan) ?? readRecord(payload?.learningPath);
  if (!record) {
    return null;
  }
  const rawSteps = Array.isArray(record.steps) ? record.steps : [];
  const steps = rawSteps
    .map((item) => readRecord(item))
    .filter((item): item is Record<string, unknown> => Boolean(item))
    .map((item) => ({
      stepId: readString(item.stepId),
      title: readString(item.title),
      order: readNumericRaw(item.order),
      intent: readString(item.intent) || readString(item.objective) || readString(item.reason) || undefined,
      reason: readString(item.reason) || undefined,
      targetKnowledgePoints: readStringArray(item.targetKnowledgePoints),
      preferredResourceTypes: readStringArray(item.preferredResourceTypes),
      estimatedMinutes: readNumericRaw(item.estimatedMinutes),
      checkpoint: readString(item.checkpoint) || undefined,
      agentName: readString(item.agentName) || undefined,
      serviceType: readString(item.serviceType) || undefined,
      status: readString(item.status) || undefined,
    }))
    .filter((step) => step.stepId || step.title);
  if (!readString(record.planId) && !readString(record.goal) && steps.length === 0) {
    return null;
  }
  return {
    planId: readString(record.planId) || readString(record.goal),
    goal: readString(record.goal),
    status: readString(record.status) || undefined,
    createdBy: readString(record.createdBy) || undefined,
    provider: readString(record.provider) || undefined,
    model: readString(record.model) || undefined,
    steps,
  };
}

function readMasteryDiagnosis(payload: Record<string, unknown> | undefined): MasteryDiagnosisView | null {
  const record = readRecord(payload?.masteryDiagnosis);
  if (!record) {
    return null;
  }
  const rawTargetScope = readRecord(record.targetScope);
  const knowledgeDiagnoses = Array.isArray(record.knowledgeDiagnoses)
    ? record.knowledgeDiagnoses
        .map((item) => readRecord(item))
        .filter((item): item is Record<string, unknown> => Boolean(item))
        .map((item) => ({
          knowledgePoint: readString(item.knowledgePoint),
          masteryScore: readNumericRaw(item.masteryScore),
          status: readString(item.status) || undefined,
          priority: readNumericRaw(item.priority),
          evidence: readStringArray(item.evidence),
          errorPatterns: readStringArray(item.errorPatterns),
          nextFocus: readString(item.nextFocus) || undefined,
          recommendedResourceTypes: readStringArray(item.recommendedResourceTypes),
        }))
        .filter((item) => item.knowledgePoint || item.evidence.length > 0)
    : [];
  const summaryText = readString(record.summaryText);
  const overallLevel = readString(record.overallLevel);
  if (!summaryText && !overallLevel && knowledgeDiagnoses.length === 0) {
    return null;
  }
  return {
    diagnosisSource: readString(record.diagnosisSource) || undefined,
    primaryDimension: readString(record.primaryDimension) || undefined,
    overallLevel: overallLevel || undefined,
    overallMasteryScore: readNumericRaw(record.overallMasteryScore),
    confidence: readNumericRaw(record.confidence),
    targetScope: rawTargetScope
      ? {
          course: readString(rawTargetScope.course) || undefined,
          chapter: readString(rawTargetScope.chapter) || undefined,
          knowledgePoints: readStringArray(rawTargetScope.knowledgePoints),
        }
      : undefined,
    knowledgeDiagnoses,
    behaviorSignals: readBehaviorSignals(record.behaviorSignals),
    planAdjustmentHints: readPlanAdjustmentHints(record.planAdjustmentHints),
    summaryText: summaryText || undefined,
  };
}

function readBehaviorSignals(value: unknown): MasteryDiagnosisView['behaviorSignals'] {
  const record = readRecord(value);
  if (!record) {
    return undefined;
  }
  return {
    practiceAccuracy: readNumericRaw(record.practiceAccuracy),
    recentQuestionCount: readNumericRaw(record.recentQuestionCount),
    reviewCount: readNumericRaw(record.reviewCount),
    resourceDownloads: readNumericRaw(record.resourceDownloads),
    messageCount: readNumericRaw(record.messageCount),
    recentMistakeCount: readNumericRaw(record.recentMistakeCount),
  };
}

function readPlanAdjustmentHints(value: unknown): MasteryDiagnosisView['planAdjustmentHints'] {
  const record = readRecord(value);
  if (!record) {
    return undefined;
  }
  return {
    shouldRefreshPlan: typeof record.shouldRefreshPlan === 'boolean' ? record.shouldRefreshPlan : undefined,
    refreshReason: readString(record.refreshReason) || undefined,
    strategy: readString(record.strategy) || undefined,
  };
}

function readResourcePushPlan(payload: Record<string, unknown> | undefined): ResourcePushPlanView | null {
  const record = readRecord(payload?.resourcePushPlan);
  if (!record) {
    return readPushedResourcesAsPlan(payload?.pushedResources);
  }
  const stepResources = Array.isArray(record.stepResources)
    ? record.stepResources
        .map((item) => readRecord(item))
        .filter((item): item is Record<string, unknown> => Boolean(item))
        .map((item) => ({
          stepId: readString(item.stepId),
          stepTitle: readString(item.stepTitle) || undefined,
          targetKnowledgePoints: readStringArray(item.targetKnowledgePoints),
          resources: Array.isArray(item.resources)
            ? item.resources
                .map((resource) => readRecord(resource))
                .filter((resource): resource is Record<string, unknown> => Boolean(resource))
                .map((resource) => ({
                  title: readString(resource.title),
                  resourceType: readString(resource.resourceType),
                  source: readString(resource.source) || readString(resource.sourceName),
                  sourceName: readString(resource.sourceName) || undefined,
                  matchReason: readString(resource.matchReason) || readString(resource.rerankReason) || undefined,
                  downloadUrl: readString(resource.downloadUrl) || undefined,
                  summaryText: readString(resource.summaryText) || readString(resource.summary) || undefined,
                }))
                .filter((resource) => resource.title || resource.downloadUrl || resource.summaryText)
            : [],
        }))
        .filter((item) => item.stepId || item.stepTitle || item.resources.length > 0)
    : [];
  const coverageGaps = Array.isArray(record.coverageGaps)
    ? record.coverageGaps
        .map((item) => readRecord(item))
        .filter((item): item is Record<string, unknown> => Boolean(item))
        .map((item) => ({
          stepId: readString(item.stepId),
          missingResourceTypes: readStringArray(item.missingResourceTypes),
          reason: readString(item.reason) || undefined,
        }))
        .filter((item) => item.stepId || item.missingResourceTypes.length > 0)
    : [];
  if (stepResources.length === 0 && coverageGaps.length === 0) {
    return readPushedResourcesAsPlan(payload?.pushedResources);
  }
  return {
    stepResources,
    coverageGaps,
  };
}

function readPushedResourcesAsPlan(value: unknown): ResourcePushPlanView | null {
  if (!Array.isArray(value)) {
    return null;
  }
  const resources = value
    .map((item) => readRecord(item))
    .filter((item): item is Record<string, unknown> => Boolean(item))
    .map((item) => ({
      title: readString(item.title),
      resourceType: readString(item.resourceType),
      source: readString(item.source) || readString(item.sourceName),
      sourceName: readString(item.sourceName) || undefined,
      matchReason: readString(item.matchReason) || readString(item.rerankReason) || undefined,
      downloadUrl: readString(item.downloadUrl) || undefined,
      summaryText: readString(item.summaryText) || readString(item.summary) || undefined,
    }))
    .filter((item) => item.title || item.downloadUrl || item.summaryText);
  if (resources.length === 0) {
    return null;
  }
  return {
    stepResources: [
      {
        stepId: 'resources',
        stepTitle: '推荐资源',
        targetKnowledgePoints: [],
        resources,
      },
    ],
    coverageGaps: [],
  };
}

function readCriticReview(payload: Record<string, unknown> | undefined): CriticReviewView | null {
  const record = readRecord(payload?.criticReview);
  if (!record) {
    return null;
  }
  const issues = readStringArray(record.issues);
  const suggestions = readStringArray(record.suggestions);
  const summaryText = readString(record.summaryText);
  const verdict = readString(record.verdict);
  if (!verdict && !summaryText && issues.length === 0 && suggestions.length === 0) {
    return null;
  }
  return {
    verdict: verdict || undefined,
    coverageScore: readNumericRaw(record.coverageScore),
    pathOrderScore: readNumericRaw(record.pathOrderScore),
    resourceMatchScore: readNumericRaw(record.resourceMatchScore),
    factConsistency: readString(record.factConsistency) || undefined,
    difficultyMatch: readString(record.difficultyMatch) || undefined,
    sourceCoverage: readString(record.sourceCoverage) || undefined,
    issues,
    suggestions,
    summaryText: summaryText || undefined,
  };
}

function readAgentTrace(payload: Record<string, unknown> | undefined): AgentTraceStepView[] {
  const rawTrace = payload?.agentTrace;
  if (!Array.isArray(rawTrace)) {
    return [];
  }
  return rawTrace
    .map((item) => readRecord(item))
    .filter((item): item is Record<string, unknown> => Boolean(item))
    .map((item) => ({
      agentName: readString(item.agentName),
      status: readString(item.status),
      stage: readString(item.stage) || undefined,
      message: readString(item.message) || undefined,
    }))
    .filter((item) => item.agentName || item.stage || item.message);
}

function readNumericRaw(value: unknown): number | undefined {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === 'string') {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) {
      return parsed;
    }
  }
  return undefined;
}

function readNumeric(value: unknown): number | undefined {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return Math.max(0, Math.min(100, value));
  }
  if (typeof value === 'string') {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) {
      return Math.max(0, Math.min(100, parsed));
    }
  }
  return undefined;
}

function readString(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function readRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return null;
  }
  return value as Record<string, unknown>;
}

function readStringArray(...values: unknown[]): string[] {
  for (const value of values) {
    if (Array.isArray(value)) {
      return value.map((item) => readString(item)).filter(Boolean);
    }
    if (typeof value === 'string' && value.trim()) {
      return value
        .split(/[、,，]/)
        .map((item) => item.trim())
        .filter(Boolean);
    }
  }
  return [];
}

function stringifyCompact(value: unknown): string {
  if (value === null || value === undefined) {
    return '';
  }
  if (typeof value === 'string') {
    return value;
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

export function toUiTaskStatus(status?: string): string {
  if (!status) {
    return '执行中';
  }
  if (status === 'COMPLETED') {
    return '任务完成';
  }
  if (status === 'FAILED') {
    return '任务失败';
  }
  if (status === 'PENDING' || status === 'ACCEPTED') {
    return '等待受理';
  }
  return '执行中';
}

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });
}
