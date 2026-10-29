#!/usr/bin/env python3
"""Check GitHub App permissions against manifest files.

Usage, local, with a user token:
  GITHUB_TOKEN=<token> python3 scripts/check-apps.py

Usage, CI, with an App private key:
  APP_ID=<id> APP_PRIVATE_KEY=<key> python3 scripts/check-apps.py --mode jwt \
    --manifest apps/infralicious-safe-settings.json

App mode reads every JSON file in the apps directory.
JWT mode reads one manifest file, for the App that owns the key.
Both modes fetch live permissions, and compare them to the manifest.
Both modes compare live events to default_events, when events exist.
The check never applies a change, it only reports drift.
The check needs a token, because both Apps are private.
GITHUB_TOKEN cannot read private Apps, it returns 403 in CI.
JWT mode uses openssl, which is present on Ubuntu runners.
The check never prints a token, a key, a JWT, or a secret.
Exit 0 when every App matches its manifest.
Exit 1 when any App drifts, or any fetch fails.
"""
import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API_VERSION = "2022-11-28"


def base64url(data):
    """Encode bytes as base64url, with no padding."""
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def make_jwt(app_id, private_key):
    """Make a short GitHub App JWT, signed with openssl."""
    header = base64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    now = int(time.time())
    payload = base64url(
        json.dumps({"iat": now - 60, "exp": now + 540, "iss": app_id}).encode()
    )
    signing_input = header + "." + payload
    key_file = tempfile.NamedTemporaryFile(mode="w", delete=False)
    key_path = key_file.name
    try:
        key_file.write(private_key)
        key_file.close()
        os.chmod(key_path, 0o600)
        result = subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", key_path],
            input=signing_input.encode(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError("openssl sign failed")
        signature = base64url(result.stdout)
    finally:
        if os.path.exists(key_path):
            os.unlink(key_path)
    return signing_input + "." + signature


def fetch_json(url, token):
    """Fetch one JSON object from the GitHub API, with a Bearer token."""
    request = urllib.request.Request(url)
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("X-GitHub-Api-Version", API_VERSION)
    request.add_header("User-Agent", "infralicious-app-drift-check")
    request.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def compare_live(slug, expected_permissions, expected_events, live):
    """Compare live permissions and events to a manifest. Return True on match."""
    if not isinstance(live, dict):
        print(f"FAIL: {slug} live data is not an object")
        return False
    live_permissions = live.get("permissions") or {}
    if not isinstance(live_permissions, dict):
        live_permissions = {}
    live_events = live.get("events") or []
    if not isinstance(live_events, list):
        live_events = []
    failed = False
    missing = sorted(set(expected_permissions) - set(live_permissions))
    extra = sorted(set(live_permissions) - set(expected_permissions))
    changed = sorted(
        key
        for key in expected_permissions
        if key in live_permissions and live_permissions[key] != expected_permissions[key]
    )
    if missing:
        print(f"FAIL: {slug} missing permissions: {', '.join(missing)}")
        failed = True
    if extra:
        print(f"FAIL: {slug} extra permissions: {', '.join(extra)}")
        failed = True
    for key in changed:
        print(
            f"FAIL: {slug} permission {key} is {live_permissions[key]}, "
            f"manifest says {expected_permissions[key]}"
        )
        failed = True
    if sorted(live_events) != sorted(expected_events):
        print(
            f"FAIL: {slug} events are {sorted(live_events)}, "
            f"manifest says {sorted(expected_events)}"
        )
        failed = True
    if failed:
        return False
    print(f"PASS: {slug}")
    return True


def load_manifest(path):
    """Load one manifest file, and validate its permission fields."""
    try:
        with open(path, encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, ValueError) as error:
        print(f"FAIL: {path} cannot be read as JSON: {error}")
        return None
    if not isinstance(manifest, dict):
        print(f"FAIL: {path} is not a JSON object")
        return None
    slug = manifest.get("name")
    expected_permissions = manifest.get("default_permissions")
    expected_events = manifest.get("default_events")
    if not slug:
        print(f"FAIL: {path} has no name field")
        return None
    if not isinstance(expected_permissions, dict):
        print(f"FAIL: {slug} has no default_permissions object")
        return None
    if not isinstance(expected_events, list):
        print(f"FAIL: {slug} has no default_events list")
        return None
    return slug, expected_permissions, expected_events


def check_app_mode(path, api_base, token):
    """Check one manifest, by App slug, with a user token."""
    loaded = load_manifest(path)
    if loaded is None:
        return False
    slug, expected_permissions, expected_events = loaded
    try:
        live = fetch_json(api_base.rstrip("/") + "/apps/" + slug, token)
    except urllib.error.HTTPError as error:
        print(f"FAIL: {slug} fetch failed with HTTP {error.code}")
        return False
    except urllib.error.URLError as error:
        print(f"FAIL: {slug} fetch failed: {error.reason}")
        return False
    except ValueError as error:
        print(f"FAIL: {slug} fetch returned bad data: {error}")
        return False
    return compare_live(slug, expected_permissions, expected_events, live)


def check_jwt_mode(path, api_base):
    """Check one manifest, with a JWT from its own private key."""
    loaded = load_manifest(path)
    if loaded is None:
        return False
    slug, expected_permissions, expected_events = loaded
    app_id = os.environ.get("APP_ID")
    private_key = os.environ.get("APP_PRIVATE_KEY")
    if not app_id or not private_key:
        print("FAIL: APP_ID and APP_PRIVATE_KEY are both required in JWT mode")
        return False
    try:
        jwt = make_jwt(app_id, private_key)
        live = fetch_json(api_base.rstrip("/") + "/app", jwt)
    except urllib.error.HTTPError as error:
        print(f"FAIL: {slug} fetch failed with HTTP {error.code}")
        return False
    except urllib.error.URLError as error:
        print(f"FAIL: {slug} fetch failed: {error.reason}")
        return False
    except ValueError as error:
        print(f"FAIL: {slug} fetch returned bad data: {error}")
        return False
    except (RuntimeError, OSError) as error:
        print(f"FAIL: {slug} JWT failed: {error}")
        return False
    live_slug = live.get("slug")
    if live_slug and live_slug != slug:
        print(f"FAIL: JWT is for {live_slug}, manifest is for {slug}")
        return False
    return compare_live(slug, expected_permissions, expected_events, live)


def main():
    """Run the drift check, in app mode or in JWT mode."""
    parser = argparse.ArgumentParser(description="Check GitHub App drift.")
    parser.add_argument(
        "--mode",
        choices=["app", "jwt"],
        default="app",
        help="app: user token, by slug. jwt: App private key, own App only.",
    )
    parser.add_argument(
        "--apps-dir",
        default=os.path.join(ROOT, "apps"),
        help="Directory with App manifest JSON files, app mode only.",
    )
    parser.add_argument(
        "--manifest",
        default=None,
        help="Single manifest file, JWT mode only.",
    )
    parser.add_argument(
        "--api-base",
        default="https://api.github.com",
        help="GitHub API base URL.",
    )
    args = parser.parse_args()
    if args.mode == "jwt":
        if not args.manifest:
            print("FAIL: --manifest is required in JWT mode")
            return 1
        ok = check_jwt_mode(args.manifest, args.api_base)
        if not ok:
            print("Drift found. Fix the live App, or fix the manifest, then re-run.")
            return 1
        print("App matches its manifest.")
        return 0
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("FAIL: GITHUB_TOKEN is not set, private Apps need a token")
        return 1
    try:
        paths = sorted(
            path for path in os.listdir(args.apps_dir) if path.endswith(".json")
        )
    except OSError as error:
        print(f"FAIL: cannot list {args.apps_dir}: {error}")
        return 1
    if not paths:
        print(f"FAIL: no manifest files in {args.apps_dir}")
        return 1
    ok = True
    for name in paths:
        path = os.path.join(args.apps_dir, name)
        if not check_app_mode(path, args.api_base, token):
            ok = False
    if not ok:
        print("Drift found. Fix the live App, or fix the manifest, then re-run.")
        return 1
    print(f"All {len(paths)} Apps match their manifests.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
