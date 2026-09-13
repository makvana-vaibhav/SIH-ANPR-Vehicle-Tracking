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
import {
  Badge,
  Button,
  Checkbox,
  Drawer,
  Field,
  Icon,
  InfoHint,
  Input,
  PageHeader,
  SegmentedControl,
  Select,
  StatusBadge,
  Table,
  Td,
  Th,
  Thead,
  Tr,
} from '@/components/ui'
import { useAuth } from '@/hooks/useAuth'
import * as api from '@/lib/api'
import { PERMISSIONS } from '@/lib/permissions'
import type { Camera, Department, VmsInstance } from '@/lib/types'

/**
 * Which slide-over is open, if any.
 *
 * These were two tabs above a permanently-expanded form, so the screen called
 * "Cameras" opened on a twenty-field onboarding form with the fleet pushed
 * entirely below the fold. Onboarding is the rare act and looking at the fleet
 * is the common one; the common one is what the page opens on now.
 */
type Drawered = 'none' | 'camera' | 'import'

/** Gujarat's bounding box, mirroring the server-side validator. Checked here
 *  too so a typo is caught while the operator is still looking at the field,
 *  rather than as a 422 after they press the button. */
const BOUNDS = { latMin: 20.0, latMax: 24.8, lonMin: 68.1, lonMax: 74.5 }

/** The values `Protocol` in app/models/enums.py actually accepts.
 *
 * This list previously read `['rtsp', 'http', 'hls', 'onvif']`. Two of those
 * are not members of the enum, so choosing either produced a 422 on save with
 * no hint that the dropdown had offered an impossible option. `http`/`hls`
 * describe how the *browser* is served video, which is the gateway's business
 * and never a property of the upstream camera. */
const PROTOCOLS = ['rtsp', 'onvif', 'vendor_api'] as const

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
  /** 'live' → pull an RTSP/ONVIF URL. 'recorded' → replay a clip from the
   *  video directory. One camera, two ways to give it pictures. */
  source_kind: 'live' as 'live' | 'recorded',
  source_file: '',
  resolution: '1920x1080',
  fps: '15',
  anpr_enabled: true,
}

export default function Cameras() {
  const toast = useToast()
  const { can } = useAuth()
  const mayCreate = can(PERMISSIONS.cameraCreate)
  const mayUpdate = can(PERMISSIONS.cameraUpdate)
  const mayDelete = can(PERMISSIONS.cameraDelete)

  const [drawer, setDrawer] = useState<Drawered>('none')
  const [cameras, setCameras] = useState<Camera[]>([])
  const [departments, setDepartments] = useState<Department[]>([])
  const [vms, setVms] = useState<VmsInstance[]>([])
  const [adapters, setAdapters] = useState<Record<string, string>>({})
  const [sourceVideos, setSourceVideos] = useState<api.SourceVideo[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [form, setForm] = useState({ ...EMPTY })
  const [editing, setEditing] = useState<Camera | null>(null)
  const [filter, setFilter] = useState('')

  const load = useCallback(async () => {
    try {
      const [fleet, depts, instances, adapterInfo, clips] = await Promise.all([
        api.getCameras({ limit: '500' }),
        api.getDepartments(),
        api.getVmsInstances(),
        api.getAdapters(),
        // An empty list is a legitimate answer — no footage on this machine —
        // so a failure here must not take the whole page down with it. The
        // form falls back to the live-URL path, which is all it ever had.
        api.getSourceVideos().catch(() => [] as api.SourceVideo[]),
      ])
      setCameras(Array.isArray(fleet) ? fleet : (fleet as { items: Camera[] }).items ?? [])
      setDepartments(depts)
      setVms(instances)
      setAdapters(adapterInfo.adapters)
      setSourceVideos(clips)
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
      // `source_file` *is* returned, so unlike the URL this can be shown and
      // edited truthfully, and a camera already replaying a clip opens on the
      // recorded tab with that clip selected.
      source_kind: camera.source_file ? 'recorded' : 'live',
      source_file: camera.source_file ?? '',
      resolution: camera.resolution ?? '',
      fps: camera.fps == null ? '' : String(camera.fps),
      anpr_enabled: camera.anpr_enabled,
    })
    setDrawer('camera')
  }

  function cancelEdit() {
    setEditing(null)
    setForm({ ...EMPTY })
    setDrawer('none')
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

    const recorded = form.source_kind === 'recorded'
    if (recorded && !form.source_file) {
      toast.error('Choose a recorded clip, or switch back to a live stream URL')
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
      // The two source kinds are exclusive in the form: picking a recorded
      // clip clears the URL and vice versa, so a camera cannot end up with a
      // live URL and a clip both claiming to be its pictures.
      stream_url: recorded ? null : form.stream_url.trim() || null,
      source_file: recorded ? form.source_file || null : null,
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
    <div className="flex h-full flex-col gap-4 overflow-hidden p-6">
      <PageHeader
        title="Cameras"
        subtitle={
          <>
            <span>{cameras.length} registered</span>
            <span className="text-muted-foreground/40">·</span>
            <span>{cameras.filter((c) => c.anpr_enabled).length} analysed for plates</span>
            <InfoHint label="What onboarding a camera does">
              A camera appears on the map, starts being health-probed, and joins
              the ANPR fleet if it has a stream and analysis is enabled. Every
              camera here has a video source behind it — a registry record
              without one can never be watched or analysed, which is a
              legitimate thing to record but should be a decision rather than an
              accident.
            </InfoHint>
          </>
        }
        actions={
          <div className="flex items-end gap-2">
            <Input
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Filter by code, name or district"
              aria-label="Filter cameras"
              className="h-8 w-64 py-0"
            />
            {mayCreate && (
              <>
                <Button variant="outline" className="h-8" onClick={() => setDrawer('import')}>
                  Import CSV
                </Button>
                <Button
                  className="flex h-8 items-center gap-1.5"
                  onClick={() => {
                    setEditing(null)
                    setForm({ ...EMPTY })
                    setDrawer('camera')
                  }}
                >
                  <Icon name="camera" size={14} />
                  Onboard camera
                </Button>
              </>
            )}
          </div>
        }
      />

      {/* ── The fleet ─────────────────────────────────────────────────
          What the page is for, and what it now opens on. */}
      <div className="min-h-0 flex-1 overflow-y-auto">
        {loading ? (
          <SkeletonRows rows={8} />
        ) : (
          <Table>
            <Thead>
              <tr>
                <Th>Code</Th>
                <Th>Name</Th>
                <Th>District</Th>
                <Th>Status</Th>
                <Th>ANPR</Th>
                <Th>Source</Th>
                <Th />
              </tr>
            </Thead>
            <tbody>
              {shown.map((camera) => (
                <Tr key={camera.id}>
                  <Td className="font-mono text-xs">{camera.camera_code}</Td>
                  <Td>{camera.name}</Td>
                  <Td className="text-muted-foreground">{camera.district ?? '—'}</Td>
                  <Td>
                    <StatusBadge status={camera.status} />
                  </Td>
                  <Td className="text-xs">
                    {camera.anpr_enabled ? (
                      <span className="text-status-online">on</span>
                    ) : (
                      <span className="text-muted-foreground">off</span>
                    )}
                  </Td>
                  <Td className="text-xs text-muted-foreground">
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
                      <Badge tone="warning">recorded clip</Badge>
                    ) : (
                      <span className="text-priority-high">no stream</span>
                    )}
                  </Td>
                  <Td className="text-right">
                    <span className="flex justify-end gap-3">
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
                          className="text-xs text-status-offline hover:underline"
                        >
                          Remove
                        </button>
                      )}
                    </span>
                  </Td>
                </Tr>
              ))}
              {shown.length === 0 && (
                <tr>
                  <Td colSpan={7} className="py-8 text-center text-muted-foreground">
                    {filter ? `No cameras match “${filter}”.` : 'No cameras registered.'}
                  </Td>
                </tr>
              )}
            </tbody>
          </Table>
        )}

        <FederationStrip vms={vms} adapters={adapters} />
      </div>

      {/* ── Onboarding / amendment ────────────────────────────────── */}
      <Drawer
        open={drawer === 'camera'}
        onClose={cancelEdit}
        title={editing ? `Amend ${editing.camera_code}` : 'Onboard a camera'}
        description={
          editing
            ? 'The camera code is the estate-wide identity and cannot be changed.'
            : 'A code, a name, a position and a video source.'
        }
      >
        {mayCreate && (
          <form onSubmit={submit} className="space-y-4">
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label="Camera code" hint="Unique, estate-wide">
                  <Input
                    required
                    disabled={Boolean(editing)}
                    value={form.camera_code}
                    onChange={(e) => set('camera_code', e.target.value.toUpperCase())}
                    placeholder="CAM-00301"
                  />
                </Field>
                <Field label="Name" hint="What an operator will recognise">
                  <Input
                    required
                    value={form.name}
                    onChange={(e) => set('name', e.target.value)}
                    placeholder="Kalawad Road Junction"
                  />
                </Field>
                <Field label="Latitude" hint={`${BOUNDS.latMin} – ${BOUNDS.latMax}`}>
                  <Input
                    required
                    type="number"
                    step="any"
                    value={form.lat}
                    onChange={(e) => set('lat', e.target.value)}
                    placeholder="22.2863"
                  />
                </Field>
                <Field label="Longitude" hint={`${BOUNDS.lonMin} – ${BOUNDS.lonMax}`}>
                  <Input
                    required
                    type="number"
                    step="any"
                    value={form.lon}
                    onChange={(e) => set('lon', e.target.value)}
                    placeholder="70.7728"
                  />
                </Field>
              </div>

              {/* Where this camera's pictures come from. A live URL is the
                  real-deployment path; a recorded clip is how footage shot on
                  a phone becomes a camera without editing .env and restarting
                  a container. */}
              <Field label="Video source">
                <SegmentedControl
                  value={form.source_kind}
                  onChange={(v) => set('source_kind', v)}
                  options={[
                    { value: 'live', label: 'Live stream URL' },
                    { value: 'recorded', label: 'Recorded video' },
                  ]}
                />
              </Field>

              {form.source_kind === 'live' ? (
                <Field
                  label="Stream URL"
                  hint={
                    editing
                      ? editing.has_stream
                        ? 'A source is configured. Leave blank to keep it, or type a new one to replace it — the existing URL is never sent to the browser.'
                        : 'This camera has no source, so it can never be watched or analysed. Add one here.'
                      : 'RTSP or ONVIF. Without one the camera is a registry record that can never be watched or analysed.'
                  }
                >
                  <Input
                    value={form.stream_url}
                    onChange={(e) => set('stream_url', e.target.value)}
                    placeholder="rtsp://10.0.0.24:554/Streaming/Channels/101"
                    className="font-mono text-xs"
                  />
                </Field>
              ) : (
                <Field
                  label="Recorded clip"
                  hint={
                    sourceVideos.length === 0
                      ? 'No clips found in the video directory. Drop an .mp4 into data/videos/ and reopen this form.'
                      : 'Replayed on a loop as this camera’s feed. Copy the file into data/videos/ and it appears here.'
                  }
                >
                  <Select
                    value={form.source_file}
                    onChange={(e) => set('source_file', e.target.value)}
                    disabled={sourceVideos.length === 0}
                  >
                    <option value="">Select a clip…</option>
                    {sourceVideos.map((v) => (
                      <option key={v.filename} value={v.filename}>
                        {v.filename} ({api.formatBytes(v.size_bytes)})
                      </option>
                    ))}
                  </Select>
                </Field>
              )}

              <div className="grid gap-3 sm:grid-cols-2">
                <Field label="District">
                  <Input
                    value={form.district}
                    onChange={(e) => set('district', e.target.value)}
                    placeholder="Rajkot"
                  />
                </Field>
                <Field label="City">
                  <Input
                    value={form.city}
                    onChange={(e) => set('city', e.target.value)}
                    placeholder="Rajkot"
                  />
                </Field>
                <Field label="Department">
                  <Select
                    value={form.department_code}
                    onChange={(e) => set('department_code', e.target.value)}
                  >
                    <option value="">Unassigned</option>
                    {departments.map((d) => (
                      <option key={d.code} value={d.code}>
                        {d.name}
                      </option>
                    ))}
                  </Select>
                </Field>
                <Field
                  label="Heading"
                  hint="Degrees the camera faces; used to reject impossible route hops"
                >
                  <Input
                    type="number"
                    min={0}
                    max={359}
                    value={form.heading_deg}
                    onChange={(e) => set('heading_deg', e.target.value)}
                    placeholder="90"
                  />
                </Field>
              </div>

              <div className="grid gap-3 sm:grid-cols-2">
                <Field label="Type">
                  <Select
                    value={form.camera_type}
                    onChange={(e) => set('camera_type', e.target.value)}
                  >
                    {['fixed', 'ptz', 'anpr', 'dome', 'thermal'].map((t) => (
                      <option key={t} value={t}>
                        {t}
                      </option>
                    ))}
                  </Select>
                </Field>
                <Field label="Protocol">
                  <Select
                    value={form.protocol}
                    onChange={(e) => set('protocol', e.target.value)}
                  >
                    {PROTOCOLS.map((p) => (
                      <option key={p} value={p}>
                        {p}
                      </option>
                    ))}
                  </Select>
                </Field>
                <Field label="Resolution">
                  <Input
                    value={form.resolution}
                    onChange={(e) => set('resolution', e.target.value)}
                    placeholder="1920x1080"
                  />
                </Field>
                <Field label="Frame rate">
                  <Input
                    type="number"
                    min={1}
                    max={120}
                    value={form.fps}
                    onChange={(e) => set('fps', e.target.value)}
                    placeholder="15"
                  />
                </Field>
              </div>

              <Checkbox
                checked={form.anpr_enabled}
                onChange={(e) => set('anpr_enabled', e.target.checked)}
                label={
                  <>
                    Analyse this camera for number plates{' '}
                    <span className="text-xs text-muted-foreground">
                      — the worker picks it up on its next discovery pass
                    </span>
                  </>
                }
              />

              <div className="flex items-center pt-1">
                <Button type="submit" disabled={busy}>
                {busy ? 'Saving…' : editing ? 'Save changes' : 'Onboard camera'}
              </Button>
              {editing && (
                <Button variant="ghost" className="ml-2" onClick={cancelEdit}>
                  Cancel
                </Button>
              )}
            </div>
          </form>
        )}
      </Drawer>

      {/* ── Bulk onboarding ──────────────────────────────────────── */}
      <Drawer
        open={drawer === 'import'}
        onClose={() => setDrawer('none')}
        title="Onboard an estate from CSV"
        description="Validated first. Nothing is written until you say so."
      >
        <CsvImport
          allowed={mayCreate}
          onDone={async () => {
            await load()
            setDrawer('none')
          }}
        />
      </Drawer>
    </div>
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
        <Button
          variant="outline"
          onClick={() => void run(true)}
          disabled={!file || busy || !allowed}
        >
          {busy ? 'Checking…' : 'Validate only'}
        </Button>
        <Button onClick={() => void run(false)} disabled={!file || busy || !allowed}>
          Import
        </Button>
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
              <span className="self-center text-xs text-priority-high">
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
    <section className="mt-4 rounded-md border border-border bg-card/50 p-4">
      <h2 className="flex items-center gap-1.5 text-sm font-semibold">
        Federation
        <InfoHint>
          Departmental VMS platforms stay authoritative for their own video.
          This platform holds their metadata and resolves streams on demand, so
          the central tier carries events, not video — which is what lets a city
          deployment run on commodity hardware.
        </InfoHint>
      </h2>

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
