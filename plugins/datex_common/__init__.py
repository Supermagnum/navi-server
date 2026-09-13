"""Shared helpers for navi-server DATEX provider plugins.

Provider packages live under plugins/datex_<id>/ and publish under
data/published/datex/<id>/. Credentials never go under DocumentRoot.
"""

from .enable import is_truthy, provider_enabled
from .publish import atomic_write_bytes, atomic_write_text, provider_publish_dir, write_source_json
from .providers_index import PROVIDER_ID_NPRA, rebuild_providers_index
from .secrets import AuthError, Credentials, load_basic_credentials

__all__ = [
    "AuthError",
    "Credentials",
    "PROVIDER_ID_NPRA",
    "atomic_write_bytes",
    "atomic_write_text",
    "is_truthy",
    "load_basic_credentials",
    "provider_enabled",
    "provider_publish_dir",
    "rebuild_providers_index",
    "write_source_json",
]
