import hashlib
import io
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from fetch_installer import fetch
from manifest import combine, write_record


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
    def test_digest_describes_final_bytes_and_combination_detects_modification(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for platform, suffix, data in (
                ("windows", "exe", b"MZtest"),
                ("macos", "pkg", b"xar!test"),
            ):
                path = root / f"TacticalSupport-{platform}.{suffix}"
                path.write_bytes(data)
                write_record(root, platform, "1.0.0", "Synthetic CI identity")
            combine(root)
            manifest = json.loads((root / "manifest.json").read_text())
            self.assertEqual(
                manifest["installers"][0]["sha256"],
                hashlib.sha256(b"MZtest").hexdigest(),
            )
            self.assertTrue(manifest["installers"][1]["signature"]["notarized"])
            (root / "TacticalSupport-windows.exe").write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "changed after signing"):
                combine(root)


if __name__ == "__main__":
    unittest.main()
