# Public remote support

The public page is served by this Django application at **`https://<api-host>/support/`**. The technician console is at **`/support/manage/`**. It uses the existing Tactical RMM login session, including the dashboard's MFA/SSO flow. Sign in to the dashboard, then open the console. The separately released Vue dashboard does not need to be rebuilt.

The product is called **JustConnect**, and it has two halves: **Support**, this console, and **Access**, the RMM dashboard. A Support | Access switch at the top of each one moves between them. Both share one sign-in. Once `support-site.sh` has run (see [Deploy this fork](#deploy-this-fork)), both live on the dashboard's address.

Technicians need both **Install agents** and **Use MeshCentral** permissions. Site visibility follows their existing role. Administrators can manage invitations across permitted sites; other technicians see only their own invitations. Full codes appear once when created. The database stores an HMAC digest and the last three digits. Codes expire after 15 minutes to four hours, can be revoked, and have an atomic download limit. The public form is CSRF protected and limited to five attempts per minute and 100 per day per client IP using the application's cache.

## Branding

Open **Settings** in the console to set the logo, company name, help phone and help email. The logo, name and contacts appear on the public page and in the console header. People without a code see the phone and email. Logos can be PNG, JPEG, WebP or SVG up to 512 KB. The server checks the file's bytes, not its name, and serves SVGs with a sandboxing Content Security Policy. Branding is stored in the database, so it needs no media storage or nginx changes. Changing it requires the role permission for editing global settings (`can_edit_core_settings`; superusers always have it) and is recorded in the audit log. Tick **Use the default logo** and save to go back to the JustConnect logo. With no logo uploaded, the JustConnect wordmark stands in for the name, with a dark-mode copy for dark pages. Set a company name other than JustConnect, and the JustConnect mark sits beside that name instead. In dark mode, an uploaded logo sits on a light chip because most logos are drawn for light backgrounds.

The pages use the JustUI kit (`static/support/superhuman/`, Geist fonts) and follow the visitor's light or dark system setting. Page styles live in `static/support/support.css` and use only the kit's tokens. Visitors on Windows or a Mac get their platform as the main download button. The same Windows MSI supports Windows 7 SP1 and later. The other versions stay one click away, and visitors on a phone or tablet are told to switch to their computer.

## Access codes and downloads

Entering a valid code creates a browser session grant lasting at most 20 minutes, bounded by the code's expiry. Each download rechecks expiry, revocation, quota, and file integrity. Codes, download grants, and private installer paths never appear in URLs. Creation, revocation, and successful download reservations are recorded in the existing audit log. A started or interrupted download counts toward the limit; create a new invitation if someone exhausts their retries.

## Configure the support group

This feature distributes your existing **MeshCentral** clients. It does not require a ScreenConnect account. Open **Settings** in the console and choose **Create support group**. This creates a MeshCentral device group named "TacticalRMM Support" that is owned by the server's own MeshCentral account, which the console needs in order to see it. Settings then shows the group's ID; use it as `SUPPORT_MESH_GROUP_ID` when you build the clients. To use a group you made yourself, set `SUPPORT_MESH_GROUP` to its name or ID and give the server's MeshCentral account full rights to it. Configure the group's remote-control consent requirements in MeshCentral.

The invitation's client/site is an access-control and audit association in Tactical RMM. Installers enroll in the configured MeshCentral support group; invitation codes do not change embedded enrollment credentials or assign devices to a Tactical RMM site. Connect to the new device through MeshCentral using your existing technician identity.

The Windows installer sets up a per-user support tool with MeshCentral's **run-only** installation flag, a Start menu shortcut, and an uninstaller. It starts no persistent Windows service. The universal Mac package installs the MeshCentral service and needs administrator permission plus the macOS permissions required for remote control. Configure user consent in MeshCentral; a download invitation is not a remote-control permission or a remote session lifetime. Revoking or expiring a code stops downloads and does not disconnect or uninstall an already downloaded client. Advise users about removal after support. Upstream MeshCentral downloads and already distributed copies remain subject to MeshCentral's own enrollment policy.

## Sessions: the technician workspace

The console is laid out like a remote support host console: session groups on the left, the live session list in the middle, and the session you pick on the right.

- **Sessions.** A session is a code waiting to be used, or a computer connected through the support app. **New session** asks for a name (only technicians see it), a client site, how long the code lasts, and how many downloads it allows. The code appears once, with **Copy code**, **Copy invitation** and **Email invitation**. When someone downloads the support app with a code and a computer connects from the same address within four hours, that computer takes over the code's session and its name. The code's page notices on its own and links to the computer. You can rename any session from its **General** tab.
- **Groups.** **All sessions**, **My sessions** (codes you made and installs you started), **Connected**, **Waiting to connect**, **Offline**, **Moving to monitoring**, and **Past codes** (expired, used up or cancelled). Each group shows a count. Search filters the list by session name, computer, signed-in user or client. Press `/` to search.
- **Live updates.** The list checks for changes every 10 seconds. When the open session changes (it connects, goes offline, or its agent checks in), its page updates too, unless you're typing in it or reading output.
- **Visibility.** Everyone with console access sees every support computer. Client and site names, and session names that came from a code, are shown only to technicians who can see that site. Other technicians see the computer's own name. Administrators see every code in the sites they can see. Other technicians see only their own codes.

**Screen previews.** The session list and each session's **General** tab show a small picture of the computer's screen. Click the large one to join. Each picture is one frame of MeshCentral's own remote desktop: a Celery task opens a desktop tunnel to the support app the way MeshCentral's web client does, asks for small JPEG tiles, stitches the first full screen together with Pillow, and closes the tunnel. Nothing runs on the computer, so antivirus has no screenshot script to flag, and a Mac uses the Screen Recording permission it already has for joining. Pictures are taken only while someone has the console open: every 20 seconds for the session that's open, and about once a minute for the rest of the list. They stay in the cache for up to 10 minutes and are served only to people with console access. Each picture is a short remote desktop session, so MeshCentral's device event log records it as "Started remote desktop without notification". If the support group's MeshCentral consent settings notify or ask people before remote desktop, or show a privacy bar, there are no previews, because every picture would notify or ask them. Turn previews off in **Settings**. Changing that needs the global settings permission and is recorded in the audit log.

Open a computer's session to:

- **Join.** **Join** opens the remote control window (below) in its own window. **Terminal** and **Files** open MeshCentral in a new tab, already signed in, the same way the dashboard's Take Control does. With MeshCentral sync on (Tactical RMM's default), you sign in as your own MeshCentral account, and the console first gives that account rights to this one computer. A brand-new technician may need to wait a few minutes for sync to create their account. With sync off, the server's MeshCentral account is used, as in the dashboard. Each join is recorded in the audit log. The Windows support app runs in the person's own session, so it can't show the lock screen or a Windows permission prompt. On a Mac, the screen stays black until they allow Screen Recording for the support app.
- **Run commands** on the **Commands** tab. It's a console that keeps the session's history: every command, who ran it, and what it printed, shared by everyone helping that computer. Choose PowerShell or Command Prompt on Windows, or the shell on a Mac. Pick a saved command from the toolbox, or press Ctrl+Enter to run what you typed. This needs the **Send command** role permission (`can_send_cmd`). On Windows, commands run as the person who opened the support app, without administrator rights. On a Mac, they run as root. The support app runs one command at a time, so a command that takes longer than 60 seconds shows "No reply" and keeps running. Every command is recorded in the audit log.
- **Move to monitoring** on the **Monitoring** tab. Choose a client site and Workstation or Server, then **Install RMM agent**. The tab warns you if an agent with the same name is already monitored in a site you can see.
  - On Windows, the server generates a monitoring MSI and the support app downloads it into the person's private temp folder. Windows asks for administrator permission, then shows native installation progress. Download and cleanup run hidden. The capability used to download the MSI goes in an Authorization header and expires after 20 minutes. Enrollment data stays out of displayed commands and MSI logs; the monitoring client receives its short-lived token through its existing native command-line interface.
  - On a Mac, the install needs a Tactical RMM code signing token. The RMM installer removes any MeshCentral agent it finds, including the support app, so the console starts the install as its own launchd job. The job keeps running after the support app closes, and then the computer shows as offline.
  - The session shows **Monitored** once an agent with the same computer name checks in to that site (`Danas-MacBook` and `Danas-MacBook.local` match). After 20 minutes without a check-in, it stops waiting and says so.
  - **Check progress** shows the person's answer to the Windows prompt and the end of the install log (`%TEMP%\JustConnect-install.log` or `/var/log/tacticalrmm-support-install.log`). While the Windows prompt is still open, the support app is busy and the check says so.
- **Keep notes** on the **Notes** tab. Notes stay with the session for everyone who helps.
- **See what happened** on the **Timeline** tab: the code, downloads, joins, commands, installs and notes, newest first.
- **End session.** Use this once the computer is monitored, or when you're done helping. The Mac service uninstalls itself. On Windows, the server stops the run-only support app and runs its uninstaller. Then the computer is removed from the support group, so it can't reconnect. A computer that's offline is only taken off the list. If the person opens the support app again, it comes back.

Installing the agent uses the same installer account and two-hour install token as the dashboard's installer. It's unavailable while `TRMM_INSECURE` is on. All MeshCentral traffic goes through the server's existing MeshCentral connection, and session actions only reach computers in the support group. Don't send the Mac support app to a Mac that already has the RMM agent: both use MeshCentral's standard Mac service. Use Take Control in the dashboard instead.

## Remote control window

**Join** in the console, and the dashboard's **Take Control** once `take-control.sh` has been run, open the same remote control window. It's laid out like ScreenConnect's: a toolbar across the top, their screen in the middle, and the connection details along the bottom.

- **Ctrl+Alt+Del** and **Keys**. Keys sends Windows, Alt+Tab, Esc, Ctrl+Shift+Esc (Task Manager), Windows+R, Windows+L and Print Screen. **Type text** types what you enter as keystrokes, which works in password fields. **Type my clipboard** does the same with your clipboard. Windows-only keys are hidden for Macs.
- **Block input** stops them using their mouse and keyboard while you work. It's Windows only.
- **Display** picks which of their displays to show, or all of them, when they have more than one.
- **Zoom**: **Fit to window**, or 50% to 200%. The minus and plus buttons step between levels. When you're zoomed in, scroll to move around their screen.
- **Quality**: **Best quality**, **Balanced** (the default) or **Fastest**, for slow connections. Zoom and quality are remembered in this browser.
- **Full screen**, **Files** and **Terminal**. The arrow at the right hides the toolbar to give their screen the whole window.
- **Disconnect** ends the session. **Reconnect** starts it again.

Your mouse and keyboard go to their computer while the window is in front. The status bar shows their screen size, zoom, quality and the round-trip time.

The window uses MeshCentral's own remote desktop: its desktop client (`static/support/vendor/meshcentral`, Apache-2.0, unchanged from MeshCentral 1.3.1) draws their screen. The server opens the desktop tunnel as the technician's own MeshCentral account (or the server's account with sync off), and the browser joins it on MeshCentral directly. That means MeshCentral's consent settings, session log and recording still apply, and the window shows "Waiting for them to allow it" when MeshCentral asks first. Opening it is recorded in the audit log like Take Control.

For monitored agents, `take-control.sh` makes the dashboard's Take Control open this window. The dashboard opens Take Control at `https://<dashboard>/takecontrol/<agent id>`. The script adds an nginx redirect from there to `/support/control/agents/<agent id>/` on the API host, which needs **Use MeshCentral** and access to that agent, the same as Take Control. Run `./take-control.sh` in `/rmm` to turn it on, and `./take-control.sh --off` to go back to MeshCentral's page.

## Toolbox

**Toolbox** holds saved commands that everyone using remote support shares. Each command has a name and runs in PowerShell, Command Prompt, or the Mac shell. Each session's **Commands** tab offers the commands that fit that computer under **From the toolbox**. Choosing one puts it in the command box so you can check it before running it. **Save to toolbox** saves the command you just typed without leaving the session. On an empty toolbox, **Add starter commands** adds everyday read-only checks: system summary, disk space, network settings, an internet connection test, installed apps, a pending restart check, and the busiest apps. They all work without administrator rights.

Anyone with console access can run toolbox commands, which also needs **Send command**. Adding, editing and deleting them needs the **Manage scripts** role permission (`can_manage_scripts`), because a saved command runs on whatever computer a technician picks. Changes are recorded in the audit log. The dashboard's Script Manager remains the place for scripts that run through the full RMM agent.

## Deploy this fork

`install.sh` and `update.sh` in this fork install from `JustSuperHuman/tacticalrmm` on the `remote-support` branch (`TRMM_REPO` and `TRMM_BRANCH` at the top of each). Upstream's own update script would reset the server to upstream's `master`, so only use this fork's `update.sh`. It compares versions, so run it as `./update.sh --force` to pick up changes that didn't bump `TRMM_VERSION`.

Remote support uses two addresses: one for the people you help, and the dashboard's address for technicians. Point the public name at the server, make sure the server's certificate covers it, then run `./support-site.sh support.example.com` from `/rmm`. It sets up both:

- **The public address** serves only the support page, the support app downloads and the logo. The script adds the name to `ALLOWED_HOSTS` and sets `SUPPORT_PUBLIC_URL`, so invitations and the console's **Support page** link use it. Links to the console or the remote control window on this address are redirected to the dashboard's address.
- **The dashboard's address** serves Access (the dashboard) at `/` and Support (the console) at `/support/manage/`, through an nginx snippet (`/etc/nginx/snippets/trmm-support-console.conf`) included in `frontend.conf`. The snippet also has nginx add `static/support/access.js` to the dashboard's page. That script puts the JustConnect logo where the dashboard says Tactical RMM and adds the Support | Access switch beside it, without rebuilding the dashboard. If `take-control.sh` has run, its redirect moves to the dashboard's address too.

Run it again with another public name to move the support page, for example to the bare domain once it points at the server. The technician side stays where it is, so people being helped can move without technicians noticing. The script needs nginx's `http_sub_module`, which the nginx.org and Ubuntu packages include.

Technicians sign in to Access. The session is shared across the root domain, so Support works straight away after that, and so does `https://<api-host>/support/manage/`.

## Generated installers only

The support page serves generated packages: one Windows MSI and a universal Mac package. Raw MeshCentral Windows EXEs and the old generated EXE/PowerShell paths are no longer download choices. Existing installer records stay inactive until a generated package replaces them.

In Support, open **Settings** and choose **Generate support installers**. This requires the global settings permission and records the result in the audit log. On the server:

```sh
sudo apt install wixl msitools g++-mingw-w64-i686
cd /rmm/api/tacticalrmm
./env/bin/python manage.py migrate
./env/bin/python manage.py collectstatic --noinput
./env/bin/python manage.py generate_support_installers
```

The installation and update scripts install these build tools. Docker images include them too. Use `--platform windows` or `--platform macos` to generate just one package. The older `publish_meshcentral_installers` command is an alias for this generator and cannot publish raw Windows clients. Existing signed generated packages are kept unless `--replace-signed` is supplied.

### Windows setup

`TacticalSupport-windows.msi` installs for the current Windows user. The native wizard offers Install, progress, cancellation and Finish, with an option to open the support app. It creates a Start menu shortcut and a Windows Installer uninstall entry. It does not install a persistent support service.

Setup selects the x86 client on Windows 7 and on any 32-bit Windows edition. It selects x64 on newer 64-bit systems. There is no separate Windows 7 button. Downloads use verified HTTPS and an expected SHA-256 digest baked into the MSI. A changed or incomplete payload never runs. Cancellation removes the partial download; rollback restores the previous support executable. Repair downloads the same verified client again. No PowerShell, .NET runtime or command window is needed by the MSI.

Clients are staged without enrollment data at `/support/payload/<sha256>/`. The generated MSI supplies the support group or monitoring enrollment settings. Payloads do not need the person's browser session, and remain available after an invitation expires so a previously downloaded MSI can complete setup. Keep these immutable payload files when publishing a new release; older MSIs still refer to their hashes.

### Access and moving to monitoring

Access's Windows **Add an agent** dialog uses **Generate and download MSI**. The form retains client/site, server/workstation, enrollment expiry and optional Windows settings. It selects the client architecture during installation. The old `exe`, `manual` and `powershell` API values all return a generated MSI for Windows, preserving integrations while removing script and raw EXE responses. The generator runs on this server; enrollment tokens are no longer sent to the external EXE-generation service.

The monitoring MSI needs administrator permission. Its native actions download a verified upstream monitoring setup, run that setup hidden, and enroll the client with its existing native interface. Service setup and cleanup stay with the upstream agent. An `unins001.exe` bridge directs the agent's existing remote Remove action to Windows Installer. Repair preserves an existing enrollment. Existing monitored installations must be updated through Access rather than enrolled again.

`support/static/support/access.js`, already injected by `support-site.sh`, supplies the new Windows form in the separately built dashboard. Run `support-site.sh` after deploying this fork if the dashboard does not yet load that script. Mac and Linux monitoring retain their platform-specific installation paths.

### Windows 7 compatibility

The MSI targets **Windows 7 SP1, 32-bit and 64-bit**. Its native action library and uninstall bridge are x86 builds using Windows 7 APIs. The generator checks the compatibility client's PE minimum OS/subsystem version and rejects known Windows 8+ static imports. It also checks the monitoring setup's x86 binary headers. These checks and native action tests do not replace installing and connecting from an actual Windows 7 computer. Test desktop control on both Windows 7 editions before marking a release verified on those systems.

Windows must have working TLS 1.2 and current certificate roots. Setup preserves certificate validation and explains download failures. Azure-signed packages also need the Windows signing updates and Microsoft root certificate described in [Microsoft KB5022661](https://support.microsoft.com/en-gb/servicing/azure/update/2022/12/kb5022661-windows-support-for-the-trusted-signing-formerly-azure-code-signing-program). Unsigned packages can show an unknown-publisher warning.

### Mac setup

MeshCentral generates a configured flat `.pkg` containing both Intel and Apple silicon binaries, the launch daemon and launch agent. Installation needs a Mac administrator password, followed by Screen Recording and Accessibility permission for remote control. The package is unsigned until the Apple signing and notarization workflow runs. Signing does not affect whether a package qualifies as generated.

## Build signed installers

Run the **Signed support installers** workflow manually from a trusted branch and provide a release version, such as `1.0.0`. Choose `all`, `windows` or `macos`; a Mac-only release does not need Azure credentials or a Windows build. Create a GitHub environment named **`support-signing`**. Restrict that environment to your release branch and authorized release operators. The workflow fetches configured binaries from your MeshCentral server over verified HTTPS, signs the generated Windows MSI with Azure, signs the Mac payload and package with Apple, notarizes and staples it, then emits the **`signed-support-installers`** artifact.

Azure Artifact Signing (formerly Trusted Signing) signs the generated Windows MSI. macOS distribution uses Apple's Developer ID certificates and Apple notarization; Azure Windows signatures cannot replace those.

Configure these environment variables:

| Variable | Value |
| --- | --- |
| `SUPPORT_INSTALLER_BASE_URL` | Public HTTPS API or support host that serves `/support/payload/`; required for the MSI build |
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

Grant the Azure identity **Artifact Signing Certificate Profile Signer** on the appropriate profile/account (older tenants may label this **Trusted Signing Certificate Profile Signer**). Configure a GitHub federated credential for the **`support-signing` environment**, with issuer `https://token.actions.githubusercontent.com` and audience `api://AzureADTokenExchange`.

Use the repository's actual OIDC subject format. GitHub repositories created, renamed, or transferred after July 15, 2026 use permanent owner and repository IDs in the subject; existing repositories can opt in. Check it before configuring Azure:

```sh
gh api repos/OWNER/REPOSITORY/actions/oidc/customization/sub
```

Append `:environment:support-signing` to the returned `sub_claim_prefix`. For immutable subjects this produces `repo:OWNER@OWNER_ID/REPOSITORY@REPOSITORY_ID:environment:support-signing`; older repositories may use `repo:OWNER/REPOSITORY:environment:support-signing`. Azure's GitHub scenario accepts the numeric IDs and generates the immutable subject. Do not substitute the older format when `use_immutable_subject` is true. See [GitHub's OIDC reference](https://docs.github.com/en/actions/reference/security/oidc#immutable-subject-claims).

There is no Azure client secret stored in Django. Signing credentials stay in CI, and the Mac keychain is temporary and removed on completion.

Signatures are verified with native platform tools before the manifest is produced. Linux generates the MSI with both client payloads; Windows signs the final package with Azure. Mac signing and notarization run independently. The release manifest uses schema 2 and records generated package formats, hashes and the immutable client payloads. Every selected platform must succeed before the final bundle is assembled. Public downloads serve the generated bytes without modifying them. Regenerate packages after changing the support group, server address, client binaries or branding.

## Publish a release on the RMM server

Download and extract **`signed-support-installers`** from your trusted workflow. Keep `manifest.json`, the included installers and the `payloads/` directory together. A bundle can contain Windows, Mac or both; publishing it preserves the other platforms already available. Use the RMM application's operating system account so the web workers can read the resulting private files:

```sh
cd /rmm/api/tacticalrmm
./env/bin/python manage.py migrate
./env/bin/python manage.py collectstatic --noinput
./env/bin/python manage.py publish_support_installers /path/to/extracted-release
# For a trusted generated bundle that has not been signed yet:
# ./env/bin/python manage.py publish_support_installers /path/to/extracted-release --allow-unsigned
```

Use the Python interpreter from your deployment's actual virtual environment (the path varies). Restart the Django workers after deploying this feature. Docker deployments run these commands inside the backend container and must persist the installer directory alongside other private agent artifacts.

The publisher validates both platforms, format, provenance fields, version, byte counts, and SHA-256 hashes before activating either record. Files are stored under SHA-derived names in `EXE_DIR/support`, outside public static files, and served through the authorized Django download view. The manifest is a trusted CI attestation: importing an arbitrary manifest is not a substitute for platform signature verification. Only import artifacts obtained from the trusted signing workflow. The Linux server verifies file hashes, while Windows and Mac CI runners verify the signatures and notarization.

Optional `local_settings.py` settings. The name and email are only defaults; anything saved under **Branding** in the console takes priority:

```python
SUPPORT_BRAND_NAME = "JustConnect"
SUPPORT_INSTALLER_BASE_URL = "https://api.example.com"
SUPPORT_INSTALLER_VERSION = "1.0.0"
SUPPORT_CONTACT_EMAIL = "support@example.com"
SUPPORT_INSTALLER_DIR = "/var/lib/tactical-support/installers"
SUPPORT_MESH_GROUP = "TacticalRMM Support"  # MeshCentral group name or ID
```

Apply the migration and collect static files even before publishing installers. The page remains usable and shows each unavailable download clearly. For a custom support hostname, point it at the Django backend and configure the application's allowed hosts, TLS, session cookie domain, and CSRF trusted origins consistently. Serving `/support/` on the existing API hostname works with the standard nginx configuration.

## Verification

```sh
GHACTIONS=yes pytest support/tests.py -o addopts=
python -m unittest discover -s packaging/support -p 'test_*.py'
```

The suite exercises anonymous access, CSRF, code expiry/revocation, quotas, throttling, role/site restrictions, corrupt or missing artifacts, trusted-bundle validation, and concurrent download reservations. CI requires the same PostgreSQL service as the existing test suite. The signing workflow requires your Azure/Apple accounts and a reachable configured MeshCentral server; ordinary unit tests do not call those services.

Sources: [Azure signing action](https://github.com/Azure/artifact-signing-action), [Azure integrations](https://learn.microsoft.com/en-us/azure/trusted-signing/how-to-signing-integrations), [Apple notarization](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution), [MeshCentral 1.3.1 download handlers](https://github.com/Ylianst/MeshCentral/blob/1.3.1/webserver.js).
