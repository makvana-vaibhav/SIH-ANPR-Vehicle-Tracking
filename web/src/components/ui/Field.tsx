/** Labelled form controls, one shape. `Field` supplies the label + hint;
 *  `Input`/`Select`/`Checkbox` supply one focus ring and one radius so a
 *  control never looks different depending which screen it's on (Watchlist's
 *  inputs used `rounded`, Login's used `rounded-md` — the same element,
 *  two shapes). */

import {
  forwardRef,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
} from 'react'

export function Field({
  label,
  hint,
  children,
}: {
  label: ReactNode
  hint?: ReactNode
  children: ReactNode
}) {
  return (
    <label className="block">
      <span className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
        {label}
      </span>
      {/* The label-to-control gap lives here, not on the control itself — a
          bare Input/Select used outside a Field (an inline table cell, a
          compact toolbar) should not carry a gap meant for a label above it.
          A Tailwind class string cannot reliably override this at the call
          site: `mt-1` and `mt-0` have equal specificity, and Tailwind's
          generated stylesheet — not the order classes are written in JSX —
          decides which one wins. */}
      <div className="mt-1">{children}</div>
      {hint && <span className="mt-1 block text-[11px] text-muted-foreground/80">{hint}</span>}
    </label>
  )
}

export const controlClass =
  'w-full rounded-md border border-input bg-background px-2.5 py-1.5 text-sm outline-none transition focus:border-primary focus:ring-1 focus:ring-primary'

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  ({ className = '', ...rest }, ref) => (
    <input ref={ref} className={`${controlClass} ${className}`} {...rest} />
  ),
)
Input.displayName = 'Input'

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(
  ({ className = '', children, ...rest }, ref) => (
    <select ref={ref} className={`${controlClass} ${className}`} {...rest}>
      {children}
    </select>
  ),
)
Select.displayName = 'Select'

export function Checkbox({
  label,
  className = '',
  labelClassName = 'gap-2 text-sm',
  ...rest
}: InputHTMLAttributes<HTMLInputElement> & { label?: ReactNode; labelClassName?: string }) {
  const box = (
    <input
      type="checkbox"
      className={`h-4 w-4 shrink-0 rounded border-input bg-background accent-primary ${className}`}
      {...rest}
    />
  )
  if (!label) return box
  return (
    <label className={`flex cursor-pointer items-center ${labelClassName}`}>
      {box}
      <span>{label}</span>
    </label>
  )
}
