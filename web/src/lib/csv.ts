/**
 * Export a table as CSV.
 *
 * Built here rather than pulled in: the whole job is quoting, and a dependency
 * for that is a dependency to audit, pin and carry into a deployment that is
 * meant to run offline on one laptop.
 *
 * The quoting matters more than it looks. Audit rows carry free text, plate
 * candidates carry commas, and an unquoted comma silently shifts every later
 * column — producing a file that opens cleanly and says something false, which
 * is the worst available outcome for an evidence export.
 */

/** RFC 4180: double the quotes, wrap anything containing a delimiter. */
function cell(value: unknown): string {
  if (value === null || value === undefined) return ''
  const text =
    typeof value === 'object' ? JSON.stringify(value) : String(value)
  if (/[",\n\r]/.test(text)) {
    return `"${text.replace(/"/g, '""')}"`
  }
  return text
}

export interface Column<T> {
  header: string
  value: (row: T) => unknown
}

export function toCsv<T>(rows: T[], columns: Column<T>[]): string {
  const lines = [columns.map((c) => cell(c.header)).join(',')]
  for (const row of rows) {
    lines.push(columns.map((c) => cell(c.value(row))).join(','))
  }
  // CRLF, because Excel on Windows is where these end up.
  return lines.join('\r\n')
}

export function downloadCsv<T>(
  filename: string,
  rows: T[],
  columns: Column<T>[],
): void {
  // A BOM, so Excel reads the file as UTF-8 rather than the local codepage.
  // Without it Gujarati camera names arrive as mojibake.
  const blob = new Blob(['﻿', toCsv(rows, columns)], {
    type: 'text/csv;charset=utf-8',
  })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  URL.revokeObjectURL(url)
}

/** `nagarnetra-audit-2026-09-02.csv` */
export function stampedName(prefix: string): string {
  return `nagarnetra-${prefix}-${new Date().toISOString().slice(0, 10)}.csv`
}
