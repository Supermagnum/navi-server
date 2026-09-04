"""DATEX II v3.1 NPRA snapshot redistributor for navi-server.

Off by default. Enable only via NAVI_DATEX_NPRA_ENABLED=1 after credentials
exist. Clients never see NPRA credentials; they only GET cached files under
data/published/datex/.

UNVERIFIED against live NPRA (requires real credentials before production):
  - exact 401/403 response body shape
  - Last-Modified header presence/casing on successful GET
  - XML namespace/root element per endpoint
  - whether any unauthenticated GET returns anything other than 401
"""

from __future__ import annotations

__all__ = [
    "AuthError",
    "Config",
    "DEFAULT_ENDPOINTS",
    "FetchResult",
    "auth_failure_message",
    "is_enabled",
    "load_config",
    "load_credentials",
    "poll_once",
]
