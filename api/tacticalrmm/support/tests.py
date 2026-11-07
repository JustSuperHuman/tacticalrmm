import hashlib
import io
import json
import tempfile
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import close_old_connections
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from model_bakery import baker

from accounts.models import Role, User
from clients.models import Site
from logs.models import AuditLog

from . import generation, meshapps
from .installer.builder import MSI_MAGIC
from .models import SupportAccessCode, SupportBranding, SupportInstaller, code_digest

TEST_SETTINGS = {
    "CACHES": {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
    "ALLOWED_HOSTS": ["testserver", "api.example.com"],
    "SESSION_COOKIE_DOMAIN": None,
    "CSRF_COOKIE_DOMAIN": None,
    "DEMO": False,
}


def make_invitation(user=None, site=None, raw="123456789", **overrides):
    return SupportAccessCode.objects.create(
        digest=code_digest(raw),
        last_three=raw[-3:],
        site=site or baker.make(Site),
        created_by=user or baker.make(User, is_superuser=True),
        expires_at=overrides.pop("expires_at", timezone.now() + timedelta(minutes=30)),
        **overrides,
    )


def make_installer(directory, platform="windows"):
    # Synthetic format markers for HTTP/integrity tests; these are not signed clients.
    payload = (
        b"xar!" if platform == "macos" else MSI_MAGIC
    ) + b"test-only-support-tool"
    installer = SupportInstaller.objects.create(
        platform=platform,
        generated=True,
        version="1.0.0-test",
        size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        signer="Synthetic test publisher",
    )
    (Path(directory) / installer.storage_name).write_bytes(payload)
    return installer, payload


@override_settings(**TEST_SETTINGS)
class PublicSupportTests(TestCase):
    def setUp(self):
        cache.clear()
        self.code = make_invitation(label="Private client detail")
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = self.temporary.name
        self.settings_override = override_settings(SUPPORT_INSTALLER_DIR=self.directory)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)

    def grant(self, client=None):
        return (client or self.client).post("/support/", {"code": "123 456-789"})

    def test_public_page_needs_no_login_and_discloses_no_invitation(self):
        response = self.client.get("/support/")
        self.assertContains(response, "Get remote support")
        self.assertNotContains(response, self.code.digest)
        self.assertNotContains(response, "Private client detail")
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(response["Referrer-Policy"], "same-origin")
        self.assertIn("frame-ancestors 'none'", response["Content-Security-Policy"])

    def test_code_is_normalized_and_grant_does_not_persist_the_code(self):
        response = self.grant()
        self.assertRedirects(response, "/support/")
        grant = self.client.session["support_download"]
        self.assertEqual(grant["id"], str(self.code.pk))
        self.assertLessEqual(grant["until"], time.time() + 1200)
        self.assertNotIn("123456789", json.dumps(dict(self.client.session)))
        response = self.client.get("/support/")
        self.assertContains(response, "Code accepted")
        self.assertContains(response, "The Windows version isn’t available yet")
        self.assertNotContains(response, self.code.digest)

    def test_code_exchange_supports_progressive_enhancement(self):
        response = self.client.post(
            "/support/", {"code": "123456789"}, HTTP_ACCEPT="application/json"
        )
        self.assertEqual(response.json(), {"redirect": "/support/"})

    def test_invalid_codes_are_rejected_without_disclosure(self):
        for raw in ("", "123", "１２３４５６７８９", "<script>", "999999999"):
            with self.subTest(raw=raw):
                cache.clear()
                response = self.client.post("/support/", {"code": raw})
                self.assertEqual(response.status_code, 400)
                self.assertNotIn("support_download", self.client.session)

    def test_expired_revoked_and_exhausted_codes_have_same_error(self):
        responses = []
        for change in (
            {"expires_at": timezone.now() - timedelta(seconds=1)},
            {"revoked_at": timezone.now()},
            {"download_count": 5},
        ):
            self.code.expires_at = timezone.now() + timedelta(minutes=30)
            self.code.revoked_at = None
            self.code.download_count = 0
            for field, value in change.items():
                setattr(self.code, field, value)
            self.code.save()
            responses.append(
                self.client.post(
                    "/support/", {"code": "123456789"}, HTTP_ACCEPT="application/json"
                )
            )
        self.assertTrue(all(r.status_code == 400 for r in responses))
        self.assertEqual(responses[0].json(), responses[1].json())
        self.assertEqual(responses[1].json(), responses[2].json())

    def test_public_exchange_and_download_are_csrf_protected(self):
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(
            client.post("/support/", {"code": "123456789"}).status_code, 403
        )
        self.assertEqual(client.post("/support/download/windows/").status_code, 403)
        client.get("/support/")
        csrf = client.cookies["csrftoken"].value
        response = client.post(
            "/support/", {"code": "123456789", "csrfmiddlewaretoken": csrf}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(client.post("/support/reset/").status_code, 403)

    def test_rate_limit_includes_invalid_format_and_valid_codes(self):
        for _ in range(5):
            self.client.post("/support/", {"code": "wrong"})
        response = self.client.post("/support/", {"code": "123456789"})
        self.assertEqual(response.status_code, 429)
        self.assertIn("Retry-After", response)

    def test_download_requires_post_and_valid_grant(self):
        self.assertEqual(self.client.get("/support/download/windows/").status_code, 405)
        self.assertEqual(
            self.client.post("/support/download/windows/").status_code, 403
        )
        self.grant()
        self.assertEqual(self.client.post("/support/download/linux/").status_code, 404)

    def test_downloads_all_platforms_and_audits_without_raw_code(self):
        self.grant()
        for platform in ("windows", "macos"):
            installer, payload = make_installer(self.directory, platform)
            response = self.client.post(f"/support/download/{platform}/")
            self.assertEqual(response.status_code, 200)
            self.assertIn(installer.filename, response["Content-Disposition"])
            self.assertEqual(b"".join(response.streaming_content), payload)
        self.code.refresh_from_db()
        self.assertEqual(self.code.download_count, 2)
        # Kept so the computer that connects next from here joins this code's session.
        self.assertEqual(self.code.downloaded_from, "127.0.0.1")
        self.assertIsNotNone(self.code.downloaded_at)
        entries = AuditLog.objects.filter(message__startswith="Downloaded")
        self.assertEqual(entries.count(), 2)
        self.assertNotIn("123456789", json.dumps(list(entries.values("after_value"))))

    def test_download_rechecks_revocation_after_exchange(self):
        self.grant()
        self.code.revoked_at = timezone.now()
        self.code.save()
        self.assertEqual(
            self.client.post("/support/download/windows/").status_code, 403
        )

    def test_browser_grant_expiration_is_enforced(self):
        self.grant()
        session = self.client.session
        session["support_download"]["until"] = time.time() - 1
        session.save()
        self.assertEqual(
            self.client.post("/support/download/windows/").status_code, 403
        )
        self.assertNotIn("support_download", self.client.session)

    def test_expired_code_blocks_existing_browser_grant(self):
        self.grant()
        self.code.expires_at = timezone.now() - timedelta(seconds=1)
        self.code.save()
        self.assertEqual(
            self.client.post("/support/download/windows/").status_code, 403
        )

    def test_missing_and_corrupted_installers_do_not_consume_quota(self):
        self.grant()
        self.assertEqual(
            self.client.post("/support/download/windows/").status_code, 503
        )
        installer, _ = make_installer(self.directory)
        path = Path(self.directory) / installer.storage_name
        path.write_bytes(b"corrupted bytes")
        self.assertEqual(
            self.client.post("/support/download/windows/").status_code, 503
        )
        path.unlink()
        self.assertEqual(
            self.client.post("/support/download/windows/").status_code, 503
        )
        self.code.refresh_from_db()
        self.assertEqual(self.code.download_count, 0)

    def test_download_limit_is_enforced_and_reset_removes_access(self):
        self.code.max_downloads = 1
        self.code.save()
        make_installer(self.directory)
        self.grant()
        response = self.client.post("/support/download/windows/")
        self.assertEqual(response.status_code, 200)
        list(response.streaming_content)
        self.assertEqual(
            self.client.post("/support/download/windows/").status_code, 403
        )
        self.assertRedirects(self.client.post("/support/reset/"), "/support/")
        self.assertNotIn("support_download", self.client.session)


@override_settings(**TEST_SETTINGS)
class SupportConsoleTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = baker.make(User, is_superuser=True)
        self.site = baker.make(Site)
        self.client.force_login(self.admin)

    def create_code(self, **overrides):
        return self.client.post(
            "/support/manage/",
            {
                "action": "create",
                "site": self.site.pk,
                "label": "Front desk",
                "lifetime": 30,
                "max_downloads": 5,
                **overrides,
            },
        )

    def test_anonymous_console_requires_dashboard_sign_in(self):
        self.client.logout()
        response = self.client.get("/support/manage/")
        self.assertContains(response, "Sign in to Access", status_code=401)
        self.assertNotContains(response, "Create code", status_code=401)

    def test_existing_dashboard_session_can_create_code_and_see_it_once(self):
        response = self.create_code()
        self.assertEqual(response.status_code, 200)
        raw = response.context["new_code"].replace(" ", "")
        code = SupportAccessCode.objects.get()
        self.assertEqual(code.digest, code_digest(raw))
        self.assertEqual(code.created_by_id, self.admin.pk)
        self.assertEqual(code.last_three, raw[-3:])
        self.assertLessEqual(code.expires_at, timezone.now() + timedelta(minutes=30))
        response = self.client.get("/support/manage/")
        self.assertNotContains(
            response, " ".join(raw[i : i + 3] for i in range(0, 9, 3))
        )
        self.assertEqual(
            AuditLog.objects.filter(message="Created a support download code.").count(),
            1,
        )

    def test_revoke_stops_existing_grant_and_is_audited(self):
        code = make_invitation(self.admin, self.site)
        public = Client()
        public.post("/support/", {"code": "123456789"})
        response = self.client.post(
            "/support/manage/", {"action": "revoke", "invitation": code.pk}
        )
        self.assertRedirects(response, f"/support/manage/codes/{code.pk}/")
        self.assertEqual(public.post("/support/download/windows/").status_code, 403)
        self.assertTrue(
            AuditLog.objects.filter(message="Revoked a support download code.").exists()
        )

    def test_bad_revoke_identifier_is_not_a_server_error(self):
        self.assertEqual(
            self.client.post(
                "/support/manage/", {"action": "revoke", "invitation": "bad"}
            ).status_code,
            404,
        )

    def test_invitation_values_are_bounded(self):
        for value in (0, 21, -1):
            self.assertEqual(self.create_code(max_downloads=value).status_code, 400)
        self.assertEqual(self.create_code(lifetime=999999).status_code, 400)
        self.assertEqual(SupportAccessCode.objects.count(), 0)

    def test_installer_and_agent_accounts_cannot_manage_codes(self):
        for user in (
            baker.make(User, is_superuser=True, is_installer_user=True),
            baker.make(User, is_superuser=True, agent=baker.make("agents.Agent")),
            baker.make(User, is_superuser=False, role=None),
        ):
            self.client.force_login(user)
            self.assertEqual(self.create_code().status_code, 403)

    def test_role_scope_cannot_create_or_revoke_other_site_or_technician_codes(self):
        role = baker.make(Role, can_use_mesh=True, can_install_agents=True)
        role.can_view_sites.add(self.site)
        user = baker.make(User, role=role)
        other_site = baker.make(Site)
        other_code = make_invitation(
            self.admin, self.site, label="Other technician private name"
        )
        self.client.force_login(user)
        self.assertEqual(self.create_code(site=other_site.pk).status_code, 404)
        self.assertEqual(
            self.client.post(
                "/support/manage/", {"action": "revoke", "invitation": other_code.pk}
            ).status_code,
            404,
        )
        response = self.client.get("/support/manage/")
        self.assertNotContains(response, "Other technician private name")
        self.assertEqual(
            self.client.get(f"/support/manage/codes/{other_code.pk}/").status_code, 404
        )
        self.assertEqual(self.create_code().status_code, 200)

    def test_both_remote_and_install_permissions_are_required(self):
        role = baker.make(Role, can_use_mesh=True, can_install_agents=False)
        self.client.force_login(baker.make(User, role=role))
        self.assertEqual(self.create_code().status_code, 403)

    def test_a_waiting_code_has_its_own_session_page(self):
        code = make_invitation(self.admin, self.site, label="Front desk")
        response = self.client.get(f"/support/manage/codes/{code.pk}/")
        self.assertContains(response, "Waiting to connect")
        self.assertContains(response, "that ends in <b>789</b>", html=False)
        self.assertNotContains(response, "123 456 789")
        self.assertEqual(
            self.client.get("/support/manage/codes/not-a-code/").status_code, 404
        )

    @override_settings(SUPPORT_PUBLIC_URL="https://support.example.com")
    def test_invitations_send_people_to_the_public_support_address(self):
        response = self.create_code()
        code = response.context["new_code"]
        self.assertContains(
            response,
            f"Go to https://support.example.com and enter the code {code}.",
        )
        self.assertContains(response, 'href="https://support.example.com"')

    def test_new_sessions_show_their_code_once_with_ways_to_send_it(self):
        response = self.create_code()
        self.assertContains(response, "Your session is ready")
        self.assertContains(response, "Email invitation")
        code = SupportAccessCode.objects.get()
        self.assertContains(response, f'data-url="/support/manage/codes/{code.pk}/"')

    @override_settings(DEMO=True)
    def test_demo_does_not_issue_codes(self):
        self.assertEqual(self.create_code().status_code, 403)
        self.assertFalse(SupportAccessCode.objects.exists())


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
WINDOWS_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36"
WINDOWS7_UA = "Mozilla/5.0 (Windows NT 6.1; Win64; x64) AppleWebKit/537.36 Chrome/109 Safari/537.36"
MAC_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 Version/17.5 Safari/605.1.15"
IPHONE_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148"


@override_settings(**TEST_SETTINGS)
class PortalDeviceTests(TestCase):
    def setUp(self):
        cache.clear()
        make_invitation()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        for platform in ("windows", "macos"):
            make_installer(self.temporary.name, platform)
        self.client.post("/support/", {"code": "123456789"})

    def page(self, agent):
        return self.client.get("/support/", HTTP_USER_AGENT=agent).content.decode()

    def test_windows_visitors_get_windows_first_as_the_main_action(self):
        html = self.page(WINDOWS_UA)
        self.assertLess(html.index("Download for Windows"), html.index("Mac version?"))
        self.assertIn("sh-btn--primary", html[: html.index("Download for Windows")])

    def test_mac_visitors_get_mac_first_and_mac_steps(self):
        html = self.page(MAC_UA)
        self.assertLess(html.index("Download for Mac"), html.index("Windows version?"))
        self.assertIn('data-steps="windows" hidden', html)

    def test_windows7_visitors_get_the_same_automatic_msi(self):
        for agent in (WINDOWS7_UA, WINDOWS7_UA.replace("Win64; x64", "WOW32")):
            html = self.page(agent)
            self.assertIn("Download for Windows", html)
            self.assertIn('action="/support/download/windows/"', html)
            self.assertIn("Setup downloads the right client", html)
            self.assertNotIn("Download for Windows 7", html)

    def test_raw_legacy_downloads_are_not_offered(self):
        SupportInstaller.objects.filter(platform="windows").update(generated=False)
        html = self.page(WINDOWS7_UA)
        self.assertIn("The Windows version isn’t available yet", html)

    def test_phones_are_told_to_use_their_computer(self):
        html = self.page(IPHONE_UA)
        self.assertIn(
            'id="mobile-note" class="sh-alert sh-alert--warn sp-alert">', html
        )
        self.assertIn("Download for Windows", html)
        self.assertIn("Download for Mac", html)


@override_settings(**TEST_SETTINGS)
class SupportBrandingTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = baker.make(User, is_superuser=True)
        self.client.force_login(self.admin)

    def save(self, logo=None, **fields):
        data = {
            "action": "branding",
            "name": "",
            "contact_email": "",
            "contact_phone": "",
        }
        data.update(fields)
        if logo is not None:
            data["logo"] = logo
        return self.client.post("/support/manage/settings/", data)

    def test_defaults_until_branding_is_saved(self):
        self.assertEqual(self.client.get("/support/logo/").status_code, 404)
        response = Client().get("/support/")
        self.assertContains(response, "support/brand/justconnect.webp")
        self.assertContains(response, "support/brand/justconnect-dark.webp")
        self.assertContains(response, 'alt="JustConnect"')
        self.assertContains(response, "support/brand/favicon.png")
        self.assertNotContains(response, "Tactical RMM")
        self.assertContains(response, "Ask your technician to send you one.")

    def test_another_company_name_gets_the_mark_beside_it(self):
        self.save(name="Acme IT")
        page = Client().get("/support/")
        self.assertNotContains(page, "justconnect.webp")
        self.assertContains(page, "support/brand/mark.webp")
        self.assertContains(page, "<span>Acme IT</span>", html=False)

    def test_console_switches_between_support_and_access(self):
        page = self.client.get("/support/manage/").content.decode()
        switch = page[page.index('class="sh-seg sp-switch"') :]
        switch = switch[: switch.index("</nav>")]
        self.assertIn('<a href="/support/manage/" aria-current="page">', switch)
        dashboard = settings.CORS_ORIGIN_WHITELIST[0].rstrip("/")
        self.assertIn(f'<a href="{dashboard}">', switch)
        self.assertIn("Access</a>", switch)

    def test_logo_name_and_contacts_appear_on_the_public_page(self):
        response = self.save(
            SimpleUploadedFile("logo.png", PNG, content_type="image/png"),
            name="Acme IT",
            contact_email="help@acme.test",
            contact_phone="(555) 010-0100",
        )
        self.assertRedirects(response, "/support/manage/settings/?saved=branding")
        public = Client()
        page = public.get("/support/")
        self.assertContains(page, "/support/logo/?v=")
        self.assertContains(page, 'alt="Acme IT"')
        self.assertContains(page, 'href="tel:5550100100"')
        self.assertContains(page, "mailto:help@acme.test")
        logo = public.get("/support/logo/")
        self.assertEqual(logo["Content-Type"], "image/png")
        self.assertEqual(logo.content, PNG)
        self.assertIn("immutable", logo["Cache-Control"])
        self.assertTrue(
            AuditLog.objects.filter(
                message="Updated the support page branding."
            ).exists()
        )

    def test_svg_logos_are_sandboxed(self):
        svg = b'<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg"></svg>'
        self.save(SimpleUploadedFile("logo.svg", svg, content_type="image/svg+xml"))
        logo = Client().get("/support/logo/")
        self.assertEqual(logo["Content-Type"], "image/svg+xml")
        self.assertIn("sandbox", logo["Content-Security-Policy"])
        self.assertEqual(logo["X-Content-Type-Options"], "nosniff")

    def test_files_that_are_not_images_or_too_large_are_rejected(self):
        for upload in (
            SimpleUploadedFile(
                "logo.png", b"<html><script></script>", content_type="image/png"
            ),
            SimpleUploadedFile(
                "logo.png", PNG + b"\x00" * 600_000, content_type="image/png"
            ),
        ):
            response = self.save(upload)
            self.assertEqual(response.status_code, 400)
        self.assertFalse(SupportBranding.load().has_logo)

    def test_saving_text_keeps_the_logo_and_remove_restores_the_default(self):
        self.save(SimpleUploadedFile("logo.png", PNG, content_type="image/png"))
        self.save(name="Acme IT")
        self.assertEqual(Client().get("/support/logo/").content, PNG)
        self.save(remove_logo="on")
        self.assertEqual(Client().get("/support/logo/").status_code, 404)
        self.assertEqual(SupportBranding.load().name, "")

    def test_branding_needs_the_global_settings_permission(self):
        role = baker.make(Role, can_use_mesh=True, can_install_agents=True)
        self.client.force_login(baker.make(User, role=role))
        self.assertNotContains(
            self.client.get("/support/manage/settings/"), "Save branding"
        )
        response = self.save(name="Not allowed")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(SupportBranding.load().name, "")
        role.can_edit_core_settings = True
        role.save()
        self.assertRedirects(
            self.save(name="Allowed"), "/support/manage/settings/?saved=branding"
        )

    def test_new_codes_come_with_a_ready_to_send_invitation(self):
        site = baker.make(Site)
        response = self.client.post(
            "/support/manage/",
            {"action": "create", "site": site.pk, "lifetime": 60, "max_downloads": 5},
        )
        code = response.context["new_code"]
        self.assertContains(
            response,
            f"Go to http://testserver/support/ and enter the code {code}. "
            "It works for the next 1 hour.",
        )


@override_settings(**TEST_SETTINGS)
class PublishSupportTests(TestCase):
    def setUp(self):
        self.bundle = tempfile.TemporaryDirectory()
        self.storage = tempfile.TemporaryDirectory()
        self.addCleanup(self.bundle.cleanup)
        self.addCleanup(self.storage.cleanup)
        self.override = override_settings(SUPPORT_INSTALLER_DIR=self.storage.name)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.payload = b"MZunconfigured-test-client"
        self.payload_digest = hashlib.sha256(self.payload).hexdigest()
        payload_dir = Path(self.bundle.name) / "payloads"
        payload_dir.mkdir()
        (payload_dir / f"{self.payload_digest}.exe").write_bytes(self.payload)
        inspector = patch(
            "support.management.commands.publish_support_installers.msi_properties",
            return_value={
                "GeneratedInstaller": "1",
                "PackageMode": "support-service",
                "ALLUSERS": "1",
                "LegacySha256": self.payload_digest,
                "ModernSha256": self.payload_digest,
            },
        )
        inspector.start()
        self.addCleanup(inspector.stop)
        self.manifest = {
            "schema": 2,
            "version": "1.2.3",
            "installers": [],
            "payloads": [
                {
                    "filename": f"payloads/{self.payload_digest}.exe",
                    "sha256": self.payload_digest,
                    "size": len(self.payload),
                }
            ],
        }
        for platform in ("windows", "macos"):
            installer, payload = make_installer(self.storage.name, platform)
            (Path(self.bundle.name) / installer.filename).write_bytes(payload)
            self.manifest["installers"].append(
                {
                    "platform": platform,
                    "generated": True,
                    "format": "msi" if platform == "windows" else "pkg",
                    "filename": installer.filename,
                    "sha256": installer.sha256,
                    "size": len(payload),
                    "signature": {
                        "verified": True,
                        "notarized": True,
                        "provider": (
                            "azure-artifact-signing"
                            if platform == "windows"
                            else "apple-developer-id"
                        ),
                        "signer": "Test CI signing identity",
                    },
                }
            )
        SupportInstaller.objects.all().delete()

    def publish(self):
        (Path(self.bundle.name) / "manifest.json").write_text(
            json.dumps(self.manifest), encoding="utf-8"
        )
        call_command("publish_support_installers", self.bundle.name)

    def test_complete_verified_release_is_published_and_idempotent(self):
        self.publish()
        self.publish()
        self.assertEqual(SupportInstaller.objects.count(), 2)
        self.assertTrue(
            all(i.version == "1.2.3" for i in SupportInstaller.objects.all())
        )

    def test_tampered_artifact_does_not_activate_either_platform(self):
        (Path(self.bundle.name) / "TacticalSupport-macos.pkg").write_bytes(
            b"xar!changed"
        )
        with self.assertRaises(CommandError):
            self.publish()
        self.assertFalse(SupportInstaller.objects.exists())

    def test_unverified_unsigned_or_unnotarized_release_is_rejected(self):
        for platform, field, value in (
            (0, "verified", False),
            (0, "provider", "unsigned"),
            (1, "notarized", False),
        ):
            with self.subTest(field=field):
                original = self.manifest["installers"][platform]["signature"][field]
                self.manifest["installers"][platform]["signature"][field] = value
                with self.assertRaises(CommandError):
                    self.publish()
                self.manifest["installers"][platform]["signature"][field] = original
        self.assertFalse(SupportInstaller.objects.exists())

    def test_path_traversal_and_empty_release_are_rejected(self):
        self.manifest["installers"][0]["filename"] = "../../outside.exe"
        with self.assertRaises(CommandError):
            self.publish()
        self.manifest["installers"] = []
        with self.assertRaises(CommandError):
            self.publish()

    def test_mac_can_be_published_independently_without_replacing_windows(self):
        existing, _ = make_installer(self.storage.name)
        self.manifest["installers"] = [self.manifest["installers"][1]]
        self.publish()
        existing.refresh_from_db()
        self.assertEqual(existing.version, "1.0.0-test")
        self.assertEqual(
            SupportInstaller.objects.get(platform="macos").version, "1.2.3"
        )

    def test_separate_windows7_executable_is_rejected(self):
        self.manifest["installers"][0]["platform"] = "windows7"
        with self.assertRaises(CommandError):
            self.publish()

    def test_generated_marker_and_payload_integrity_are_required(self):
        self.manifest["installers"][0]["generated"] = False
        with self.assertRaises(CommandError):
            self.publish()
        self.manifest["installers"][0]["generated"] = True
        (
            Path(self.bundle.name) / "payloads" / f"{self.payload_digest}.exe"
        ).write_bytes(b"changed")
        with self.assertRaises(CommandError):
            self.publish()


@override_settings(**TEST_SETTINGS)
class ConcurrentDownloadTests(TransactionTestCase):
    def test_only_one_concurrent_download_can_claim_the_last_slot(self):
        cache.clear()
        code = make_invitation(max_downloads=1)
        with tempfile.TemporaryDirectory() as directory, override_settings(
            SUPPORT_INSTALLER_DIR=directory
        ):
            make_installer(directory)
            clients = [Client(), Client()]
            for client in clients:
                client.post("/support/", {"code": "123456789"})

            def download(client):
                close_old_connections()
                try:
                    response = client.post("/support/download/windows/")
                    status = response.status_code
                    response.close()
                    return status
                finally:
                    close_old_connections()

            with ThreadPoolExecutor(max_workers=2) as executor:
                statuses = list(executor.map(download, clients))
            self.assertEqual(sorted(statuses), [200, 403])
            code.refresh_from_db()
            self.assertEqual(code.download_count, 1)


GROUP = {"id": "mesh//GroupId", "short_id": "GroupId", "name": "TacticalRMM Support"}
MESH_SITE = type("Core", (), {"mesh_site": "https://mesh.example.com"})()


def mesh_downloads(windows=b"MZ" + b"w" * 64, package=b"xar!" + b"p" * 64):
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("MeshAgent.pkg", package)
        bundle.writestr("Uninstall.command", "#!/bin/sh\n")
    responses = {"meshagents": windows, "meshosxagent": archive.getvalue()}
    return lambda url: next(v for k, v in responses.items() if f"/{k}?" in url)


@override_settings(**TEST_SETTINGS)
class UnsignedSupportAppTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = baker.make(User, is_superuser=True)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        for target, value in (
            ("support.generation.mesh.find_group", GROUP),
            ("support.generation.get_core_settings", MESH_SITE),
            ("support.host.mesh.find_group", GROUP),
        ):
            patcher = patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def publish(self, **kwargs):
        def build(output, group):
            if kwargs.get("windows", b"MZ").startswith(b"<"):
                raise generation.BuildError("Windows client unavailable")
            output.write_bytes(MSI_MAGIC + b"synthetic-package")
            return output

        with override_settings(SUPPORT_INSTALLER_DIR=self.temporary.name), patch(
            "support.meshapps._download", side_effect=mesh_downloads(**kwargs)
        ) as download, patch("support.generation.support_windows", side_effect=build):
            return generation.publish(), download

    def test_all_apps_come_from_the_support_group_unsigned(self):
        results, download = self.publish()
        self.assertEqual(
            results,
            {"windows": "generated", "macos": "generated"},
        )
        urls = [call.args[0] for call in download.call_args_list]
        self.assertEqual(
            urls,
            [
                "https://mesh.example.com/meshosxagent?id=10005&meshid=GroupId&installflags=0",
            ],
        )
        for installer in SupportInstaller.objects.all():
            self.assertFalse(installer.signed)
            stored = Path(self.temporary.name) / installer.storage_name
            self.assertEqual(
                hashlib.sha256(stored.read_bytes()).hexdigest(), installer.sha256
            )
        self.assertTrue(
            (
                Path(self.temporary.name)
                / SupportInstaller.objects.get(platform="macos").storage_name
            )
            .read_bytes()
            .startswith(b"xar!")
        )

    def test_signed_apps_are_kept(self):
        make_installer(self.temporary.name, "windows")
        results, _ = self.publish()
        self.assertEqual(results["windows"], "kept signed")
        self.assertTrue(SupportInstaller.objects.get(platform="windows").signed)
        self.assertFalse(SupportInstaller.objects.get(platform="macos").signed)

    def test_mac_can_be_fetched_when_windows_downloads_are_unavailable(self):
        with override_settings(SUPPORT_INSTALLER_DIR=self.temporary.name), patch(
            "support.meshapps._download", side_effect=mesh_downloads()
        ) as download:
            result = meshapps.publish(platforms=["macos"])
        self.assertEqual(result, {"macos": "generated"})
        self.assertEqual(download.call_count, 1)
        self.assertIn("/meshosxagent?", download.call_args.args[0])

    def test_files_that_arent_installers_are_refused(self):
        with self.assertRaisesMessage(
            generation.BuildError, "Windows client unavailable"
        ):
            self.publish(windows=b"<html>login</html>")
        with self.assertRaisesMessage(meshapps.Unavailable, "Mac installer package"):
            self.publish(package=b"not a package")
        self.assertFalse(SupportInstaller.objects.exists())

    def test_the_support_page_offers_them_and_says_what_to_expect(self):
        self.publish()
        make_invitation(self.admin)
        public = Client()
        public.post("/support/", {"code": "123456789"})
        page = public.get("/support/", HTTP_USER_AGENT=WINDOWS_UA)
        self.assertContains(page, "Windows may warn you before it opens")
        self.assertContains(page, "<b>MSI installer</b>", html=False)
        self.assertContains(page, "<b>Open Anyway</b>", html=False)
        self.assertNotContains(page, "Signed by")
        with override_settings(SUPPORT_INSTALLER_DIR=self.temporary.name):
            response = public.post("/support/download/windows/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(b"".join(response.streaming_content).startswith(MSI_MAGIC))

    def test_only_settings_admins_can_get_them(self):
        role = baker.make(Role, can_use_mesh=True, can_install_agents=True)
        self.client.force_login(baker.make(User, role=role))
        with patch("support.generation.publish") as publish:
            response = self.client.post("/support/manage/settings/", {"action": "apps"})
        self.assertEqual(response.status_code, 403)
        publish.assert_not_called()
        self.client.force_login(self.admin)
        with patch("support.generation.publish", return_value={"windows": "published"}):
            response = self.client.post("/support/manage/settings/", {"action": "apps"})
        self.assertRedirects(
            response,
            "/support/manage/settings/?saved=apps",
            fetch_redirect_response=False,
        )
        with patch(
            "support.generation.publish",
            side_effect=meshapps.Unavailable("Create the support group first."),
        ):
            response = self.client.post("/support/manage/settings/", {"action": "apps"})
        self.assertContains(
            response, "Create the support group first.", status_code=503
        )
