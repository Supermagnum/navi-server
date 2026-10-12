"""Optional RadioID.net DMR repeater mirror for Navi CAT clients.

Off by default. Never contacts RadioID without both
``NAVI_RADIOID_ENABLED=1`` and ``RADIOID_PERMISSION_CONFIRMED=1``.
"""

from plugins.repeater_mirror import SCHEMA_VERSION

SOURCE_ID = "radioid"
ATTRIBUTION_TEXT = "Data courtesy of RadioID.net"
ATTRIBUTION_URL = "https://www.radioid.net/"
LICENCE_NOTE = (
    "Provisional attribution until written approval specifies otherwise. "
    "Redistribution requires RadioID written permission; see docs/repeater-mirror.md."
)

# Join key: API ``locator`` == map.json ``locator`` (map ``id`` equals locator).
# Do not join on API ``identity_id`` (different namespace).
JOIN_KEY = "locator"
