export interface PracticeJudgeParamsInput {
  source: string;
  topic?: unknown;
  rawTopic?: unknown;
  domain?: unknown;
  query?: string;
  purpose?: string;
  count: number;
  questionCount?: unknown;
  difficulty?: unknown;
  knowledgeTags?: unknown;
  evidence?: unknown;
  learningContext?: Record<string, unknown>;
  activeLearningStep?: Record<string, unknown> | null;
  activeLearningStepId?: unknown;
  activeLearningStepTitle?: unknown;
  extraParams?: Record<string, unknown>;
  extraLearningContext?: Record<string, unknown>;
}

export function buildPracticeJudgeParams(input: PracticeJudgeParamsInput): Record<string, unknown> {
  const semanticScope = buildPracticeSemanticScope(input);
  const scopedTopic = readString(semanticScope.topic) || readString(input.topic) || '当前练习主题';
  const rawTopic = readString(semanticScope.rawTopic);
  const activeStepTitle = readString(input.activeLearningStepTitle)
    || readString(input.activeLearningStep?.title)
    || rawTopic
    || scopedTopic;
  const learningContext = pruneRecord({
    ...(input.learningContext ?? {}),
    ...(input.extraLearningContext ?? {}),
    source: input.source,
    activeLearningStepId: readString(input.activeLearningStepId)
      || readString(input.activeLearningStep?.stepId)
      || readString(input.activeLearningStep?.id),
    activeLearningStepTitle: activeStepTitle,
    chapter: readString((input.learningContext ?? {}).chapter) || scopedTopic,
    knowledgeTags: readStringArray(semanticScope.knowledgeTags),
    questionCount: input.count,
    semanticScope,
  });

  return pruneRecord({
    ...(input.extraParams ?? {}),
    purpose: input.purpose,
    topic: scopedTopic,
    rawTopic,
    query: input.query || `${scopedTopic} 练习`,
    count: input.count,
    questionCount: input.count,
    difficulty: readString(input.difficulty),
    learningContext,
  });
}

export function buildPracticeSemanticScope(input: PracticeJudgeParamsInput): Record<string, unknown> {
  const learningContext = input.learningContext ?? {};
  const existingScope = readRecord(learningContext.semanticScope);
  const rawTopic = firstString(
    input.rawTopic,
    existingScope.rawTopic,
    input.topic,
    input.activeLearningStepTitle,
    input.activeLearningStep?.title,
    learningContext.rawTopic,
    learningContext.topic,
  );
  const knowledgeTags = uniqueStrings([
    ...readStringArray(existingScope.knowledgeTags),
    ...readStringArray(input.knowledgeTags),
    ...readStringArray(input.activeLearningStep?.targetKnowledgePoints),
    ...readStringArray(learningContext.knowledgeTags),
  ]);
  const domain = firstString(
    existingScope.domain,
    input.domain,
    learningContext.domain,
    learningContext.course,
    learningContext.courseName,
    learningContext.learningGoal,
    learningContext.goal,
    readRecord(learningContext.currentGoal).shortTerm,
  );
  const evidence = uniqueStrings([
    ...readStringArray(existingScope.evidence),
    ...readStringArray(input.evidence),
    readString(input.activeLearningStep?.checkpoint),
    readString(input.activeLearningStep?.successCriteria),
    readString(input.activeLearningStep?.objective),
    readString(input.activeLearningStep?.summary),
    ...readStringArray(learningContext.evidence),
  ]);
  const topic = firstString(
    existingScope.topic,
    composeScopedTopic(rawTopic, domain, knowledgeTags),
  );

  return pruneRecord({
    domain,
    topic,
    rawTopic,
    knowledgeTags,
    source: input.source,
    evidence,
  });
}

function composeScopedTopic(rawTopic: string, domain: string, knowledgeTags: string[]): string {
  const baseTopic = rawTopic || knowledgeTags[0] || domain;
  const richerTag = knowledgeTags.find((tag) => isRicherTopic(tag, baseTopic));
  if (domain && baseTopic && !hasTextOverlap(domain, baseTopic)) {
    return `${domain}：${richerTag || baseTopic}`;
  }
  return richerTag || baseTopic;
}

function isRicherTopic(candidate: string, baseTopic: string): boolean {
  if (!candidate || !baseTopic || candidate === baseTopic || candidate.length <= baseTopic.length) {
    return false;
  }
  return hasTextOverlap(candidate, baseTopic);
}

function hasTextOverlap(left: string, right: string): boolean {
  const normalizedLeft = normalizeText(left);
  const normalizedRight = normalizeText(right);
  if (normalizedLeft.length < 2 || normalizedRight.length < 2) {
    return false;
  }
  if (normalizedLeft.includes(normalizedRight) || normalizedRight.includes(normalizedLeft)) {
    return true;
  }
  const leftTokens = semanticTokens(normalizedLeft);
  const rightTokens = semanticTokens(normalizedRight);
  return [...leftTokens].some((token) => rightTokens.has(token));
}

function firstString(...values: unknown[]): string {
  for (const value of values) {
    const text = readString(value);
    if (text) {
      return text;
    }
  }
  return '';
}

function readRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function readString(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function readStringArray(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value.map((item) => String(item).trim()).filter(Boolean);
  }
  const text = readString(value);
  return text ? [text] : [];
}

function uniqueStrings(values: string[]): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const value of values) {
    const text = value.trim();
    if (!text || seen.has(text)) {
      continue;
    }
    seen.add(text);
    result.push(text);
  }
  return result;
}

function pruneRecord(record: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(record).filter(([, value]) => {
      if (value === undefined || value === null || value === '') {
        return false;
      }
      if (Array.isArray(value) && value.length === 0) {
        return false;
      }
      if (typeof value === 'object' && !Array.isArray(value) && Object.keys(value).length === 0) {
        return false;
      }
      return true;
    }),
  );
}

function normalizeText(value: string): string {
  return value.toLowerCase().replace(/\s+/g, '');
}

function semanticTokens(value: string): Set<string> {
  const tokens = new Set<string>();
  for (const match of value.matchAll(/[a-z0-9+#_.-]{2,}/g)) {
    tokens.add(match[0]);
  }
  const chineseChars = [...value.matchAll(/[\u4e00-\u9fff]/g)].map((match) => match[0]);
  for (let index = 0; index < chineseChars.length - 1; index += 1) {
    tokens.add(`${chineseChars[index]}${chineseChars[index + 1]}`);
  }
  return tokens;
}
