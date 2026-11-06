# Public remote support

The public page is served by this Django application at **`https://<api-host>/support/`**. The technician console is at **`/support/manage/`**. It uses the existing Tactical RMM login session, including the dashboard's MFA/SSO flow. Sign in to the dashboard, then open the console. The separately released Vue dashboard does not need to be rebuilt.

Technicians need both **Install agents** and **Use MeshCentral** permissions. Site visibility follows their existing role. Administrators can manage invitations across permitted sites; other technicians see only their own invitations. Full codes appear once when created. The database stores an HMAC digest and the last three digits. Codes expire after 15 minutes to four hours, can be revoked, and have an atomic download limit. The public form is CSRF protected and limited to five attempts per minute and 100 per day per client IP using the application's cache.

## Branding

Open **Branding** in the console to set the logo, company name, help phone and help email. The logo, name and contacts appear on the public page and in the console header. People without a code see the phone and email. Logos can be PNG, JPEG, WebP or SVG up to 512 KB. The server checks the file's bytes, not its name, and serves SVGs with a sandboxing Content Security Policy. Branding is stored in the database, so it needs no media storage or nginx changes. Changing it requires the role permission for editing global settings (`can_edit_core_settings`; superusers always have it) and is recorded in the audit log. Tick **Use the default logo** and save to go back to the Tactical RMM logo. In dark mode, an uploaded logo sits on a light chip because most logos are drawn for light backgrounds.

The pages use the JustUI kit (`static/support/superhuman/`, Geist fonts) and follow the visitor's light or dark system setting. Page styles live in `static/support/support.css` and use only the kit's tokens. Visitors on Windows or a Mac get their own version as the main download button. The other version stays one click away, and visitors on a phone or tablet are told to switch to their computer.

## Access codes and downloads

Entering a valid code creates a browser session grant lasting at most 20 minutes, bounded by the code's expiry. Each download rechecks expiry, revocation, quota, and file integrity. Codes, download grants, and private installer paths never appear in URLs. Creation, revocation, and successful download reservations are recorded in the existing audit log. A started or interrupted download counts toward the limit; create a new invitation if someone exhausts their retries.

## Configure the support group

This feature distributes your existing **MeshCentral** clients. It does not require a ScreenConnect account. Open **Devices** in the console and choose **Create support group**. This creates a MeshCentral device group named "TacticalRMM Support" that is owned by the server's own MeshCentral account, which the Devices page needs in order to see it. The page then shows the group's ID; use it as `SUPPORT_MESH_GROUP_ID` when you build the clients. To use a group you made yourself, set `SUPPORT_MESH_GROUP` to its name or ID and give the server's MeshCentral account full rights to it. Configure the group's remote-control consent requirements in MeshCentral.

The invitation's client/site is an access-control and audit association in Tactical RMM. Installers enroll in the configured MeshCentral support group; invitation codes do not change embedded enrollment credentials or assign devices to a Tactical RMM site. Connect to the new device through MeshCentral using your existing technician identity.

The Windows installer sets up a per-user support tool with MeshCentral's **run-only** installation flag, a Start menu shortcut, and an uninstaller. It starts no persistent Windows service. The universal Mac package installs the MeshCentral service and needs administrator permission plus the macOS permissions required for remote control. Configure user consent in MeshCentral; a download invitation is not a remote-control permission or a remote session lifetime. Revoking or expiring a code stops downloads and does not disconnect or uninstall an already downloaded client. Advise users about removal after support. Upstream MeshCentral downloads and already distributed copies remain subject to MeshCentral's own enrollment policy.

## Devices: commands and moving to monitoring

**Devices** lists every computer connected to the support group, with its online status. Open one to:

- **Run a command.** Choose PowerShell or Command Prompt on Windows, or the shell on a Mac, and the output appears on the page. This needs the **Send command** role permission (`can_send_cmd`). On Windows, commands run as the person who opened the support app, without administrator rights. On a Mac, they run as root. The support app runs one command at a time, so a command that takes longer than 60 seconds shows "No reply" and keeps running. Every command is recorded in the audit log.
- **Move to monitoring.** Choose a client site and Workstation or Server, then **Install RMM agent**. On Windows this runs the server's own PowerShell installer as an administrator, so the person sees a Windows prompt and must choose Yes (an administrator password may be needed). On a Mac it downloads and installs the agent in the background as root, which needs a Tactical RMM code signing token. The page checks every 15 seconds and shows **Monitored** once an agent with the same computer name checks in to that site. The install writes a log to `C:\ProgramData\TacticalRMM\support-install.log` or `/var/log/tacticalrmm-support-install.log`, and **Show install log** displays it.
- **Remove support app.** Use this once the computer is monitored, or when you're done helping. The Mac service uninstalls itself. On Windows, the server stops the run-only support app and runs its uninstaller. Then the computer is removed from the support group, so it can't reconnect.

Installing the agent uses the same installer account and two-hour install token as the dashboard's installer. It is unavailable while `TRMM_INSECURE` is on. All MeshCentral traffic goes through the server's existing MeshCentral connection, and device actions only reach computers in the support group.

## Build signed installers

Run the **Signed support installers** workflow manually from a trusted branch and provide a release version, such as `1.0.0`. Create a GitHub environment named **`support-signing`**. Restrict that environment to your release branch and authorized release operators. The workflow fetches configured binaries from your MeshCentral server over verified HTTPS, signs the final Windows executable with Azure, signs the Mac payload and package with Apple, notarizes and staples it, then emits the **`signed-support-installers`** artifact.

Azure Artifact Signing (formerly Trusted Signing) signs the Windows client. macOS distribution uses Apple's Developer ID certificates and Apple notarization; Azure Windows signatures cannot replace those.

Configure these environment variables:

| Variable | Value |
| --- | --- |
| `SUPPORT_MESH_URL` | Public HTTPS MeshCentral base URL, including a domain path if used; no query string or credentials |
| `AZURE_SIGNING_ENDPOINT` | Your Azure Artifact Signing region endpoint, e.g. `https://eus.codesigning.azure.net/` |
| `AZURE_SIGNING_ACCOUNT_NAME` | Azure signing account name |
| `AZURE_SIGNING_CERTIFICATE_PROFILE` | Public Trust certificate profile name |
| `APPLE_APPLICATION_IDENTITY` | Full `Developer ID Application: Organization (TEAMID)` identity |
| `APPLE_INSTALLER_IDENTITY` | Full `Developer ID Installer: Organization (TEAMID)` identity |

Configure these environment secrets:

| Secret | Value |
| --- | --- |
| `SUPPORT_MESH_GROUP_ID` | Final identifier of the support device group, without `mesh//` |
| `SUPPORT_MESH_DOWNLOAD_COOKIE` | Optional MeshCentral HTTP cookie header for authenticated downloads when `lockAgentDownload` is enabled; this is a privileged session secret, so use a dedicated minimally privileged download account and rotate it |
| `SUPPORT_MESH_DOWNLOAD_KEY` | Optional MeshCentral domain login key if configured |
| `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` | Microsoft Entra application configured for GitHub OIDC federation |
| `APPLE_APPLICATION_CERTIFICATE_BASE64` | Base64 PKCS#12 containing the Developer ID Application certificate and private key |
| `APPLE_APPLICATION_CERTIFICATE_PASSWORD` | Application PKCS#12 password |
| `APPLE_INSTALLER_CERTIFICATE_BASE64` | Base64 PKCS#12 containing the Developer ID Installer certificate and private key |
| `APPLE_INSTALLER_CERTIFICATE_PASSWORD` | Installer PKCS#12 password |
| `APPLE_NOTARY_PRIVATE_KEY` | App Store Connect API private key in PEM (`.p8`) form |
| `APPLE_NOTARY_KEY_ID`, `APPLE_NOTARY_ISSUER_ID` | App Store Connect API identifiers for notarization |

Grant the Azure identity **Artifact Signing Certificate Profile Signer** on the appropriate profile/account (older tenants may label this **Trusted Signing Certificate Profile Signer**). Configure its federated credential subject for `repo:OWNER/REPOSITORY:environment:support-signing`, with audience `api://AzureADTokenExchange`. There is no Azure client secret stored in Django. Signing credentials stay in CI, and the Mac keychain is temporary and removed on completion.

Signatures are verified with native platform tools before the manifest is produced. Windows packaging signs the raw client, keeps its `.msh` enrollment configuration beside it, builds an interactive installer with Inno Setup 6, and signs the finished installer. This avoids losing embedded enrollment data when Authenticode signatures are replaced. Both operating system jobs must succeed before the final bundle is assembled. No signing, timestamping, customization, or binary modifications happen during a public download. Rebuild and sign a new release after changing MeshCentral's group, hostname, certificate, or client branding.

## Publish a release on the RMM server

Download and extract **`signed-support-installers`** from your trusted workflow. Keep `manifest.json` and both installers together. Use the RMM application's operating system account so the web workers can read the resulting private files:

```sh
cd /rmm/api/tacticalrmm
./env/bin/python manage.py migrate
./env/bin/python manage.py collectstatic --noinput
./env/bin/python manage.py publish_support_installers /path/to/extracted-release
```

Use the Python interpreter from your deployment's actual virtual environment (the path varies). Restart the Django workers after deploying this feature. Docker deployments run these commands inside the backend container and must persist the installer directory alongside other private agent artifacts.

The publisher validates both platforms, format, provenance fields, version, byte counts, and SHA-256 hashes before activating either record. Files are stored under SHA-derived names in `EXE_DIR/support`, outside public static files, and served through the authorized Django download view. The manifest is a trusted CI attestation: importing an arbitrary manifest is not a substitute for platform signature verification. Only import artifacts obtained from the trusted signing workflow. The Linux server verifies file hashes, while Windows and Mac CI runners verify the signatures and notarization.

Optional `local_settings.py` settings. The name and email are only defaults; anything saved under **Branding** in the console takes priority:

```python
SUPPORT_BRAND_NAME = "Tactical RMM"
SUPPORT_CONTACT_EMAIL = "support@example.com"
SUPPORT_INSTALLER_DIR = "/var/lib/tactical-support/installers"
SUPPORT_MESH_GROUP = "TacticalRMM Support"  # MeshCentral group name or ID
```

Apply the migration and collect static files even before publishing installers. The page remains usable and shows each unavailable download clearly. No unsigned fallback is served. For a custom support hostname, point it at the Django backend and configure the application's allowed hosts, TLS, session cookie domain, and CSRF trusted origins consistently. Serving `/support/` on the existing API hostname works with the standard nginx configuration.

## Verification

```sh
GHACTIONS=yes pytest support/tests.py -o addopts=
python -m unittest discover -s packaging/support -p 'test_*.py'
```

The suite exercises anonymous access, CSRF, code expiry/revocation, quotas, throttling, role/site restrictions, corrupt or missing artifacts, trusted-bundle validation, and concurrent download reservations. CI requires the same PostgreSQL service as the existing test suite. The signing workflow requires your Azure/Apple accounts and a reachable configured MeshCentral server; ordinary unit tests do not call those services.

Sources: [Azure signing action](https://github.com/Azure/artifact-signing-action), [Azure integrations](https://learn.microsoft.com/en-us/azure/trusted-signing/how-to-signing-integrations), [Apple notarization](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution), [MeshCentral 1.3.1 download handlers](https://github.com/Ylianst/MeshCentral/blob/1.3.1/webserver.js).
