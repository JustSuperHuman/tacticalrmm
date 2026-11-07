import hashlib
import io
import json
import os
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from fetch_installer import fetch
from manifest import combine, write_record
from verify_windows7 import verify


class Response:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status_code = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def iter_content(self, size):
        for start in range(0, len(self.payload), size):
            yield self.payload[start : start + size]


class FetchTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(
            os.environ,
            {
                "SUPPORT_MESH_URL": "https://mesh.example.com",
                "SUPPORT_MESH_GROUP_ID": "testgroup",
            },
            clear=True,
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)

    @patch("fetch_installer.requests.get")
    def test_windows_configuration_is_kept_outside_the_signable_binary(self, get):
        config = b"MeshID=0x123\nServerID=456\nMeshServer=wss://mesh.example.com/agent.ashx\nInstallFlags=1\n"
        get.side_effect = [Response(b"MZsynthetic-agent"), Response(config)]
        path = Path(self.temporary.name) / "MeshAgent.exe"
        fetch("windows", path)
        self.assertEqual(path.read_bytes(), b"MZsynthetic-agent")
        self.assertEqual(path.with_suffix(".msh").read_bytes(), config)
        self.assertNotIn("meshid", get.call_args_list[0].kwargs["params"])
        self.assertEqual(get.call_args_list[1].kwargs["params"]["installflags"], 1)
        self.assertFalse(get.call_args_list[0].kwargs["allow_redirects"])

    @patch("fetch_installer.requests.get")
    def test_mac_archive_never_extracts_members_to_supplied_paths(self, get):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("../../outside.pkg", b"xar!synthetic-pkg")
        get.return_value = Response(stream.getvalue())
        destination = Path(self.temporary.name) / "support.pkg"
        fetch("macos", destination)
        self.assertEqual(destination.read_bytes(), b"xar!synthetic-pkg")
        self.assertEqual(list(Path(self.temporary.name).iterdir()), [destination])
        self.assertEqual(get.call_args.kwargs["params"]["id"], 10005)
        self.assertEqual(get.call_args.kwargs["params"]["installflags"], 0)

    @patch("fetch_installer.requests.get")
    def test_windows7_uses_x86_and_keeps_run_only_enrollment_after_signing(self, get):
        config = b"MeshID=0x123\nServerID=456\nMeshServer=wss://mesh.example.com/agent.ashx\nInstallFlags=1\n"
        get.side_effect = [Response(b"MZsynthetic-x86-agent"), Response(config)]
        path = Path(self.temporary.name) / "MeshAgent.exe"
        fetch("windows7", path)
        self.assertEqual(get.call_args_list[0].kwargs["params"]["id"], 3)
        self.assertNotIn("meshid", get.call_args_list[0].kwargs["params"])
        self.assertEqual(path.with_suffix(".msh").read_bytes(), config)

    @patch("fetch_installer.requests.get")
    def test_authentication_redirects_are_not_packaged(self, get):
        get.return_value = Response(b"<html>Sign in</html>", 302)
        with self.assertRaisesRegex(ValueError, "HTTP 302"):
            fetch("windows", Path(self.temporary.name) / "tool.exe")

    @patch("fetch_installer.requests.get")
    def test_unsigned_response_formats_and_missing_group_settings_fail(self, get):
        get.return_value = Response(b"<html>Error</html>")
        with self.assertRaisesRegex(ValueError, "Windows executable"):
            fetch("windows", Path(self.temporary.name) / "tool.exe")
        get.side_effect = [Response(b"MZagent"), Response(b"MeshID=only-one-field\n")]
        with self.assertRaisesRegex(ValueError, "configured run-only"):
            fetch("windows", Path(self.temporary.name) / "tool.exe")

    @patch("fetch_installer.requests.get")
    def test_credentials_in_urls_and_plain_http_are_rejected(self, get):
        for base in (
            "http://mesh.example.com",
            "https://user:secret@mesh.example.com",
            "https://mesh.example.com/?token=secret",
        ):
            os.environ["SUPPORT_MESH_URL"] = base
            with self.assertRaises(ValueError):
                fetch("windows", Path(self.temporary.name) / "tool.exe")
        get.assert_not_called()


class ManifestTests(unittest.TestCase):
    def test_generated_mac_and_windows_can_be_released_independently(self):
        for platform, suffix in (("macos", "pkg"), ("windows", "msi")):
            with self.subTest(
                platform=platform
            ), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / f"TacticalSupport-{platform}.{suffix}").write_bytes(b"test")
                write_record(root, platform, "1.0.0", "Synthetic CI identity")
                combine(root)
                manifest = json.loads((root / "manifest.json").read_text())
                self.assertEqual(
                    [i["platform"] for i in manifest["installers"]], [platform]
                )

    def test_digest_describes_final_bytes_and_combination_detects_modification(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for platform, suffix, data in (
                ("windows", "msi", b"MSI-test"),
                ("macos", "pkg", b"xar!test"),
            ):
                path = root / f"TacticalSupport-{platform}.{suffix}"
                path.write_bytes(data)
                write_record(root, platform, "1.0.0", "Synthetic CI identity")
            combine(root)
            manifest = json.loads((root / "manifest.json").read_text())
            self.assertEqual(
                manifest["installers"][0]["sha256"],
                hashlib.sha256(b"MSI-test").hexdigest(),
            )
            self.assertTrue(manifest["installers"][1]["signature"]["notarized"])
            (root / "TacticalSupport-windows.msi").write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "changed after signing"):
                combine(root)


class Windows7CompatibilityTests(unittest.TestCase):
    def candidate(
        self, root, machine=0x14C, minimum=(6, 0), function="GetSystemTimeAsFileTime"
    ):
        data = bytearray(1024)
        data[:2] = b"MZ"
        struct.pack_into("<I", data, 60, 64)
        data[64:68] = b"PE\0\0"
        struct.pack_into("<HH", data, 68, machine, 1)
        struct.pack_into("<H", data, 84, 224)
        struct.pack_into("<H", data, 88, 0x10B)
        struct.pack_into("<HH", data, 128, *minimum)
        struct.pack_into("<HH", data, 136, *minimum)
        struct.pack_into("<II", data, 192, 0x1000, 40)
        struct.pack_into("<IIII", data, 320, 512, 0x1000, 512, 512)
        struct.pack_into("<IIIII", data, 512, 0x1080, 0, 0, 0x10C0, 0x1080)
        struct.pack_into("<I", data, 640, 0x1100)
        data[704:717] = b"KERNEL32.dll\0"
        encoded = function.encode() + b"\0"
        data[770 : 770 + len(encoded)] = encoded
        path = Path(root) / "agent.exe"
        path.write_bytes(data)
        return path

    def test_x86_payload_with_windows7_apis_is_accepted(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(verify(self.candidate(root))["static_imports_checked"], 1)

    def test_x64_and_newer_windows_requirements_are_rejected(self):
        for kwargs in ({"machine": 0x8664}, {"minimum": (6, 2)}):
            with self.subTest(kwargs=kwargs), tempfile.TemporaryDirectory() as root:
                with self.assertRaises(ValueError):
                    verify(self.candidate(root, **kwargs))

    def test_a_windows8_api_import_is_rejected_even_with_old_headers(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(ValueError, "GetSystemTimePreciseAsFileTime"):
                verify(self.candidate(root, function="GetSystemTimePreciseAsFileTime"))


if __name__ == "__main__":
    unittest.main()
