import { lazy, Suspense, useState } from 'react';
import { BookOpen, CheckCircle2, ExternalLink, FileText, Sparkles, XCircle } from 'lucide-react';
import CodeBlock from '../components/CodeBlock';
import {
  assessmentDimensionOptions,
  pushResourceTypeOptions,
  resourceTypeButtons,
  type AssessmentForm,
  type CompletedResourceView,
  type CriticReviewView,
  type EngineService,
  type EngineTaskResultRecord,
  type InlineResourceView,
  type LearningPlanView,
  type PathForm,
  type PracticeJudgeResult,
  type PracticeQuestionBatch,
  type PushForm,
  type ResourceForm,
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
  assessmentForm: AssessmentForm;
  onResourceChange: (next: ResourceForm) => void;
  onPathChange: (next: PathForm) => void;
  onPushChange: (next: PushForm) => void;
  onAssessmentChange: (next: AssessmentForm) => void;
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

  if (props.service === 'path') {
    return (
      <div className="rounded-2xl border border-slate-200 bg-slate-50/50 p-4 dark:border-slate-700 dark:bg-slate-900/50 md:p-5">
        <div className="mb-3 text-sm font-semibold text-slate-700 dark:text-slate-300">学习路径规划参数</div>
        <div className="grid gap-3 md:grid-cols-2">
          <input
            value={props.pathForm.targetPeriod}
            onChange={(e) => props.onPathChange({ ...props.pathForm, targetPeriod: e.target.value })}
            placeholder="目标周期"
            className={baseInputClass}
          />
          <input
            value={props.pathForm.weeklyHours}
            onChange={(e) => props.onPathChange({ ...props.pathForm, weeklyHours: e.target.value })}
            placeholder="每周可投入（小时）"
            className={baseInputClass}
          />
        </div>
        <textarea
          value={props.pathForm.currentProgress}
          onChange={(e) => props.onPathChange({ ...props.pathForm, currentProgress: e.target.value })}
          rows={2}
          placeholder="当前学习进度"
          className={`${baseInputClass} mt-3`}
        />
      </div>
    );
  }

  if (props.service === 'push') {
    return (
      <div className="space-y-5">
        <label className="block">
          <span className="mb-2 block text-sm font-semibold text-slate-700 dark:text-slate-300">资源类型</span>
          <select
            value={props.pushForm.preferredType}
            onChange={(e) => props.onPushChange({ ...props.pushForm, preferredType: e.target.value as typeof props.pushForm.preferredType })}
            className={`${baseSelectClass} h-14 text-base`}
          >
            {pushResourceTypeOptions.map((item) => (
              <option key={item.type} value={item.type}>
                {item.label}
              </option>
            ))}
          </select>
        </label>
        <div className="rounded-2xl border border-blue-100 bg-blue-50/70 px-4 py-4 text-sm leading-7 text-slate-600 dark:border-slate-800 dark:bg-slate-950/40 dark:text-slate-300">
          具体推送内容将在提交后由服务根据真实学习上下文自动筛选，不再手动输入关键词和课程范围。
        </div>
      </div>
    );
  }

  const nextDimensions = props.assessmentForm.dimensions;
  return (
    <div className="rounded-2xl border border-slate-200 bg-slate-50/50 p-4 dark:border-slate-700 dark:bg-slate-900/50 md:p-5">
      <div className="mb-3 text-sm font-semibold text-slate-700 dark:text-slate-300">学习效果评估参数</div>
      <div className="flex flex-wrap gap-2">
        {assessmentDimensionOptions.map((item) => {
          const checked = nextDimensions.includes(item);
          return (
            <button
              key={item}
              type="button"
              onClick={() => {
                props.onAssessmentChange({ ...props.assessmentForm, dimensions: [item] });
              }}
              className={chipButton(checked)}
            >
              {item}
            </button>
          );
        })}
      </div>
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

function PracticeQuestionPanel(props: {
  batch: PracticeQuestionBatch;
  judgeResult: PracticeJudgeResult | null;
  canSubmit: boolean;
  onSubmitAnswers: (batch: PracticeQuestionBatch, answers: Record<string, string>) => void;
}) {
  const [answers, setAnswers] = useState<Record<string, string>>({});

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
        <button
          type="button"
          disabled={!props.canSubmit}
          onClick={() => props.onSubmitAnswers(props.batch, answers)}
          className="w-full shrink-0 rounded-lg bg-primary-600 px-3 py-2 text-xs font-medium text-white transition-colors hover:bg-primary-700 disabled:cursor-not-allowed disabled:opacity-40 sm:w-auto"
        >
          {props.batch.submitLabel || '提交答案并判题'}
        </button>
      </div>

      <div className="space-y-4">
        {props.batch.questions.map((question, index) => (
          <div key={question.questionId} className="rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-950">
            <div className="text-sm font-medium text-slate-700 dark:text-slate-300">
              {index + 1}. {question.stem}
            </div>
            {question.options && question.options.length > 0 ? (
              <div className="mt-3 space-y-2">
                {question.options.map((option, optionIndex) => {
                  const optionLabel = String.fromCharCode(65 + optionIndex);
                  const checked = answers[question.questionId] === optionLabel;
                  return (
                    <label
                      key={`${question.questionId}-${optionLabel}`}
                      className="flex cursor-pointer items-start gap-2 rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-600 transition-colors hover:border-primary-300 dark:border-slate-700 dark:text-slate-400 dark:hover:border-primary-700"
                    >
                      <input
                        type="radio"
                        name={question.questionId}
                        checked={checked}
                        onChange={() => setAnswers((prev) => ({ ...prev, [question.questionId]: optionLabel }))}
                        className="mt-1"
                      />
                      <span>{optionLabel}. {option}</span>
                    </label>
                  );
                })}
              </div>
            ) : (
              <textarea
                value={answers[question.questionId] || ''}
                onChange={(event) => setAnswers((prev) => ({ ...prev, [question.questionId]: event.target.value }))}
                rows={4}
                className="mt-3 w-full rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 text-sm outline-none focus:border-primary-400 focus:ring-2 focus:ring-primary-500/20 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
                placeholder="请输入你的答案"
              />
            )}
          </div>
        ))}
      </div>

      {props.judgeResult ? (
        <div className="mt-5 rounded-xl border border-emerald-200 bg-emerald-50/60 p-4 dark:border-emerald-900 dark:bg-emerald-500/10">
          <div className="text-sm font-semibold text-emerald-700 dark:text-emerald-300">{props.judgeResult.title}</div>
          <div className="mt-1 text-sm text-emerald-700/90 dark:text-emerald-200">{props.judgeResult.summary}</div>
          {props.judgeResult.specializedAnalysis ? (
            <div className="mt-4 rounded-xl border border-white/70 bg-white/80 p-4 dark:border-slate-800 dark:bg-slate-950">
              <div className="text-sm font-semibold text-slate-700 dark:text-slate-200">
                {props.judgeResult.specializedAnalysis.title}
              </div>
              <div className="mt-1 text-sm text-slate-600 dark:text-slate-400">
                {props.judgeResult.specializedAnalysis.summary}
              </div>
              {props.judgeResult.specializedAnalysis.markdown ? (
                <div className="mt-3 text-sm leading-7 text-slate-700 dark:text-slate-300">
                  <DeferredMarkdownRenderer content={props.judgeResult.specializedAnalysis.markdown} />
                </div>
              ) : null}
            </div>
          ) : null}
          <div className="mt-3 text-xs text-emerald-700/80 dark:text-emerald-300">
            总分：{props.judgeResult.totalScore} · 正确率：{Math.round((props.judgeResult.accuracy || 0) * 100)}%
          </div>
          <div className="mt-4 space-y-3">
            {props.judgeResult.items.map((item) => (
              <div key={item.questionId} className="rounded-lg border border-emerald-200 bg-white px-3 py-3 dark:border-emerald-900 dark:bg-slate-950">
                <div className="flex items-center gap-2 text-sm font-medium text-slate-700 dark:text-slate-300">
                  {item.isCorrect ? <CheckCircle2 className="h-4 w-4 text-emerald-500" /> : <XCircle className="h-4 w-4 text-rose-500" />}
                  {item.questionId} · 得分 {item.score}
                </div>
                <div className="mt-2 text-sm text-slate-600 dark:text-slate-400">你的答案：{item.learnerAnswer || '未作答'}</div>
                {item.correctAnswer ? (
                  <div className="mt-1 text-sm text-slate-600 dark:text-slate-400">参考答案：{item.correctAnswer}</div>
                ) : null}
                {item.reason ? <div className="mt-2 text-sm text-slate-600 dark:text-slate-400">判定依据：{item.reason}</div> : null}
                {item.feedback ? <div className="mt-1 text-sm text-slate-600 dark:text-slate-400">反馈建议：{item.feedback}</div> : null}
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

export function TaskResultPanel(props: {
  service: EngineService | null;
  taskSummary: string;
  serviceResultLines: string[];
  downloadLinks: TempDownloadLink[];
  videoResult: VideoResult | null;
  inlineResource: InlineResourceView | null;
  inlineResources: InlineResourceView[];
  completedResources: CompletedResourceView[];
  learningPlan: LearningPlanView | null;
  criticReview: CriticReviewView | null;
  resultHistory: EngineTaskResultRecord[];
  selectedResultTaskId: string;
  practiceBatch: PracticeQuestionBatch | null;
  judgeResult: PracticeJudgeResult | null;
  canSubmitPractice: boolean;
  onSelectResultTask: (taskId: string) => void;
  onSubmitPracticeAnswers: (batch: PracticeQuestionBatch, answers: Record<string, string>) => void;
}) {
  const selectedRecord = props.resultHistory.find((item) => item.taskId === props.selectedResultTaskId) ?? null;
  const visibleTaskSummary = selectedRecord?.taskSummary ?? props.taskSummary;
  const visibleResultLines = selectedRecord?.serviceResultLines ?? props.serviceResultLines;
  const visibleDownloadLinks = selectedRecord?.downloadLinks ?? props.downloadLinks;
  const visibleVideoResult = selectedRecord?.videoResult ?? props.videoResult;
  const visibleInlineResources = selectedRecord?.inlineResources?.length
    ? selectedRecord.inlineResources
    : props.inlineResources.length
      ? props.inlineResources
      : props.inlineResource
        ? [props.inlineResource]
        : [];
  const visiblePracticeBatch = selectedRecord?.practiceBatch ?? props.practiceBatch;
  const visibleCompletedResources = selectedRecord?.completedResources?.length
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
        ];
  const visibleJudgeResult = selectedRecord?.judgeResult ?? props.judgeResult;
  const externalRecommendations = props.service === 'push'
    ? visibleDownloadLinks.filter(isExternalRecommendation)
    : [];
  const fileDownloads = props.service === 'push'
    ? []
    : visibleDownloadLinks.filter((item) => !isExternalRecommendation(item));

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

  if (!props.service) {
    return null;
  }

  const hasContent = Boolean(props.taskSummary)
    || visibleResultLines.length > 0
    || visibleDownloadLinks.length > 0
    || Boolean(visibleVideoResult)
    || visibleCompletedResources.length > 0
    || Boolean(visiblePracticeBatch)
    || Boolean(visibleJudgeResult)
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

      <div className="modern-card overflow-hidden">
        <div className="flex items-center gap-2 border-b border-slate-100 px-4 py-3 dark:border-slate-800">
          <BookOpen className="h-4 w-4 text-primary-500" />
          <span className="text-sm font-semibold text-slate-700 dark:text-slate-300">任务结果</span>
        </div>
        <div className="p-3 sm:p-4">
          {props.service === 'assessment' && visiblePracticeBatch ? (
            <PracticeQuestionPanel
              batch={visiblePracticeBatch}
              judgeResult={visibleJudgeResult}
              canSubmit={props.canSubmitPractice}
              onSubmitAnswers={props.onSubmitPracticeAnswers}
            />
          ) : null}
          {visibleCompletedResources.map((item, index) => (
            item.kind === 'inline' ? (
              <InlineResourcePanel key={`${item.key}-${index}`} resource={item.resource} />
            ) : props.service !== 'assessment' ? (
              <PracticeQuestionPanel
                key={`${item.key}-${index}`}
                batch={item.batch}
                judgeResult={visibleJudgeResult}
                canSubmit={props.canSubmitPractice}
                onSubmitAnswers={props.onSubmitPracticeAnswers}
              />
            ) : null
          ))}
          {visibleTaskSummary ? (
            <div className="mb-4 rounded-xl border border-slate-100 bg-slate-50/50 p-4 text-sm leading-7 text-slate-700 dark:border-slate-800 dark:bg-slate-900/50 dark:text-slate-300">
              <DeferredMarkdownRenderer content={visibleTaskSummary} />
            </div>
          ) : null}
          {visibleResultLines.length > 0 ? (
            <ul className="space-y-2">
              {visibleResultLines.map((line, index) => (
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
    case 'CODE_CASE':
      return '代码案例';
    case 'PRACTICAL_CASE':
      return '实操案例';
    case 'READING':
      return '拓展阅读';
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
