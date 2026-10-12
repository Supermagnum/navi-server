"""Optional RepeaterBook Export API mirror for Navi CAT clients.

Off by default. Never contacts RepeaterBook without both
``NAVI_REPEATERBOOK_ENABLED=1`` and ``REPEATERBOOK_PERMISSION_CONFIRMED=1``.
Credentials stay on the server; clients only GET published files.
"""

from plugins.repeater_mirror import SCHEMA_VERSION

SOURCE_ID = "repeaterbook"
ATTRIBUTION_TEXT = "Data courtesy of RepeaterBook.com"
ATTRIBUTION_URL = "https://www.repeaterbook.com/"
LICENCE_NOTE = (
    "Use subject to RepeaterBook written permission for this host; "
    "see docs/repeater-mirror.md."
)
