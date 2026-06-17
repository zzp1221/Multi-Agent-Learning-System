import { describe, expect, it } from 'vitest';
import { readNumeric, readRecord, readRecordArray, readString, readStringArray } from './valueReaders';

describe('valueReaders', () => {
  it('reads trimmed strings and ignores non-strings', () => {
    expect(readString('  Graph  ')).toBe('Graph');
    expect(readString(42)).toBe('');
  });

  it('reads finite numeric values from numbers and strings', () => {
    expect(readNumeric(12.5)).toBe(12.5);
    expect(readNumeric(' 7 ')).toBe(7);
    expect(readNumeric(Number.NaN)).toBeUndefined();
  });

  it('reads plain records and record arrays', () => {
    expect(readRecord({ id: 'a' })).toEqual({ id: 'a' });
    expect(readRecord(['a'])).toBeNull();
    expect(readRecordArray([{ id: 'a' }, null, ['b'], { id: 'c' }])).toEqual([{ id: 'a' }, { id: 'c' }]);
  });

  it('reads arrays and delimited strings as string arrays', () => {
    expect(readStringArray([' A ', 2, '', 'B'])).toEqual(['A', 'B']);
    expect(readStringArray('A, B；C、D')).toEqual(['A', 'B', 'C', 'D']);
    expect(readStringArray(null, ' X\nY ')).toEqual(['X', 'Y']);
  });
});
