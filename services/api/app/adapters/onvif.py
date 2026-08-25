"""ONVIF integration.

ONVIF is the interoperability standard most professional IP cameras implement,
and it is what makes vendor-neutral onboarding possible: a device tells you its
own capabilities, profiles and stream URIs instead of you knowing them in
advance.

Implemented directly over SOAP with httpx rather than pulling in a heavyweight
ONVIF client. We use three operations — GetDeviceInformation, GetProfiles and
GetStreamUri — and hand-rolling them keeps the runtime image small and avoids a
dependency that would need auditing for a government deployment.
"""

from __future__ import annotations

import time
from typing import Any
from xml.etree.ElementTree import Element, ParseError

import httpx

# ONVIF responses come from devices on the camera VLAN, which is untrusted
# input: a compromised or spoofed camera could answer with an entity-expansion
# bomb. defusedxml refuses those; the stdlib parser does not.
from defusedxml.ElementTree import fromstring as safe_fromstring

from app.adapters.base import (
    AdapterError,
    CameraAdapter,
    DiscoveredCamera,
    HealthProbe,
    StreamEndpoints,
)
from app.adapters.rtsp import RtspAdapter
from app.core.config import settings

_NS = {
    "s": "http://www.w3.org/2003/05/soap-envelope",
    "tds": "http://www.onvif.org/ver10/device/wsdl",
    "trt": "http://www.onvif.org/ver10/media/wsdl",
    "tt": "http://www.onvif.org/ver10/schema",
}

_SOAP_HEADERS = {"Content-Type": "application/soap+xml; charset=utf-8"}


def _envelope(body: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">'
        f"<s:Body>{body}</s:Body></s:Envelope>"
    )


class OnvifAdapter(CameraAdapter):
    """Speaks ONVIF Device and Media services over SOAP."""

    adapter_type = "onvif"

    async def _call(self, service_url: str, body: str) -> Element:
        """Issue a SOAP request and return the parsed body."""
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    service_url, content=_envelope(body), headers=_SOAP_HEADERS
                )
                response.raise_for_status()
                return safe_fromstring(response.text)
        except httpx.HTTPError as exc:
            raise AdapterError(f"ONVIF call failed: {exc}") from exc
        except ParseError as exc:
            raise AdapterError(f"ONVIF returned malformed XML: {exc}") from exc

    def _device_service(self, camera: Any = None) -> str:
        """The device service endpoint for a camera or the VMS itself."""
        host = getattr(camera, "stream_url", None) or self.base_url
        if host.startswith("rtsp://"):
            # Derive the HTTP device service from an RTSP URL's host.
            host = "http://" + host.split("://", 1)[1].split("/", 1)[0].split("@")[-1]
            host = host.rsplit(":", 1)[0] if host.count(":") > 1 else host
        return f"{host.rstrip('/')}/onvif/device_service"

    async def get_device_information(self, camera: Any = None) -> dict[str, str]:
        """Manufacturer, model, firmware and serial for the device.

        Worth recording on onboarding: a firmware version is often the first
        thing asked about when a batch of cameras starts misbehaving.
        """
        root = await self._call(
            self._device_service(camera),
            '<tds:GetDeviceInformation xmlns:tds="http://www.onvif.org/ver10/device/wsdl"/>',
        )
        response = root.find(".//tds:GetDeviceInformationResponse", _NS)
        if response is None:
            raise AdapterError("ONVIF device returned no device information")

        return {
            tag: (response.findtext(f"tds:{tag}", default="", namespaces=_NS) or "")
            for tag in ("Manufacturer", "Model", "FirmwareVersion", "SerialNumber")
        }

    async def get_profiles(self, camera: Any = None) -> list[dict[str, Any]]:
        """Enumerate media profiles.

        A camera typically publishes a high-resolution main profile and a
        low-resolution sub profile. We prefer the sub profile for AI analysis:
        at 80,000 cameras, analysing sub-streams instead of main streams is a
        very large bandwidth and GPU saving for a small accuracy cost.
        """
        media_url = (
            f"{self.base_url}/onvif/media_service"
            if self.base_url
            else self._device_service(camera)
        )
        root = await self._call(
            media_url,
            '<trt:GetProfiles xmlns:trt="http://www.onvif.org/ver10/media/wsdl"/>',
        )

        profiles: list[dict[str, Any]] = []
        for node in root.findall(".//trt:Profiles", _NS):
            resolution = node.find(".//tt:Resolution", _NS)
            profiles.append(
                {
                    "token": node.get("token", ""),
                    "name": node.findtext("tt:Name", default="", namespaces=_NS),
                    "width": int(resolution.findtext("tt:Width", "0", _NS))
                    if resolution is not None
                    else None,
                    "height": int(resolution.findtext("tt:Height", "0", _NS))
                    if resolution is not None
                    else None,
                    "fps": _int_or_none(
                        node.findtext(".//tt:FrameRateLimit", default=None, namespaces=_NS)
                    ),
                    "encoding": node.findtext(".//tt:Encoding", default="", namespaces=_NS),
                }
            )
        return profiles

    async def get_stream_uri(self, profile_token: str, camera: Any = None) -> str | None:
        """Ask the device for the RTSP URI of a given profile."""
        media_url = (
            f"{self.base_url}/onvif/media_service"
            if self.base_url
            else self._device_service(camera)
        )
        body = (
            '<trt:GetStreamUri xmlns:trt="http://www.onvif.org/ver10/media/wsdl">'
            "<trt:StreamSetup>"
            '<tt:Stream xmlns:tt="http://www.onvif.org/ver10/schema">RTP-Unicast</tt:Stream>'
            '<tt:Transport xmlns:tt="http://www.onvif.org/ver10/schema"><tt:Protocol>RTSP</tt:Protocol></tt:Transport>'
            "</trt:StreamSetup>"
            f"<trt:ProfileToken>{profile_token}</trt:ProfileToken>"
            "</trt:GetStreamUri>"
        )
        root = await self._call(media_url, body)
        return root.findtext(".//tt:Uri", default=None, namespaces=_NS)

    async def list_cameras(self) -> list[DiscoveredCamera]:
        """Enumerate this device's profiles as discoverable streams."""
        try:
            info = await self.get_device_information()
            profiles = await self.get_profiles()
        except AdapterError as exc:
            self.log.warning("onvif.discovery_failed", error=str(exc))
            return []

        label = f"{info.get('Manufacturer', '')} {info.get('Model', '')}".strip() or self.name

        discovered: list[DiscoveredCamera] = []
        for profile in profiles:
            uri = await self.get_stream_uri(profile["token"])
            width, height = profile.get("width"), profile.get("height")
            discovered.append(
                DiscoveredCamera(
                    external_id=f"{info.get('SerialNumber', self.name)}:{profile['token']}",
                    name=f"{label} — {profile['name'] or profile['token']}",
                    stream_url=uri,
                    resolution=f"{width}x{height}" if width and height else None,
                    fps=profile.get("fps"),
                    codec=profile.get("encoding") or None,
                    raw={"device": info, "profile": profile},
                )
            )
        return discovered

    async def probe_health(self, camera: Any) -> HealthProbe:
        """Probe via ONVIF, falling back to RTSP.

        A device that answers GetDeviceInformation is management-plane alive,
        which is not the same as delivering video. So a successful ONVIF
        response is followed by an actual RTSP probe — otherwise a camera with
        a dead sensor reports perfectly healthy.
        """
        started = time.perf_counter()
        try:
            await self.get_device_information(camera)
        except AdapterError as exc:
            return HealthProbe(
                reachable=False,
                latency_ms=int((time.perf_counter() - started) * 1000),
                error_code="onvif_unreachable",
                detail=str(exc),
            )

        rtsp = RtspAdapter(
            vms_id=self.vms_id,
            name=self.name,
            base_url=self.base_url,
            timeout_seconds=self.timeout_seconds,
        )
        probe = await rtsp.probe_health(camera)
        if probe.error_code == "no_stream_url":
            # Management plane responded but no stream is registered: the
            # device is up, the configuration is incomplete.
            return HealthProbe(
                reachable=True,
                latency_ms=int((time.perf_counter() - started) * 1000),
                frame_drop_pct=None,
                error_code="no_stream_configured",
                detail="ONVIF device reachable but no stream URL registered",
            )
        return probe

    async def get_stream_url(self, camera: Any) -> StreamEndpoints:
        """Republish through our gateway, as with plain RTSP."""
        code = getattr(camera, "camera_code", "unknown").lower()
        return StreamEndpoints(
            rtsp=f"rtsp://{settings.mediamtx_host}:{settings.mediamtx_rtsp_port}/{code}",
            whep=f"{settings.mediamtx_public_webrtc_url}/{code}/whep",
            hls=f"{settings.mediamtx_public_hls_url}/{code}/index.m3u8",
        )


def _int_or_none(value: str | None) -> int | None:
    try:
        return int(value) if value else None
    except ValueError:
        return None
