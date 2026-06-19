import { describe, expect, it } from 'vitest';
import { normalizeCopyMarkdown, sanitizeMarkdownContent } from './markdownSanitizer';

describe('markdownSanitizer', () => {
  it('preserves markdown tables, code fences, mermaid and latex', () => {
    const input = [
      '# 标题',
      '',
      '| A | B |',
      '| - | - |',
      '| $x^2$ | \\(y\\) |',
      '',
      '```mermaid',
      'flowchart TD',
      'A-->B',
      '```',
      '',
      '$$',
      'E=mc^2',
      '$$',
    ].join('\n');

    expect(sanitizeMarkdownContent(input)).toContain('| $x^2$ | \\(y\\) |');
    expect(sanitizeMarkdownContent(input)).toContain('```mermaid');
    expect(sanitizeMarkdownContent(input)).toContain('$$\nE=mc^2\n$$');
  });

  it('removes copy button text and debug metadata outside code blocks', () => {
    const input = [
      '复制',
      '正文',
      'metadata: internal prompt',
      '```text',
      'Copy',
      'metadata: keep in code',
      '```',
      'Copied',
    ].join('\n');

    const output = sanitizeMarkdownContent(input);
    expect(output).toContain('正文');
    expect(output).not.toContain('internal prompt');
    expect(output).toContain('Copy\nmetadata: keep in code');
    expect(output.endsWith('```')).toBe(true);
  });

  it('unwraps a single JSON fence and normalizes copy text', () => {
    expect(normalizeCopyMarkdown('```json\n{"a":1}\n```')).toBe('{"a":1}');
  });
});
