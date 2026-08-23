from __future__ import annotations

import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = PROJECT_ROOT / "agent-session-knowledge-rebuilder"
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from session_kb.config import output_guidance  # noqa: E402
from session_kb.model import display_path  # noqa: E402
from session_kb.release import release_check  # noqa: E402
from session_kb.sanitize import Sanitizer  # noqa: E402


class PrivacyHardeningTests(unittest.TestCase):
    def test_display_paths_redact_foreign_home_accounts_before_host_resolution(self) -> None:
        account = "foreign_machine_owner"
        foreign_unix = Path("/" + "home" + "/" + account + "/sessions/item.jsonl")
        foreign_windows = Path("C:" + "\\" + "Users" + "\\" + account + "\\sessions\\item.jsonl")

        unix_display = display_path(foreign_unix)
        windows_display = display_path(foreign_windows)

        self.assertNotIn(account, unix_display)
        self.assertNotIn(account, windows_display)
        self.assertTrue(unix_display.startswith("~"))
        self.assertTrue(windows_display.startswith("~"))

    def test_foreign_home_paths_are_redacted_across_platforms(self) -> None:
        account = "machine_owner_alpha"
        unix_macos = "/" + "Users" + "/" + account + "/work/item.md"
        unix_linux = "/" + "home" + "/" + account + "/work/item.md"
        windows = "D:" + "\\" + "Users" + "\\" + account + "\\work\\item.md"
        windows_slashes = "D:" + "/" + "Users" + "/" + account + "/work/item.md"
        sanitizer = Sanitizer()

        sanitized = sanitizer.sanitize_text("\n".join((unix_macos, unix_linux, windows, windows_slashes)))

        self.assertNotIn(account, sanitized)
        self.assertEqual(sanitized.splitlines(), ["~/work/item.md", "~/work/item.md", "~\\work\\item.md", "~/work/item.md"])
        self.assertEqual(sanitizer.stats["home_path_redactions"], 4)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "paths.txt").write_text(windows_slashes, encoding="utf-8")
            report = release_check(root)
        self.assertEqual(report["status"], "failed")
        self.assertIn("windows_home_path", {item["kind"] for item in report["findings"]})

    def test_generic_home_path_placeholders_are_preserved(self) -> None:
        examples = [
            "/" + "Users" + "/<name>/project",
            "/" + "home" + "/{username}/project",
            "C:" + "\\" + "Users" + "\\%USERNAME%\\project",
            "/" + "home" + "/${USER}/project",
        ]
        sanitizer = Sanitizer()

        sanitized = sanitizer.sanitize_text("\n".join(examples))

        self.assertEqual(sanitized.splitlines(), examples)
        self.assertEqual(sanitizer.stats["home_path_redactions"], 0)

        home_user = "/" + "home" + "/user"
        users_name = "/" + "Users" + "/name"
        windows_example = "C:" + "\\" + "Users" + "\\example-user"
        literal_accounts = sanitizer.sanitize_text(home_user + "/project\n" + users_name + "/project\n" + windows_example + "\\project")
        self.assertNotIn(home_user, literal_accounts)
        self.assertNotIn(users_name, literal_accounts)
        self.assertNotIn("example-user", literal_accounts)

    def test_common_separated_phone_numbers_are_redacted_without_treating_dates_as_contacts(self) -> None:
        sanitizer = Sanitizer()
        first_number = "(" + "415" + ") " + "555" + "-" + "1234"
        second_number = "020" + " " + "7946" + " " + "0958"
        text = sanitizer.sanitize_text(f"Call {first_number} or {second_number}; reviewed 2026-08-23.")

        self.assertEqual(text.count("[REDACTED:PHONE]"), 2)
        self.assertIn("2026-08-23", text)
        self.assertEqual(sanitizer.stats["phone_redactions"], 2)

    def test_wrapped_base64_is_removed_and_blocks_public_release(self) -> None:
        encoded = base64.b64encode(bytes(range(256)) * 50).decode("ascii")
        wrapped = "\n".join(encoded[index : index + 76] for index in range(0, len(encoded), 76))
        sanitizer = Sanitizer()

        sanitized = sanitizer.sanitize_text(wrapped)

        self.assertIn("[BINARY_REMOVED", sanitized)
        self.assertNotIn(encoded[:76], sanitized)
        self.assertEqual(sanitizer.stats["binary_payloads_removed"], 1)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "payload.txt").write_text(wrapped, encoding="utf-8")
            report = release_check(root)
        self.assertEqual(report["status"], "failed")
        self.assertIn("raw_base64", {item["kind"] for item in report["findings"]})

    def test_wrapped_base64_short_terminal_lines_are_fully_removed(self) -> None:
        payload_sizes: dict[int, int] = {}
        for size in range(3000, 5000):
            tail = len(base64.b64encode(bytes(size))) % 76
            if tail in range(4, 40, 4) and tail not in payload_sizes:
                payload_sizes[tail] = size
            if len(payload_sizes) == 9:
                break
        self.assertEqual(set(payload_sizes), set(range(4, 40, 4)))

        for tail, size in sorted(payload_sizes.items()):
            encoded = base64.b64encode(bytes((index * 29) % 256 for index in range(size))).decode("ascii")
            newline = "\r\n" if tail % 8 == 0 else "\n"
            wrapped = newline.join(encoded[index : index + 76] for index in range(0, len(encoded), 76))
            sanitizer = Sanitizer()
            sanitized = sanitizer.sanitize_text(wrapped)
            self.assertIn("[BINARY_REMOVED", sanitized, tail)
            self.assertNotIn(encoded[-tail:], sanitized, tail)
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                with (root / "payload.txt").open("w", encoding="utf-8", newline="") as handle:
                    handle.write(wrapped)
                report = release_check(root)
            self.assertEqual(report["status"], "failed", tail)
            self.assertIn("raw_base64", {item["kind"] for item in report["findings"]}, tail)

    def test_unpadded_base64url_is_removed_and_blocks_public_release(self) -> None:
        encoded = base64.urlsafe_b64encode(bytes(range(256)) * 80).decode("ascii").rstrip("=")
        sanitizer = Sanitizer()

        sanitized = sanitizer.sanitize_text(encoded)

        self.assertIn("[BINARY_REMOVED", sanitized)
        self.assertNotIn(encoded[:100], sanitized)
        self.assertEqual(sanitizer.stats["binary_payloads_removed"], 1)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "payload.txt").write_text(encoded, encoding="utf-8")
            report = release_check(root)
        self.assertEqual(report["status"], "failed")
        self.assertIn("raw_base64", {item["kind"] for item in report["findings"]})

    def test_json_style_cookie_password_and_api_key_are_redacted(self) -> None:
        cookie_value = "session" + "id=abcdef123456"
        password_value = "correct" + "-horse-battery"
        key_value = "abcdefghij" + "klmnopqrstuv"
        payload = (
            "{\"" + "cookie" + "\": \"" + cookie_value + "\", "
            + "\"" + "password" + "\": \"" + password_value + "\", "
            + "\"" + "api_key" + "\": \"" + key_value + "\"}"
        )
        sanitizer = Sanitizer()

        sanitized = sanitizer.sanitize_text(payload)

        for secret in (cookie_value, password_value, key_value):
            self.assertNotIn(secret, sanitized)
        self.assertGreaterEqual(sanitizer.stats["credential_redactions"], 3)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "config.txt").write_text(payload, encoding="utf-8")
            report = release_check(root)
        self.assertEqual(report["status"], "failed")
        self.assertIn("assigned_credential", {item["kind"] for item in report["findings"]})

    def test_compound_credential_keys_and_uri_userinfo_are_redacted(self) -> None:
        fields = [
            "client" + "_secret",
            "AWS" + "_SECRET_ACCESS_KEY",
            "auth" + "_token",
            "secret" + "_key",
            "client" + "Secret",
            "HF" + "_TOKEN",
            "GITLAB" + "_TOKEN",
            "API" + "_TOKEN",
            "TO" + "KEN",
        ]
        values = [f"synthetic-value-{index}-abcdef" for index in range(len(fields))]
        uri_value = "redis" + "://:" + "redis-pass-123456" + "@localhost:6379/0"
        payload = "\n".join(f"{key}={value}" for key, value in zip(fields, values)) + "\n" + uri_value
        sanitizer = Sanitizer()

        sanitized = sanitizer.sanitize_text(payload)

        for value in (*values, "redis-pass-123456"):
            self.assertNotIn(value, sanitized)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "credentials.txt").write_text(payload, encoding="utf-8")
            report = release_check(root)
        kinds = {item["kind"] for item in report["findings"]}
        self.assertEqual(report["status"], "failed")
        self.assertIn("assigned_credential", kinds)
        self.assertIn("uri_userinfo", kinds)

    def test_cookie_assignments_and_cookie_jar_values_are_redacted(self) -> None:
        session_name = "session" + "id"
        csrf_name = "csrf" + "token"
        session_value = "abcdef" + "1234567890"
        csrf_value = "csrf-value-" + "1234567890"
        jar_value = "jar-value-" + "1234567890"
        jar = json.dumps({"name": session_name, "value": jar_value})
        browser_value = "browser-cookie-" + "1234567890"
        browser_jar = json.dumps(
            {
                "name": "__Secure-next-auth.session-token",
                "value": browser_value,
                "domain": "example.test",
                "path": "/",
                "httpOnly": True,
                "secure": True,
            }
        )
        payload = f"{session_name}={session_value}\n{csrf_name}={csrf_value}\n{jar}\n{browser_jar}"
        sanitizer = Sanitizer()

        sanitized = sanitizer.sanitize_text(payload)
        mapped = sanitizer.sanitize_mapping({"name": session_name, "value": jar_value, "domain": "localhost"})
        browser_mapped = sanitizer.sanitize_mapping(json.loads(browser_jar))

        for value in (session_value, csrf_value, jar_value, browser_value):
            self.assertNotIn(value, sanitized)
        self.assertEqual(mapped["value"], "[REDACTED:COOKIE_VALUE]")
        self.assertEqual(browser_mapped["value"], "[REDACTED:COOKIE_VALUE]")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "cookies.txt").write_text(payload, encoding="utf-8")
            report = release_check(root)
        kinds = {item["kind"] for item in report["findings"]}
        self.assertEqual(report["status"], "failed")
        self.assertTrue({"cookie_assignment", "cookie_jar"}.issubset(kinds))

    def test_encrypted_private_key_and_small_parameterized_data_url_are_blocked(self) -> None:
        marker = "-----BEGIN " + "ENCRYPTED PRIVATE KEY-----"
        closing = "-----END " + "ENCRYPTED PRIVATE KEY-----"
        key_material = marker + "\n" + "opaque-private-material" + "\n" + closing
        encoded = base64.b64encode(bytes(range(64))).decode("ascii")
        data_url = "data:image/png;charset=utf-8;" + "base64," + encoded
        bare_data_url = "data:;" + "base64," + encoded
        sanitizer = Sanitizer()

        sanitized = sanitizer.sanitize_text(key_material + "\n" + data_url + "\n" + bare_data_url)

        self.assertNotIn("opaque-private-material", sanitized)
        self.assertNotIn(encoded, sanitized)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "material.txt").write_text(key_material + "\n" + data_url + "\n" + bare_data_url, encoding="utf-8")
            report = release_check(root)
        kinds = {item["kind"] for item in report["findings"]}
        self.assertEqual(report["status"], "failed")
        self.assertTrue({"private_key", "raw_base64"}.issubset(kinds))

    def test_short_password_and_non_basic_authorization_headers_are_blocked(self) -> None:
        short_value_line = "password" + "=" + "hunter2"
        digest_header = "Authorization" + ": Digest username=synthetic,response=abcdef123456"
        signed_header = "Authorization" + ": AWS4-HMAC-SHA256 Credential=synthetic,Signature=abcdef123456"
        payload = "\n".join((short_value_line, digest_header, signed_header))
        sanitizer = Sanitizer()

        sanitized = sanitizer.sanitize_text(payload)

        self.assertNotIn("hunter2", sanitized)
        self.assertNotIn("abcdef123456", sanitized)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "headers.txt").write_text(payload, encoding="utf-8")
            report = release_check(root)
        kinds = {item["kind"] for item in report["findings"]}
        self.assertEqual(report["status"], "failed")
        self.assertTrue({"assigned_credential", "authorization"}.issubset(kinds))

    def test_release_deny_terms_check_hex_text_and_relative_paths(self) -> None:
        hex_term = "deadbeef" + "cafebabefeedface01234567"
        path_term = "private" + "-customer-codename"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / (path_term + ".md")).write_text("safe body\n", encoding="utf-8")
            (root / "hex.md").write_text(hex_term + "\n", encoding="utf-8")
            report = release_check(root, (hex_term, path_term))

        findings = {(item["kind"], item.get("location")) for item in report["findings"]}
        self.assertEqual(report["status"], "failed")
        self.assertIn(("deny-term-1", None), findings)
        self.assertIn(("deny-term-2", "path"), findings)

    def test_ipv4_and_ipv6_non_global_addresses_are_redacted(self) -> None:
        private_v4 = "192" + ".168.10.20"
        unique_local_v6 = "fc00" + ":" + ":1"
        link_local_v6 = "fe80" + ":" + ":1"
        loopback_v6 = ":" + ":1"
        public_v6 = "2001:4860:4860" + "::8888"
        sanitizer = Sanitizer()

        sanitized = sanitizer.sanitize_text(" ".join((private_v4, unique_local_v6, link_local_v6, loopback_v6, public_v6)))

        self.assertEqual(sanitized.count("[REDACTED:PRIVATE_IP]"), 4)
        self.assertIn(public_v6, sanitized)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "network.txt").write_text(unique_local_v6, encoding="utf-8")
            report = release_check(root)
        self.assertEqual(report["status"], "failed")
        self.assertIn("private_ip", {item["kind"] for item in report["findings"]})

    def test_release_check_rejects_generated_cache_binary_and_unknown_types(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "LICENSE").write_text("safe license text\n", encoding="utf-8")
            cache = root / "package" / "__pycache__"
            cache.mkdir(parents=True)
            (cache / "module.pyc").write_bytes(bytes((0x42, 0x0D, 0x0D, 0x0A, 0xFF, 0xFE)))
            (root / "disguised.md").write_bytes(bytes((0xFF, 0xFE, 0xFD)))
            (root / "unknown.opaque").write_text("valid UTF-8 but unapproved type\n", encoding="utf-8")
            (root / "unknown-extensionless").write_text("valid UTF-8 but unapproved name\n", encoding="utf-8")

            report = release_check(root)

        self.assertEqual(report["status"], "failed")
        finding_pairs = {(item["kind"], item["path"]) for item in report["findings"]}
        self.assertIn(("generated-directory", "package/__pycache__"), finding_pairs)
        self.assertIn(("binary-or-unapproved-file-type", "package/__pycache__/module.pyc"), finding_pairs)
        self.assertIn(("binary-or-unapproved-file-type", "disguised.md"), finding_pairs)
        self.assertIn(("binary-or-unapproved-file-type", "unknown.opaque"), finding_pairs)
        self.assertIn(("binary-or-unapproved-file-type", "unknown-extensionless"), finding_pairs)
        self.assertEqual(report["files_scanned"], 1)

    def test_output_guidance_requires_storage_risk_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            guidance = output_guidance("portable-private-kb", cwd=Path(temporary))

        self.assertEqual(guidance["guide_version"], 2)
        self.assertTrue(guidance["privacy_confirmation"]["required"])
        check_ids = {item["id"] for item in guidance["privacy_confirmation"]["checks"]}
        self.assertEqual(check_ids, {"cloud-sync", "sharing", "backup-retention", "encryption-and-access"})
        self.assertIn("explicitly confirmed", guidance["selection_rule"])
        self.assertIn("cloud-synced", guidance["options"][0]["tradeoff"])


if __name__ == "__main__":
    unittest.main()
