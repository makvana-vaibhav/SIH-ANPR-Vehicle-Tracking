/** The "event feed live / not connected" indicator. Dashboard, Live ANPR and
 *  Alerts each drew this pill independently, two of them with a raw
 *  `amber-500` for the down state instead of a token — the kind of drift
 *  that leaves the same fact looking like two different colours depending
 *  which screen you're on. */

export function ConnectionBadge({
  live,
  label,
  title,
}: {
  live: boolean
  label: string
  title?: string
}) {
  return (
    <span
      title={title}
      className={`flex items-center gap-1.5 rounded px-2 py-1 text-[11px] font-medium ${
        live ? 'bg-status-online/15 text-status-online' : 'bg-priority-high/15 text-priority-high'
      }`}
    >
      <span
        className={`h-1.5 w-1.5 rounded-full ${live ? 'animate-pulse-alert bg-status-online' : 'bg-priority-high'}`}
      />
      {label}
    </span>
  )
}
