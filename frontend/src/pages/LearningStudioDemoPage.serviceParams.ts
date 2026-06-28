import type { EngineService, ResourceType, ServiceFormsPayload } from './LearningStudioDemoPage.types';
import { buildPracticeSemanticScope } from './practiceSemanticScope';

export function buildServiceParams(service: EngineService, payload: ServiceFormsPayload): Record<string, unknown> {
  if (service === 'resource') {
    const resourceForm = payload.resourceForm;
    const selectedResourceTypes = resolveSelectedResourceTypes(resourceForm.resourceTypes, resourceForm.resourceType);
    const normalizedResourceTypes = uniqueResourceTypes(selectedResourceTypes.map(normalizeResourceType));
    const includeVideo = normalizedResourceTypes.includes('VIDEO');
    const resourceTypeLabelText = selectedResourceTypes.map(resourceTypeLabel).join('、');
    const difficultyLabel = resourceDifficultyLabel(resourceForm.difficulty);
    const semanticScope = buildPracticeSemanticScope({
      source: 'QNA_RESOURCE',
      topic: resourceForm.keyPoints || resourceForm.course,
      domain: resourceForm.course,
      count: 5,
      knowledgeTags: resourceForm.keyPoints ? [resourceForm.keyPoints] : [],
    });
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
        knowledgeTags: resourceForm.keyPoints ? [resourceForm.keyPoints] : [],
        semanticScope,
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
