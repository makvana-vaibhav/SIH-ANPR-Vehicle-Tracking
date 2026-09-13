/**
 * The product mark: a trail tapering into the point it trails behind.
 *
 * Replaces a single letter set in a coloured square — a legible placeholder,
 * but the single most recognisable way an interface signals that nobody
 * designed a mark for it. This is a literal contrail: the same shape the
 * product's own map draws every time it animates a vehicle's route across the
 * city, so the mark and the product argue the same point rather than being
 * two unrelated decisions. Drawn as one filled taper rather than several
 * overlapping strokes — three semi-transparent lines this close together blur
 * into a smudge below about 32px, where the mark actually has to work.
 *
 * Single colour, via `currentColor` — set the wrapper's text colour rather
 * than passing one in, so it inherits the one accent this product allows
 * itself rather than becoming a second place a colour is chosen.
 */
export function BrandMark({
  size = 22,
  className = '',
}: {
  size?: number
  className?: string
}) {
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      aria-hidden="true"
      focusable="false"
      className={`shrink-0 ${className}`}
    >
      <path
        d="M3.2 21.5 Q10.5 19.8 16.8 9.3 Q13 14.5 7.2 19 Z"
        fill="currentColor"
      />
      <circle cx="18.6" cy="7.3" r="2.15" fill="currentColor" />
    </svg>
  )
}
