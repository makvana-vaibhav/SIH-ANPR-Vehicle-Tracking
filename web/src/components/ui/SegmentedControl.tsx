/** A row of mutually-exclusive choices — time windows, basemap picks,
 *  status filters. VehicleSearch and AuditLog each hand-rolled the same
 *  "1h / 24h / 7d / 30d" control independently; this is the one they both
 *  should have been. */

export function SegmentedControl<T extends string>({
  options,
  value,
  onChange,
  disabledValues,
}: {
  options: { value: T; label: string; title?: string }[]
  value: T
  onChange: (value: T) => void
  disabledValues?: T[]
}) {
  return (
    <div className="flex overflow-hidden rounded-md border border-border">
      {options.map((opt) => (
        <button
          key={opt.value}
          type="button"
          title={opt.title}
          disabled={disabledValues?.includes(opt.value)}
          onClick={() => onChange(opt.value)}
          className={`px-2.5 py-1.5 text-xs transition disabled:cursor-not-allowed disabled:opacity-40 ${
            value === opt.value
              ? 'bg-primary text-primary-foreground'
              : 'text-muted-foreground hover:bg-secondary/50 hover:text-foreground'
          }`}
        >
          {opt.label}
        </button>
      ))}
    </div>
  )
}
