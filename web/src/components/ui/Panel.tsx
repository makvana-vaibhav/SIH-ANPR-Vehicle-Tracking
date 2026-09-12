/** The bordered card shell used for forms and grouped sections everywhere.
 *  One radius, one border weight, one background — the shape-consistency
 *  rule that made every hand-rolled `rounded-md border border-border
 *  bg-card p-4` block worth naming. */

import type { HTMLAttributes } from 'react'

export function Panel({ className = '', ...rest }: HTMLAttributes<HTMLDivElement>) {
  return <div className={`rounded-md border border-border bg-card p-4 ${className}`} {...rest} />
}

export function PanelHeader({
  title,
  action,
}: {
  title: string
  action?: React.ReactNode
}) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <h2 className="text-sm font-semibold">{title}</h2>
      {action}
    </div>
  )
}
