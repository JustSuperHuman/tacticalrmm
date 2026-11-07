import asyncio
import base64
import io
import re
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from model_bakery import baker
from PIL import Image

from accounts.models import Role, User
from clients.models import Site
from logs.models import AuditLog

from . import mesh, screens
from .models import (
    SupportBranding,
    SupportCommand,
    SupportHandoff,
    SupportNote,
    SupportRun,
    SupportSession,
)
from .tests import TEST_SETTINGS, make_invitation

GROUP = {"id": "mesh//GroupId", "short_id": "GroupId", "name": "TacticalRMM Support"}
WINDOWS_PC = {
    "id": "WinNodeId0000000000000000",
    "node_id": "node//WinNodeId0000000000000000",
    "name": "FRONT-DESK",
    "os": "Microsoft Windows 11 Pro",
    "windows": True,
    "platform": "windows",
    "online": True,
    "connected_at": None,
    "first_seen": None,
    "ip": "",
    "users": ["dana"],
}
MAC = {
    "id": "MacNodeId0000000000000000",
    "node_id": "node//MacNodeId0000000000000000",
    "name": "Danas-MacBook.local",
    "os": "macOS 15",
    "windows": False,
    "platform": "macos",
    "online": True,
    "connected_at": None,
    "first_seen": None,
    "ip": "",
    "users": [],
}


def elevated_script(command):
    encoded = re.search(r"FromBase64String\('([^']+)'\)", command).group(1)
    return base64.b64decode(encoded).decode("utf-8")


@override_settings(**TEST_SETTINGS)
class MeshHelperTests(TestCase):
    def test_devices_are_parsed_and_sorted_online_first(self):
        reply = {
            "action": "nodes",
            "nodes": {
                "mesh//GroupId": [
                    {
                        "_id": "node//b",
                        "name": "zeta",
                        "agent": {"id": 10005},
                        "conn": 0,
                    },
                    {
                        "_id": "node//a",
                        "name": "Alpha",
                        "agent": {"id": 4},
                        "conn": 1,
                        "agct": 1793945229278,
                        "users": ["ALPHA\\dana"],
                    },
                    {"name": "no id"},
                ]
            },
        }
        with patch.object(mesh, "_request", return_value=reply) as request:
            devices = mesh.list_devices(GROUP)
        self.assertEqual(request.call_args.args[0]["meshid"], "mesh//GroupId")
        self.assertEqual([d["name"] for d in devices], ["Alpha", "zeta"])
        self.assertTrue(devices[0]["windows"] and devices[0]["online"])
        self.assertEqual(devices[0]["users"], ["dana"])
        self.assertEqual(devices[0]["connected_at"].year, 2026)
        self.assertEqual(devices[0]["platform"], "windows")
        self.assertFalse(devices[1]["windows"] or devices[1]["online"])
        self.assertIsNone(devices[1]["connected_at"])

    def test_command_output_errors_and_timeouts(self):
        output = {"action": "msg", "type": "runcommands", "result": "hello\n"}
        with patch.object(mesh, "_request", return_value=output) as request:
            self.assertEqual(
                mesh.run_command(WINDOWS_PC, "echo hello", mesh.CMD), "hello\n"
            )
        sent = request.call_args.args[0]
        self.assertEqual(sent["nodeids"], [WINDOWS_PC["node_id"]])
        self.assertTrue(sent["reply"])
        with patch.object(
            mesh,
            "_request",
            return_value={"action": "runcommands", "result": "Agent not connected"},
        ):
            with self.assertRaisesMessage(mesh.MeshError, "Agent not connected"):
                mesh.run_command(WINDOWS_PC, "dir", mesh.CMD)
        with patch.object(mesh, "_request", return_value=None):
            self.assertIsNone(mesh.run_command(WINDOWS_PC, "dir", mesh.CMD))

    def test_missing_meshcentral_settings_are_explained(self):
        with patch.object(mesh, "get_mesh_ws_url", side_effect=AttributeError):
            with self.assertRaisesMessage(mesh.MeshError, "isn’t set up"):
                mesh.find_group()

    @patch("support.mesh.time.sleep")
    def test_windows_removal_uses_msi_before_dropping_the_device(self, _):
        sent = []

        def fake_request(payload, wanted, timeout=15):
            sent.append(payload)
            if payload["action"] == "runcommands":
                return {"action": "msg", "type": "runcommands", "result": "Stopping."}
            return {"action": payload["action"], "result": "ok"}

        with patch.object(mesh, "_request", side_effect=fake_request):
            mesh.remove_device(WINDOWS_PC)
        self.assertEqual(
            [p["action"] for p in sent],
            ["runcommands", "removedevices"],
        )
        self.assertEqual(sent[0]["cmds"], mesh.STOP_WINDOWS)
        self.assertEqual(sent[0]["type"], mesh.POWERSHELL)
        self.assertIn("HKLM:", mesh.STOP_WINDOWS)
        self.assertIn("WOW6432Node", mesh.STOP_WINDOWS)

        def disconnected(payload, wanted, timeout=15):
            if payload["action"] == "runcommands":
                return {"action": "runcommands", "result": "Agent not connected"}
            return {"action": payload["action"], "result": "ok"}

        with patch.object(mesh, "_request", side_effect=disconnected) as request:
            mesh.remove_device(MAC)
        self.assertEqual(request.call_args.args[0]["action"], "removedevices")

    def test_device_ids_are_checked_before_asking_meshcentral(self):
        with patch.object(mesh, "_request") as request:
            self.assertIsNone(mesh.get_device(GROUP, "../../etc"))
        request.assert_not_called()

    @patch("support.mesh.get_login_token", return_value="login-token")
    def test_session_links_give_synced_technicians_rights_to_that_computer(self, _):
        user = baker.make(User, username="sam")
        core = patch(
            "support.mesh.get_core_settings",
            return_value=type(
                "Core",
                (),
                {
                    "mesh_site": "https://mesh.example.com/",
                    "mesh_token": "key",
                    "sync_mesh_with_trmm": True,
                    "mesh_api_superuser": "tactical",
                },
            )(),
        )
        sent = []

        def fake_request(payload, wanted, timeout=15):
            sent.append(payload)
            if payload["action"] == "users":
                return {"action": "users", "users": [{"_id": user.mesh_user_id}]}
            return {"action": "adddeviceuser", "result": "ok"}

        with core, patch.object(mesh, "_request", side_effect=fake_request):
            url = mesh.session_url(WINDOWS_PC, user, "desktop")
        self.assertEqual(
            url,
            "https://mesh.example.com/?login=login-token&gotonode="
            f"{WINDOWS_PC['node_id']}&viewmode=11&hide=31",
        )
        self.assertEqual(sent[1]["nodeid"], WINDOWS_PC["node_id"])
        self.assertEqual(sent[1]["userids"], [user.mesh_user_id])
        self.assertFalse(sent[1]["rights"] & 7)  # no group-management rights

        with core, patch.object(
            mesh, "_request", return_value={"action": "users", "users": []}
        ):
            with self.assertRaisesMessage(mesh.MeshError, "isn’t ready yet"):
                mesh.session_url(WINDOWS_PC, user, "terminal")


def computers(*devices):
    """Fresh copies, so nothing a request does to them leaks into the next test."""
    return patch("support.mesh.list_devices", return_value=[dict(d) for d in devices])


@override_settings(**TEST_SETTINGS)
@patch("support.mesh.find_group", return_value=GROUP)
class DeviceConsoleTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = baker.make(User, is_superuser=True)
        baker.make(User, is_installer_user=True)
        self.site = baker.make(Site, name="Main office", client__name="Acme")
        self.client.force_login(self.admin)
        self.url = f"/support/manage/devices/{WINDOWS_PC['id']}/"
        # The list asks for screen previews in the background. Tests don't run Celery.
        self.ticket = patch(
            "support.generation.monitoring_ticket",
            return_value=(
                "https://api.example.com/support/generated/",
                "synthetic-private-ticket",
            ),
        ).start()
        self.capture = patch("support.tasks.capture_screen.delay").start()
        self.addCleanup(patch.stopall)

    def test_people_without_console_access_are_sent_to_sign_in(self, _):
        self.client.logout()
        self.assertRedirects(
            self.client.get(self.url), "/support/manage/", fetch_redirect_response=False
        )

    def test_the_old_devices_page_opens_the_connected_group(self, _):
        self.assertRedirects(
            self.client.get("/support/manage/devices/"),
            "/support/manage/?group=connected",
            fetch_redirect_response=False,
        )

    def test_sessions_list_computers_and_waiting_codes(self, _):
        make_invitation(self.admin, self.site, label="Printer trouble")
        with computers(WINDOWS_PC, {**MAC, "online": False}):
            response = self.client.get("/support/manage/")
        self.assertContains(response, "FRONT-DESK")
        self.assertContains(response, "Danas-MacBook.local")
        self.assertContains(response, "Printer trouble")
        self.assertContains(response, 'data-live="10"')
        counts = {g["key"]: g["count"] for g in response.context["groups"]}
        self.assertEqual(
            (counts["all"], counts["connected"], counts["waiting"], counts["offline"]),
            (3, 1, 1, 1),
        )
        with computers(WINDOWS_PC, MAC):
            response = self.client.get("/support/manage/?group=waiting")
        hidden = {r["title"]: r["hidden"] for r in response.context["rows"]}
        self.assertEqual(
            hidden,
            {"FRONT-DESK": True, "Danas-MacBook.local": True, "Printer trouble": False},
        )

    def test_a_computer_takes_over_the_session_of_the_code_it_came_from(self, _):
        code = make_invitation(
            self.admin,
            self.site,
            label="Ledger review for Acme",
            downloaded_from="203.0.113.5",
            downloaded_at=timezone.now() - timedelta(minutes=5),
        )
        arrived = {
            **WINDOWS_PC,
            "ip": "::ffff:203.0.113.5",
            "first_seen": timezone.now(),
        }
        with computers(arrived):
            response = self.client.get("/support/manage/")
        session = SupportSession.objects.get(device_id=WINDOWS_PC["id"])
        self.assertEqual((session.code, session.name), (code, "Ledger review for Acme"))
        titles = [r["title"] for r in response.context["rows"]]
        # The code no longer waits on its own: the computer carries its session.
        self.assertEqual(titles, ["Ledger review for Acme"])
        with computers(arrived):
            page = self.client.get(self.url)
        self.assertContains(page, "Code ending 789")
        self.assertContains(page, "Acme / Main office")
        # Technicians who can't see that client get the computer's own name.
        role = baker.make(Role, can_use_mesh=True, can_install_agents=True)
        role.can_view_sites.add(baker.make(Site))
        self.client.force_login(baker.make(User, role=role))
        with computers(arrived):
            response = self.client.get("/support/manage/")
        self.assertEqual([r["title"] for r in response.context["rows"]], ["FRONT-DESK"])
        self.assertNotContains(response, "Ledger review for Acme")
        self.assertNotContains(response, "Acme")

    def test_computers_from_other_addresses_or_much_later_are_not_matched(self, _):
        make_invitation(
            self.admin,
            self.site,
            label="Someone else",
            downloaded_from="203.0.113.5",
            downloaded_at=timezone.now() - timedelta(hours=6),
        )
        with computers(
            {**WINDOWS_PC, "ip": "203.0.113.5", "first_seen": timezone.now()},
            {**MAC, "ip": "198.51.100.7", "first_seen": timezone.now()},
        ):
            self.client.get("/support/manage/")
        self.assertFalse(SupportSession.objects.filter(code__isnull=False).exists())

    def test_other_clients_names_stay_hidden_from_site_limited_technicians(self, _):
        SupportHandoff.objects.create(
            device_id=WINDOWS_PC["id"],
            device_name="FRONT-DESK",
            site=self.site,
            agent_type="workstation",
        )
        with computers(WINDOWS_PC):
            self.assertContains(self.client.get("/support/manage/"), "Acme")
        role = baker.make(Role, can_use_mesh=True, can_install_agents=True)
        role.can_view_sites.add(baker.make(Site))
        self.client.force_login(baker.make(User, role=role))
        with computers(WINDOWS_PC):
            response = self.client.get("/support/manage/")
        self.assertContains(response, "Installing")
        self.assertNotContains(response, "Acme")

    def test_meshcentral_outage_keeps_codes_working(self, find_group):
        find_group.side_effect = mesh.MeshError("MeshCentral isn’t responding.")
        make_invitation(self.admin, self.site, label="Printer trouble")
        response = self.client.get("/support/manage/")
        self.assertContains(response, "MeshCentral isn’t responding.")
        self.assertContains(response, "Printer trouble")
        self.assertContains(
            self.client.get(self.url), "MeshCentral isn’t responding.", status_code=503
        )

    def test_missing_group_points_admins_to_settings(self, find_group):
        find_group.return_value = None
        self.assertContains(
            self.client.get("/support/manage/"), "The support group isn’t set up yet"
        )

    def test_computers_outside_the_support_group_are_not_found(self, _):
        with computers(MAC):
            self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(
            self.client.get("/support/manage/devices/bad%20id/").status_code, 404
        )

    def test_join_opens_meshcentral_and_is_audited(self, _):
        link = "https://mesh.example.com/?login=t&gotonode=x&viewmode=11&hide=31"
        with computers(WINDOWS_PC), patch(
            "support.mesh.session_url", return_value=link
        ) as session:
            response = self.client.post(
                self.url, {"action": "connect", "view": "desktop"}
            )
        self.assertRedirects(response, link, fetch_redirect_response=False)
        self.assertEqual(session.call_args.args[0]["node_id"], WINDOWS_PC["node_id"])
        self.assertEqual(session.call_args.args[1:], (self.admin, "desktop"))
        # Sessions open in place, so every workspace page allows MeshCentral as a
        # form target.
        mesh_site = type("Core", (), {"mesh_site": "https://mesh.example.com"})()
        with computers(WINDOWS_PC), patch(
            "support.host.get_core_settings", return_value=mesh_site
        ):
            for url in (self.url, "/support/manage/"):
                policy = self.client.get(url)["Content-Security-Policy"]
                self.assertIn("form-action 'self' https://mesh.example.com;", policy)
        self.assertTrue(
            AuditLog.objects.filter(
                agent="FRONT-DESK", action="remote_session"
            ).exists()
        )
        with computers(WINDOWS_PC), patch("support.mesh.session_url") as session:
            response = self.client.post(self.url, {"action": "connect", "view": "x"})
        self.assertEqual(response.status_code, 404)
        session.assert_not_called()

    def test_commands_are_kept_in_the_session_history_and_audited(self, _):
        with computers(WINDOWS_PC), patch(
            "support.mesh.run_command", return_value="Windows 11\n"
        ) as run:
            response = self.client.post(
                self.url,
                {
                    "action": "run",
                    "shell": mesh.POWERSHELL,
                    "command": "Get-ComputerInfo",
                },
                HTTP_ACCEPT="application/json",
            )
        run_json = response.json()["run"]
        self.assertEqual(
            (run_json["command"], run_json["output"], run_json["prompt"]),
            ("Get-ComputerInfo", "Windows 11\n", "PS>"),
        )
        self.assertEqual(run.call_args.args[1:], ("Get-ComputerInfo", mesh.POWERSHELL))
        self.assertEqual(run.call_args.kwargs, {"timeout": 60})
        self.assertTrue(
            AuditLog.objects.filter(
                agent="FRONT-DESK", action="execute_command"
            ).exists()
        )
        with computers(WINDOWS_PC), patch(
            "support.mesh.run_command", return_value=None
        ):
            response = self.client.post(
                self.url, {"action": "run", "command": "Start-Sleep 90"}
            )
        # Without JavaScript the page comes back with the history in its console.
        self.assertContains(response, "Windows 11")
        self.assertContains(response, "No reply within 60 seconds")
        self.assertEqual(response.context["tab"], "commands")
        self.assertEqual(SupportRun.objects.count(), 2)
        self.assertTrue(SupportRun.objects.get(command="Start-Sleep 90").no_reply)

    def test_running_commands_needs_the_send_command_permission(self, _):
        role = baker.make(
            Role, can_use_mesh=True, can_install_agents=True, can_send_cmd=False
        )
        self.client.force_login(baker.make(User, role=role))
        with computers(WINDOWS_PC), patch("support.mesh.run_command") as run:
            response = self.client.post(
                self.url,
                {"action": "run", "command": "whoami"},
                HTTP_ACCEPT="application/json",
            )
        self.assertEqual(response.status_code, 403)
        self.assertIn("permission", response.json()["error"])
        run.assert_not_called()

    def test_sessions_can_be_renamed_and_noted(self, _):
        with computers(WINDOWS_PC):
            response = self.client.post(
                self.url, {"action": "rename", "name": "Dana’s laptop"}
            )
        self.assertRedirects(
            response, f"{self.url}?tab=general", fetch_redirect_response=False
        )
        with computers(WINDOWS_PC):
            response = self.client.post(
                self.url, {"action": "note", "note": "Printer driver reinstalled."}
            )
        self.assertRedirects(
            response, f"{self.url}?tab=notes", fetch_redirect_response=False
        )
        with computers(WINDOWS_PC):
            page = self.client.get(f"{self.url}?tab=timeline")
        self.assertContains(page, "<h1>Dana’s laptop</h1>", html=False)
        self.assertContains(page, "Printer driver reinstalled.")
        self.assertContains(page, "Named the session “Dana’s laptop”.")
        self.assertEqual(page.context["tab"], "timeline")
        self.assertEqual(SupportNote.objects.get().author, self.admin)
        with computers(WINDOWS_PC):
            response = self.client.post(self.url, {"action": "note", "note": " "})
        self.assertContains(response, "Write the note first.", status_code=400)

    def test_windows_install_keeps_the_token_out_of_sight(self, _):
        with computers(WINDOWS_PC), patch(
            "support.mesh.run_command", return_value=None
        ) as run:
            response = self.client.post(
                self.url,
                {"action": "install", "site": self.site.pk, "agent_type": "server"},
            )
        self.assertRedirects(
            response,
            f"{self.url}?sent=sent&tab=monitoring",
            fetch_redirect_response=False,
        )
        command, shell = run.call_args.args[1:3]
        self.assertEqual(shell, mesh.POWERSHELL)
        self.assertEqual(run.call_args.kwargs["timeout"], 20)
        self.assertIn("-Verb RunAs", command)
        self.assertIn("msiexec.exe", command)
        self.assertIn("/passive /norestart", command)
        self.assertIn("WinHttp.WinHttpRequest.5.1", command)
        self.assertNotIn("--auth", command)
        self.assertNotIn("tacticalagent-v", command)
        self.assertEqual(self.ticket.call_args.kwargs["site"], self.site.pk)
        self.assertEqual(self.ticket.call_args.kwargs["client"], self.site.client_id)
        self.assertIn("JustConnect-install.log", command)
        handoff = SupportHandoff.objects.get()
        self.assertEqual((handoff.site, handoff.agent_type), (self.site, "server"))
        with computers(WINDOWS_PC):
            self.assertContains(
                self.client.get(self.url), "Installing in Acme / Main office"
            )

    def test_windows_install_reports_their_answer(self, _):
        data = {"action": "install", "site": self.site.pk, "agent_type": "workstation"}
        with computers(WINDOWS_PC), patch(
            "support.mesh.run_command", return_value="Install started."
        ):
            response = self.client.post(self.url, data)
        self.assertRedirects(
            response,
            f"{self.url}?sent=started&tab=monitoring",
            fetch_redirect_response=False,
        )
        with computers(WINDOWS_PC):
            self.assertContains(
                self.client.get(f"{self.url}?sent=started"), "They chose Yes"
            )
        SupportHandoff.objects.all().delete()
        with computers(WINDOWS_PC), patch(
            "support.mesh.run_command",
            return_value="Install not started: The operation was canceled by the user.",
        ):
            response = self.client.post(self.url, data)
        self.assertContains(response, "They chose No", status_code=409)
        self.assertFalse(SupportHandoff.objects.exists())

    def test_install_progress_explains_a_busy_support_app(self, _):
        with computers(WINDOWS_PC), patch(
            "support.mesh.run_command", return_value=None
        ) as run:
            response = self.client.post(self.url, {"action": "progress"})
        self.assertContains(response, "Windows prompt is still waiting")
        self.assertEqual(response.context["tab"], "monitoring")
        self.assertEqual(run.call_args.kwargs["timeout"], 15)
        self.assertIn("tacticalrmm-support-install.txt", run.call_args.args[1])
        # The install check is plumbing, so it stays out of the command history.
        self.assertFalse(SupportRun.objects.exists())

    def test_install_is_limited_to_sites_the_technician_can_see(self, _):
        role = baker.make(Role, can_use_mesh=True, can_install_agents=True)
        role.can_view_sites.add(baker.make(Site))
        self.client.force_login(baker.make(User, role=role))
        with computers(WINDOWS_PC), patch("support.mesh.run_command") as run:
            response = self.client.post(
                self.url,
                {
                    "action": "install",
                    "site": self.site.pk,
                    "agent_type": "workstation",
                },
            )
        self.assertEqual(response.status_code, 400)
        run.assert_not_called()

    def test_mac_install_runs_as_its_own_job(self, _):
        url = f"/support/manage/devices/{MAC['id']}/"
        data = {"action": "install", "site": self.site.pk, "agent_type": "workstation"}
        with computers(MAC), patch(
            "support.install.token_is_valid", return_value=("", False)
        ):
            response = self.client.post(url, data)
        self.assertContains(response, "code signing token", status_code=409)
        with computers(MAC), patch(
            "support.install.token_is_valid", return_value=("abc", True)
        ), patch("support.mesh.run_command", return_value="Install started.\n") as run:
            self.client.post(url, data)
        command, shell = run.call_args.args[1:3]
        self.assertEqual(shell, mesh.SHELL)
        # The RMM installer removes the support app, so the install can't be its child.
        self.assertIn("launchctl bootstrap system", command)
        self.assertIn("hw.optional.arm64", command)
        self.assertIn('-K "$d/curl.conf"', command)
        self.assertIn(f"--site-id {self.site.pk} --agent-type workstation", command)

    def test_checked_in_agent_finishes_the_move(self, _):
        SupportHandoff.objects.create(
            device_id=WINDOWS_PC["id"],
            device_name="FRONT-DESK",
            site=self.site,
            agent_type="workstation",
        )
        baker.make("agents.Agent", site=self.site, hostname="front-desk")
        with computers(WINDOWS_PC):
            response = self.client.get(self.url)
        self.assertContains(response, "Now monitored as")
        with computers(WINDOWS_PC), patch("support.mesh.remove_device") as remove:
            response = self.client.post(
                self.url, {"action": "remove", "group": "monitoring"}
            )
        self.assertRedirects(
            response,
            "/support/manage/?group=monitoring&removed=1",
            fetch_redirect_response=False,
        )
        self.assertEqual(remove.call_args.args[0]["node_id"], WINDOWS_PC["node_id"])
        self.assertIsNotNone(SupportHandoff.objects.get().finished_at)
        self.assertIsNotNone(SupportHandoff.objects.get().agent)

    def test_agents_match_by_short_name_and_only_once(self, _):
        handoff = SupportHandoff.objects.create(
            device_id=MAC["id"],
            device_name="Danas-MacBook",
            site=self.site,
            agent_type="workstation",
        )
        linked = baker.make("agents.Agent", site=self.site, hostname="danas-macbook")
        SupportHandoff.objects.create(
            device_id="other",
            device_name="x",
            site=self.site,
            agent_type="workstation",
            agent=linked,
        )
        self.assertIsNone(handoff.find_agent())
        agent = baker.make(
            "agents.Agent", site=self.site, hostname="Danas-MacBook.local"
        )
        self.assertEqual(handoff.find_agent(), agent)

    def test_installs_that_never_check_in_stop_waiting(self, _):
        SupportHandoff.objects.create(
            device_id=WINDOWS_PC["id"],
            device_name="FRONT-DESK",
            site=self.site,
            agent_type="workstation",
            requested_at=timezone.now() - timedelta(minutes=25),
        )
        with computers(WINDOWS_PC):
            response = self.client.get(self.url)
        self.assertContains(response, "No agent has checked in")
        self.assertNotContains(response, "This page updates when the agent checks in")
        self.assertEqual(
            [r["monitoring"] for r in response.context["rows"]], ["stalled"]
        )

    def test_offline_computers_cannot_run_commands(self, _):
        offline = {**WINDOWS_PC, "online": False}
        with computers(offline), patch("support.mesh.run_command") as run:
            response = self.client.post(self.url, {"action": "run", "command": "dir"})
        self.assertContains(response, "offline", status_code=409)
        run.assert_not_called()
        with computers(offline), patch("support.mesh.remove_device"):
            response = self.client.post(self.url, {"action": "remove"})
        self.assertRedirects(
            response,
            "/support/manage/?removed=offline",
            fetch_redirect_response=False,
        )


@override_settings(**TEST_SETTINGS)
class ToolboxTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = baker.make(User, is_superuser=True)
        self.client.force_login(self.admin)
        # The list asks for screen previews in the background. Tests don't run Celery.
        self.capture = patch("support.tasks.capture_screen.delay").start()
        self.addCleanup(patch.stopall)

    def test_commands_are_saved_edited_and_deleted(self):
        self.assertContains(
            self.client.get("/support/manage/toolbox/"), "Your toolbox is empty"
        )
        data = {
            "action": "save",
            "name": "Disk space",
            "shell": 2,
            "command": "Get-PSDrive",
        }
        response = self.client.post("/support/manage/toolbox/", data)
        self.assertRedirects(
            response, "/support/manage/toolbox/?saved=1", fetch_redirect_response=False
        )
        item = SupportCommand.objects.get()
        self.assertEqual(
            (item.name, item.shell, item.created_by), ("Disk space", 2, self.admin)
        )
        self.assertContains(
            self.client.get(f"/support/manage/toolbox/?edit={item.pk}"),
            "Edit “Disk space”",
        )
        self.client.post(
            "/support/manage/toolbox/",
            {**data, "id": item.pk, "name": "Free space"},
        )
        self.assertEqual(SupportCommand.objects.get().name, "Free space")
        self.client.post(
            "/support/manage/toolbox/", {"action": "delete", "id": item.pk}
        )
        self.assertFalse(SupportCommand.objects.exists())
        self.assertEqual(
            AuditLog.objects.filter(message__contains="support toolbox").count(), 3
        )

    def test_bad_input_is_explained(self):
        response = self.client.post(
            "/support/manage/toolbox/",
            {"action": "save", "name": "", "shell": 2, "command": "dir"},
        )
        self.assertContains(response, "Give the command a name", status_code=400)
        response = self.client.post(
            "/support/manage/toolbox/",
            {"action": "save", "name": "x", "shell": 9, "command": "dir"},
        )
        self.assertContains(response, "Choose where the command runs", status_code=400)

    def test_changing_the_toolbox_needs_the_manage_scripts_permission(self):
        item = baker.make(SupportCommand, name="Disk space", shell=2, command="dir")
        role = baker.make(Role, can_use_mesh=True, can_install_agents=True)
        self.client.force_login(baker.make(User, role=role))
        response = self.client.get("/support/manage/toolbox/")
        self.assertContains(response, "Disk space")
        self.assertNotContains(response, "Save command")
        response = self.client.post(
            "/support/manage/toolbox/", {"action": "delete", "id": item.pk}
        )
        self.assertEqual(response.status_code, 403)
        self.assertTrue(SupportCommand.objects.exists())

    def test_starter_commands_are_added_once(self):
        self.client.post("/support/manage/toolbox/", {"action": "starter"})
        count = SupportCommand.objects.count()
        self.assertGreater(count, 10)
        self.assertTrue(SupportCommand.objects.filter(shell=3).exists())
        self.client.post("/support/manage/toolbox/", {"action": "starter"})
        self.assertEqual(SupportCommand.objects.count(), count)

    @patch("support.mesh.find_group", return_value=GROUP)
    def test_sessions_offer_saved_commands_for_their_system(self, _):
        windows = baker.make(
            SupportCommand, name="Disk space", shell=2, command="Get-PSDrive"
        )
        baker.make(SupportCommand, name="Mac disk", shell=3, command="df -h")
        url = f"/support/manage/devices/{WINDOWS_PC['id']}/"
        with computers(WINDOWS_PC):
            response = self.client.get(url)
        self.assertContains(response, "Disk space")
        self.assertNotContains(response, "Mac disk")
        with computers(WINDOWS_PC), patch(
            "support.mesh.run_command", return_value="C: 10 GB"
        ) as run:
            self.client.post(url, {"action": "run", "saved": windows.pk, "command": ""})
        self.assertEqual(run.call_args.args[1:], ("Get-PSDrive", 2))

    @patch("support.mesh.find_group", return_value=GROUP)
    def test_commands_can_be_saved_from_a_session(self, _):
        url = f"/support/manage/devices/{WINDOWS_PC['id']}/"
        with computers(WINDOWS_PC):
            response = self.client.post(
                url,
                {"action": "save", "name": "Who", "shell": 1, "command": "whoami"},
                HTTP_ACCEPT="application/json",
            )
        self.assertEqual(
            response.json(), {"saved": "Who", "id": SupportCommand.objects.get().pk}
        )
        self.assertEqual(SupportCommand.objects.get().shell, 1)
        with computers(WINDOWS_PC):
            response = self.client.post(
                url,
                {"action": "save", "name": "Mac", "shell": 3, "command": "id"},
                HTTP_ACCEPT="application/json",
            )
        self.assertEqual(response.status_code, 400)


@override_settings(**TEST_SETTINGS)
@patch("support.mesh.find_group", return_value=GROUP)
class SettingsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = baker.make(User, is_superuser=True)
        self.client.force_login(self.admin)

    def test_settings_show_the_group_id_for_building_the_apps(self, _):
        response = self.client.get("/support/manage/settings/")
        self.assertContains(response, "SUPPORT_MESH_GROUP_ID")
        self.assertContains(response, "GroupId")

    def test_group_can_be_created_by_admins_only(self, find_group):
        find_group.return_value = None
        self.assertContains(
            self.client.get("/support/manage/settings/"), "Create support group"
        )
        with patch("support.mesh.create_group", return_value=GROUP) as create:
            response = self.client.post(
                "/support/manage/settings/", {"action": "group"}
            )
        self.assertRedirects(
            response,
            "/support/manage/settings/?saved=group",
            fetch_redirect_response=False,
        )
        create.assert_called_once()
        role = baker.make(Role, can_use_mesh=True, can_install_agents=True)
        self.client.force_login(baker.make(User, role=role))
        with patch("support.mesh.create_group") as create:
            self.assertEqual(
                self.client.post(
                    "/support/manage/settings/", {"action": "group"}
                ).status_code,
                403,
            )
        create.assert_not_called()


def jpeg(width, height, color=(200, 40, 40)):
    out = io.BytesIO()
    Image.new("RGB", (width, height), color).save(out, "JPEG")
    return out.getvalue()


def command(cmd, payload=b""):
    size = 4 + len(payload)
    return bytes([cmd >> 8, cmd & 0xFF, size >> 8, size & 0xFF]) + payload


def tile(x, y, data):
    return command(3, bytes([x >> 8, x & 0xFF, y >> 8, y & 0xFF]) + data)


def jumbo_tile(x, y, data):
    size = 8 + len(data)
    header = bytes([0, 27, 0, 8, 0, size >> 16 & 0xFF, size >> 8 & 0xFF, size & 0xFF])
    return header + bytes([0, 3, 0, 0, x >> 8, x & 0xFF, y >> 8, y & 0xFF]) + data


class FakeRelay:
    """Replays what a support app sends through MeshCentral's desktop tunnel."""

    def __init__(self, messages):
        self.messages, self.sent = list(messages), []

    async def recv(self):
        if self.messages:
            return self.messages.pop(0)
        await asyncio.sleep(5)

    async def send(self, data):
        self.sent.append(data)


def read(messages):
    relay = FakeRelay(messages)
    return asyncio.run(screens._read_screen(relay)), relay


def preview(device_id, data=None):
    from django.core.cache import cache as django_cache

    django_cache.set(
        f"support:screen:{device_id}",
        {"jpeg": data or jpeg(480, 300), "at": timezone.now().timestamp()},
        600,
    )


@override_settings(**TEST_SETTINGS)
@patch("support.mesh.find_group", return_value=GROUP)
class ScreenPreviewTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = baker.make(User, is_superuser=True)
        self.client.force_login(self.admin)
        self.capture = patch("support.tasks.capture_screen.delay").start()
        self.addCleanup(patch.stopall)
        self.url = f"/support/manage/devices/{WINDOWS_PC['id']}/"

    def test_the_desktop_tunnel_is_driven_like_meshcentral_does(self, _):
        left, right = jpeg(32, 40), jpeg(32, 40, (0, 0, 200))
        (tiles, screen), relay = read(
            [
                "c",
                command(7, bytes([0, 64, 0, 40])),
                tile(0, 0, left),
                jumbo_tile(32, 0, right),
            ]
        )
        # The desktop protocol, then JPEG at full scale for a small screen, unpause, refresh.
        self.assertEqual(relay.sent[0], "2")
        self.assertEqual(relay.sent[1], bytes([0, 5, 0, 10, 1, 40, 4, 0, 3, 232]))
        self.assertEqual(relay.sent[2:], [bytes([0, 8, 0, 5, 0]), bytes([0, 6, 0, 4])])
        self.assertEqual(screen, (64, 40, 1024))
        self.assertEqual(
            [(x, y, data) for x, y, data in tiles], [(0, 0, left), (32, 0, right)]
        )
        picture = Image.open(io.BytesIO(screens.stitch(tiles, screen)))
        self.assertEqual(picture.size, (64, 40))
        self.assertGreater(picture.getpixel((5, 20))[0], 150)
        self.assertGreater(picture.getpixel((60, 20))[2], 150)

    def test_large_screens_are_asked_for_small_and_cut_to_size(self, _):
        big = jumbo_tile(0, 0, jpeg(960, 288))
        # A tile split across two messages is put back together.
        (tiles, screen), relay = read(
            ["c", command(7, bytes([10, 0, 1, 32])), big[:5000], big[5000:]]
        )
        self.assertEqual(screen, (2560, 288, 384))
        self.assertEqual(relay.sent[1][6:8], bytes([1, 128]))
        picture = Image.open(io.BytesIO(screens.stitch(tiles, screen)))
        self.assertEqual(picture.size, (480, 54))

    def test_a_consent_prompt_stops_the_preview(self, _):
        with self.assertRaises(screens.Consent):
            read(
                [
                    "c",
                    '{"ctrlChannel":"102938","type":"console","msg":"Waiting for user to grant access...","msgid":1}',
                ]
            )
        self.assertIsNone(screens.stitch([(0, 0, b"not a picture")]))

    def test_previews_are_asked_for_once_and_not_where_people_are_asked(self, _):
        self.assertTrue(screens.request(dict(WINDOWS_PC)))
        self.assertFalse(screens.request(dict(WINDOWS_PC)))
        self.capture.assert_called_once_with(
            {"id": WINDOWS_PC["id"], "node_id": WINDOWS_PC["node_id"]}
        )
        self.assertFalse(screens.request({**MAC, "online": False}))
        self.assertFalse(screens.request(dict(MAC), group_consent=8))
        self.assertFalse(screens.request({**MAC, "consent": 64}))
        self.assertTrue(screens.request(dict(MAC)))

    def test_captures_are_kept_and_failures_wait(self, _):
        picture = jpeg(64, 40)
        with patch(
            "support.screens._tiles", return_value=([(0, 0, picture)], (64, 40, 1024))
        ):
            self.assertIsNotNone(screens.capture(dict(WINDOWS_PC)))
        self.assertIsNotNone(screens.latest(WINDOWS_PC["id"]))
        # Fresh enough for the list, not for the open session a while later.
        self.assertFalse(screens.request(dict(WINDOWS_PC), every=60))
        with patch("support.screens.time.time", return_value=screens.time.time() + 30):
            self.assertTrue(screens.request(dict(WINDOWS_PC), every=20))
        cache.clear()
        with patch("support.screens._tiles", side_effect=OSError):
            self.assertIsNone(screens.capture(dict(MAC)))
        self.assertFalse(screens.request(dict(MAC)))

    def test_the_list_and_the_session_show_the_picture(self, _):
        picture = jpeg(480, 300)
        preview(WINDOWS_PC["id"], picture)
        with computers(WINDOWS_PC):
            response = self.client.get("/support/manage/")
        self.assertContains(response, f"{self.url}screen/?v=")
        with computers(WINDOWS_PC):
            page = self.client.get(self.url)
        self.assertContains(page, "Their screen")
        self.assertContains(page, 'id="screen-preview"')
        image = self.client.get(f"{self.url}screen/")
        self.assertEqual(image["Content-Type"], "image/jpeg")
        self.assertEqual(image.content, picture)
        self.assertIn("no-store", image["Cache-Control"])
        self.client.logout()
        self.assertEqual(self.client.get(f"{self.url}screen/").status_code, 404)

    def test_the_open_session_is_kept_fresher(self, _):
        with computers(WINDOWS_PC, MAC), patch("support.screens.request") as request:
            self.client.get(f"/support/manage/?open={WINDOWS_PC['id']}")
        every = {call.args[0]["id"]: call.args[2] for call in request.call_args_list}
        self.assertEqual(
            every,
            {WINDOWS_PC["id"]: screens.OPEN_SECONDS, MAC["id"]: screens.LIST_SECONDS},
        )

    def test_a_computer_without_a_picture_yet_says_so(self, _):
        with computers(WINDOWS_PC):
            page = self.client.get(self.url)
        self.assertContains(page, "Getting a picture of their screen")
        self.capture.assert_called_once()

    def test_groups_that_ask_people_first_get_no_previews(self, find_group):
        find_group.return_value = {**GROUP, "consent": 8}
        with computers(WINDOWS_PC):
            page = self.client.get(self.url)
        self.assertNotContains(page, "Their screen")
        self.capture.assert_not_called()
        self.assertContains(
            self.client.get("/support/manage/settings/"), "tells or asks people"
        )

    def test_previews_can_be_turned_off_by_admins(self, _):
        preview(WINDOWS_PC["id"])
        role = baker.make(Role, can_use_mesh=True, can_install_agents=True)
        self.client.force_login(baker.make(User, role=role))
        response = self.client.post("/support/manage/settings/", {"action": "previews"})
        self.assertEqual(response.status_code, 403)
        self.assertTrue(SupportBranding.load().screen_previews)
        self.client.force_login(self.admin)
        response = self.client.post("/support/manage/settings/", {"action": "previews"})
        self.assertRedirects(
            response,
            "/support/manage/settings/?saved=previews",
            fetch_redirect_response=False,
        )
        self.assertFalse(SupportBranding.load().screen_previews)
        self.assertTrue(
            AuditLog.objects.filter(
                message__startswith="Turned screen previews off"
            ).exists()
        )
        with computers(WINDOWS_PC):
            response = self.client.get("/support/manage/")
        self.assertNotContains(response, "screen/?v=")
        self.capture.assert_not_called()
        self.assertEqual(self.client.get(f"{self.url}screen/").status_code, 404)
