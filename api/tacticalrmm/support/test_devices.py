import base64
import re
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from model_bakery import baker

from accounts.models import Role, User
from clients.models import Site
from logs.models import AuditLog

from . import mesh
from .models import SupportHandoff
from .tests import TEST_SETTINGS

GROUP = {"id": "mesh//GroupId", "short_id": "GroupId", "name": "TacticalRMM Support"}
WINDOWS_PC = {
    "id": "WinNodeId0000000000000000",
    "node_id": "node//WinNodeId0000000000000000",
    "name": "FRONT-DESK",
    "os": "Microsoft Windows 11 Pro",
    "windows": True,
    "online": True,
}
MAC = {
    "id": "MacNodeId0000000000000000",
    "node_id": "node//MacNodeId0000000000000000",
    "name": "Danas-MacBook.local",
    "os": "macOS 15",
    "windows": False,
    "online": True,
}


def decoded_elevated_script(command):
    encoded = re.search(r"'-EncodedCommand','([^']+)'", command).group(1)
    return base64.b64decode(encoded).decode("utf-16-le")


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
                    {"_id": "node//a", "name": "Alpha", "agent": {"id": 4}, "conn": 1},
                ]
            },
        }
        with patch.object(mesh, "_request", return_value=reply) as request:
            devices = mesh.list_devices(GROUP)
        self.assertEqual(request.call_args.args[0]["meshid"], "mesh//GroupId")
        self.assertEqual([d["name"] for d in devices], ["Alpha", "zeta"])
        self.assertTrue(devices[0]["windows"] and devices[0]["online"])
        self.assertFalse(devices[1]["windows"] or devices[1]["online"])

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

    @patch("support.mesh.time.sleep")
    def test_removal_stops_run_only_agents_before_dropping_them(self, _):
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
            ["uninstallagent", "runcommands", "removedevices"],
        )
        self.assertEqual(sent[1]["cmds"], mesh.STOP_WINDOWS)
        self.assertEqual(sent[1]["type"], mesh.POWERSHELL)

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


@override_settings(**TEST_SETTINGS)
@patch("support.devices.mesh.find_group", return_value=GROUP)
class DeviceConsoleTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = baker.make(User, is_superuser=True)
        baker.make(User, is_installer_user=True)
        self.site = baker.make(Site, name="Main office", client__name="Acme")
        self.client.force_login(self.admin)
        self.url = f"/support/manage/devices/{WINDOWS_PC['id']}/"

    def test_people_without_console_access_are_sent_to_sign_in(self, _):
        self.client.logout()
        self.assertRedirects(
            self.client.get("/support/manage/devices/"),
            "/support/manage/",
            fetch_redirect_response=False,
        )

    @patch("support.devices.mesh.list_devices", return_value=[WINDOWS_PC, MAC])
    def test_list_shows_connected_computers(self, *_):
        response = self.client.get("/support/manage/devices/")
        self.assertContains(response, "FRONT-DESK")
        self.assertContains(response, "Danas-MacBook.local")
        self.assertContains(response, "GroupId")

    def test_group_can_be_created_by_admins_only(self, find_group):
        find_group.return_value = None
        self.assertContains(
            self.client.get("/support/manage/devices/"), "Set up the support group"
        )
        with patch("support.devices.mesh.create_group", return_value=GROUP) as create:
            response = self.client.post("/support/manage/devices/")
        self.assertRedirects(
            response, "/support/manage/devices/", fetch_redirect_response=False
        )
        create.assert_called_once()
        role = baker.make(Role, can_use_mesh=True, can_install_agents=True)
        self.client.force_login(baker.make(User, role=role))
        with patch("support.devices.mesh.create_group") as create:
            self.assertEqual(
                self.client.post("/support/manage/devices/").status_code, 403
            )
        create.assert_not_called()

    def test_meshcentral_outage_is_explained(self, find_group):
        find_group.side_effect = mesh.MeshError("MeshCentral isn’t responding.")
        response = self.client.get("/support/manage/devices/")
        self.assertContains(response, "MeshCentral isn’t responding.", status_code=503)

    @patch("support.devices.mesh.get_device", return_value=None)
    def test_computers_outside_the_support_group_are_not_found(self, *_):
        self.assertEqual(self.client.get(self.url).status_code, 404)

    @patch("support.devices.mesh.get_device", return_value=WINDOWS_PC)
    def test_commands_run_show_output_and_are_audited(self, *_):
        with patch(
            "support.devices.mesh.run_command", return_value="Windows 11\n"
        ) as run:
            response = self.client.post(
                self.url,
                {
                    "action": "run",
                    "shell": mesh.POWERSHELL,
                    "command": "Get-ComputerInfo",
                },
            )
        self.assertContains(response, "Windows 11")
        run.assert_called_once_with(WINDOWS_PC, "Get-ComputerInfo", mesh.POWERSHELL)
        self.assertTrue(
            AuditLog.objects.filter(
                agent="FRONT-DESK", action="execute_command"
            ).exists()
        )
        with patch("support.devices.mesh.run_command", return_value=None):
            response = self.client.post(
                self.url, {"action": "run", "command": "Start-Sleep 90"}
            )
        self.assertContains(response, "No reply within 60 seconds")

    @patch("support.devices.mesh.get_device", return_value=WINDOWS_PC)
    def test_running_commands_needs_the_send_command_permission(self, *_):
        role = baker.make(
            Role, can_use_mesh=True, can_install_agents=True, can_send_cmd=False
        )
        self.client.force_login(baker.make(User, role=role))
        with patch("support.devices.mesh.run_command") as run:
            response = self.client.post(
                self.url, {"action": "run", "command": "whoami"}
            )
        self.assertEqual(response.status_code, 403)
        run.assert_not_called()

    @patch("support.devices.mesh.get_device", return_value=WINDOWS_PC)
    def test_windows_install_asks_for_elevation_and_tracks_the_move(self, *_):
        with patch("support.devices.mesh.run_command", return_value="") as run:
            response = self.client.post(
                self.url,
                {"action": "install", "site": self.site.pk, "agent_type": "server"},
            )
        self.assertRedirects(
            response, f"{self.url}?sent=install", fetch_redirect_response=False
        )
        command, shell = run.call_args.args[1:3]
        self.assertEqual(shell, mesh.POWERSHELL)
        self.assertFalse(run.call_args.kwargs["reply"])
        self.assertIn("-Verb RunAs", command)
        script = decoded_elevated_script(command)
        self.assertIn(f"$siteid = '{self.site.pk}'", script)
        self.assertIn(f"$clientid = '{self.site.client_id}'", script)
        self.assertIn("$agenttype = '\"server\"'", script)
        self.assertNotIn("tokenchange", script)
        self.assertIn("support-install.log", script)
        handoff = SupportHandoff.objects.get()
        self.assertEqual((handoff.site, handoff.agent_type), (self.site, "server"))
        self.assertContains(
            self.client.get(self.url), "Installing in Acme / Main office"
        )

    @patch("support.devices.mesh.get_device", return_value=WINDOWS_PC)
    def test_install_is_limited_to_sites_the_technician_can_see(self, *_):
        role = baker.make(Role, can_use_mesh=True, can_install_agents=True)
        role.can_view_sites.add(baker.make(Site))
        self.client.force_login(baker.make(User, role=role))
        with patch("support.devices.mesh.run_command") as run:
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

    @patch("support.devices.mesh.get_device", return_value=MAC)
    def test_mac_install_needs_code_signing(self, *_):
        url = f"/support/manage/devices/{MAC['id']}/"
        data = {"action": "install", "site": self.site.pk, "agent_type": "workstation"}
        with patch("support.install.token_is_valid", return_value=("", False)):
            response = self.client.post(url, data)
        self.assertContains(response, "code signing token", status_code=409)
        with patch("support.install.token_is_valid", return_value=("abc", True)), patch(
            "support.devices.mesh.run_command", return_value=""
        ) as run:
            self.client.post(url, data)
        command, shell = run.call_args.args[1:3]
        self.assertEqual(shell, mesh.SHELL)
        self.assertIn("uname -m", command)
        self.assertIn("nohup", command)
        self.assertIn(f" {self.site.pk} workstation ", command)

    @patch("support.devices.mesh.get_device", return_value=WINDOWS_PC)
    def test_checked_in_agent_finishes_the_move(self, *_):
        SupportHandoff.objects.create(
            device_id=WINDOWS_PC["id"],
            device_name="FRONT-DESK",
            site=self.site,
            agent_type="workstation",
        )
        baker.make("agents.Agent", site=self.site, hostname="front-desk")
        response = self.client.get(self.url)
        self.assertContains(response, "now monitored as")
        with patch("support.devices.mesh.remove_device") as remove:
            response = self.client.post(self.url, {"action": "remove"})
        self.assertRedirects(
            response,
            "/support/manage/devices/?removed=1",
            fetch_redirect_response=False,
        )
        remove.assert_called_once_with(WINDOWS_PC)
        self.assertIsNotNone(SupportHandoff.objects.get().finished_at)
        self.assertIsNotNone(SupportHandoff.objects.get().agent)

    @patch(
        "support.devices.mesh.get_device", return_value={**WINDOWS_PC, "online": False}
    )
    def test_offline_computers_cannot_run_commands(self, *_):
        with patch("support.devices.mesh.run_command") as run:
            response = self.client.post(self.url, {"action": "run", "command": "dir"})
        self.assertContains(response, "offline", status_code=409)
        run.assert_not_called()
