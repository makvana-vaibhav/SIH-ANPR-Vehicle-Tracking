/**
 * The product's icon set, drawn here rather than installed.
 *
 * Two reasons it is hand-rolled. An icon package is a runtime dependency and a
 * tree-shaking problem for the sake of sixteen glyphs, and the obvious packages
 * (Lucide, Feather) carry a look that reads as "default choice" — every
 * dashboard built this year uses them.
 *
 * Every path is drawn on the same 24×24 grid at the same 1.5 stroke, with round
 * caps and joins, so the set looks like one hand. `currentColor` throughout: an
 * icon takes the colour of the text it sits beside, and never carries a colour
 * of its own.
 */

import type { SVGProps } from 'react'

export type IconName =
  | 'dashboard'
  | 'map'
  | 'video'
  | 'alert'
  | 'chart'
  | 'search'
  | 'bookmark'
  | 'pulse'
  | 'camera'
  | 'users'
  | 'ledger'
  | 'chevronLeft'
  | 'chevronDown'
  | 'info'
  | 'close'
  | 'arrowRight'
  | 'logout'
  | 'key'

/**
 * Path data only — every wrapper attribute is applied once, below, so a glyph
 * cannot drift from the set by carrying its own stroke width or cap style.
 */
const PATHS: Record<IconName, string> = {
  dashboard: 'M4 4h6v7H4zM14 4h6v4h-6zM14 12h6v8h-6zM4 15h6v5H4z',
  map: 'M9 4 3 6.5v13L9 17l6 3 6-2.5v-13L15 7zM9 4v13M15 7v13',
  video: 'M3 7a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2zM15 10l6-3.5v11L15 14z',
  alert: 'M12 4 3 19h18zM12 10v4M12 17h.01',
  chart: 'M4 20V10M10 20V4M16 20v-7M22 20H2',
  search: 'M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM20 20l-4.2-4.2',
  bookmark: 'M6 4h12v16l-6-4-6 4z',
  pulse: 'M2 12h4l3-7 4 14 3-7h6',
  camera: 'M3 8a2 2 0 0 1 2-2h2.5l1.5-2h6l1.5 2H19a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2zM12 16a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7z',
  users: 'M9 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zM2 20c0-3.3 3.1-5.5 7-5.5s7 2.2 7 5.5M17 4.5a3.5 3.5 0 0 1 0 7M18 14.8c2.4.6 4 2.3 4 5.2',
  ledger: 'M5 3h11l3 3v15H5zM8 8h7M8 12h7M8 16h4',
  chevronLeft: 'm14 6-6 6 6 6',
  chevronDown: 'm6 9 6 6 6-6',
  info: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM12 11v5M12 8h.01',
  close: 'm6 6 12 12M18 6 6 18',
  arrowRight: 'M4 12h15M13 6l6 6-6 6',
  logout: 'M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3M10 8l-4 4 4 4M6 12h11',
  key: 'M15 4a5 5 0 1 0-4.6 7L9 12.4 7.4 14 6 12.6 4 14.6 5.5 16 4 17.5 6 19.5 12.6 13A5 5 0 0 0 15 4zM16.5 7.5h.01',
}

interface IconProps extends Omit<SVGProps<SVGSVGElement>, 'name'> {
  name: IconName
  /** Edge length in px. The nav rail uses 18; inline hints use 14. */
  size?: number
}

export function Icon({ name, size = 18, className = '', ...rest }: IconProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      // Decorative by default: every icon in this product sits beside its own
      // label, so announcing it twice is noise. A caller using an icon as the
      // only content of a control passes its own aria-label and overrides this.
      aria-hidden="true"
      focusable="false"
      className={`shrink-0 ${className}`}
      {...rest}
    >
      <path d={PATHS[name]} />
    </svg>
  )
}
