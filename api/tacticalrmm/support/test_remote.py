import json
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from model_bakery import baker

from accounts.models import Role, User
from logs.models import AuditLog

from . import mesh
from .test_devices import GROUP, MAC, WINDOWS_PC, computers
from .tests import TEST_SETTINGS

CORE = type(
    "Core",
    (),
    {
        "mesh_site": "https://mesh.example.com",
        "mesh_token": "ab" * 48,
        "sync_mesh_with_trmm": True,
        "mesh_api_superuser": "tactical",
    },
)()
RELAY = "wss://mesh.example.com/meshrelay.ashx?browser=1&p=2&nodeid=node//x&id=1&auth=c"


@override_settings(**TEST_SETTINGS)
@patch("support.mesh.find_group", return_value=GROUP)
class SupportViewerTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = baker.make(User, is_superuser=True)
        self.client.force_login(self.admin)
        patch("support.tasks.capture_screen.delay").start()
        self.addCleanup(patch.stopall)
        self.url = f"/support/manage/devices/{WINDOWS_PC['id']}/control/"

    def test_the_viewer_opens_for_technicians(self, _):
        with computers(WINDOWS_PC), patch(
            "support.remote.get_core_settings", return_value=CORE
        ):
            response = self.client.get(self.url)
        self.assertContains(response, 'data-theme="dark"')
        self.assertContains(response, "Ctrl+Alt+Del")
        self.assertContains(response, "Fit to window")
        data = response.context["data"]
        self.assertEqual(
            (data["computer"], data["platform"], data["online"]),
            ("FRONT-DESK", "windows", True),
        )
        self.assertEqual(data["relay_url"], f"{self.url}relay/")
        policy = response["Content-Security-Policy"]
        self.assertIn("connect-src 'self' wss://mesh.example.com;", policy)
        self.assertIn("form-action 'self' https://mesh.example.com;", policy)
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_the_session_page_joins_through_the_viewer(self, _):
        with computers(WINDOWS_PC):
            page = self.client.get(f"/support/manage/devices/{WINDOWS_PC['id']}/")
        self.assertContains(page, f'href="{self.url}"', count=1)
        self.assertContains(page, 'data-window="control-')

    def test_starting_control_opens_a_desktop_tunnel_and_is_audited(self, _):
        with computers(WINDOWS_PC), patch(
            "support.mesh.desktop_relay", return_value=RELAY
        ) as relay:
            response = self.client.post(f"{self.url}relay/")
        self.assertEqual(response.json(), {"relay": RELAY})
        self.assertEqual(relay.call_args.args[0], WINDOWS_PC["node_id"])
        self.assertEqual(relay.call_args.args[1], self.admin)
        self.assertEqual(relay.call_args.kwargs, {"grant": True})
        self.assertTrue(
            AuditLog.objects.filter(
                agent="FRONT-DESK",
                action="remote_session",
                message__startswith="Took control of FRONT-DESK",
            ).exists()
        )

    def test_offline_and_unreachable_computers_are_explained(self, _):
        with computers({**MAC, "online": False}), patch(
            "support.mesh.desktop_relay"
        ) as relay:
            response = self.client.post(
                f"/support/manage/devices/{MAC['id']}/control/relay/"
            )
        self.assertEqual(response.status_code, 409)
        self.assertIn("offline", response.json()["error"])
        relay.assert_not_called()
        with computers(WINDOWS_PC), patch(
            "support.mesh.desktop_relay",
            side_effect=mesh.MeshError("MeshCentral didn’t answer in time."),
        ):
            response = self.client.post(f"{self.url}relay/")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"], "MeshCentral didn’t answer in time.")

    def test_only_console_users_can_start_control(self, _):
        role = baker.make(Role, can_use_mesh=True, can_install_agents=False)
        self.client.force_login(baker.make(User, role=role))
        with computers(WINDOWS_PC), patch("support.mesh.desktop_relay") as relay:
            response = self.client.post(f"{self.url}relay/")
        self.assertEqual(response.status_code, 403)
        relay.assert_not_called()


@override_settings(**TEST_SETTINGS)
class AgentViewerTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = baker.make(User, is_superuser=True)
        self.client.force_login(self.admin)
        self.agent = baker.make(
            "agents.Agent",
            hostname="ACCOUNTING-01",
            agent_id="AgentId123",
            plat="windows",
            operating_system="Windows 11 Pro",
            mesh_node_id="node//AgentNode",
            site__name="HQ",
            site__client__name="Acme",
        )
        self.url = "/support/control/agents/AgentId123/"

    def test_take_control_opens_the_viewer_for_an_agent(self):
        with patch("support.remote.get_core_settings", return_value=CORE):
            response = self.client.get(self.url)
        data = response.context["data"]
        self.assertEqual(
            (data["title"], data["platform"], data["site"]),
            ("ACCOUNTING-01", "windows", "Acme / HQ"),
        )
        self.assertEqual(data["relay_url"], f"{self.url}relay/")
        self.assertIn("wss://mesh.example.com", response["Content-Security-Policy"])

    def test_control_uses_the_agents_own_rights_and_is_audited(self):
        with patch("support.mesh.desktop_relay", return_value=RELAY) as relay:
            response = self.client.post(f"{self.url}relay/")
        self.assertEqual(response.json(), {"relay": RELAY})
        self.assertEqual(relay.call_args.args[0], "node//AgentNode")
        # Tactical RMM's sync already gives technicians rights to their agents.
        self.assertEqual(relay.call_args.kwargs, {"grant": False})
        self.assertTrue(
            AuditLog.objects.filter(
                agent_id="AgentId123", action="remote_session"
            ).exists()
        )

    def test_files_and_terminal_open_meshcentral(self):
        with patch(
            "support.mesh.session_url", return_value="https://mesh.example.com/?x"
        ) as session:
            response = self.client.post(f"{self.url}open/", {"view": "files"})
        self.assertRedirects(
            response, "https://mesh.example.com/?x", fetch_redirect_response=False
        )
        self.assertEqual(session.call_args.args[2], "files")
        self.assertEqual(session.call_args.kwargs, {"grant": False})

    def test_agents_without_meshcentral_or_access_are_refused(self):
        self.agent.mesh_node_id = ""
        self.agent.save()
        self.assertEqual(self.client.post(f"{self.url}relay/").status_code, 409)
        role = baker.make(Role, can_use_mesh=False)
        self.client.force_login(baker.make(User, role=role))
        self.assertEqual(self.client.post(f"{self.url}relay/").status_code, 403)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.client.logout()
        self.assertContains(
            self.client.get(self.url), "Sign in to take control", status_code=401
        )


@override_settings(**TEST_SETTINGS)
class DesktopRelayTests(TestCase):
    def test_the_relay_address_joins_the_tunnel_as_the_technician(self):
        user = baker.make(User, username="dana")
        users = {"action": "users", "users": [{"_id": user.mesh_user_id}]}
        with patch("support.mesh.get_core_settings", return_value=CORE), patch.object(
            mesh, "_request", return_value=users
        ) as request, patch(
            "support.mesh.get_mesh_ws_url",
            return_value="ws://127.0.0.1:4430/control.ashx?auth=server",
        ), patch(
            "support.mesh._open_desktop", return_value="relaycookie"
        ) as opened, patch(
            "support.mesh.get_auth_token", return_value="danatoken"
        ) as token:
            url = mesh.desktop_relay("node//Abc", user, grant=False)
        token.assert_called_once_with(user.mesh_user_id, CORE.mesh_token)
        control_url, node, tunnel = opened.call_args.args
        self.assertEqual(control_url, "ws://127.0.0.1:4430/control.ashx?auth=danatoken")
        self.assertEqual(node, "node//Abc")
        self.assertEqual(
            url,
            "wss://mesh.example.com/meshrelay.ashx?browser=1&p=2&nodeid=node//Abc"
            f"&id={tunnel}&auth=relaycookie",
        )
        # Agents keep the rights Tactical RMM's sync gives them.
        actions = [call.args[0]["action"] for call in request.call_args_list]
        self.assertEqual(actions, ["users"])

    def test_the_tunnel_request_asks_for_the_desktop(self):
        sent = []

        class Control:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def send(self, message):
                sent.append(json.loads(message))

            def __aiter__(self):
                async def replies():
                    yield json.dumps({"action": "serverinfo"})
                    yield json.dumps(
                        {"action": "authcookie", "cookie": "c1", "rcookie": "r1"}
                    )

                return replies()

        import asyncio

        with patch.object(mesh.websockets, "connect", return_value=Control()):
            cookie = asyncio.run(
                mesh._open_desktop("ws://x/control.ashx", "node//A", "t1")
            )
        self.assertEqual(cookie, "c1")
        self.assertEqual(sent[0], {"action": "authcookie"})
        self.assertEqual(
            sent[1],
            {
                "action": "msg",
                "type": "tunnel",
                "nodeid": "node//A",
                "usage": 2,
                "value": "*/meshrelay.ashx?p=2&nodeid=node//A&id=t1&rauth=r1",
            },
        )
