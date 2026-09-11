/** The one loading spinner in the product — used only where there is no
 *  layout to skeleton yet (auth bootstrap, a map still fetching its first
 *  page). Everywhere the final shape is known, prefer Skeleton.tsx instead:
 *  it tells the operator what is coming, not just that something is. */

export function Spinner({ className = 'h-8 w-8' }: { className?: string }) {
  return (
    <div className={`animate-spin rounded-full border-2 border-primary border-t-transparent ${className}`} />
  )
}
