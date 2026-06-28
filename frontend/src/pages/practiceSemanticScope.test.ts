import { describe, expect, it } from 'vitest';
import { buildPracticeJudgeParams } from './practiceSemanticScope';

describe('buildPracticeJudgeParams', () => {
  it('scopes ambiguous stage titles with domain and target knowledge points', () => {
    const params = buildPracticeJudgeParams({
      source: 'LEARNING_PATH',
      purpose: 'STAGE_TEST',
      topic: '函数定义概念补强',
      domain: 'Linux Shell',
      count: 10,
      activeLearningStep: {
        stepId: 'step-1',
        title: '函数定义概念补强',
        targetKnowledgePoints: ['Shell 脚本函数定义与调用', '参数传递'],
        checkpoint: '能够写出 myfunc() { ... } 并完成调用',
      },
    });

    const learningContext = params.learningContext as Record<string, unknown>;
    const semanticScope = learningContext.semanticScope as Record<string, unknown>;

    expect(params.topic).toBe('Linux Shell：Shell 脚本函数定义与调用');
    expect(params.rawTopic).toBe('函数定义概念补强');
    expect(semanticScope).toMatchObject({
      domain: 'Linux Shell',
      topic: 'Linux Shell：Shell 脚本函数定义与调用',
      rawTopic: '函数定义概念补强',
      source: 'LEARNING_PATH',
    });
    expect(semanticScope.knowledgeTags).toEqual(['Shell 脚本函数定义与调用', '参数传递']);
  });

  it('preserves existing semantic scope when upstream already supplied one', () => {
    const params = buildPracticeJudgeParams({
      source: 'KNOWLEDGE_GRAPH',
      topic: '函数定义',
      count: 5,
      learningContext: {
        semanticScope: {
          domain: 'Python',
          topic: 'Python 函数定义 def 语法',
          rawTopic: '函数定义',
          knowledgeTags: ['def', '参数'],
        },
      },
    });

    expect(params.topic).toBe('Python 函数定义 def 语法');
    expect((params.learningContext as Record<string, unknown>).chapter).toBe('Python 函数定义 def 语法');
  });
});
