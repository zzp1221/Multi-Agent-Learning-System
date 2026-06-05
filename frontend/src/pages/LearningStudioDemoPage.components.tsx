import { lazy, Suspense } from 'react';
import { BookOpen, ExternalLink, FileText, Sparkles } from 'lucide-react';
import CodeBlock from '../components/CodeBlock';
import {
  resourceTypeButtons,
  type CompletedResourceView,
  type EngineService,
  type EngineTaskResultRecord,
  type InlineResourceView,
  type LearningPlanView,
  type PathForm,
  type PracticeQuestionBatch,
  type PushForm,
  type ResourceCoverageGapView,
  type ResourceForm,
  type ResourcePushPlanResourceView,
  type ResourcePushPlanStepView,
  type ResourcePushPlanView,
  type ResourceType,
  type TempDownloadLink,
  type VideoResult,
} from './LearningStudioDemoPage.types';
import { request } from '../api/request';

const LazyMarkdownRenderer = lazy(() => import('../components/MarkdownRenderer'));
const LazyMermaidDiagram = lazy(() => import('../components/MermaidDiagram'));
const LazyVideoCard = lazy(() => import('../components/VideoCard'));

function DeferredMarkdownRenderer(props: { content: string; isStreaming?: boolean }) {
  return (
    <Suspense fallback={<span className="text-slate-400 dark:text-slate-500">加载中...</span>}>
      <LazyMarkdownRenderer {...props} />
    </Suspense>
  );
}

function DeferredMermaidDiagram(props: { chart: string }) {
  return (
    <Suspense fallback={<div className="rounded-xl border border-dashed border-slate-200 px-4 py-6 text-sm text-slate-400 dark:border-slate-700 dark:text-slate-500">图表加载中...</div>}>
      <LazyMermaidDiagram {...props} />
    </Suspense>
  );
}

export function ServiceDynamicForm(props: {
  service: EngineService | null;
  resourceForm: ResourceForm;
  resourceErrors?: Partial<Record<'course' | 'keyPoints', string>>;
  pathForm: PathForm;
  pushForm: PushForm;
  onResourceChange: (next: ResourceForm) => void;
  onPathChange: (next: PathForm) => void;
  onPushChange: (next: PushForm) => void;
}) {
  if (!props.service) {
    return (
      <div className="flex flex-col items-center justify-center rounded-2xl border border-dashed border-slate-300 bg-slate-50/50 px-4 py-8 text-center dark:border-slate-700 dark:bg-slate-900/50">
        <div className="mb-2 flex h-10 w-10 items-center justify-center rounded-xl bg-slate-100 dark:bg-slate-800">
          <Sparkles className="h-5 w-5 text-slate-400" />
        </div>
        <p className="text-sm text-slate-500 dark:text-slate-400">请先选择一项服务，再填写参数</p>
      </div>
    );
  }

  const baseInputClass = "w-full rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 text-sm outline-none transition-all duration-200 focus:border-primary-400 focus:ring-2 focus:ring-primary-500/20 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200 dark:focus:border-primary-500";
  const baseSelectClass = "w-full rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 text-sm outline-none transition-all duration-200 focus:border-primary-400 focus:ring-2 focus:ring-primary-500/20 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200 dark:focus:border-primary-500";
  const chipButton = (active: boolean) => `rounded-full border px-3 py-1.5 text-xs font-medium transition-all duration-200 ${
    active
      ? 'border-primary-300 bg-primary-50 text-primary-700 dark:border-primary-700 dark:bg-primary-500/10 dark:text-primary-400'
      : 'border-slate-200 bg-white text-slate-600 hover:border-primary-200 hover:text-primary-600 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-400 dark:hover:border-primary-600'
  }`;

  if (props.service === 'resource') {
    const selectedResourceTypes = resolveSelectedResourceTypes(props.resourceForm);
    const includesVideo = selectedResourceTypes.includes('VIDEO');
    const courseError = props.resourceErrors?.course;
    const keyPointsError = props.resourceErrors?.keyPoints;
    const toggleResourceType = (resourceType: ResourceType) => {
      const active = selectedResourceTypes.includes(resourceType);
      const nextResourceTypes = active
        ? selectedResourceTypes.filter((item) => item !== resourceType)
        : [...selectedResourceTypes, resourceType];
      const safeResourceTypes = nextResourceTypes.length > 0 ? nextResourceTypes : [resourceType];
      props.onResourceChange({
        ...props.resourceForm,
        resourceType: safeResourceTypes[0],
        resourceTypes: safeResourceTypes,
      });
    };

    return (
      <div className="rounded-2xl border border-slate-200 bg-slate-50/50 p-4 dark:border-slate-700 dark:bg-slate-900/50 md:p-5">
        <div className="mb-3 text-sm font-semibold text-slate-700 dark:text-slate-300">资源生成参数</div>
        <div className="mb-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {resourceTypeButtons.map((item) => {
            const active = selectedResourceTypes.includes(item.type);
            return (
              <button
                key={item.type}
                type="button"
                aria-pressed={active}
                onClick={() => toggleResourceType(item.type)}
                className={`${chipButton(active)} w-full justify-center py-2 text-sm`}
              >
                {item.label}
              </button>
            );
          })}
        </div>
        <div className="grid gap-3 md:grid-cols-2">
          <label className="block">
            <span className="mb-1.5 block text-xs font-semibold text-slate-600 dark:text-slate-300">
              课程名称 <span className="text-rose-500">*</span>
            </span>
            <input
              value={props.resourceForm.course}
              onChange={(e) => props.onResourceChange({ ...props.resourceForm, course: e.target.value })}
              placeholder="请输入课程名称"
              aria-invalid={Boolean(courseError)}
              className={`${baseInputClass} ${courseError ? 'border-rose-300 bg-rose-50/40 focus:border-rose-400 focus:ring-rose-500/15 dark:border-rose-500/70 dark:bg-rose-950/20' : ''}`}
            />
            {courseError ? <p className="mt-1.5 text-xs text-rose-500">{courseError}</p> : null}
          </label>
          <select
            value={props.resourceForm.difficulty}
            onChange={(e) =>
              props.onResourceChange({
                ...props.resourceForm,
                difficulty: e.target.value as ResourceForm['difficulty'],
              })
            }
            className={baseSelectClass}
          >
            <option value="basic">基础</option>
            <option value="intermediate">中等</option>
            <option value="advanced">进阶</option>
          </select>
        </div>
        <label className="mt-3 block">
          <span className="mb-1.5 block text-xs font-semibold text-slate-600 dark:text-slate-300">
            重点知识点 <span className="text-rose-500">*</span>
          </span>
          <textarea
            value={props.resourceForm.keyPoints}
            onChange={(e) => props.onResourceChange({ ...props.resourceForm, keyPoints: e.target.value })}
            rows={2}
            placeholder="请输入重点知识点，例如：Spring Bean 生命周期、依赖注入"
            aria-invalid={Boolean(keyPointsError)}
            className={`${baseInputClass} ${keyPointsError ? 'border-rose-300 bg-rose-50/40 focus:border-rose-400 focus:ring-rose-500/15 dark:border-rose-500/70 dark:bg-rose-950/20' : ''}`}
          />
          {keyPointsError ? <p className="mt-1.5 text-xs text-rose-500">{keyPointsError}</p> : null}
        </label>
        {includesVideo ? (
          <div className="mt-4 rounded-2xl border border-primary-200 bg-primary-50/70 px-4 py-3 text-sm text-primary-700 dark:border-primary-700 dark:bg-primary-500/10 dark:text-primary-200">
            已固定为数字人视频生成，系统将按默认时长自动完成脚本、TTS，并在当前浏览器本地渲染视频。
          </div>
        ) : null}
      </div>
    );
  }

  if (props.service === 'personalized' || props.service === 'path' || props.service === 'push') {
    return (
      <div className="rounded-2xl border border-slate-200 bg-slate-50/50 p-4 dark:border-slate-700 dark:bg-slate-900/50 md:p-5">
        <div className="mb-3 text-sm font-semibold text-slate-700 dark:text-slate-300">自动分析范围</div>
        <div className="grid gap-3 text-sm leading-6 text-slate-600 dark:text-slate-400">
          <div className="rounded-xl border border-blue-100 bg-white/80 p-3 dark:border-slate-800 dark:bg-slate-950/40">
            系统将读取学习画像、学习进度、知识掌握图谱、练习测试记录、错题复习和资源使用反馈。
          </div>
          <div className="rounded-xl border border-blue-100 bg-white/80 p-3 dark:border-slate-800 dark:bg-slate-950/40">
            提交后自动完成学习状态分析、路径规划和资源推送策略调整，不需要手动填写课程、周期、进度或资源偏好。
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="rounded-2xl border border-slate-200 bg-slate-50/50 p-4 dark:border-slate-700 dark:bg-slate-900/50 md:p-5">
      <div className="text-sm leading-6 text-slate-600 dark:text-slate-400">请选择服务后继续。</div>
    </div>
  );
}

function resolveSelectedResourceTypes(resourceForm: ResourceForm): ResourceType[] {
  return resourceForm.resourceTypes?.length ? resourceForm.resourceTypes : [resourceForm.resourceType];
}

function InlineResourcePanel(props: { resource: InlineResourceView }) {
  if (props.resource.kind === 'code') {
    return (
      <div className="mb-4 rounded-xl border border-slate-100 bg-slate-50/50 p-4 dark:border-slate-800 dark:bg-slate-900/50">
        <div className="mb-3">
          <div className="text-sm font-semibold text-slate-700 dark:text-slate-300">{props.resource.title}</div>
          {props.resource.summary ? (
            <div className="mt-1 text-xs text-slate-500 dark:text-slate-400">{props.resource.summary}</div>
          ) : null}
        </div>
        <CodeBlock language={props.resource.language || 'text'}>{props.resource.content}</CodeBlock>
        {props.resource.explanation ? (
          <div className="mt-4 rounded-xl border border-primary-100 bg-white p-4 dark:border-primary-900 dark:bg-slate-950">
            <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-primary-600 dark:text-primary-400">讲解</div>
            <DeferredMarkdownRenderer content={props.resource.explanation} />
          </div>
        ) : null}
      </div>
    );
  }

  if (props.resource.kind === 'mermaid') {
    return (
      <div className="mb-4 rounded-xl border border-slate-100 bg-slate-50/50 p-4 dark:border-slate-800 dark:bg-slate-900/50">
        <div className="mb-3">
          <div className="text-sm font-semibold text-slate-700 dark:text-slate-300">{props.resource.title}</div>
          {props.resource.summary ? (
            <div className="mt-1 text-xs text-slate-500 dark:text-slate-400">{props.resource.summary}</div>
          ) : null}
        </div>
        <DeferredMermaidDiagram chart={props.resource.content} />
      </div>
    );
  }

  return (
    <div className="mb-4 rounded-xl border border-slate-100 bg-slate-50/50 p-4 text-sm leading-7 text-slate-700 dark:border-slate-800 dark:bg-slate-900/50 dark:text-slate-300">
      <DeferredMarkdownRenderer content={props.resource.content} />
    </div>
  );
}

function sanitizeResourceDisplayText(value: string): string {
  if (!value.trim()) {
    return '';
  }
  const cleanedLines: string[] = [];
  let skippingSourceBlock = false;
  for (const rawLine of value.split(/\r?\n/)) {
    const line = rawLine.trimEnd();
    const compact = line.trim();
    if (/^#{1,6}\s*(参考来源|引用依据)\s*$/.test(compact) || /^参考来源[:：]?\s*$/.test(compact)) {
      skippingSourceBlock = true;
      continue;
    }
    if (skippingSourceBlock) {
      if (/^#{1,6}\s+/.test(compact) && !/^#{1,6}\s*(参考来源|引用依据)\s*$/.test(compact)) {
        skippingSourceBlock = false;
      } else {
        continue;
      }
    }
    if (/^[-*]\s*(课程|章节|学生水平|学习风格)[:：]/.test(compact)) {
      continue;
    }
    if (/^证据说明[:：]/.test(compact) || /^\[?来源\d+\]?/.test(compact) || /^[-*]\s*\[?来源\d+\]?/.test(compact)) {
      continue;
    }
    cleanedLines.push(
      line
        .replace(/真实\s*LLM\s*产物[:：]?/gi, '资源')
        .replace(/真实LLM产物[:：]?/gi, '资源'),
    );
  }
  return cleanedLines.join('\n').replace(/\n{3,}/g, '\n\n').trim();
}

function sanitizeResourceInlineResource(resource: InlineResourceView): InlineResourceView {
  return {
    ...resource,
    summary: resource.summary ? sanitizeResourceDisplayText(resource.summary) : resource.summary,
    content: sanitizeResourceDisplayText(resource.content),
    explanation: resource.explanation ? sanitizeResourceDisplayText(resource.explanation) : resource.explanation,
  };
}

function sanitizeResourceCompletedItem(item: CompletedResourceView): CompletedResourceView {
  if (item.kind !== 'inline') {
    return item;
  }
  return {
    ...item,
    resource: sanitizeResourceInlineResource(item.resource),
  };
}

function isInternalLearningEvaluationResource(item: CompletedResourceView): boolean {
  if (item.kind !== 'inline') {
    return false;
  }
  const resource = item.resource;
  const haystack = [resource.title, resource.summary, resource.content]
    .filter(Boolean)
    .join('\n');
  return isInternalLearningEvaluationText(haystack);
}

function isInternalLearningEvaluationText(value: string): boolean {
  const compact = value.replace(/\s+/g, '');
  return (
    (compact.includes('学习效果') && compact.includes('评估') && compact.includes('结果'))
    || (compact.includes('学习') && compact.includes('诊断') && compact.includes('摘要'))
    || (compact.includes('多智能体') && compact.includes('协同') && compact.includes('轨迹'))
    || (compact.includes('质量') && compact.includes('审查'))
  );
}

function PracticeQuestionPanel(props: {
  batch: PracticeQuestionBatch;
}) {
  return (
    <div className="mb-4 rounded-xl border border-slate-100 bg-slate-50/50 p-4 dark:border-slate-800 dark:bg-slate-900/50">
      <div className="mb-4 flex flex-col items-stretch gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <div className="text-sm font-semibold text-slate-700 dark:text-slate-300">{props.batch.title}</div>
          <div className="mt-1 text-xs text-slate-500 dark:text-slate-400">
            主题：{props.batch.topic || '未指定'} · 难度：{props.batch.difficulty || '未指定'}
          </div>
          {props.batch.description ? (
            <div className="mt-3 rounded-xl border border-primary-100 bg-primary-50/70 px-3 py-2 text-sm leading-6 text-primary-700 dark:border-primary-900 dark:bg-primary-500/10 dark:text-primary-200">
              {props.batch.description}
            </div>
          ) : null}
        </div>
        <div className="w-full shrink-0 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs font-medium text-amber-700 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200 sm:w-auto">
          已进入浮动练习助手
        </div>
      </div>

      <div className="rounded-xl border border-amber-100 bg-white px-4 py-3 text-sm leading-6 text-amber-800 dark:border-amber-500/20 dark:bg-slate-950 dark:text-amber-100">
        共 {props.batch.questions.length} 道题。题干、选项和逐题判题只在对话页浮动练习助手中展示，资源结果页不展开题目正文。
      </div>
    </div>
  );
}

export function TaskResultPanel(props: {
  service: EngineService | null;
  taskId: string;
  taskSummary: string;
  serviceResultLines: string[];
  downloadLinks: TempDownloadLink[];
  videoResult: VideoResult | null;
  inlineResource: InlineResourceView | null;
  inlineResources: InlineResourceView[];
  completedResources: CompletedResourceView[];
  learningPlan: LearningPlanView | null;
  resourcePushPlan: ResourcePushPlanView | null;
  resultHistory: EngineTaskResultRecord[];
  selectedResultTaskId: string;
  practiceBatch: PracticeQuestionBatch | null;
  onSelectResultTask: (taskId: string) => void;
}) {
  const selectedRecord = props.resultHistory.find((item) => item.taskId === props.selectedResultTaskId) ?? null;
  const selectedRecordUsesCurrentSnapshot = !selectedRecord || selectedRecord.taskId === props.taskId;
  const activeInlineResources = props.inlineResources.length
    ? props.inlineResources
    : props.inlineResource
      ? [props.inlineResource]
      : [];
  const visibleTaskSummary = selectedRecordUsesCurrentSnapshot
    ? selectedRecord?.taskSummary ?? props.taskSummary
    : selectedRecord.taskSummary;
  const visibleResultLines = selectedRecordUsesCurrentSnapshot
    ? selectedRecord?.serviceResultLines ?? props.serviceResultLines
    : selectedRecord.serviceResultLines;
  const visibleDownloadLinks = selectedRecordUsesCurrentSnapshot
    ? selectedRecord?.downloadLinks ?? props.downloadLinks
    : selectedRecord.downloadLinks;
  const visibleVideoResult = selectedRecordUsesCurrentSnapshot
    ? selectedRecord?.videoResult ?? props.videoResult
    : selectedRecord.videoResult;
  const visibleInlineResources = selectedRecordUsesCurrentSnapshot
    ? selectedRecord?.inlineResources?.length
      ? selectedRecord.inlineResources
      : activeInlineResources
    : selectedRecord.inlineResources;
  const visiblePracticeBatch = selectedRecordUsesCurrentSnapshot
    ? selectedRecord?.practiceBatch ?? props.practiceBatch
    : selectedRecord.practiceBatch;
  const visibleLearningPlan = selectedRecordUsesCurrentSnapshot
    ? selectedRecord?.learningPlan ?? props.learningPlan
    : selectedRecord.learningPlan;
  const visibleResourcePushPlan = selectedRecordUsesCurrentSnapshot
    ? selectedRecord?.resourcePushPlan ?? props.resourcePushPlan
    : selectedRecord.resourcePushPlan;
  const visibleCompletedResources = selectedRecordUsesCurrentSnapshot
    ? selectedRecord?.completedResources?.length
      ? selectedRecord.completedResources
      : props.completedResources.length
        ? props.completedResources
        : [
            ...visibleInlineResources.map((resource) => ({
              kind: 'inline' as const,
              key: `inline:${resource.kind}:${resource.title}`,
              resource,
            })),
            ...(visiblePracticeBatch
              ? [{
                  kind: 'question_batch' as const,
                  key: `question_batch:${visiblePracticeBatch.title}:${visiblePracticeBatch.topic}`,
                  batch: visiblePracticeBatch,
                }]
              : []),
          ]
    : selectedRecord.completedResources;
  const cleanedTaskSummary = props.service === 'resource'
    ? sanitizeResourceDisplayText(visibleTaskSummary)
    : visibleTaskSummary;
  const displayedResultLines = visibleResultLines.filter((line) => !isInternalLearningEvaluationText(line));
  const cleanedResultLines = props.service === 'resource'
    ? displayedResultLines.map(sanitizeResourceDisplayText).filter(Boolean)
    : displayedResultLines;
  const userFacingCompletedResources = visibleCompletedResources.filter((item) => !isInternalLearningEvaluationResource(item));
  const cleanedCompletedResources = props.service === 'resource'
    ? userFacingCompletedResources.map(sanitizeResourceCompletedItem)
    : userFacingCompletedResources;
  const externalRecommendations = props.service === 'push' || props.service === 'personalized'
    ? visibleDownloadLinks.filter(isExternalRecommendation)
    : [];
  const fileDownloads = props.service === 'push'
    ? []
    : visibleDownloadLinks.filter((item) => !isExternalRecommendation(item));
  const resourcesByStepId = buildResourcePlanStepMap(visibleResourcePushPlan);
  const gapsByStepId = buildResourceCoverageGapMap(visibleResourcePushPlan);
  const standaloneStepResources = visibleResourcePushPlan?.stepResources.filter((step) => !resourcesByLearningStep(visibleLearningPlan, step.stepId)) ?? [];
  const standaloneCoverageGaps = visibleResourcePushPlan?.coverageGaps.filter((gap) => !resourcesByLearningStep(visibleLearningPlan, gap.stepId)) ?? [];

  const handleDownload = async (item: TempDownloadLink) => {
    const absoluteUrl = /^https?:\/\//i.test(item.url) ? item.url : `${window.location.origin}${item.url.startsWith('/') ? item.url : `/${item.url}`}`;
    const sameOrigin = absoluteUrl.startsWith(window.location.origin);
    const fallbackOpen = () => {
      const anchor = document.createElement('a');
      anchor.href = absoluteUrl;
      anchor.target = '_blank';
      anchor.rel = 'noopener noreferrer';
      document.body.appendChild(anchor);
      anchor.click();
      document.body.removeChild(anchor);
    };

    if (!sameOrigin) {
      fallbackOpen();
      return;
    }

    try {
      const response = await request.getInstance().get(absoluteUrl, {
        responseType: 'blob',
      });
      const blobUrl = window.URL.createObjectURL(response.data as Blob);
      const anchor = document.createElement('a');
      anchor.href = blobUrl;
      anchor.download = item.fileName || extractFileName(item.url, item.title);
      anchor.target = '_blank';
      document.body.appendChild(anchor);
      anchor.click();
      document.body.removeChild(anchor);
      window.URL.revokeObjectURL(blobUrl);
    } catch {
      fallbackOpen();
    }
  };

  const handleResourcePlanOpen = (item: ResourcePushPlanResourceView) => {
    if (!item.downloadUrl) {
      return;
    }
    const anchor = document.createElement('a');
    anchor.href = item.downloadUrl;
    anchor.target = '_blank';
    anchor.rel = 'noopener noreferrer';
    document.body.appendChild(anchor);
    anchor.click();
    document.body.removeChild(anchor);
  };

  if (!props.service) {
    return null;
  }

  const hasContent = Boolean(props.taskSummary)
    || cleanedResultLines.length > 0
    || visibleDownloadLinks.length > 0
    || Boolean(visibleVideoResult)
    || cleanedCompletedResources.length > 0
    || Boolean(visiblePracticeBatch)
    || Boolean(visibleLearningPlan)
    || Boolean(visibleResourcePushPlan)
    || props.resultHistory.length > 0;
  if (!hasContent) {
    return null;
  }

  return (
    <div className="space-y-4">
      {props.resultHistory.length > 1 ? (
        <div className="modern-card overflow-hidden">
          <div className="flex items-center gap-2 border-b border-slate-100 px-4 py-3 dark:border-slate-800">
            <Sparkles className="h-4 w-4 text-primary-500" />
            <span className="text-sm font-semibold text-slate-700 dark:text-slate-300">结果选择</span>
          </div>
          <div className="flex gap-2 overflow-x-auto p-3 sm:p-4">
            {props.resultHistory.map((record) => {
              const active = record.taskId === (selectedRecord?.taskId || props.selectedResultTaskId);
              const assetCount =
                (record.inlineResources?.length ?? 0)
                + (record.downloadLinks?.length ?? 0)
                + (record.practiceBatch ? 1 : 0)
                + (record.videoResult ? 1 : 0);
              return (
                <button
                  key={record.taskId}
                  type="button"
                  onClick={() => props.onSelectResultTask(record.taskId)}
                  className={`min-w-[180px] rounded-xl border px-3 py-2 text-left transition-all ${
                    active
                      ? 'border-primary-300 bg-primary-50 text-primary-700 dark:border-primary-700 dark:bg-primary-500/10 dark:text-primary-200'
                      : 'border-slate-200 bg-slate-50/70 text-slate-600 hover:border-primary-200 hover:bg-white dark:border-slate-700 dark:bg-slate-900/50 dark:text-slate-300 dark:hover:border-primary-700'
                  }`}
                >
                  <div className="truncate text-sm font-semibold">{record.title}</div>
                  <div className="mt-1 text-[11px] opacity-75">{record.taskStatus || '任务结果'} · {assetCount} 个产物</div>
                </button>
              );
            })}
          </div>
        </div>
      ) : null}

      {visibleVideoResult ? (
        <div className="modern-card overflow-hidden">
          <div className="flex items-center gap-2 border-b border-slate-100 px-4 py-3 dark:border-slate-800">
            <Sparkles className="h-4 w-4 text-primary-500" />
            <span className="text-sm font-semibold text-slate-700 dark:text-slate-300">视频结果</span>
          </div>
          <div className="p-3 sm:p-4">
            <Suspense fallback={<div className="aspect-video rounded-xl border border-dashed border-slate-200 bg-slate-50 dark:border-slate-700 dark:bg-slate-900" />}>
              <LazyVideoCard
                title={visibleVideoResult.title}
                videoUrl={visibleVideoResult.videoUrl}
                thumbnailUrl={visibleVideoResult.thumbnailUrl}
                duration={visibleVideoResult.duration}
                style={visibleVideoResult.style}
                knowledgePoint={visibleVideoResult.knowledgePoint}
                expiresHint={visibleVideoResult.expiresHint}
                fileName={visibleVideoResult.fileName}
                renderStatus={visibleVideoResult.renderStatus}
                renderMessage={visibleVideoResult.renderMessage}
              />
            </Suspense>
          </div>
        </div>
      ) : null}

      {externalRecommendations.length > 0 ? (
        <div className="modern-card overflow-hidden">
          <div className="flex items-center gap-2 border-b border-slate-100 px-4 py-3 dark:border-slate-800">
            <Sparkles className="h-4 w-4 text-primary-500" />
            <span className="text-sm font-semibold text-slate-700 dark:text-slate-300">推荐资源</span>
          </div>
          <div className="grid gap-3 p-3 sm:p-4 md:grid-cols-2">
            {externalRecommendations.map((item) => (
              <ExternalResourceRecommendationCard key={`${item.title}-${item.url}`} item={item} />
            ))}
          </div>
        </div>
      ) : null}

      {visibleLearningPlan ? (
        <div className="modern-card overflow-hidden">
          <div className="flex items-center gap-2 border-b border-slate-100 px-4 py-3 dark:border-slate-800">
            <BookOpen className="h-4 w-4 text-primary-500" />
            <span className="text-sm font-semibold text-slate-700 dark:text-slate-300">学习路径</span>
          </div>
          <div className="space-y-3 p-3 sm:p-4">
            {visibleLearningPlan.goal ? (
              <div className="text-sm font-semibold text-slate-800 dark:text-slate-200">{visibleLearningPlan.goal}</div>
            ) : null}
            {visibleLearningPlan.steps.map((step, index) => (
              (() => {
                const resourceStep = resourcesByStepId.get(step.stepId);
                const stepGaps = gapsByStepId.get(step.stepId) ?? [];
                return (
                  <div key={`${step.stepId}-${index}`} className="rounded-xl border border-slate-200 bg-slate-50/60 px-4 py-3 dark:border-slate-700 dark:bg-slate-900/50">
                    <div className="flex flex-wrap items-start justify-between gap-2">
                      <div className="min-w-0 text-sm font-semibold text-slate-800 dark:text-slate-200">
                        {step.order ?? index + 1}. {step.title || step.stepId}
                      </div>
                      {step.estimatedMinutes ? (
                        <span className="rounded-full bg-white px-2 py-1 text-[11px] font-medium text-slate-500 ring-1 ring-slate-200 dark:bg-slate-950 dark:text-slate-400 dark:ring-slate-700">
                          {step.estimatedMinutes} 分钟
                        </span>
                      ) : null}
                    </div>
                    {step.intent ? <div className="mt-1 text-sm leading-6 text-slate-600 dark:text-slate-400">{step.intent}</div> : null}
                    {step.reason && step.reason !== step.intent ? <div className="mt-1 text-xs leading-5 text-slate-500 dark:text-slate-400">依据：{step.reason}</div> : null}
                    <div className="mt-2 flex flex-wrap gap-2 text-[11px] text-slate-500 dark:text-slate-400">
                      {step.targetKnowledgePoints.map((point) => (
                        <span key={point} className="rounded-full bg-white px-2 py-1 ring-1 ring-slate-200 dark:bg-slate-950 dark:ring-slate-700">{point}</span>
                      ))}
                      {step.preferredResourceTypes.map((type) => (
                        <span key={type} className="rounded-full bg-primary-50 px-2 py-1 text-primary-600 ring-1 ring-primary-100 dark:bg-primary-500/10 dark:text-primary-300 dark:ring-primary-700">{recommendationTypeLabel(type)}</span>
                      ))}
                      {step.status ? <span className="rounded-full bg-white px-2 py-1 ring-1 ring-slate-200 dark:bg-slate-950 dark:ring-slate-700">{step.status}</span> : null}
                    </div>
                    {step.checkpoint ? (
                      <div className="mt-3 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs leading-5 text-slate-600 dark:border-slate-700 dark:bg-slate-950/70 dark:text-slate-300">
                        检查点：{step.checkpoint}
                      </div>
                    ) : null}
                    {resourceStep?.resources.length ? (
                      <ResourcePushPlanStepResources
                        step={resourceStep}
                        onOpenResource={handleResourcePlanOpen}
                      />
                    ) : null}
                    {stepGaps.map((gap, gapIndex) => (
                      <ResourceCoverageGapNote key={`${gap.stepId}-${gapIndex}`} gap={gap} />
                    ))}
                  </div>
                );
              })()
            ))}
          </div>
        </div>
      ) : null}

      {standaloneStepResources.length > 0 || standaloneCoverageGaps.length > 0 ? (
        <div className="modern-card overflow-hidden">
          <div className="flex items-center gap-2 border-b border-slate-100 px-4 py-3 dark:border-slate-800">
            <Sparkles className="h-4 w-4 text-primary-500" />
            <span className="text-sm font-semibold text-slate-700 dark:text-slate-300">资源推送计划</span>
          </div>
          <div className="space-y-3 p-3 sm:p-4">
            {standaloneStepResources.map((step, index) => (
              <ResourcePushPlanStepResources
                key={`${step.stepId}-${index}`}
                step={step}
                onOpenResource={handleResourcePlanOpen}
                standalone
              />
            ))}
            {standaloneCoverageGaps.map((gap, index) => (
              <ResourceCoverageGapNote key={`${gap.stepId}-${index}`} gap={gap} />
            ))}
          </div>
        </div>
      ) : null}

      <div className="modern-card overflow-hidden">
        <div className="flex items-center gap-2 border-b border-slate-100 px-4 py-3 dark:border-slate-800">
          <BookOpen className="h-4 w-4 text-primary-500" />
          <span className="text-sm font-semibold text-slate-700 dark:text-slate-300">任务结果</span>
        </div>
        <div className="p-3 sm:p-4">
          {cleanedCompletedResources.map((item, index) => (
            item.kind === 'inline' ? (
              <InlineResourcePanel key={`${item.key}-${index}`} resource={item.resource} />
            ) : (
              <PracticeQuestionPanel
                key={`${item.key}-${index}`}
                batch={item.batch}
              />
            )
          ))}
          {cleanedTaskSummary ? (
            <div className="mb-4 rounded-xl border border-slate-100 bg-slate-50/50 p-4 text-sm leading-7 text-slate-700 dark:border-slate-800 dark:bg-slate-900/50 dark:text-slate-300">
              <DeferredMarkdownRenderer content={cleanedTaskSummary} />
            </div>
          ) : null}
          {cleanedResultLines.length > 0 ? (
            <ul className="space-y-2">
              {cleanedResultLines.map((line, index) => (
                <li key={`${index}-${line}`} className="flex items-start gap-2 text-sm text-slate-600 dark:text-slate-400">
                  <span className="mt-1.5 block h-1.5 w-1.5 shrink-0 rounded-full bg-primary-400" />
                  {line}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      </div>

      {fileDownloads.length > 0 ? (
        <div className="modern-card overflow-hidden">
          <div className="flex items-center gap-2 border-b border-slate-100 px-4 py-3 dark:border-slate-800">
            <FileText className="h-4 w-4 text-primary-500" />
            <span className="text-sm font-semibold text-slate-700 dark:text-slate-300">产物下载</span>
          </div>
          <div className="p-3 sm:p-4">
            <div className="grid gap-2 md:grid-cols-2">
              {fileDownloads.map((item) => (
                <div
                  key={`${item.title}-${item.url}`}
                  className="flex flex-col items-stretch justify-between gap-3 rounded-xl border border-slate-200 bg-slate-50/50 px-4 py-3 transition-all hover:border-primary-200 hover:bg-white dark:border-slate-700 dark:bg-slate-900/50 dark:hover:border-primary-700 sm:flex-row sm:items-center"
                >
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium text-slate-700 dark:text-slate-300">{item.title}</div>
                    <div className="mt-0.5 flex items-center gap-2 text-[11px] text-slate-400 dark:text-slate-500">
                      {item.resourceType ? <span>{item.resourceType}</span> : null}
                      <span>{item.expiresHint}</span>
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => { void handleDownload(item); }}
                    className="w-full shrink-0 rounded-lg bg-primary-50 px-3 py-2 text-xs font-medium text-primary-600 transition-colors hover:bg-primary-100 dark:bg-primary-500/10 dark:text-primary-400 dark:hover:bg-primary-500/20 sm:w-auto sm:py-1.5"
                  >
                    下载
                  </button>
                </div>
              ))}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function ResourcePushPlanStepResources(props: {
  step: ResourcePushPlanStepView;
  onOpenResource: (item: ResourcePushPlanResourceView) => void;
  standalone?: boolean;
}) {
  if (props.step.resources.length === 0) {
    return null;
  }
  return (
    <div className={props.standalone ? 'rounded-xl border border-slate-200 bg-slate-50/60 px-4 py-3 dark:border-slate-700 dark:bg-slate-900/50' : 'mt-3 border-t border-slate-200 pt-3 dark:border-slate-700'}>
      <div className="flex flex-wrap items-center gap-2 text-xs font-semibold text-slate-600 dark:text-slate-300">
        <span>{props.standalone ? props.step.stepTitle || props.step.stepId || '推荐资源' : '推荐资源'}</span>
        {props.step.targetKnowledgePoints.map((point) => (
          <span key={point} className="rounded-full bg-white px-2 py-0.5 text-[11px] font-medium text-slate-500 ring-1 ring-slate-200 dark:bg-slate-950 dark:text-slate-400 dark:ring-slate-700">
            {point}
          </span>
        ))}
      </div>
      <div className="mt-2 grid gap-2 md:grid-cols-2">
        {props.step.resources.map((item, index) => (
          <div key={`${item.title}-${index}`} className="rounded-lg border border-slate-200 bg-white px-3 py-2 dark:border-slate-700 dark:bg-slate-950/70">
            <div className="flex flex-wrap items-center gap-2 text-[11px] text-slate-500 dark:text-slate-400">
              {item.resourceType ? <span className="rounded-full bg-primary-50 px-2 py-0.5 text-primary-600 dark:bg-primary-500/10 dark:text-primary-300">{recommendationTypeLabel(item.resourceType)}</span> : null}
              {item.sourceName || item.source ? <span>{item.sourceName || item.source}</span> : null}
            </div>
            <div className="mt-1 text-sm font-medium text-slate-800 dark:text-slate-200">{item.title || item.resourceType || '资源'}</div>
            {item.matchReason || item.summaryText ? (
              <div className="mt-1 line-clamp-2 text-xs leading-5 text-slate-500 dark:text-slate-400">
                {item.matchReason || item.summaryText}
              </div>
            ) : null}
            {item.downloadUrl ? (
              <button
                type="button"
                onClick={() => props.onOpenResource(item)}
                className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-primary-600 transition-colors hover:text-primary-700 dark:text-primary-400 dark:hover:text-primary-300"
              >
                打开资源
                <ExternalLink className="h-3.5 w-3.5" />
              </button>
            ) : null}
          </div>
        ))}
      </div>
    </div>
  );
}

function ResourceCoverageGapNote(props: { gap: ResourceCoverageGapView }) {
  const missingTypes = props.gap.missingResourceTypes.map((item) => recommendationTypeLabel(item)).join('、');
  if (!missingTypes && !props.gap.reason) {
    return null;
  }
  return (
    <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50/70 px-3 py-2 text-xs leading-5 text-amber-800 dark:border-amber-900/70 dark:bg-amber-950/30 dark:text-amber-200">
      资源缺口：{missingTypes || '待补齐'}{props.gap.reason ? `。${props.gap.reason}` : ''}
    </div>
  );
}

function buildResourcePlanStepMap(plan: ResourcePushPlanView | null): Map<string, ResourcePushPlanStepView> {
  const map = new Map<string, ResourcePushPlanStepView>();
  plan?.stepResources.forEach((step) => {
    if (step.stepId && !map.has(step.stepId)) {
      map.set(step.stepId, step);
    }
  });
  return map;
}

function buildResourceCoverageGapMap(plan: ResourcePushPlanView | null): Map<string, ResourceCoverageGapView[]> {
  const map = new Map<string, ResourceCoverageGapView[]>();
  plan?.coverageGaps.forEach((gap) => {
    if (!gap.stepId) {
      return;
    }
    const current = map.get(gap.stepId) ?? [];
    map.set(gap.stepId, [...current, gap]);
  });
  return map;
}

function resourcesByLearningStep(plan: LearningPlanView | null, stepId: string): boolean {
  return Boolean(stepId && plan?.steps.some((step) => step.stepId === stepId));
}

function ExternalResourceRecommendationCard(props: { item: TempDownloadLink }) {
  const actionLabel = recommendationActionLabel(props.item.resourceType);
  const typeLabel = recommendationTypeLabel(props.item.resourceType);
  const isVideo = props.item.resourceType === 'VIDEO';
  return (
    <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm dark:border-slate-700 dark:bg-slate-900">
      <div className={`relative ${isVideo ? 'aspect-video' : 'aspect-[16/10]'} bg-slate-100 dark:bg-slate-800`}>
        {props.item.thumbnailUrl ? (
          <img
            src={props.item.thumbnailUrl}
            alt={props.item.title}
            className="h-full w-full object-cover"
            loading="lazy"
            referrerPolicy="no-referrer"
          />
        ) : (
          <div className="flex h-full w-full items-center justify-center bg-gradient-to-br from-slate-100 to-primary-100 text-sm text-slate-500 dark:from-slate-800 dark:to-slate-900 dark:text-slate-400">
            {typeLabel}
          </div>
        )}
      </div>
      <div className="space-y-3 p-4">
        <div>
          <div className="text-base font-semibold text-slate-800 dark:text-slate-100">{props.item.title}</div>
          {props.item.knowledgePoint ? (
            <div className="mt-1 text-sm text-slate-500 dark:text-slate-400">知识点：{props.item.knowledgePoint}</div>
          ) : null}
          {props.item.summary ? (
            <p className="mt-2 line-clamp-3 text-sm leading-6 text-slate-600 dark:text-slate-300">{props.item.summary}</p>
          ) : null}
        </div>
        <div className="flex flex-wrap gap-2 text-xs text-slate-500 dark:text-slate-400">
          {props.item.sourceName ? (
            <span className="rounded-full bg-slate-100 px-2.5 py-1 dark:bg-slate-800">{props.item.sourceName}</span>
          ) : null}
          <span className="rounded-full bg-primary-50 px-2.5 py-1 text-primary-600 dark:bg-primary-500/10 dark:text-primary-300">{typeLabel}</span>
        </div>
        <div className="flex flex-col items-stretch justify-between gap-3 border-t border-slate-100 pt-3 dark:border-slate-800 sm:flex-row sm:items-center">
          <span className="text-xs text-slate-400 dark:text-slate-500">{props.item.expiresHint || '点击后将在新窗口打开资源'}</span>
          <a
            href={props.item.url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center justify-center gap-1 text-sm font-medium text-primary-600 transition-colors hover:text-primary-700 dark:text-primary-400 dark:hover:text-primary-300 sm:justify-start"
          >
            {actionLabel}
            <ExternalLink className="h-3.5 w-3.5" />
          </a>
        </div>
      </div>
    </div>
  );
}

function isExternalRecommendation(item: TempDownloadLink): boolean {
  return /^https?:\/\//i.test(item.url);
}

function recommendationActionLabel(resourceType?: string): string {
  switch (resourceType) {
    case 'VIDEO':
      return '打开视频';
    case 'CODE_CASE':
      return '查看案例';
    case 'PRACTICAL_CASE':
      return '开始实操';
    default:
      return '打开资源';
  }
}

function recommendationTypeLabel(resourceType?: string): string {
  switch (resourceType) {
    case 'VIDEO':
      return '外部视频';
    case 'DOCUMENT':
    case 'EXPLANATION':
      return '讲解文档';
    case 'QUIZ':
      return '练习题';
    case 'CODE':
    case 'CODE_CASE':
      return '代码案例';
    case 'PRACTICAL_CASE':
      return '实操案例';
    case 'READING':
      return '拓展阅读';
    case 'SLIDES':
      return '演示课件';
    case 'MINDMAP':
      return '思维导图';
    default:
      return '讲解文档';
  }
}

function extractFileName(url: string, fallbackTitle: string): string {
  try {
    const normalizedUrl = /^https?:\/\//i.test(url) ? url : `${window.location.origin}${url.startsWith('/') ? url : `/${url}`}`;
    const pathname = new URL(normalizedUrl).pathname;
    const basename = pathname.split('/').filter(Boolean).pop();
    return basename || fallbackTitle;
  } catch {
    return fallbackTitle;
  }
}
