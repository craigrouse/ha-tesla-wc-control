"""Serve the Tesla Fleet public key at the address Tesla checks during partner registration.

Tesla Fleet API registration (core tesla_fleet config flow, "domain
registration" step) requires the app's public key at
https://<domain>/.well-known/appspecific/com.tesla.3p.public-key.pem, where
<domain> is the app's allowed origin. When that origin is a URL that reaches
Home Assistant (e.g. a Nabu Casa remote UI address), Home Assistant can serve
the file itself, with no separate web host. The key pair is the one core
tesla_fleet creates at /config/tesla_fleet.key during its region step; only
the public half is served.
"""

from __future__ import annotations

from pathlib import Path

from aiohttp import web
from cryptography.hazmat.primitives import serialization

from homeassistant.components.http import HomeAssistantView
from homeassistant.helpers.http import KEY_HASS

PUBLIC_KEY_URL = "/.well-known/appspecific/com.tesla.3p.public-key.pem"
PRIVATE_KEY_FILE = "tesla_fleet.key"


def _public_pem(private_key_path: str) -> str | None:
    path = Path(private_key_path)
    if not path.exists():
        return None
    private_key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    return (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )


class TeslaPublicKeyView(HomeAssistantView):
    """Unauthenticated GET for the Tesla Fleet public key (public by design)."""

    url = PUBLIC_KEY_URL
    name = "tesla_wc_control:public_key"
    requires_auth = False

    async def get(self, request: web.Request) -> web.Response:
        """Return the public key PEM, or 404 until tesla_fleet has created the key."""
        hass = request.app[KEY_HASS]
        pem = await hass.async_add_executor_job(_public_pem, hass.config.path(PRIVATE_KEY_FILE))
        if pem is None:
            return web.Response(status=404, text="tesla_wc_control: no tesla_fleet.key yet")
        return web.Response(text=pem, content_type="application/x-pem-file")
