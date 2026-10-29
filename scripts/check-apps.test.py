#!/usr/bin/env python3
"""Unit tests for check-apps.py. Stdlib only, no network, no secrets.

Run: python3 scripts/check-apps.test.py
The JWT test generates a throwaway RSA key with openssl.
That key is never committed, and never used for a real App.
Network calls are mocked, so tests never hit the GitHub API.
"""
import base64
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SPEC = importlib.util.spec_from_file_location(
    "check_apps", os.path.join(HERE, "check-apps.py")
)
check_apps = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check_apps)

EXPECTED = {"contents": "write", "issues": "write", "metadata": "read"}
LIVE_MATCH = {
    "slug": "test-app",
    "permissions": dict(EXPECTED),
    "events": [],
}


def capture(func, *args, **kwargs):
    """Run func, return (result, stdout text)."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        result = func(*args, **kwargs)
    return result, out.getvalue()


def write_manifest(directory, name="test-app", permissions=None, events=None):
    """Write one manifest file to directory, return its path."""
    if permissions is None:
        permissions = EXPECTED
    if events is None:
        events = []
    path = os.path.join(directory, name + ".json")
    data = {
        "name": name,
        "url": "https://example.com",
        "default_permissions": permissions,
        "default_events": events,
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle)
    return path


class TestHelpers(unittest.TestCase):
    """Tests for base64url and JWT generation."""

    def test_base64url_has_no_padding(self):
        """base64url output must not end with padding."""
        self.assertNotIn("=", check_apps.base64url(b"hello world"))

    def test_make_jwt_verifies_with_openssl(self):
        """A JWT from make_jwt must verify with its public key."""
        key_path = os.path.join(tempfile.gettempdir(), "check-apps-test-key.pem")
        pub_path = os.path.join(tempfile.gettempdir(), "check-apps-test-pub.pem")
        try:
            subprocess.run(
                ["openssl", "genrsa", "-out", key_path, "2048"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
            )
            subprocess.run(
                ["openssl", "rsa", "-in", key_path, "-pubout", "-out", pub_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
            )
            with open(key_path, encoding="utf-8") as handle:
                key = handle.read()
            token = check_apps.make_jwt("12345", key)
            header, payload, signature = token.split(".")
            self.assertEqual(len(token.split(".")), 3)
            decoded = json.loads(
                base64.urlsafe_b64decode(payload + "==").decode()
            )
            self.assertEqual(decoded["iss"], "12345")
            self.assertLess(decoded["iat"], decoded["exp"])
            sig_path = os.path.join(tempfile.gettempdir(), "check-apps-test.sig")
            with open(sig_path, "wb") as handle:
                handle.write(base64.urlsafe_b64decode(signature + "=="))
            result = subprocess.run(
                [
                    "openssl",
                    "dgst",
                    "-sha256",
                    "-verify",
                    pub_path,
                    "-signature",
                    sig_path,
                ],
                input=f"{header}.{payload}".encode(),
                stdout=subprocess.PIPE,
                check=False,
            )
            self.assertIn(b"Verified OK", result.stdout)
            os.unlink(sig_path)
        finally:
            for path in (key_path, pub_path):
                if os.path.exists(path):
                    os.unlink(path)


class TestCompareLive(unittest.TestCase):
    """Tests for permission and event comparison."""

    def test_pass(self):
        """Matching permissions and events must pass."""
        result, out = capture(
            check_apps.compare_live, "test-app", EXPECTED, [], LIVE_MATCH
        )
        self.assertTrue(result)
        self.assertIn("PASS", out)

    def test_missing_permission(self):
        """A missing live permission must fail."""
        live = {"permissions": {"contents": "write"}, "events": []}
        result, out = capture(
            check_apps.compare_live, "test-app", EXPECTED, [], live
        )
        self.assertFalse(result)
        self.assertIn("missing", out)

    def test_extra_permission(self):
        """An extra live permission must fail."""
        live = {
            "permissions": dict(EXPECTED, workflows="write"),
            "events": [],
        }
        result, out = capture(
            check_apps.compare_live, "test-app", EXPECTED, [], live
        )
        self.assertFalse(result)
        self.assertIn("extra", out)

    def test_changed_permission(self):
        """A changed permission level must fail."""
        live = {
            "permissions": dict(EXPECTED, contents="read"),
            "events": [],
        }
        result, out = capture(
            check_apps.compare_live, "test-app", EXPECTED, [], live
        )
        self.assertFalse(result)
        self.assertIn("contents", out)

    def test_events_mismatch(self):
        """Different events must fail, order must not matter."""
        live = {"permissions": dict(EXPECTED), "events": ["issues"]}
        result, _ = capture(
            check_apps.compare_live, "test-app", EXPECTED, [], live
        )
        self.assertFalse(result)
        result, _ = capture(
            check_apps.compare_live,
            "test-app",
            EXPECTED,
            ["issues", "pull_request"],
            {"permissions": dict(EXPECTED), "events": ["pull_request", "issues"]},
        )
        self.assertTrue(result)

    def test_null_permissions_and_events_do_not_crash(self):
        """Null live permissions and events must fail, not crash."""
        live = {"permissions": None, "events": None}
        result, out = capture(
            check_apps.compare_live, "test-app", EXPECTED, [], live
        )
        self.assertFalse(result)
        self.assertIn("FAIL", out)

    def test_non_object_live_data_do_not_crash(self):
        """Non-object live data must fail, not crash."""
        result, out = capture(
            check_apps.compare_live, "test-app", EXPECTED, [], ["not", "an", "object"]
        )
        self.assertFalse(result)
        self.assertIn("FAIL", out)


class TestLoadManifest(unittest.TestCase):
    """Tests for manifest loading and validation."""

    def setUp(self):
        """Create a temp directory for manifest files."""
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_valid_manifest(self):
        """A valid manifest must load."""
        path = write_manifest(self.tmp.name)
        loaded = check_apps.load_manifest(path)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded[0], "test-app")

    def test_missing_file_returns_none(self):
        """A missing file must return None, not raise."""
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            loaded = check_apps.load_manifest("/nonexistent/manifest.json")
        self.assertIsNone(loaded)
        self.assertIn("FAIL", out.getvalue())

    def test_invalid_json_returns_none(self):
        """Invalid JSON must return None, not raise."""
        path = os.path.join(self.tmp.name, "bad.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            loaded = check_apps.load_manifest(path)
        self.assertIsNone(loaded)

    def test_non_object_json_returns_none(self):
        """A JSON list must return None, not raise."""
        path = os.path.join(self.tmp.name, "list.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(["a", "b"], handle)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            loaded = check_apps.load_manifest(path)
        self.assertIsNone(loaded)

    def test_missing_fields_return_none(self):
        """Missing name, permissions, or events must return None."""
        for data in (
            {"url": "https://example.com"},
            {"name": "x", "default_events": []},
            {"name": "x", "default_permissions": {}},
        ):
            path = os.path.join(self.tmp.name, "field.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(data, handle)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                loaded = check_apps.load_manifest(path)
            self.assertIsNone(loaded)


class TestCheckModes(unittest.TestCase):
    """Tests for app mode and JWT mode, with mocked network."""

    def setUp(self):
        """Create a temp directory with one manifest."""
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = write_manifest(self.tmp.name)

    def test_app_mode_pass_with_mocked_fetch(self):
        """App mode must pass when mocked live data matches."""
        with mock.patch.object(
            check_apps, "fetch_json", return_value=dict(LIVE_MATCH)
        ):
            result, out = capture(
                check_apps.check_app_mode, self.path, "https://api.github.com", "tok"
            )
        self.assertTrue(result)
        self.assertIn("PASS", out)

    def test_app_mode_http_error_does_not_leak_token(self):
        """App mode must not print the token on fetch failure."""
        error = urllib.error.HTTPError(
            "https://api.github.com", 403, "Forbidden", {}, None
        )
        with mock.patch.object(
            check_apps, "fetch_json", side_effect=error
        ):
            result, out = capture(
                check_apps.check_app_mode,
                self.path,
                "https://api.github.com",
                "SECRET-TOKEN-123",
            )
        self.assertFalse(result)
        self.assertNotIn("SECRET-TOKEN-123", out)
        self.assertIn("403", out)

    def test_app_mode_bad_data_returns_false(self):
        """App mode must handle bad fetch data, not crash."""
        with mock.patch.object(
            check_apps, "fetch_json", side_effect=ValueError("bad json")
        ):
            result, _ = capture(
                check_apps.check_app_mode, self.path, "https://api.github.com", "tok"
            )
        self.assertFalse(result)

    def test_jwt_mode_pass_with_mocked_fetch(self):
        """JWT mode must pass when mocked live data matches."""
        env = {"APP_ID": "12345", "APP_PRIVATE_KEY": "fake-key"}
        with mock.patch.dict(os.environ, env):
            with mock.patch.object(
                check_apps, "make_jwt", return_value="fake.jwt.token"
            ):
                with mock.patch.object(
                    check_apps, "fetch_json", return_value=dict(LIVE_MATCH)
                ):
                    result, out = capture(
                        check_apps.check_jwt_mode,
                        self.path,
                        "https://api.github.com",
                    )
        self.assertTrue(result)
        self.assertIn("PASS", out)

    def test_jwt_mode_slug_mismatch_fails(self):
        """JWT mode must fail when the JWT App differs from the manifest."""
        live = dict(LIVE_MATCH, slug="other-app")
        env = {"APP_ID": "12345", "APP_PRIVATE_KEY": "fake-key"}
        with mock.patch.dict(os.environ, env):
            with mock.patch.object(
                check_apps, "make_jwt", return_value="fake.jwt.token"
            ):
                with mock.patch.object(
                    check_apps, "fetch_json", return_value=live
                ):
                    result, out = capture(
                        check_apps.check_jwt_mode,
                        self.path,
                        "https://api.github.com",
                    )
        self.assertFalse(result)
        self.assertIn("other-app", out)

    def test_jwt_mode_missing_env_fails(self):
        """JWT mode must fail without APP_ID and APP_PRIVATE_KEY."""
        env = {"APP_ID": "", "APP_PRIVATE_KEY": ""}
        with mock.patch.dict(os.environ, env, clear=False):
            with mock.patch.dict(os.environ, {}, clear=True):
                result, out = capture(
                    check_apps.check_jwt_mode, self.path, "https://api.github.com"
                )
        self.assertFalse(result)
        self.assertIn("APP_ID", out)


class TestMain(unittest.TestCase):
    """Tests for main entry points, without network."""

    def test_main_no_token_fails(self):
        """Main in app mode must fail without GITHUB_TOKEN."""
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(sys, "argv", ["check-apps.py"]):
                result, out = capture(check_apps.main)
        self.assertEqual(result, 1)
        self.assertIn("GITHUB_TOKEN", out)

    def test_main_missing_apps_dir_fails(self):
        """Main must fail cleanly for a missing apps directory."""
        env = {"GITHUB_TOKEN": "tok"}
        with mock.patch.dict(os.environ, env):
            with mock.patch.object(
                sys, "argv", ["check-apps.py", "--apps-dir", "/nonexistent-dir"]
            ):
                result, out = capture(check_apps.main)
        self.assertEqual(result, 1)
        self.assertIn("FAIL", out)

    def test_main_jwt_without_manifest_fails(self):
        """Main in JWT mode must fail without --manifest."""
        with mock.patch.object(sys, "argv", ["check-apps.py", "--mode", "jwt"]):
            result, out = capture(check_apps.main)
        self.assertEqual(result, 1)
        self.assertIn("--manifest", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
