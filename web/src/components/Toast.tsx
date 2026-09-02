/**
 * Transient notices.
 *
 * Inline red text under a form is easy to miss and easy to leave behind: it
 * stays on screen after the thing it described stopped being true, and on a
 * long page it can be scrolled out of view entirely while the operator waits
 * for something that already failed.
 *
 * Errors are **not** auto-dismissed. A success can vanish — the outcome is
 * visible in the thing that changed — but a failure the operator did not read
 * is a failure they will assume did not happen.
 */

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from 'react'

export type ToastKind = 'success' | 'error' | 'info'

interface Toast {
  id: number
  kind: ToastKind
  message: string
}

interface ToastApi {
  success: (message: string) => void
  error: (message: unknown) => void
  info: (message: string) => void
}

const ToastContext = createContext<ToastApi | null>(null)

/** Success and info clear themselves; errors wait to be dismissed. */
const DISMISS_AFTER_MS = 5_000

const STYLE: Record<ToastKind, string> = {
  success: 'border-status-online/50 bg-status-online/10 text-status-online',
  error: 'border-status-offline/50 bg-status-offline/10 text-status-offline',
  info: 'border-border bg-card text-foreground',
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])

  const dismiss = useCallback((id: number) => {
    setToasts((current) => current.filter((t) => t.id !== id))
  }, [])

  const push = useCallback(
    (kind: ToastKind, message: string) => {
      const id = Date.now() + Math.random()
      setToasts((current) => [...current, { id, kind, message }])
      if (kind !== 'error') {
        window.setTimeout(() => dismiss(id), DISMISS_AFTER_MS)
      }
    },
    [dismiss],
  )

  const api = useMemo<ToastApi>(
    () => ({
      success: (message) => push('success', message),
      info: (message) => push('info', message),
      // Accepts an unknown so every catch block can hand it whatever it caught
      // rather than each one re-deriving the same message.
      error: (thrown) =>
        push('error', thrown instanceof Error ? thrown.message : String(thrown)),
    }),
    [push],
  )

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div
        // Announced to screen readers: an operator who cannot see the corner
        // of the screen still needs to know the action failed.
        role="status"
        aria-live="polite"
        className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-full max-w-sm flex-col gap-2"
      >
        {toasts.map((toast) => (
          <div
            key={toast.id}
            className={`pointer-events-auto flex items-start gap-3 rounded-md border px-3 py-2 text-xs shadow-lg backdrop-blur ${STYLE[toast.kind]}`}
          >
            <span className="flex-1 leading-relaxed">{toast.message}</span>
            <button
              type="button"
              onClick={() => dismiss(toast.id)}
              aria-label="Dismiss"
              className="shrink-0 opacity-60 transition hover:opacity-100"
            >
              ✕
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}

export function useToast(): ToastApi {
  const context = useContext(ToastContext)
  if (!context) throw new Error('useToast must be used inside a ToastProvider')
  return context
}
