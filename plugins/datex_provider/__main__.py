"""python -m plugins.datex_provider — operator CLI for DATEX providers."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path

# Ensure repo root on path when run as script
_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from plugins.datex_common.auth_types import AuthError, load_auth_material, redact_secrets
from plugins.datex_common.fetcher import fetch_publication, validate_datex_xml
from plugins.datex_common.multi_poll import poll_all, poll_provider
from plugins.datex_common.provider_spec import (
    ProviderSpec,
    ProviderSpecError,
    default_providers_dir,
    load_all_providers,
    load_provider_file,
    parse_provider_dict,
    provider_is_enabled,
)
from plugins.datex_common.publish import provider_publish_dir


def _pack_root() -> Path:
    return Path(os.environ.get("NAVI_PACK_ROOT", "/media/navi/navi-server/data"))


def _providers_dir() -> Path:
    override = os.environ.get("NAVI_DATEX_PROVIDERS_DIR", "").strip()
    if override:
        return Path(override)
    return default_providers_dir(_REPO)


def _secrets_dir() -> Path:
    return Path(os.environ.get("NAVI_DATEX_SECRETS_DIR", "/etc/navi-server/datex"))


def cmd_list(_: argparse.Namespace) -> int:
    specs = load_all_providers(_providers_dir())
    root = _pack_root()
    rows = []
    for spec in specs:
        status_path = root / "datex_state" / spec.id / "status.json"
        status = {}
        if status_path.is_file():
            try:
                status = json.loads(status_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                status = {}
        rows.append(
            {
                "id": spec.id,
                "publish_id": spec.public_id,
                "enabled": provider_is_enabled(spec),
                "auth": spec.auth_type,
                "datex_version": spec.datex_version,
                "last_ok": status.get("ok"),
                "updated_unix": status.get("updated_unix"),
                "publications": status.get("publications"),
            }
        )
    print(json.dumps({"providers": rows}, indent=2))
    return 0


def cmd_test(ns: argparse.Namespace) -> int:
    specs = {s.id: s for s in load_all_providers(_providers_dir())}
    # also allow publish_id lookup
    for s in list(specs.values()):
        specs[s.public_id] = s
    spec = specs.get(ns.id)
    if spec is None:
        print(f"FATAL: unknown provider {ns.id!r}", file=sys.stderr)
        return 2
    root = _pack_root()
    # Scratch publish for open tests when --scratch-root set
    if ns.scratch_root:
        root = Path(ns.scratch_root)
        root.mkdir(parents=True, exist_ok=True)
    try:
        auth = load_auth_material(spec)
    except AuthError as exc:
        print(f"FAIL auth: {redact_secrets(str(exc))}", file=sys.stderr)
        return 2
    code, results = poll_provider(
        spec, pack_root=root, jitter=False, force=True
    )
    for r in results:
        print(
            json.dumps(
                {
                    "publication": r.name,
                    "status": r.status,
                    "bytes": r.bytes,
                    "error": redact_secrets(r.error or "") or None,
                }
            )
        )
    return code


def cmd_poll(ns: argparse.Namespace) -> int:
    only = [ns.id] if ns.id else None
    return poll_all(
        pack_root=_pack_root() if not ns.scratch_root else Path(ns.scratch_root),
        providers_dir=_providers_dir(),
        only=only,
        jitter=not ns.force,
        force=ns.force,
    )


def _write_secrets_file(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)


def cmd_add(ns: argparse.Namespace) -> int:
    """Interactive or flag-driven provider add."""
    interactive = not ns.non_interactive
    pid = ns.id or (input("provider id: ").strip() if interactive else "")
    if not pid:
        print("FATAL: id required", file=sys.stderr)
        return 2
    display = ns.name or (
        input("display name: ").strip() if interactive else pid
    )
    country = ns.country or (input("country (ISO): ").strip() if interactive else "")
    version = ns.datex_version or (
        input("DATEX version (2.3 / 3.1): ").strip() if interactive else "3.1"
    )
    auth = ns.auth_type or (
        input("auth type [none/basic/api-key/bearer]: ").strip() if interactive else "none"
    )
    attribution = ns.attribution or (
        input("attribution text: ").strip() if interactive else ""
    )
    licence = ns.licence or (input("licence: ").strip() if interactive else "")
    publish_id = ns.publish_id or pid
    urls = list(ns.url or [])
    if not urls and interactive:
        print("Enter publication URL(s), empty line to finish:")
        while True:
            u = input("  url: ").strip()
            if not u:
                break
            urls.append(u)
    if not urls:
        print("FATAL: at least one --url required", file=sys.stderr)
        return 2

    pubs = []
    for i, url in enumerate(urls):
        name = f"publication{i+1}" if len(urls) > 1 else "feed"
        if "=" in url and url.split("=", 1)[0].isalnum():
            # name=url form
            name, _, url = url.partition("=")
        pubs.append(
            {
                "name": name,
                "url": url,
                "interval_secs": ns.interval or 300,
                "enabled": True,
                "use_conditional": True,
            }
        )

    data = {
        "id": pid,
        "publish_id": publish_id,
        "display_name": display or pid,
        "country": country,
        "attribution": attribution,
        "licence": licence,
        "datex_version": version,
        "auth_type": auth,
        "enabled": False,  # enable after successful test
        "accept_encoding_gzip": bool(ns.gzip),
        "secrets_file": str(_secrets_dir() / f"{pid}.env"),
        "publications": pubs,
    }
    if auth == "api-key":
        data["api_key_in"] = ns.api_key_in or "header"
        data["api_key_name"] = ns.api_key_name or "Ocp-Apim-Subscription-Key"

    try:
        spec = parse_provider_dict(data)
    except ProviderSpecError as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return 2

    # Secrets
    secret_lines: list[str] = [f"# DATEX secrets for {pid} — mode 0600"]
    if auth == "basic":
        user = ns.username or (
            getpass.getpass("username: ") if interactive else ""
        )
        password = ns.password
        if password is None and interactive:
            password = getpass.getpass("password: ")
            confirm = getpass.getpass("confirm password: ")
            if password != confirm:
                print("FATAL: password mismatch", file=sys.stderr)
                return 2
        if not user or password is None:
            print("FATAL: username/password required for basic auth", file=sys.stderr)
            return 2
        secret_lines += [
            f"NAV_DATEX_USERNAME={user}",
            f"NAV_DATEX_PASSWORD={password}",
        ]
    elif auth == "bearer":
        token = ns.token
        if token is None and interactive:
            token = getpass.getpass("bearer token: ")
        if not token:
            print("FATAL: token required", file=sys.stderr)
            return 2
        secret_lines.append(f"NAV_DATEX_BEARER_TOKEN={token}")
    elif auth == "api-key":
        token = ns.token
        if token is None and interactive:
            token = getpass.getpass("API key: ")
        if not token:
            print("FATAL: API key required", file=sys.stderr)
            return 2
        secret_lines.append(f"NAV_DATEX_API_KEY={token}")

    out_toml = _providers_dir() / f"{pid}.toml"
    if out_toml.exists() and not ns.force:
        print(f"FATAL: {out_toml} exists (use --force)", file=sys.stderr)
        return 2

    # Write toml without secrets
    import tomllib  # noqa: F401 — validate roundtrip via parse only

    lines = [
        f'id = "{pid}"',
        f'publish_id = "{publish_id}"',
        f'display_name = "{display}"',
        f'country = "{country}"',
        f'attribution = "{attribution}"',
        f'licence = "{licence}"',
        f'datex_version = "{version}"',
        f'auth_type = "{auth}"',
        "enabled = false",
        f'secrets_file = "{_secrets_dir() / f"{pid}.env"}"',
    ]
    if auth == "api-key":
        lines.append(f'api_key_in = "{data["api_key_in"]}"')
        lines.append(f'api_key_name = "{data["api_key_name"]}"')
    if ns.gzip:
        lines.append("accept_encoding_gzip = true")
    for pub in pubs:
        lines += [
            "",
            "[[publications]]",
            f'name = "{pub["name"]}"',
            f'url = "{pub["url"]}"',
            f'interval_secs = {pub["interval_secs"]}',
            "enabled = true",
            "use_conditional = true",
        ]
    out_toml.parent.mkdir(parents=True, exist_ok=True)
    out_toml.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {out_toml}")

    if auth != "none":
        sec = _secrets_dir() / f"{pid}.env"
        try:
            _write_secrets_file(sec, secret_lines)
            print(f"wrote secrets file {sec} (mode 0600)")
        except PermissionError:
            print(
                f"WARN: cannot write {sec} (need sudo). "
                f"Write secrets manually then re-run test.",
                file=sys.stderr,
            )

    # Test before enable
    ns_test = argparse.Namespace(
        id=pid, scratch_root=ns.scratch_root, force=True
    )
    rc = cmd_test(ns_test)
    if rc != 0 and not ns.force_enable:
        print("test failed — leaving enabled=false (pass --force-enable to override)")
        return rc
    # Enable
    text = out_toml.read_text(encoding="utf-8").replace(
        "enabled = false", "enabled = true", 1
    )
    out_toml.write_text(text, encoding="utf-8")
    print(f"enabled {pid}")
    return 0


def cmd_enable(ns: argparse.Namespace) -> int:
    path = _providers_dir() / f"{ns.id}.toml"
    if not path.is_file():
        print(f"FATAL: missing {path}", file=sys.stderr)
        return 2
    text = path.read_text(encoding="utf-8")
    if "enabled = true" in text:
        print(f"{ns.id} already enabled")
        return 0
    path.write_text(
        text.replace("enabled = false", "enabled = true", 1), encoding="utf-8"
    )
    print(f"enabled {ns.id}")
    return 0


def cmd_disable(ns: argparse.Namespace) -> int:
    path = _providers_dir() / f"{ns.id}.toml"
    if not path.is_file():
        print(f"FATAL: missing {path}", file=sys.stderr)
        return 2
    text = path.read_text(encoding="utf-8")
    path.write_text(
        text.replace("enabled = true", "enabled = false", 1), encoding="utf-8"
    )
    print(f"disabled {ns.id}")
    return 0


def cmd_remove(ns: argparse.Namespace) -> int:
    path = _providers_dir() / f"{ns.id}.toml"
    sec = _secrets_dir() / f"{ns.id}.env"
    if not ns.yes and sys.stdin.isatty():
        ans = input(f"Remove {path} and {sec}? [y/N] ").strip().lower()
        if ans not in ("y", "yes"):
            print("aborted")
            return 1
    if path.is_file():
        path.unlink()
        print(f"removed {path}")
    if sec.is_file():
        try:
            sec.unlink()
            print(f"removed {sec}")
        except PermissionError:
            print(f"WARN: need root to remove {sec}", file=sys.stderr)
    return 0


def cmd_rotate(ns: argparse.Namespace) -> int:
    spec = load_provider_file(_providers_dir() / f"{ns.id}.toml")
    if spec.auth_type == "none":
        print("no secrets for auth_type=none")
        return 0
    lines = [f"# rotated secrets for {ns.id}"]
    if spec.auth_type == "basic":
        user = getpass.getpass("username: ")
        password = getpass.getpass("password: ")
        lines += [f"NAV_DATEX_USERNAME={user}", f"NAV_DATEX_PASSWORD={password}"]
    elif spec.auth_type == "bearer":
        lines.append(f"NAV_DATEX_BEARER_TOKEN={getpass.getpass('bearer token: ')}")
    else:
        lines.append(f"NAV_DATEX_API_KEY={getpass.getpass('API key: ')}")
    sec = Path(spec.secrets_file) if spec.secrets_file else _secrets_dir() / f"{ns.id}.env"
    tmp = sec.with_suffix(".env.new")
    _write_secrets_file(tmp, lines)
    # test with temp via env overlay is hard; swap then test then keep
    backup = sec.with_suffix(".env.bak")
    if sec.is_file():
        sec.replace(backup)
    tmp.replace(sec)
    rc = cmd_test(argparse.Namespace(id=ns.id, scratch_root=None, force=True))
    if rc != 0:
        if backup.is_file():
            backup.replace(sec)
            print("test failed — restored previous secrets", file=sys.stderr)
        return rc
    if backup.is_file():
        backup.unlink()
    print("credentials rotated")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="datex-provider")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("list")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("test")
    sp.add_argument("id")
    sp.add_argument("--scratch-root", default=None)
    sp.set_defaults(func=cmd_test)

    sp = sub.add_parser("poll")
    sp.add_argument("id", nargs="?", default=None)
    sp.add_argument("--force", action="store_true")
    sp.add_argument("--scratch-root", default=None)
    sp.set_defaults(func=cmd_poll)

    sp = sub.add_parser("add")
    sp.add_argument("--id")
    sp.add_argument("--name")
    sp.add_argument("--country")
    sp.add_argument("--datex-version")
    sp.add_argument("--auth-type")
    sp.add_argument("--attribution")
    sp.add_argument("--licence")
    sp.add_argument("--publish-id")
    sp.add_argument("--url", action="append")
    sp.add_argument("--interval", type=int)
    sp.add_argument("--username")
    sp.add_argument("--password")
    sp.add_argument("--token")
    sp.add_argument("--api-key-in")
    sp.add_argument("--api-key-name")
    sp.add_argument("--gzip", action="store_true")
    sp.add_argument("--non-interactive", action="store_true")
    sp.add_argument("--force", action="store_true")
    sp.add_argument("--force-enable", action="store_true")
    sp.add_argument("--scratch-root", default=None)
    sp.set_defaults(func=cmd_add)

    sp = sub.add_parser("enable")
    sp.add_argument("id")
    sp.set_defaults(func=cmd_enable)

    sp = sub.add_parser("disable")
    sp.add_argument("id")
    sp.set_defaults(func=cmd_disable)

    sp = sub.add_parser("remove")
    sp.add_argument("id")
    sp.add_argument("-y", "--yes", action="store_true")
    sp.set_defaults(func=cmd_remove)

    sp = sub.add_parser("rotate-credentials")
    sp.add_argument("id")
    sp.set_defaults(func=cmd_rotate)

    ns = p.parse_args(argv)
    return int(ns.func(ns))


if __name__ == "__main__":
    raise SystemExit(main())
