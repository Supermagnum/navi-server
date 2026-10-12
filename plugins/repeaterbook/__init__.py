"""Optional RepeaterBook Export API mirror for Navi CAT clients.

Off by default. Never contacts RepeaterBook without both
``NAVI_REPEATERBOOK_ENABLED=1`` and ``REPEATERBOOK_PERMISSION_CONFIRMED=1``.
Credentials stay on the server; clients only GET published files.
"""

SCHEMA_VERSION = 1
ATTRIBUTION_TEXT = "Data courtesy of RepeaterBook.com"
ATTRIBUTION_URL = "https://www.repeaterbook.com/"
