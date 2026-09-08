/**
 * Per-camera event filtering.
 *
 * The failure this guards is the worst one this screen can have: showing one
 * camera's plate reads under another camera's name. A federated camera that is
 * unreachable reads nothing, so anything appearing beneath it is invented —
 * and a judge looking at a dead feed with plates scrolling past it would be
 * looking at a lie the platform told itself.
 */

import { act, renderHook } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { EventStreamProvider, useCameraEvents } from '@/hooks/useEventStream'
import { setTokens } from '@/lib/api'
import type { LiveVehicleEvent } from '@/lib/types'

/**
 * A WebSocket stand-in that hands the test its own `onmessage`, so events can
 * be delivered exactly when the test wants them.
 */
class FakeSocket {
  static last: FakeSocket | null = null
  onopen: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  onerror: (() => void) | null = null
  onclose: (() => void) | null = null

  constructor(readonly url: string) {
    FakeSocket.last = this
    // The provider sets its handlers synchronously after constructing.
    queueMicrotask(() => this.onopen?.())
  }

  close(): void {
    this.onclose?.()
  }

  deliver(payload: unknown): void {
    this.onmessage?.({ data: JSON.stringify(payload) })
  }
}

function reading(cameraId: string, plate: string, vehicleId = 1): LiveVehicleEvent {
  return {
    event: 'vehicle.observed',
    event_time: new Date().toISOString(),
    source: { camera_id: cameraId },
    vehicle: {
      vehicle_id: vehicleId,
      track_ids: [vehicleId],
      type: 'car',
      confidence: 0.9,
      bbox: { x1: 0, y1: 0, x2: 10, y2: 10, w: 10, h: 10 },
      first_seen_s: 0,
      last_seen_s: 1,
    },
    plate: {
      text: plate,
      confidence: 0.95,
      readable: true,
      grammar_valid: true,
      ambiguous: false,
      corrected_from: null,
      format: 'standard',
    },
    frame: { width: 1920, height: 1080 },
  }
}

const wrapper = ({ children }: { children: ReactNode }) => (
  <EventStreamProvider>{children}</EventStreamProvider>
)

describe('useCameraEvents', () => {
  beforeEach(() => {
    vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket)
    setTokens('test-access-token', null)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    setTokens(null, null)
    FakeSocket.last = null
  })

  it('keeps only the selected camera’s reads', async () => {
    const { result } = renderHook(() => useCameraEvents('CAM-DEMO'), { wrapper })
    await act(async () => {})

    act(() => {
      FakeSocket.last?.deliver(reading('CAM-DEMO', 'GJ03AB1234', 1))
      FakeSocket.last?.deliver(reading('SBX-00001', 'GJ05XY9999', 2))
    })

    expect(result.current).toHaveLength(1)
    expect(result.current[0]?.plate.text).toBe('GJ03AB1234')
  })

  it('matches the camera code case-insensitively', async () => {
    // The worker publishes the registry's code; nothing guarantees its case
    // survives every hop, and a case mismatch would silently show nothing.
    const { result } = renderHook(() => useCameraEvents('CAM-DEMO'), { wrapper })
    await act(async () => {})

    act(() => {
      FakeSocket.last?.deliver(reading('cam-demo', 'GJ03AB1234'))
    })

    expect(result.current).toHaveLength(1)
  })

  it('drops the previous camera’s reads when the selection changes', async () => {
    const { result, rerender } = renderHook(
      ({ code }: { code: string }) => useCameraEvents(code),
      { wrapper, initialProps: { code: 'CAM-DEMO' } },
    )
    await act(async () => {})

    act(() => {
      FakeSocket.last?.deliver(reading('CAM-DEMO', 'GJ03AB1234'))
    })
    expect(result.current).toHaveLength(1)

    // Switching to an unreachable federated camera must leave nothing behind:
    // it reads no plates, so it must display none.
    rerender({ code: 'SBX-00001' })
    expect(result.current).toHaveLength(0)
  })

  it('shows nothing when no camera is selected', async () => {
    const { result } = renderHook(() => useCameraEvents(null), { wrapper })
    await act(async () => {})

    act(() => {
      FakeSocket.last?.deliver(reading('CAM-DEMO', 'GJ03AB1234'))
    })

    expect(result.current).toHaveLength(0)
  })

  it('ignores a vehicle whose plate could not be read', async () => {
    const { result } = renderHook(() => useCameraEvents('CAM-DEMO'), { wrapper })
    await act(async () => {})

    const unreadable = reading('CAM-DEMO', '')
    unreadable.plate.readable = false

    act(() => {
      FakeSocket.last?.deliver(unreadable)
    })

    expect(result.current).toHaveLength(0)
  })
})
