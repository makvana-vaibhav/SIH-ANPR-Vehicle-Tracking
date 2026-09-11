/**
 * Camera administration — onboard, amend and retire cameras.
 *
 * This replaced a read-only "Integration layer" screen that listed the
 * federated VMS instances and the adapters reaching them. It was accurate and
 * it was useless: it told an operator what the platform *could* integrate
 * without letting them integrate anything. The federation evidence is still
 * here, at the bottom, where it costs a strip of screen rather than a tab.
 *
 * Onboarding a camera is the act that makes this a platform rather than a
 * demo, so the form is the first thing on the page. CSV import is the second
 * tab because a state onboards estates, not cameras — but a person adding one
 * camera should not have to build a spreadsheet to do it.
 *
 * The form asks for a stream URL and says why: a camera without one is a
 * registry record that can never be watched or analysed. That is a legitimate
 * thing to record, and it should be a decision rather than an accident — the
 * registry used to hold 250 of them by default.
 */

import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react'

import { SkeletonRows } from '@/components/Skeleton'
import { useToast } from '@/components/Toast'
import { useAuth } from '@/hooks/useAuth'
import * as api from '@/lib/api'
import { PERMISSIONS } from '@/lib/permissions'
import type { Camera, Department, VmsInstance } from '@/lib/types'

type Tab = 'fleet' | 'import'

/** Gujarat's bounding box, mirroring the server-side validator. Checked here
 *  too so a typo is caught while the operator is still looking at the field,
 *  rather than as a 422 after they press the button. */
const BOUNDS = { latMin: 20.0, latMax: 24.8, lonMin: 68.1, lonMax: 74.5 }

const EMPTY = {
  camera_code: '',
  name: '',
  lat: '',
  lon: '',
  district: '',
  city: '',
  junction: '',
  department_code: '',
  heading_deg: '',
  camera_type: 'fixed',
  protocol: 'rtsp',
  stream_url: '',
  resolution: '1920x1080',
  fps: '15',
  anpr_enabled: true,
}

const STATUS_STYLE: Record<string, string> = {
  online: 'bg-status-online/15 text-status-online border-status-online/40',
  offline: 'bg-status-offline/15 text-status-offline border-status-offline/40',
  degraded: 'bg-amber-500/15 text-amber-400 border-amber-500/40',
  unknown: 'bg-muted text-muted-foreground border-border',
}

export default function Cameras() {
  const toast = useToast()
  const { can } = useAuth()
  const mayCreate = can(PERMISSIONS.cameraCreate)
  const mayUpdate = can(PERMISSIONS.cameraUpdate)
  const mayDelete = can(PERMISSIONS.cameraDelete)

  const [tab, setTab] = useState<Tab>('fleet')
  const [cameras, setCameras] = useState<Camera[]>([])
  const [departments, setDepartments] = useState<Department[]>([])
  const [vms, setVms] = useState<VmsInstance[]>([])
  const [adapters, setAdapters] = useState<Record<string, string>>({})
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [form, setForm] = useState({ ...EMPTY })
  const [editing, setEditing] = useState<Camera | null>(null)
  const [filter, setFilter] = useState('')

  const load = useCallback(async () => {
    try {
      const [fleet, depts, instances, adapterInfo] = await Promise.all([
        api.getCameras({ limit: '500' }),
        api.getDepartments(),
        api.getVmsInstances(),
        api.getAdapters(),
      ])
      setCameras(Array.isArray(fleet) ? fleet : (fleet as { items: Camera[] }).items ?? [])
      setDepartments(depts)
      setVms(instances)
      setAdapters(adapterInfo.adapters)
    } catch (err) {
      toast.error(err)
    } finally {
      setLoading(false)
    }
  }, [toast])

  useEffect(() => {
    void load()
  }, [load])

  const shown = useMemo(() => {
    const needle = filter.trim().toLowerCase()
    if (!needle) return cameras
    return cameras.filter(
      (c) =>
        c.camera_code.toLowerCase().includes(needle) ||
        c.name.toLowerCase().includes(needle) ||
        (c.district ?? '').toLowerCase().includes(needle),
    )
  }, [cameras, filter])

  const set = (key: keyof typeof EMPTY, value: string | boolean) =>
    setForm((current) => ({ ...current, [key]: value }))

  function startEdit(camera: Camera) {
    setEditing(camera)
    setForm({
      camera_code: camera.camera_code,
      name: camera.name,
      lat: String(camera.lat ?? ''),
      lon: String(camera.lon ?? ''),
      district: camera.district ?? '',
      city: camera.city ?? '',
      junction: camera.junction ?? '',
      department_code: '',
      heading_deg: camera.heading_deg == null ? '' : String(camera.heading_deg),
      camera_type: camera.camera_type ?? 'fixed',
      protocol: camera.protocol ?? 'rtsp',
      // Blank, always. The API never returns the URL — see Camera.has_stream —
      // so anything shown here would be an invention. Left empty it means
      // "keep whatever is configured".
      stream_url: '',
      resolution: camera.resolution ?? '',
      fps: camera.fps == null ? '' : String(camera.fps),
      anpr_enabled: camera.anpr_enabled,
    })
    setTab('fleet')
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  function cancelEdit() {
    setEditing(null)
    setForm({ ...EMPTY })
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    const lat = Number(form.lat)
    const lon = Number(form.lon)
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
      toast.error('Latitude and longitude are required')
      return
    }
    if (lat < BOUNDS.latMin || lat > BOUNDS.latMax || lon < BOUNDS.lonMin || lon > BOUNDS.lonMax) {
      toast.error(
        `(${lat}, ${lon}) is outside Gujarat. Check the values are not swapped.`,
      )
      return
    }

    const body: api.CameraInput = {
      camera_code: form.camera_code.trim().toUpperCase(),
      name: form.name.trim(),
      lat,
      lon,
      district: form.district.trim() || null,
      city: form.city.trim() || null,
      junction: form.junction.trim() || null,
      department_code: form.department_code || null,
      heading_deg: form.heading_deg === '' ? null : Number(form.heading_deg),
      camera_type: form.camera_type || null,
      protocol: form.protocol || null,
      stream_url: form.stream_url.trim() || null,
      resolution: form.resolution.trim() || null,
      fps: form.fps === '' ? null : Number(form.fps),
      anpr_enabled: form.anpr_enabled,
    }

    setBusy(true)
    try {
      if (editing) {
        // camera_code is the estate-wide identity and is not editable: renaming
        // it would silently detach every detection already recorded against it.
        const { camera_code: _ignored, ...amendable } = body
        // An empty field means "leave the configured source alone", not "clear
        // it" — the form cannot show the current value, so blank cannot mean
        // delete without silently disconnecting cameras on every other edit.
        if (!form.stream_url.trim()) delete amendable.stream_url
        await api.updateCamera(editing.id, amendable)
        toast.success(`${editing.camera_code} updated`)
      } else {
        await api.createCamera(body)
        toast.success(
          body.anpr_enabled && body.stream_url
            ? `${body.camera_code} onboarded — ANPR starts within a minute`
            : `${body.camera_code} onboarded as a registry record (no stream)`,
        )
      }
      cancelEdit()
      await load()
    } catch (err) {
      toast.error(err)
    } finally {
      setBusy(false)
    }
  }

  async function remove(camera: Camera) {
    if (
      !window.confirm(
        `Remove ${camera.camera_code} from the registry?\n\n` +
          'Its recorded detections stay, but stop being attributable to a camera.',
      )
    ) {
      return
    }
    try {
      await api.deleteCamera(camera.id)
      toast.success(`${camera.camera_code} removed`)
      await load()
    } catch (err) {
      toast.error(err)
    }
  }

  return (
    <div className="space-y-6 overflow-y-auto p-6">
      <header>
        <h1 className="text-xl font-semibold">Cameras</h1>
        <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
          Every camera here has a video source behind it. Onboard one with the
          form, or a whole estate from CSV — either way it appears on the map,
          starts being health-probed, and joins the ANPR fleet if it has a
          stream and analysis is enabled.
        </p>
      </header>

      <nav className="flex gap-1 border-b border-border" role="tablist">
        {(
          [
            ['fleet', `Fleet (${cameras.length})`],
            ['import', 'Import CSV'],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            role="tab"
            aria-selected={tab === key}
            onClick={() => setTab(key)}
            className={`-mb-px border-b-2 px-4 py-2 text-sm transition ${
              tab === key
                ? 'border-primary font-medium text-foreground'
                : 'border-transparent text-muted-foreground hover:text-foreground'
            }`}
          >
            {label}
          </button>
        ))}
      </nav>

      {tab === 'fleet' && (
        <>
          {mayCreate && (
            <form
              onSubmit={submit}
              className="space-y-4 rounded-lg border border-border bg-card p-4"
            >
              <div className="flex items-baseline justify-between">
                <h2 className="text-sm font-semibold">
                  {editing ? `Amend ${editing.camera_code}` : 'Onboard a camera'}
                </h2>
                {editing && (
                  <button
                    type="button"
                    onClick={cancelEdit}
                    className="text-xs text-muted-foreground hover:text-foreground"
                  >
                    Cancel
                  </button>
                )}
              </div>

              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <Field label="Camera code" hint="Unique, estate-wide">
                  <input
                    required
                    disabled={Boolean(editing)}
                    value={form.camera_code}
                    onChange={(e) => set('camera_code', e.target.value.toUpperCase())}
                    placeholder="CAM-00301"
                    className={inputClass}
                  />
                </Field>
                <Field label="Name" hint="What an operator will recognise">
                  <input
                    required
                    value={form.name}
                    onChange={(e) => set('name', e.target.value)}
                    placeholder="Kalawad Road Junction"
                    className={inputClass}
                  />
                </Field>
                <Field label="Latitude" hint={`${BOUNDS.latMin} – ${BOUNDS.latMax}`}>
                  <input
                    required
                    type="number"
                    step="any"
                    value={form.lat}
                    onChange={(e) => set('lat', e.target.value)}
                    placeholder="22.2863"
                    className={inputClass}
                  />
                </Field>
                <Field label="Longitude" hint={`${BOUNDS.lonMin} – ${BOUNDS.lonMax}`}>
                  <input
                    required
                    type="number"
                    step="any"
                    value={form.lon}
                    onChange={(e) => set('lon', e.target.value)}
                    placeholder="70.7728"
                    className={inputClass}
                  />
                </Field>
              </div>

              <Field
                label="Stream URL"
                hint={
                  editing
                    ? editing.has_stream
                      ? 'A source is configured. Leave blank to keep it, or type a new one to replace it — the existing URL is never sent to the browser.'
                      : 'This camera has no source, so it can never be watched or analysed. Add one here.'
                    : 'RTSP or HLS. Without one the camera is a registry record that can never be watched or analysed.'
                }
              >
                <input
                  value={form.stream_url}
                  onChange={(e) => set('stream_url', e.target.value)}
                  placeholder="rtsp://user:password@10.0.0.24:554/Streaming/Channels/101"
                  className={`${inputClass} font-mono text-xs`}
                />
              </Field>

              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <Field label="District">
                  <input
                    value={form.district}
                    onChange={(e) => set('district', e.target.value)}
                    placeholder="Rajkot"
                    className={inputClass}
                  />
                </Field>
                <Field label="City">
                  <input
                    value={form.city}
                    onChange={(e) => set('city', e.target.value)}
                    placeholder="Rajkot"
                    className={inputClass}
                  />
                </Field>
                <Field label="Department">
                  <select
                    value={form.department_code}
                    onChange={(e) => set('department_code', e.target.value)}
                    className={inputClass}
                  >
                    <option value="">Unassigned</option>
                    {departments.map((d) => (
                      <option key={d.code} value={d.code}>
                        {d.name}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field
                  label="Heading"
                  hint="Degrees the camera faces; used to reject impossible route hops"
                >
                  <input
                    type="number"
                    min={0}
                    max={359}
                    value={form.heading_deg}
                    onChange={(e) => set('heading_deg', e.target.value)}
                    placeholder="90"
                    className={inputClass}
                  />
                </Field>
              </div>

              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <Field label="Type">
                  <select
                    value={form.camera_type}
                    onChange={(e) => set('camera_type', e.target.value)}
                    className={inputClass}
                  >
                    {['fixed', 'ptz', 'anpr', 'dome', 'thermal'].map((t) => (
                      <option key={t} value={t}>
                        {t}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="Protocol">
                  <select
                    value={form.protocol}
                    onChange={(e) => set('protocol', e.target.value)}
                    className={inputClass}
                  >
                    {['rtsp', 'http', 'hls', 'onvif'].map((p) => (
                      <option key={p} value={p}>
                        {p}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="Resolution">
                  <input
                    value={form.resolution}
                    onChange={(e) => set('resolution', e.target.value)}
                    placeholder="1920x1080"
                    className={inputClass}
                  />
                </Field>
                <Field label="Frame rate">
                  <input
                    type="number"
                    min={1}
                    max={120}
                    value={form.fps}
                    onChange={(e) => set('fps', e.target.value)}
                    placeholder="15"
                    className={inputClass}
                  />
                </Field>
              </div>

              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={form.anpr_enabled}
                  onChange={(e) => set('anpr_enabled', e.target.checked)}
                  className="h-4 w-4 rounded border-border bg-background"
                />
                <span>Analyse this camera for number plates</span>
                <span className="text-xs text-muted-foreground">
                  — the worker picks it up on its next discovery pass
                </span>
              </label>

              <button
                type="submit"
                disabled={busy}
                className="rounded bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition hover:opacity-90 disabled:opacity-50"
              >
                {busy ? 'Saving…' : editing ? 'Save changes' : 'Onboard camera'}
              </button>
            </form>
          )}

          <div className="space-y-3">
            <input
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Filter by code, name or district"
              className={`${inputClass} max-w-sm`}
            />

            {loading ? (
              <SkeletonRows rows={6} />
            ) : (
              <div className="overflow-x-auto rounded-lg border border-border">
                <table className="w-full min-w-[54rem] text-sm">
                  <thead className="bg-muted/40 text-left text-xs uppercase tracking-wide text-muted-foreground">
                    <tr>
                      <th className="px-3 py-2">Code</th>
                      <th className="px-3 py-2">Name</th>
                      <th className="px-3 py-2">District</th>
                      <th className="px-3 py-2">Status</th>
                      <th className="px-3 py-2">ANPR</th>
                      <th className="px-3 py-2">Source</th>
                      <th className="px-3 py-2" />
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {shown.map((camera) => (
                      <tr key={camera.id} className="hover:bg-muted/20">
                        <td className="px-3 py-2 font-mono text-xs">{camera.camera_code}</td>
                        <td className="px-3 py-2">{camera.name}</td>
                        <td className="px-3 py-2 text-muted-foreground">
                          {camera.district ?? '—'}
                        </td>
                        <td className="px-3 py-2">
                          <span
                            className={`rounded border px-2 py-0.5 text-xs ${
                              STATUS_STYLE[camera.status] ?? STATUS_STYLE.unknown
                            }`}
                          >
                            {camera.status}
                          </span>
                        </td>
                        <td className="px-3 py-2 text-xs">
                          {camera.anpr_enabled ? (
                            <span className="text-status-online">on</span>
                          ) : (
                            <span className="text-muted-foreground">off</span>
                          )}
                        </td>
                        <td className="px-3 py-2 text-xs text-muted-foreground">
                          {camera.has_stream ? (
                            // A boolean, never the URL: it carries credentials
                            // for every federated camera on the grid.
                            <span title="Stream configured">configured</span>
                          ) : (camera.tags ?? []).includes('demo') ? (
                            // Read from the registry's own tags rather than
                            // matched against one hardcoded camera code: the
                            // demonstration fleet is three cameras now, and a
                            // code match silently mislabelled the other two as
                            // having no stream at all.
                            <span>recorded clip</span>
                          ) : (
                            <span className="text-amber-400">no stream</span>
                          )}
                        </td>
                        <td className="px-3 py-2 text-right">
                          {mayUpdate && (
                            <button
                              onClick={() => startEdit(camera)}
                              className="text-xs text-primary hover:underline"
                            >
                              Edit
                            </button>
                          )}
                          {mayDelete && (
                            <button
                              onClick={() => void remove(camera)}
                              className="ml-3 text-xs text-status-offline hover:underline"
                            >
                              Remove
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                    {shown.length === 0 && (
                      <tr>
                        <td colSpan={7} className="px-3 py-8 text-center text-muted-foreground">
                          No cameras match “{filter}”.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      )}

      {tab === 'import' && <CsvImport onDone={load} allowed={mayCreate} />}

      <FederationStrip vms={vms} adapters={adapters} />
    </div>
  )
}

const inputClass =
  'w-full rounded border border-border bg-background px-3 py-2 text-sm outline-none transition focus:border-primary'

function Field({
  label,
  hint,
  children,
}: {
  label: string
  hint?: string
  children: React.ReactNode
}) {
  return (
    <label className="block space-y-1">
      <span className="text-xs font-medium text-muted-foreground">{label}</span>
      {children}
      {hint && <span className="block text-[11px] text-muted-foreground/80">{hint}</span>}
    </label>
  )
}

/**
 * CSV onboarding.
 *
 * Validate-first is the default, and deliberately: a file of four thousand
 * cameras is not something an operator should commit to before seeing what it
 * will do. The dry run reports exactly what a real run would, writing nothing.
 *
 * Valid rows are committed even when others fail. Rejecting a whole estate
 * because three rows have a typo is how bulk onboarding becomes a thing people
 * avoid using.
 */
function CsvImport({ onDone, allowed }: { onDone: () => Promise<void>; allowed: boolean }) {
  const toast = useToast()
  const [file, setFile] = useState<File | null>(null)
  const [result, setResult] = useState<api.BulkUploadResult | null>(null)
  const [busy, setBusy] = useState(false)

  async function run(dryRun: boolean) {
    if (!file) return
    setBusy(true)
    try {
      const outcome = await api.bulkUploadCameras(file, dryRun)
      setResult(outcome)
      if (!dryRun) {
        toast.success(`${outcome.created} created, ${outcome.updated} updated`)
        await onDone()
      }
    } catch (err) {
      toast.error(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-4 rounded-lg border border-border bg-card p-4">
      <div>
        <h2 className="text-sm font-semibold">Onboard an estate from CSV</h2>
        <p className="mt-1 max-w-3xl text-xs text-muted-foreground">
          Required columns: <code className="font-mono">camera_code</code>,{' '}
          <code className="font-mono">name</code>, <code className="font-mono">lat</code>,{' '}
          <code className="font-mono">lon</code>. Everything else is optional and
          unknown columns are ignored, so a department&rsquo;s own spreadsheet
          export usually works unmodified. A sample sits in{' '}
          <code className="font-mono">data/seed/sample_cameras.csv</code>.
        </p>
      </div>

      <input
        type="file"
        accept=".csv,text/csv"
        disabled={!allowed}
        onChange={(e) => {
          setFile(e.target.files?.[0] ?? null)
          setResult(null)
        }}
        className="block w-full text-sm file:mr-3 file:rounded file:border-0 file:bg-muted file:px-3 file:py-2 file:text-sm file:text-foreground"
      />

      <div className="flex gap-2">
        <button
          onClick={() => void run(true)}
          disabled={!file || busy || !allowed}
          className="rounded border border-border px-4 py-2 text-sm transition hover:bg-muted disabled:opacity-50"
        >
          {busy ? 'Checking…' : 'Validate only'}
        </button>
        <button
          onClick={() => void run(false)}
          disabled={!file || busy || !allowed}
          className="rounded bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition hover:opacity-90 disabled:opacity-50"
        >
          Import
        </button>
      </div>

      {result && (
        <div className="space-y-3 rounded border border-border bg-background p-3">
          <div className="flex flex-wrap gap-4 text-sm">
            <Tally label="Created" value={result.created} tone="text-status-online" />
            <Tally label="Updated" value={result.updated} tone="text-primary" />
            <Tally
              label="Rejected"
              value={result.failed}
              tone={result.failed ? 'text-status-offline' : 'text-muted-foreground'}
            />
            {result.dry_run && (
              <span className="self-center text-xs text-amber-400">
                validation only — nothing was written
              </span>
            )}
          </div>

          {result.errors.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead className="text-left text-muted-foreground">
                  <tr>
                    <th className="py-1 pr-3">Row</th>
                    <th className="py-1 pr-3">Code</th>
                    <th className="py-1">Why it was rejected</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {result.errors.map((row) => (
                    <tr key={`${row.row}-${row.camera_code ?? ''}`}>
                      <td className="py-1.5 pr-3 font-mono">{row.row}</td>
                      <td className="py-1.5 pr-3 font-mono">{row.camera_code ?? '—'}</td>
                      <td className="py-1.5 text-status-offline">{row.errors.join('; ')}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function Tally({ label, value, tone }: { label: string; value: number; tone: string }) {
  return (
    <span>
      <span className={`text-lg font-semibold ${tone}`}>{value}</span>{' '}
      <span className="text-xs text-muted-foreground">{label}</span>
    </span>
  )
}

/**
 * The federation evidence, kept.
 *
 * This was a whole tab. It is real and it matters — one adapter interface
 * reaching four kinds of system is the Model 3 argument — but it is reference
 * information, not something anyone acts on, and a tab implied otherwise.
 */
function FederationStrip({
  vms,
  adapters,
}: {
  vms: VmsInstance[]
  adapters: Record<string, string>
}) {
  return (
    <section className="rounded-lg border border-border bg-card/50 p-4">
      <h2 className="text-sm font-semibold">Federation</h2>
      <p className="mt-1 max-w-3xl text-xs text-muted-foreground">
        Departmental VMS platforms stay authoritative for their own video. This
        platform holds their metadata and resolves streams on demand, so the
        central tier carries{' '}
        <strong className="text-foreground">events, not video</strong>.
      </p>

      <div className="mt-3 grid gap-4 md:grid-cols-2">
        <div>
          <h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Connected systems
          </h3>
          <ul className="mt-2 space-y-1 text-xs">
            {vms.map((instance) => (
              <li key={instance.id} className="flex justify-between gap-3">
                <span>{instance.name}</span>
                <span className="font-mono text-muted-foreground">
                  {instance.vendor} · {instance.adapter_type}
                </span>
              </li>
            ))}
            {vms.length === 0 && (
              <li className="text-muted-foreground">No federated systems registered.</li>
            )}
          </ul>
        </div>
        <div>
          <h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Available adapters
          </h3>
          <ul className="mt-2 space-y-1 text-xs">
            {Object.entries(adapters).map(([key, description]) => (
              <li key={key}>
                <span className="font-mono text-foreground">{key}</span>
                <span className="text-muted-foreground"> — {description}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </section>
  )
}
