export function readString(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

export function readRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

export function readNumeric(value: unknown): number | undefined {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : undefined;
  }
  return undefined;
}

export function readStringArray(...values: unknown[]): string[] {
  for (const value of values) {
    if (Array.isArray(value)) {
      return value.map((item) => readString(item)).filter(Boolean);
    }
    if (typeof value === 'string' && value.trim()) {
      return value
        .split(/[,;，；、\n]+/)
        .map((item) => item.trim())
        .filter(Boolean);
    }
  }
  return [];
}

export function readRecordArray(value: unknown): Array<Record<string, unknown>> {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .map((item) => readRecord(item))
    .filter((item): item is Record<string, unknown> => item !== null);
}
