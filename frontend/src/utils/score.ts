export function normalizeScore(
  score: number,
  originalMax: number,
  targetMax: number
): number {
  if (originalMax === 0) return 0;
  return (score / originalMax) * targetMax;
}
