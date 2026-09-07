"""Adapter registry.

Resolves a ``vms_instances`` row to the class that knows how to talk to it.
This one lookup is what lets the health monitor, the stream gateway and the AI
pipeline treat a Milestone VMS, a bare ONVIF camera and the challenge sandbox
identically.

Adding a vendor: write the class, add one line to ``_ADAPTERS``. Nothing else
in the platform changes.
"""

from __future__ import annotations

from typing import Any

from app.adapters.base import (
    AdapterError,
    CameraAdapter,
    DiscoveredCamera,
    HealthProbe,
    StreamEndpoints,
)
from app.adapters.onvif import OnvifAdapter
from app.adapters.rtsp import RtspAdapter
from app.adapters.sandbox import HostedGridAdapter
from app.adapters.simulated import SimulatedVmsAdapter
from app.adapters.vendor import VendorVmsAdapter
from app.core.logging import get_logger
from app.models.enums import AdapterType

log = get_logger("api.adapters")

_ADAPTERS: dict[str, type[CameraAdapter]] = {
    AdapterType.RTSP.value: RtspAdapter,
    AdapterType.ONVIF.value: OnvifAdapter,
    AdapterType.VENDOR_API.value: VendorVmsAdapter,
    AdapterType.HOSTED_GRID.value: HostedGridAdapter,
    AdapterType.SIMULATED.value: SimulatedVmsAdapter,
}


def available_adapters() -> dict[str, str]:
    """Registered adapter types, for the admin UI and diagnostics."""
    return {key: cls.__name__ for key, cls in _ADAPTERS.items()}


def adapter_for(vms: Any | None, *, timeout_seconds: float = 5.0) -> CameraAdapter:
    """Build the adapter for a VMS instance.

    A camera with no VMS, or one whose ``adapter_type`` we do not recognise,
    falls back to direct RTSP. Falling back is deliberate: an unrecognised
    vendor string should degrade to the lowest common denominator that usually
    works, not take the camera off the map entirely.
    """
    if vms is None:
        return RtspAdapter(name="direct-rtsp", timeout_seconds=timeout_seconds)

    adapter_type = getattr(vms, "adapter_type", "") or ""
    adapter_class = _ADAPTERS.get(adapter_type)

    if adapter_class is None:
        log.warning(
            "adapters.unknown_type",
            adapter_type=adapter_type,
            vms=getattr(vms, "name", "?"),
            detail="Falling back to direct RTSP",
        )
        adapter_class = RtspAdapter

    return adapter_class(
        vms_id=getattr(vms, "id", None),
        name=getattr(vms, "name", "unnamed"),
        base_url=getattr(vms, "base_url", None),
        credentials_ref=getattr(vms, "credentials_ref", None),
        timeout_seconds=timeout_seconds,
    )


__all__ = [
    "AdapterError",
    "CameraAdapter",
    "DiscoveredCamera",
    "HealthProbe",
    "HostedGridAdapter",
    "OnvifAdapter",
    "RtspAdapter",
    "SimulatedVmsAdapter",
    "StreamEndpoints",
    "VendorVmsAdapter",
    "adapter_for",
    "available_adapters",
]
