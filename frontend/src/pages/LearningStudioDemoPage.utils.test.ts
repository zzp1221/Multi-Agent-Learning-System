import { describe, expect, it } from 'vitest';
import { readPracticeQuestionBatch, readPracticeJudgeResult } from './LearningStudioDemoPage.utils';

describe('LearningStudioDemoPage payload readers', () => {
  it('reads practice question batches from nested payloads', () => {
    const batch = readPracticeQuestionBatch({
      practiceQuestionBatch: {
        title: '阶段测试',
        topic: 'Graph Traversal',
        questions: [
          {
            questionId: 'q1',
            questionType: 'SHORT_ANSWER',
            stem: 'Explain BFS.',
            options: [' A ', 'B'],
            answer: 'Use a queue.',
            knowledgeTags: ['graph'],
          },
        ],
      },
    });

    expect(batch?.topic).toBe('Graph Traversal');
    expect(batch?.questions).toHaveLength(1);
    expect(batch?.questions[0].options).toEqual(['A', 'B']);
  });

  it('reads judge result summaries and score details', () => {
    const result = readPracticeJudgeResult({
      judgeResult: {
        summary: 'Good',
        totalScore: '82',
        accuracy: 0.8,
        items: [{ questionId: 'q1', score: 82, feedback: 'OK' }],
      },
    });

    expect(result?.summary).toBe('Good');
    expect(result?.totalScore).toBe(82);
    expect(result?.items[0].feedback).toBe('OK');
  });
});
