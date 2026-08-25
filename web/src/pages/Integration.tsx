/**
 * Integration layer — the interoperability evidence.
 *
 * Shows the federated VMS instances and the adapters that reach them. This is
 * what "heterogeneous integration" looks like concretely: one interface, many
 * vendors, resolved per VMS instance.
 */

import { useEffect, useState } from 'react'

import * as api from '@/lib/api'
import type { VmsInstance } from '@/lib/types'

export default function Integration() {
  const [vms, setVms] = useState<VmsInstance[]>([])
  const [adapters, setAdapters] = useState<Record<string, string>>({})
  const [detail, setDetail] = useState('')
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    Promise.all([api.getVmsInstances(), api.getAdapters()])
      .then(([v, a]) => {
        setVms(v)
        setAdapters(a.adapters)
        setDetail(a.detail)
      })
      .catch((err) =>
        setError(err instanceof Error ? err.message : String(err)),
      )
  }, [])

  return (
    <div className="space-y-6 overflow-y-auto p-6">
      <header>
        <h1 className="text-xl font-semibold">Integration layer</h1>
        <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
          Departmental VMS platforms stay authoritative for their own video. This
          platform ingests their metadata and resolves streams on demand, so the
          central tier carries <strong className="text-foreground">events, not video</strong>.
        </p>
      </header>

      {error && (
        <p className="rounded border border-status-offline/40 bg-status-offline/10 px-4 py-3 text-sm text-status-offline">
          {error}
        </p>
      )}

      <section className="rounded-lg border border-border bg-card">
        <h2 className="border-b border-border px-4 py-3 text-sm font-semibold">
          Federated VMS instances
        </h2>
        <div className="divide-y divide-border">
          {vms.map((instance) => (
            <div key={instance.id} className="px-4 py-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{instance.name}</span>
                <span className="rounded bg-secondary px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wide text-muted-foreground">
                  {instance.vendor}
                </span>
                <span className="rounded border border-primary/30 bg-primary/10 px-1.5 py-0.5 font-mono text-[10px] text-primary">
                  {instance.adapter_type}
                </span>
                <span className="ml-auto font-mono text-xs tabular-nums text-muted-foreground">
                  {instance.camera_count} cameras
                </span>
              </div>
              {instance.base_url && (
                <p className="mt-1 break-all font-mono text-[11px] text-muted-foreground">
                  {instance.base_url}
                </p>
              )}
            </div>
          ))}
        </div>
      </section>

      <section className="rounded-lg border border-border bg-card">
        <h2 className="border-b border-border px-4 py-3 text-sm font-semibold">
          Registered adapters
        </h2>
        <div className="grid gap-px bg-border sm:grid-cols-2 lg:grid-cols-3">
          {Object.entries(adapters).map(([type, className]) => (
            <div key={type} className="bg-card px-4 py-3">
              <p className="font-mono text-sm text-primary">{type}</p>
              <p className="mt-0.5 font-mono text-[11px] text-muted-foreground">
                {className}
              </p>
            </div>
          ))}
        </div>
        {detail && (
          <p className="border-t border-border px-4 py-3 text-xs leading-relaxed text-muted-foreground">
            {detail}
          </p>
        )}
      </section>

      <section className="rounded-lg border border-border bg-card p-4">
        <h2 className="text-sm font-semibold">Protocols supported</h2>
        <ul className="mt-3 space-y-2 text-sm text-muted-foreground">
          <li>
            <span className="font-mono text-foreground">RTSP</span> — direct to
            camera or NVR. Health probed with ffprobe, which negotiates a real
            session rather than just opening a socket.
          </li>
          <li>
            <span className="font-mono text-foreground">ONVIF</span> —
            device and media services over SOAP; enumerates profiles and stream
            URIs, preferring the sub-stream for analytics.
          </li>
          <li>
            <span className="font-mono text-foreground">Vendor REST API</span> —
            token auth, camera sync, stream and playback resolution. One
            field-mapped adapter covers Milestone, Genetec, CP Plus and
            Hikvision.
          </li>
          <li>
            <span className="font-mono text-foreground">Sentinel sandbox</span> —
            the challenge&apos;s own camera grid, read from its{' '}
            <span className="font-mono">/api/ingest</span> catalogue.
          </li>
        </ul>
      </section>
    </div>
  )
}
