"""Optional OSM amateur-radio repeater extract for Navi CAT clients.

Off by default. Reads held Geofabrik/OSM.fr PBFs only (no planet download).
ODbL — no upstream permission flag or secrets.
"""

from plugins.repeater_mirror import SCHEMA_VERSION

SOURCE_ID = "osm"
ATTRIBUTION_TEXT = "© OpenStreetMap contributors"
ATTRIBUTION_URL = "https://www.openstreetmap.org/copyright"
LICENCE = "ODbL-1.0"
LICENCE_URL = "https://opendatacommons.org/licenses/odbl/1-0/"
LICENCE_NOTE = (
    "ODbL-1.0 derived database; share-alike applies. "
    "See https://opendatacommons.org/licenses/odbl/1-0/"
)

REPEATER_KEY = "communication:amateur_radio:repeater"
CALLSIGN_KEY = "communication:amateur_radio:callsign"
PREFIX = "communication:amateur_radio:repeater:"
