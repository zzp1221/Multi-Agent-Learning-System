export function sanitizeMarkdownContent(input: string): string {
  if (!input) {
    return '';
  }
  const normalized = input.replace(/\r\n/g, '\n');
  const withoutJsonWrapper = stripSingleJsonFence(normalized);
  const lines = withoutJsonWrapper.split('\n');
  const output: string[] = [];
  let inFence = false;

  for (const rawLine of lines) {
    const trimmed = rawLine.trim();
    if (/^```/.test(trimmed)) {
      inFence = !inFence;
      output.push(rawLine.trimEnd());
      continue;
    }
    if (!inFence && shouldDropMarkdownPollutionLine(trimmed)) {
      continue;
    }
    output.push(rawLine.trimEnd());
  }

  return output
    .join('\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

export function normalizeCopyMarkdown(input: string): string {
  return sanitizeMarkdownContent(input).replace(/\n{3,}/g, '\n\n').trim();
}

function stripSingleJsonFence(value: string): string {
  const trimmed = value.trim();
  const match = trimmed.match(/^```(?:json|JSON)\s*\n([\s\S]*?)\n```$/);
  if (!match) {
    return value;
  }
  const body = match[1].trim();
  return body.startsWith('{') || body.startsWith('[') ? body : value;
}

function shouldDropMarkdownPollutionLine(trimmed: string): boolean {
  if (!trimmed) {
    return false;
  }
  if (/^(复制|已复制|copy|copied)$/i.test(trimmed)) {
    return true;
  }
  if (/^(debug|trace|provenance|metadata|internal|resource[_ -]?debug)\s*[:：]/i.test(trimmed)) {
    return true;
  }
  if (/^<!--\s*(debug|trace|metadata|internal)[\s\S]*?-->$/i.test(trimmed)) {
    return true;
  }
  return false;
}
