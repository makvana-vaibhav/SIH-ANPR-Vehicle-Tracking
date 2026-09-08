/**
 * CSV quoting.
 *
 * The failure this guards against is silent: an unquoted comma shifts every
 * later column, so the file opens cleanly in Excel and says something false.
 * For an audit export that is worse than failing to export at all.
 */

import { describe, expect, it } from 'vitest'

import { toCsv, type Column } from '@/lib/csv'

interface Row {
  a: unknown
  b: unknown
}

const cols: Column<Row>[] = [
  { header: 'a', value: (r) => r.a },
  { header: 'b', value: (r) => r.b },
]

describe('toCsv', () => {
  it('writes a header and one line per row', () => {
    expect(toCsv([{ a: 1, b: 2 }], cols)).toBe('a,b\r\n1,2')
  })

  it('quotes a value containing a comma', () => {
    // Unquoted, this would become two columns and shift everything after it.
    expect(toCsv([{ a: 'Rajkot, Gujarat', b: 'x' }], cols)).toBe(
      'a,b\r\n"Rajkot, Gujarat",x',
    )
  })

  it('doubles embedded quotes', () => {
    expect(toCsv([{ a: 'he said "no"', b: 'x' }], cols)).toBe(
      'a,b\r\n"he said ""no""",x',
    )
  })

  it('quotes a value containing a newline', () => {
    expect(toCsv([{ a: 'line1\nline2', b: 'x' }], cols)).toBe(
      'a,b\r\n"line1\nline2",x',
    )
  })

  it('writes null and undefined as empty, not as the words', () => {
    expect(toCsv([{ a: null, b: undefined }], cols)).toBe('a,b\r\n,')
  })

  it('serialises an object rather than emitting [object Object]', () => {
    // Audit rows carry a JSONB `params` column.
    expect(toCsv([{ a: { plate: 'GJ03AB1234' }, b: 1 }], cols)).toBe(
      'a,b\r\n"{""plate"":""GJ03AB1234""}",1',
    )
  })

  it('keeps a header-only file valid when there are no rows', () => {
    expect(toCsv([], cols)).toBe('a,b')
  })

  it('preserves a leading zero in a plate', () => {
    // GJ03… must not be mangled; the value is written as text, and what Excel
    // does on import is Excel's business.
    expect(toCsv([{ a: 'GJ03AB1234', b: '0755' }], cols)).toBe(
      'a,b\r\nGJ03AB1234,0755',
    )
  })
})
