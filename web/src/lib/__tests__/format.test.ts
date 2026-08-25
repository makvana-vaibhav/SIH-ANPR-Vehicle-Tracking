/**
 * Presentation-layer formatting.
 *
 * CLAUDE.md: everything is stored and transported as timezone-aware UTC, and
 * converted to IST only at the presentation edge. These tests pin that down —
 * a timestamp rendered in the wrong zone on a police timeline is not a
 * cosmetic bug, it is wrong evidence.
 */

import { describe, expect, it } from 'vitest'

import { formatIST, relativeTime } from '@/lib/api'

describe('formatIST', () => {
  it('converts UTC to IST (+5:30)', () => {
    // 09:00 UTC is 14:30 IST.
    const formatted = formatIST('2026-09-10T09:00:00+00:00')
    expect(formatted).toContain('14:30')
  })

  it('handles the half-hour offset across a date boundary', () => {
    // 20:00 UTC on the 10th is 01:30 IST on the 11th.
    const formatted = formatIST('2026-09-10T20:00:00+00:00')
    expect(formatted).toContain('01:30')
    expect(formatted).toContain('11')
  })

  it('uses 24-hour time, so 14:30 is never ambiguous', () => {
    const formatted = formatIST('2026-09-10T09:00:00+00:00')
    expect(formatted).not.toMatch(/am|pm/i)
  })

  it('can render time only', () => {
    expect(formatIST('2026-09-10T09:00:00+00:00', false)).toContain('14:30')
  })
})

describe('relativeTime', () => {
  it('reports seconds, minutes, hours and days', () => {
    const ago = (seconds: number) =>
      relativeTime(new Date(Date.now() - seconds * 1000).toISOString())

    expect(ago(5)).toMatch(/s ago/)
    expect(ago(120)).toBe('2m ago')
    expect(ago(7200)).toBe('2h ago')
    expect(ago(172800)).toBe('2d ago')
  })
})
