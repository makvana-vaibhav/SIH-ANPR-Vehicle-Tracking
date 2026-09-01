/**
 * Plates as they are read, newest first.
 *
 * This is the authoritative view of what the AI found — unlike the overlay,
 * nothing here depends on lining up with a moving picture. Each card carries
 * the evidence behind the reading: how many frames were involved, whether they
 * agreed, and whether the grammar had to repair the string. An operator acting
 * on a plate should be able to see how much to trust it without leaving the
 * screen.
 */

import type { LiveVehicleEvent } from '@/lib/types'

interface Props {
  events: LiveVehicleEvent[]
  /** Show which camera each read came from. Off for a single-camera view. */
  showCamera?: boolean
  emptyMessage?: string
}

function confidenceColour(value: number): string {
  if (value >= 0.85) return 'text-status-online'
  if (value >= 0.6) return 'text-amber-400'
  return 'text-status-offline'
}

export default function LivePlateFeed({
  events,
  showCamera = false,
  emptyMessage = 'No plates read yet.',
}: Props) {
  // `vehicle.observed` repeats for a vehicle still in view. Collapse to the
  // latest reading per vehicle, so the list is one row per car rather than one
  // per frame the car appeared in.
  const latest = new Map<string, LiveVehicleEvent>()
  for (const event of events) {
    const key = `${event.source?.camera_id ?? ''}:${event.vehicle?.vehicle_id}`
    if (!latest.has(key)) latest.set(key, event)
  }
  const rows = [...latest.values()]

  if (rows.length === 0) {
    return (
      <div className="flex h-full min-h-32 items-center justify-center rounded-md border border-dashed border-border p-6 text-center">
        <p className="text-xs text-muted-foreground">{emptyMessage}</p>
      </div>
    )
  }

  return (
    <ul className="space-y-1.5">
      {rows.map((event) => {
        const plate = event.plate
        const evidence = plate.evidence ?? {}
        const agreement = evidence.agreement
        const settled = event.event === 'vehicle.completed'

        return (
          <li
            key={`${event.source?.camera_id}:${event.vehicle.vehicle_id}`}
            className="rounded-md border border-border bg-card px-3 py-2"
          >
            <div className="flex items-baseline justify-between gap-2">
              <span className="font-mono text-sm font-bold tracking-wide">
                {plate.text}
              </span>
              <span
                className={`font-mono text-xs ${confidenceColour(plate.confidence)}`}
              >
                {plate.confidence.toFixed(2)}
              </span>
            </div>

            <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[10px] text-muted-foreground">
              {showCamera && (
                <span className="font-mono uppercase">{event.source?.camera_id}</span>
              )}
              <span>{event.vehicle?.type ?? 'vehicle'}</span>
              <span>
                {new Date(event.event_time).toLocaleTimeString('en-IN', {
                  timeZone: 'Asia/Kolkata',
                  hour12: false,
                })}
              </span>

              {/* Settled vs still-in-view matters: a completed read is the one
                  that was written to the database and matched against the
                  watchlist. */}
              <span
                className={
                  settled
                    ? 'rounded bg-muted px-1 py-px'
                    : 'rounded bg-primary/15 px-1 py-px text-primary'
                }
              >
                {settled ? 'settled' : 'in view'}
              </span>

              {typeof evidence.reads_total === 'number' && (
                <span>
                  {evidence.reads_total} frame
                  {evidence.reads_total === 1 ? '' : 's'}
                  {typeof agreement === 'number' &&
                    `, ${Math.round(agreement * 100)}% agree`}
                </span>
              )}

              {!plate.grammar_valid && (
                <span className="rounded bg-status-offline/15 px-1 py-px text-status-offline">
                  invalid format
                </span>
              )}
              {plate.ambiguous && (
                <span className="rounded bg-amber-500/15 px-1 py-px text-amber-400">
                  ambiguous
                </span>
              )}
              {plate.corrected_from && (
                <span className="rounded bg-amber-500/15 px-1 py-px font-mono text-amber-400">
                  was {plate.corrected_from}
                </span>
              )}
            </div>
          </li>
        )
      })}
    </ul>
  )
}
