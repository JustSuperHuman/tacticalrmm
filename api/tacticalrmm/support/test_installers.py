import hashlib
import io
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.core import signing
from django.core.cache import cache
from django.http import FileResponse
from django.test import TestCase, override_settings
from model_bakery import baker
from rest_framework.test import APIClient

from accounts.models import Role, User
from clients.models import Site

from . import generation
from .installer.builder import BuildError, MSI_MAGIC, Payload, build_msi
from .management.commands.publish_support_installers import msi_properties
from .models import SupportPayload
from .tests import TEST_SETTINGS, make_installer, make_invitation


@override_settings(**TEST_SETTINGS)
class GeneratedInstallerTests(TestCase):
    def setUp(self):
        cache.clear()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.settings = override_settings(SUPPORT_INSTALLER_DIR=self.root)
        self.settings.enable()
        self.addCleanup(self.settings.disable)
        self.admin = baker.make(User, is_superuser=True)
        self.site = baker.make(Site)
        baker.make(User, is_installer_user=True)

    def test_payloads_are_unconfigured_and_hash_verified_without_a_browser_grant(self):
        data = b"MZunconfigured-client"
        payload = generation.store_payload(data)
        self.assertEqual(
            SupportPayload.objects.get().sha256, hashlib.sha256(data).hexdigest()
        )
        response = self.client.get(payload.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), data)
        self.assertIn("immutable", response["Cache-Control"])
        (self.root / "payloads" / f"{payload.sha256}.exe").write_bytes(b"corrupt")
        self.assertEqual(self.client.get(payload.url).status_code, 404)

    def test_old_exes_are_never_served_as_an_msi(self):
        installer, _ = make_installer(self.root)
        installer.generated = False
        installer.save()
        make_invitation(self.admin)
        self.client.post("/support/", {"code": "123456789"})
        self.assertEqual(
            self.client.post("/support/download/windows/").status_code, 503
        )
        self.assertNotContains(self.client.get("/support/"), "Download for Windows")

    def test_legacy_windows7_link_returns_the_universal_msi(self):
        installer, data = make_installer(self.root)
        make_invitation(self.admin)
        self.client.post("/support/", {"code": "123456789"})
        response = self.client.post("/support/download/windows7/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("TacticalSupport-windows.msi", response["Content-Disposition"])
        self.assertEqual(b"".join(response.streaming_content), data)

    def test_access_only_offers_generated_windows_setup(self):
        self.client.force_login(self.admin)
        page = self.client.get("/support/manage/installer/?embed=1")
        self.assertContains(page, "Generate and download MSI")
        self.assertNotContains(page, "PowerShell")
        self.assertIn("frame-ancestors 'self'", page["Content-Security-Policy"])
        self.client.logout()
        self.assertEqual(self.client.get("/support/manage/installer/").status_code, 403)

    def test_access_installer_limits_sites_to_the_technician(self):
        role = baker.make(Role, can_install_agents=True)
        permitted = baker.make(Site)
        role.can_view_sites.add(permitted)
        self.client.force_login(baker.make(User, role=role))
        with patch("support.installer_views.monitoring_response") as build:
            response = self.client.post(
                "/support/manage/installer/",
                {"site": self.site.pk, "agent_type": "server", "expires": 2},
            )
        self.assertEqual(response.status_code, 400)
        build.assert_not_called()

    def test_old_access_methods_all_route_to_the_generated_msi(self):
        client = APIClient()
        client.force_authenticate(self.admin)
        body = {
            "client": self.site.client_id,
            "site": self.site.pk,
            "expires": 2,
            "agenttype": "workstation",
            "rdp": 0,
            "ping": 0,
            "power": 0,
            "plat": "windows",
            "goarch": "amd64",
            "api": "https://api.example.com",
            "fileName": "old-generated.exe",
        }
        for method in ("msi", "exe", "manual", "powershell"):
            with self.subTest(method=method), patch(
                "support.generation.monitoring_response",
                return_value=FileResponse(
                    io.BytesIO(MSI_MAGIC), filename="generated.msi", as_attachment=True
                ),
            ) as build:
                response = client.post(
                    "/agents/installer/",
                    {**body, "installMethod": method},
                    format="json",
                )
                self.assertEqual(response.status_code, 200)
                self.assertIn("generated.msi", response["Content-Disposition"])
                build.assert_called_once()

    def test_failed_generation_revokes_its_enrollment_token(self):
        from knox.models import AuthToken

        self.client.force_login(self.admin)
        before = AuthToken.objects.count()
        with patch(
            "support.installer_views.monitoring_response",
            side_effect=BuildError("Build tools unavailable"),
        ):
            response = self.client.post(
                "/support/manage/installer/",
                {"site": self.site.pk, "agent_type": "server", "expires": 2},
            )
        self.assertContains(response, "Build tools unavailable", status_code=400)
        self.assertEqual(AuthToken.objects.count(), before)

    def test_private_handoff_download_requires_a_fresh_header_ticket(self):
        data = MSI_MAGIC + b"private-enrollment-config"
        digest = hashlib.sha256(data).hexdigest()
        directory = self.root / "generated"
        directory.mkdir()
        (directory / f"{digest}.msi").write_bytes(data)
        ticket = signing.dumps(
            {"sha256": digest, "size": len(data)}, salt="generated-installers"
        )
        self.assertEqual(self.client.get("/support/generated/").status_code, 403)
        self.assertEqual(
            self.client.get("/support/generated/?token=" + ticket).status_code, 403
        )
        response = self.client.get(
            "/support/generated/", HTTP_AUTHORIZATION="Bearer " + ticket
        )
        self.assertEqual(b"".join(response.streaming_content), data)
        self.assertIn("no-store", response["Cache-Control"])
        with patch(
            "django.core.signing.time.time", return_value=signing.time.time() + 1201
        ):
            self.assertEqual(
                self.client.get(
                    "/support/generated/", HTTP_AUTHORIZATION="Bearer " + ticket
                ).status_code,
                403,
            )


@override_settings(**TEST_SETTINGS)
class NativePackageIntegrationTests(TestCase):
    """Run the real Linux builder and inspect its resulting MSI database."""

    def setUp(self):
        if not all(
            shutil.which(tool)
            for tool in ("wixl", "msibuild", "msiinfo", "i686-w64-mingw32-g++")
        ):
            self.skipTest("Native MSI build tools are not installed")
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_universal_msi_selects_legacy_payload_in_native_action_and_preserves_rollback_order(
        self,
    ):
        client = Payload(
            "https://api.example.com/support/payload/" + "a" * 64 + "/", "a" * 64
        )
        path = build_msi(
            self.root / "support.msi",
            legacy=client,
            modern=client,
            mesh_settings=b"MeshID=test\nServerID=test\nMeshServer=wss://mesh.example.com/agent.ashx\nInstallFlags=0\nfileName=OtherAgent\nmeshServiceName=Mesh Agent\n",
        )
        self.assertEqual(path.read_bytes()[:8], MSI_MAGIC)
        properties = msi_properties(path)
        self.assertEqual(properties["GeneratedInstaller"], "1")
        self.assertEqual(properties["LegacySha256"], client.sha256)
        self.assertEqual(properties["ALLUSERS"], "1")
        self.assertEqual(properties["PackageMode"], "support-service")
        actions = subprocess.check_output(
            ["msiinfo", "export", str(path), "CustomAction"], text=True
        )
        install = next(
            line.split("\t")
            for line in actions.splitlines()
            if line.startswith("InstallClient\t")
        )
        self.assertTrue(int(install[1]) & 2048)
        controls = subprocess.check_output(
            ["msiinfo", "export", str(path), "Control"], text=True
        )
        self.assertNotIn("Open the support app", controls)
        self.assertIn("after you sign out or restart", controls)
        extracted = self.root / "unpacked"
        subprocess.run(
            ["msiextract", "-C", str(extracted), str(path)],
            check=True,
            capture_output=True,
        )
        config = next(extracted.rglob("MeshAgent.msh")).read_text()
        self.assertIn("InstallFlags=0", config)
        self.assertIn("meshServiceName=JustConnect Support", config)
        self.assertNotIn("meshServiceName=Mesh Agent", config)
        self.assertNotIn("fileName=", config)
        sequence = subprocess.check_output(
            ["msiinfo", "export", str(path), "InstallExecuteSequence"], text=True
        )
        rows = dict(
            (parts[0], int(parts[2]))
            for line in sequence.splitlines()[3:]
            if len(parts := line.split("\t")) == 3
        )
        self.assertLess(rows["PrepareInstall"], rows["InstallInitialize"])
        self.assertLess(rows["StopClient"], rows["RemoveFiles"])
        self.assertLess(rows["InstallFiles"], rows["RollbackClient"])
        self.assertLess(rows["RollbackClient"], rows["InstallClient"])
        self.assertLess(rows["InstallClient"], rows["InstallFinalize"])
        dialogs = subprocess.check_output(
            ["msiinfo", "export", str(path), "Dialog"], text=True
        )
        for name in (
            "Welcome",
            "Progress",
            "Complete",
            "Cancelled",
            "Failed",
            "Maintenance",
        ):
            self.assertIn(name, dialogs)

    def test_monitoring_msi_uses_elevated_native_actions_without_plaintext_token_properties(
        self,
    ):
        payload = Payload("https://api.example.com/client.exe", "a" * 64)
        path = build_msi(
            self.root / "monitoring.msi",
            legacy=payload,
            modern=payload,
            mode="monitoring",
            arguments="-m install --auth SYNTHETIC-TEST-TOKEN",
        )
        properties = msi_properties(path)
        self.assertEqual(properties["ALLUSERS"], "1")
        self.assertNotIn("SYNTHETIC-TEST-TOKEN", " ".join(properties.values()))
        self.assertIn("InstallArgumentsHex", properties["MsiHiddenProperties"])
        actions = subprocess.check_output(
            ["msiinfo", "export", str(path), "CustomAction"], text=True
        )
        install = next(
            line.split("\t")
            for line in actions.splitlines()
            if line.startswith("InstallClient\t")
        )
        self.assertTrue(int(install[1]) & 2048)  # Deferred system context, after UAC.
        files = subprocess.check_output(
            ["msiinfo", "export", str(path), "File"], text=True
        )
        self.assertIn("unins001.exe", files)
