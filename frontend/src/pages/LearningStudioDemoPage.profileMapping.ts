import {
  readRecord as readSharedRecord,
  readString as readSharedString,
  readStringArray as readSharedStringArray,
} from '../utils/valueReaders';
import type {
  ProfileCurrentGoal,
  ProfileDimensionScore,
  ProfileHistoryPoint,
  ProfileLearningHabits,
  ProfileSnapshot,
  ProfileSkillMastery,
  UserProfileResponse,
  WeakPointRank,
} from './LearningStudioDemoPage.types';

interface ProfileErrorPattern {
  pattern: string;
  examples: string[];
}

function readString(value: unknown): string {
  return readSharedString(value);
}

function readRecord(value: unknown): Record<string, unknown> | null {
  return readSharedRecord(value);
}

function readStringArray(...values: unknown[]): string[] {
  return readSharedStringArray(...values);
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
