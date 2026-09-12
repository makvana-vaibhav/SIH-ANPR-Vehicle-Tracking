/** Every clickable action, one component. `primary` is the one commitful
 *  action a form should have; `outline` and `ghost` are for anything beside
 *  it; `destructive` is reserved for actions an operator cannot easily walk
 *  back (remove a camera, deactivate an account). Mixing raw
 *  `bg-primary`/`border-border` buttons per file is exactly how a product
 *  ends up with four slightly different button paddings. */

import { forwardRef, type ButtonHTMLAttributes } from 'react'

type Variant = 'primary' | 'outline' | 'ghost' | 'destructive'
type Size = 'sm' | 'md'

const VARIANT_CLASS: Record<Variant, string> = {
  primary: 'bg-primary text-primary-foreground hover:opacity-90',
  outline:
    'border border-border text-muted-foreground hover:border-muted-foreground/60 hover:text-foreground',
  ghost: 'text-muted-foreground hover:bg-secondary/50 hover:text-foreground',
  destructive:
    'border border-status-offline/40 text-status-offline hover:bg-status-offline/10',
}

const SIZE_CLASS: Record<Size, string> = {
  sm: 'px-2 py-1 text-[11px]',
  md: 'px-3 py-1.5 text-sm',
}

export const Button = forwardRef<
  HTMLButtonElement,
  ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: Size }
>(({ variant = 'primary', size = 'md', className = '', type = 'button', ...rest }, ref) => (
  <button
    ref={ref}
    type={type}
    className={`rounded-md font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${VARIANT_CLASS[variant]} ${SIZE_CLASS[size]} ${className}`}
    {...rest}
  />
))
Button.displayName = 'Button'
