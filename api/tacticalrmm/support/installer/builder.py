"""Build a universal Windows MSI on the Linux server, without a remote generator.

Only the native action library is compiled. Enrollment values are data in the MSI,
never interpolated into code, shell scripts, compiler arguments or build logs.
"""

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .compatibility import verify

MSI_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")
NAMESPACE = "http://schemas.microsoft.com/wix/2006/wi"
RECIPE_VERSION = b"native-msi-v1-static-win7"


class BuildError(Exception):
    """A package could not be generated; safe to show to the technician."""


@dataclass(frozen=True)
class Payload:
    url: str
    sha256: str

    def validate(self):
        parsed = urlsplit(self.url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
            or any(c in self.url for c in "|\r\n\x00")
            or len(self.url) > 4096
            or not re.fullmatch(r"[a-f0-9]{64}", self.sha256)
        ):
            raise BuildError(
                "Client downloads need an HTTPS address and a verified SHA-256 digest."
            )


def _run(command, **kwargs):
    try:
        subprocess.run(command, check=True, capture_output=True, timeout=180, **kwargs)
    except (OSError, subprocess.SubprocessError) as exc:
        # Compiler and MSI tool output can contain enrollment data. Keep it private.
        raise BuildError(
            "The installer builder could not finish. Check the server's installer build tools."
        ) from exc


def native_actions(cache):
    source = Path(__file__).with_name("actions.cpp")
    fingerprint = hashlib.sha256(RECIPE_VERSION + source.read_bytes()).hexdigest()
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    output = cache / f"{fingerprint}.dll"
    if output.is_file():
        return output
    compiler = shutil.which("i686-w64-mingw32-g++")
    if not compiler:
        raise BuildError(
            "Install the server's MSI build tools: wixl, msitools and g++-mingw-w64-i686."
        )
    with tempfile.TemporaryDirectory(dir=cache) as temporary:
        dll = Path(temporary) / "SupportActions.dll"
        _run(
            [
                compiler,
                "-std=c++17",
                "-Os",
                "-shared",
                "-static",
                "-static-libgcc",
                "-static-libstdc++",
                "-s",
                "-Wl,--kill-at",
                "-Wl,--major-os-version,6",
                "-Wl,--minor-os-version,1",
                "-Wl,--major-subsystem-version,6",
                "-Wl,--minor-subsystem-version,1",
                "-o",
                str(dll),
                str(source),
                "-lmsi",
                "-lwinhttp",
                "-lbcrypt",
                "-lshlwapi",
                "-ladvapi32",
            ]
        )
        verify(dll)
        os.replace(dll, output)
    return output


def _version(version):
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)(?:[.+_-][A-Za-z0-9.+_-]+)?", version)
    if not match:
        raise BuildError("Use an installer version such as 1.0.0.")
    numbers = tuple(map(int, match.groups()))
    if numbers[0] > 255 or numbers[1] > 255 or numbers[2] > 65535:
        raise BuildError("MSI version components must fit 255.255.65535.")
    return ".".join(map(str, numbers))


def native_uninstaller(cache):
    source = Path(__file__).with_name("uninstall.cpp")
    digest = hashlib.sha256(RECIPE_VERSION + source.read_bytes()).hexdigest()
    output = Path(cache) / f"uninstall-{digest}.exe"
    if not output.is_file():
        with tempfile.TemporaryDirectory(dir=cache) as temporary:
            binary = Path(temporary) / "unins001.exe"
            _run(
                [
                    "i686-w64-mingw32-g++",
                    "-Os",
                    "-s",
                    "-static",
                    "-municode",
                    "-mwindows",
                    "-Wl,--major-os-version,6",
                    "-Wl,--minor-os-version,1",
                    "-Wl,--major-subsystem-version,6",
                    "-Wl,--minor-subsystem-version,1",
                    "-o",
                    str(binary),
                    str(source),
                    "-ladvapi32",
                ]
            )
            verify(binary)
            os.replace(binary, output)
    return output


def _table(directory, name, columns, types, keys, rows):
    path = directory / f"{name}.idt"
    lines = ["\t".join(columns), "\t".join(types), "\t".join([name, *keys])]
    for row in rows:
        values = ["" if value is None else str(value) for value in row]
        if any(any(char in value for char in "\r\n\t\x00") for value in values):
            raise BuildError(
                "Installer display text contains an unsupported control character."
            )
        lines.append("\t".join(values))
    path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    return path


def _user_interface(directory, name, monitoring, service=False):
    dialogs, controls, events = [], [], []

    def dialog(
        key, heading, body, primary="Install", secondary="Cancel", progress=False
    ):
        dialogs.append(
            (
                key,
                50,
                50,
                370,
                270,
                1 if progress else 3,
                "[ProductName] Setup",
                "Primary",
                "Primary",
                "Secondary",
            )
        )
        controls.extend(
            [
                (
                    key,
                    "Heading",
                    "Text",
                    20,
                    20,
                    330,
                    42,
                    3,
                    None,
                    "{\\Title}" + heading,
                    None,
                    None,
                ),
                (
                    key,
                    "Description",
                    "Text",
                    20,
                    76,
                    330,
                    105,
                    3,
                    None,
                    body,
                    None,
                    None,
                ),
                (key, "Line", "Line", 0, 226, 370, 0, 3, None, None, None, None),
                (
                    key,
                    "Primary",
                    "PushButton",
                    202,
                    241,
                    70,
                    18,
                    3,
                    None,
                    primary,
                    "Secondary",
                    None,
                ),
                (
                    key,
                    "Secondary",
                    "PushButton",
                    282,
                    241,
                    70,
                    18,
                    3,
                    None,
                    secondary,
                    "Primary",
                    None,
                ),
            ]
        )
        if progress:
            controls.extend(
                [
                    (
                        key,
                        "Status",
                        "Text",
                        20,
                        147,
                        330,
                        32,
                        3,
                        "ActionText",
                        "Preparing setup…",
                        None,
                        None,
                    ),
                    (
                        key,
                        "Progress",
                        "ProgressBar",
                        20,
                        185,
                        330,
                        12,
                        65537,
                        None,
                        None,
                        None,
                        None,
                    ),
                ]
            )
            events.append((key, "Primary", "EndDialog", "Exit", "1", 1))
        else:
            events.append((key, "Primary", "EndDialog", "Return", "1", 10))
        events.append((key, "Secondary", "EndDialog", "Exit", "1", 1))

    if monitoring:
        welcome = (
            "Your technician will be able to monitor and manage this computer after setup. "
            "The agent stays connected until it is removed.\n\n"
            "Windows will ask for administrator permission. Setup chooses the right client "
            "for this computer and handles the rest."
        )
    elif service:
        welcome = (
            "Install remote access for your technician. The support service stays available "
            "after you sign out or restart, until it is removed. Only continue if you trust your technician. "
            "Windows will ask for administrator permission. Setup chooses the right client, including Windows 7."
        )
    else:
        welcome = (
            "Set up remote support for your Windows account. Your technician can view and "
            "control this computer when you connect. Only continue if you requested help.\n\n"
            "Setup chooses the right client for this computer, including Windows 7."
        )
    # MSI Text controls use CRLF escape sequences, while IDT rows stay single-line.
    welcome = welcome.replace("\n", "[\r\n]")
    # Keep control data single-line; paragraphs are separate native text controls below.
    welcome = welcome.replace("[\r\n]", " ")
    dialog("Welcome", "Get connected with your support team", welcome)
    dialog(
        "Maintenance",
        "Change your installation",
        "Repair the installed app, or choose Remove to uninstall it.",
        "Repair",
        "Remove",
    )
    events = [e for e in events if not (e[0] == "Maintenance" and e[1] == "Secondary")]
    events.extend(
        [
            ("Maintenance", "Primary", "[REINSTALL]", "ALL", "1", 1),
            ("Maintenance", "Primary", "[REINSTALLMODE]", "amus", "1", 2),
            ("Maintenance", "Secondary", "[REMOVE]", "ALL", "1", 1),
            ("Maintenance", "Secondary", "EndDialog", "Return", "1", 10),
        ]
    )
    dialog(
        "Progress",
        "Setting up your computer",
        "Keep this window open. You can cancel if you need to stop setup.",
        "Cancel",
        "Cancel",
        progress=True,
    )
    dialog(
        "Complete",
        "You’re ready",
        "Setup has finished. Your technician will take it from here.",
        "Finish",
        "Close",
    )
    if not monitoring and not service:
        controls.append(
            (
                "Complete",
                "Launch",
                "CheckBox",
                20,
                185,
                330,
                18,
                3,
                "LaunchAfterSetup",
                "Open the support app",
                None,
                None,
            )
        )
        events.append(
            (
                "Complete",
                "Primary",
                "DoAction",
                "LaunchClient",
                'LaunchAfterSetup = "1" AND NOT REMOVE AND NOT Installed',
                1,
            )
        )
    dialog(
        "Cancelled",
        "Setup was cancelled",
        "You can run this installer again when you’re ready.",
        "Finish",
        "Close",
    )
    dialog(
        "Failed",
        "Setup couldn’t finish",
        "Check your internet connection or ask your technician for a new installer.",
        "Finish",
        "Close",
    )
    tables = [
        _table(
            directory,
            "Dialog",
            [
                "Dialog",
                "HCentering",
                "VCentering",
                "Width",
                "Height",
                "Attributes",
                "Title",
                "Control_First",
                "Control_Default",
                "Control_Cancel",
            ],
            ["s72", "i2", "i2", "i2", "i2", "i4", "L128", "s50", "S50", "S50"],
            ["Dialog"],
            dialogs,
        ),
        _table(
            directory,
            "Control",
            [
                "Dialog_",
                "Control",
                "Type",
                "X",
                "Y",
                "Width",
                "Height",
                "Attributes",
                "Property",
                "Text",
                "Control_Next",
                "Help",
            ],
            [
                "s72",
                "s50",
                "s20",
                "i2",
                "i2",
                "i2",
                "i2",
                "i4",
                "S72",
                "L0",
                "S50",
                "L50",
            ],
            ["Dialog_", "Control"],
            controls,
        ),
        _table(
            directory,
            "ControlEvent",
            ["Dialog_", "Control_", "Event", "Argument", "Condition", "Ordering"],
            ["s72", "s50", "s50", "l255", "S255", "I2"],
            ["Dialog_", "Control_", "Event", "Argument", "Condition"],
            events,
        ),
        _table(
            directory,
            "TextStyle",
            ["TextStyle", "FaceName", "Size", "Color", "StyleBits"],
            ["s72", "l32", "i2", "I4", "I2"],
            ["TextStyle"],
            [("Normal", "Segoe UI", 9, None, 0), ("Title", "Segoe UI", 16, None, 1)],
        ),
        _table(
            directory,
            "EventMapping",
            ["Dialog_", "Control_", "Event", "Attribute"],
            ["s72", "s50", "s50", "s50"],
            ["Dialog_", "Control_", "Event"],
            [
                ("Progress", "Status", "ActionText", "Text"),
                ("Progress", "Progress", "SetProgress", "Progress"),
            ],
        ),
        _table(
            directory,
            "CheckBox",
            ["Property", "Value"],
            ["s72", "S64"],
            ["Property"],
            [("LaunchAfterSetup", "1")],
        ),
    ]
    return tables


def build_msi(
    destination,
    *,
    legacy,
    modern,
    mesh_settings=b"",
    arguments="",
    mode="support-service",
    name="JustConnect Support",
    publisher="JustConnect",
    version="1.0.1",
    cache=None,
):
    legacy.validate()
    modern.validate()
    if (
        mode not in ("support", "support-service", "monitoring")
        or len(mesh_settings) > 64 * 1024
        or len(arguments) > 4096
    ):
        raise BuildError("The installer configuration is invalid or too large.")
    if (
        not name.strip()
        or len(name) > 100
        or any(c in name + publisher for c in "\r\n\t\x00")
    ):
        raise BuildError("The installer name is invalid.")
    if not shutil.which("wixl") or not shutil.which("msibuild"):
        raise BuildError(
            "Install the server's MSI build tools: wixl, msitools and g++-mingw-w64-i686."
        )
    destination = Path(destination).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    cache = Path(cache) if cache else destination.parent / "build-cache"
    dll = native_actions(cache)
    monitoring = mode == "monitoring"
    service = mode == "support-service"
    machine = monitoring or service
    registry_mode = "monitoring" if monitoring else "support"
    if service:
        # Ignore server-wide agent naming overrides. This MSI owns a separate
        # service and folder, and must coexist with Access's Mesh Agent service.
        entries = dict(
            line.split("=", 1)
            for line in mesh_settings.decode("utf-8-sig").splitlines()
            if "=" in line
        )
        if not all(entries.get(key) for key in ("MeshID", "MeshServer", "ServerID")):
            raise BuildError("The support service needs its MeshCentral configuration.")
        for key in ("fileName", "installPath", "companyName"):
            entries.pop(key, None)
        entries.update(
            InstallFlags="0", meshServiceName="JustConnect Support", displayName=name
        )
        mesh_settings = (
            "\n".join(f"{key}={value}" for key, value in entries.items()) + "\n"
        ).encode()
    ET.register_namespace("", NAMESPACE)

    def element(parent, tag, **attributes):
        return ET.SubElement(parent, f"{{{NAMESPACE}}}{tag}", attributes)

    with tempfile.TemporaryDirectory(prefix="justconnect-msi-") as temporary:
        directory = Path(temporary)
        root = ET.Element(f"{{{NAMESPACE}}}Wix")
        product = element(
            root,
            "Product",
            Id=str(uuid.uuid4()),
            Name=name,
            Language="1033",
            Version=_version(version),
            Manufacturer=publisher,
            UpgradeCode=(
                "2D65D370-D538-4074-B9C0-99D74831D112"
                if monitoring
                else (
                    "07809887-591C-45A1-B033-9CB310EA242E"
                    if service
                    else "2F861486-F597-47D8-9EB7-C55F62548EA8"
                )
            ),
        )
        element(
            product,
            "Package",
            Id="*",
            InstallerVersion="500",
            Compressed="yes",
            InstallScope="perMachine" if machine else "perUser",
            Description=f"{name} installer",
        )
        element(
            product,
            "MajorUpgrade",
            DowngradeErrorMessage="A newer version is already installed.",
            AllowSameVersionUpgrades="yes",
        )
        element(product, "Media", Id="1", Cabinet="support.cab", EmbedCab="yes")
        props = {
            "PackageMode": mode,
            "LegacyUrl": legacy.url,
            "LegacySha256": legacy.sha256,
            "ModernUrl": modern.url,
            "ModernSha256": modern.sha256,
            "InstallArgumentsHex": arguments.encode().hex(),
            "DefaultUIFont": "Normal",
            "LaunchAfterSetup": "1",
            "ARPNOMODIFY": "1",
            "GeneratedInstaller": "1",
            "MSIFASTINSTALL": "7",
            "MsiHiddenProperties": "LegacyUrl;ModernUrl;MeshSettingsHex;InstallArgumentsHex;InstallClient;RollbackClient;CommitClient;UninstallClient;StopClient;LaunchClient",
        }
        for key, value in props.items():
            if value:
                element(product, "Property", Id=key, Value=value)
        condition = element(
            product,
            "Condition",
            Message="This installer needs Windows 7 Service Pack 1 or later.",
        )
        condition.text = "Installed OR VersionNT > 601 OR (VersionNT = 601 AND ServicePackLevel >= 1)"
        target = element(product, "Directory", Id="TARGETDIR", Name="SourceDir")
        if machine:
            volume = element(target, "Directory", Id="WindowsVolume")
            parent = element(
                volume, "Directory", Id="ProgramFilesRoot", Name="Program Files"
            )
            folder = element(
                parent,
                "Directory",
                Id="SupportInstallDir",
                Name="TacticalAgent" if monitoring else "JustConnectSupport",
            )
        else:
            parent = element(target, "Directory", Id="LocalAppDataFolder")
            folder = element(
                parent, "Directory", Id="SupportInstallDir", Name="JustConnectSupport"
            )
        component = element(
            folder, "Component", Id="Installation", Guid="*", Win64="no"
        )
        element(component, "CreateFolder")
        element(
            component,
            "RegistryValue",
            Root="HKLM" if machine else "HKCU",
            Key=f"Software\\JustConnect\\{registry_mode}",
            Name="InstallDir",
            Value="[SupportInstallDir]",
            Type="string",
            KeyPath="yes",
        )
        element(
            component,
            "RegistryValue",
            Root="HKLM" if machine else "HKCU",
            Key=f"Software\\JustConnect\\{registry_mode}",
            Name="ProductCode",
            Value="[ProductCode]",
            Type="string",
        )
        element(component, "RemoveFolder", Id="RemoveInstallFolder", On="uninstall")
        feature = element(product, "Feature", Id="Client", Title=name, Level="1")
        element(feature, "ComponentRef", Id="Installation")
        if monitoring:
            uninstall = element(
                folder,
                "Component",
                Id="Uninstaller",
                Guid=str(
                    uuid.uuid5(uuid.NAMESPACE_URL, "justconnect/monitoring/uninstaller")
                ),
            )
            element(
                uninstall,
                "File",
                Id="UninstallBridge",
                Source=str(native_uninstaller(cache).resolve()),
                Name="unins001.exe",
                KeyPath="yes",
            )
            element(feature, "ComponentRef", Id="Uninstaller")
        if not monitoring:
            settings_file = directory / "MeshAgent.msh"
            settings_file.write_bytes(mesh_settings)
            configuration = element(
                folder,
                "Component",
                Id="Configuration",
                Guid=str(
                    uuid.uuid5(uuid.NAMESPACE_URL, f"justconnect/{mode}/configuration")
                ),
            )
            element(
                configuration,
                "File",
                Id="MeshSettingsFile",
                Source=str(settings_file),
                Name="MeshAgent.msh",
                KeyPath="yes",
            )
            element(feature, "ComponentRef", Id="Configuration")
        if not monitoring and not service:
            menu = element(target, "Directory", Id="ProgramMenuFolder")
            menu_folder = element(
                menu, "Directory", Id="SupportMenu", Name="JustConnect"
            )
            shortcuts = element(menu_folder, "Component", Id="Shortcuts", Guid="*")
            element(
                shortcuts,
                "Shortcut",
                Id="OpenSupport",
                Name=name,
                Target="[SupportInstallDir]MeshAgent.exe",
                WorkingDirectory="SupportInstallDir",
            )
            element(shortcuts, "RemoveFolder", Id="RemoveMenu", On="uninstall")
            element(
                shortcuts,
                "RegistryValue",
                Root="HKCU",
                Key="Software\\JustConnect\\support",
                Name="Shortcut",
                Type="integer",
                Value="1",
                KeyPath="yes",
            )
            element(feature, "ComponentRef", Id="Shortcuts")
        element(product, "Binary", Id="NativeActions", SourceFile=str(dll.resolve()))
        for action_name, execute, result in (
            ("PrepareInstall", "immediate", "check"),
            ("StopClient", "deferred", "check"),
            ("RollbackClient", "rollback", "ignore"),
            ("InstallClient", "deferred", "check"),
            ("CommitClient", "commit", "ignore"),
            ("UninstallClient", "deferred", "check"),
            ("LaunchClient", "immediate", "check"),
        ):
            element(
                product,
                "CustomAction",
                Id=action_name,
                BinaryKey="NativeActions",
                DllEntry=action_name,
                Execute=execute,
                Return=result,
                Impersonate="no" if machine and execute != "immediate" else "yes",
            )
        sequence = element(product, "InstallExecuteSequence")
        for action_name, after, condition_text in (
            ("PrepareInstall", "CostFinalize", "1"),
            ("StopClient", "InstallInitialize", "1"),
            ("RollbackClient", "InstallFiles", 'NOT REMOVE~="ALL"'),
            ("InstallClient", "RollbackClient", 'NOT REMOVE~="ALL"'),
            ("CommitClient", "InstallClient", 'NOT REMOVE~="ALL"'),
            ("UninstallClient", "StopClient", 'REMOVE~="ALL"'),
        ):
            entry = element(sequence, "Custom", Action=action_name, After=after)
            entry.text = condition_text
        source = directory / "package.wxs"
        ET.ElementTree(root).write(source, encoding="utf-8", xml_declaration=True)
        package = directory / "package.msi"
        _run(["wixl", "-a", "x86", "-o", str(package), str(source)])
        # Explicit MSI sequence numbers: wixl's ordering of multiple relative
        # custom actions is insufficient for deferred-data and rollback ordering.
        for action_name, order in (
            ("PrepareInstall", 1100),
            ("StopClient", 1520),
            ("UninstallClient", 3400),
            ("RollbackClient", 4010),
            ("InstallClient", 4020),
            ("CommitClient", 4030),
            ("RemoveExistingProducts", 1510),
        ):
            _run(
                [
                    "msibuild",
                    str(package),
                    "-q",
                    f"UPDATE `InstallExecuteSequence` SET `Sequence` = {order} WHERE `Action` = '{action_name}'",
                ]
            )
        # wixl does not author RemoveFile elements. Import exact product files;
        # never use a wildcard or recursively remove a configurable directory.
        remove = _table(
            directory,
            "RemoveFile",
            ["FileKey", "Component_", "FileName", "DirProperty", "InstallMode"],
            ["s72", "s72", "L255", "s72", "i2"],
            ["FileKey"],
            [
                (f"RemoveFile{i}", "Installation", filename, "SupportInstallDir", 2)
                for i, filename in enumerate(
                    (
                        "tacticalrmm.exe",
                        "MeshAgent.exe",
                        "client.download",
                        "client.previous",
                        "client.replaced",
                        "settings.previous",
                        "service.previous",
                        "identity.previous",
                        "setup.pending",
                        "MeshAgent.msh",
                    )
                )
            ]
            + [("RemoveInstallFolder", "Installation", None, "SupportInstallDir", 2)]
            + (
                []
                if monitoring or service
                else [("RemoveMenu", "Shortcuts", None, "SupportMenu", 2)]
            ),
        )
        _run(["msibuild", str(package), "-i", str(remove)])
        for action_name, execute in (
            ("PrepareInstall", 0),
            ("StopClient", 1024),
            ("RollbackClient", 1280),
            ("InstallClient", 1024),
            ("CommitClient", 1536),
            ("UninstallClient", 1024),
            ("LaunchClient", 0),
        ):
            flags = (
                1
                + execute
                + (2048 if machine and execute else 0)
                + (8192 if execute else 0)
            )
            if action_name in ("RollbackClient", "CommitClient"):
                flags += 64  # Continue MSI rollback/commit even if a cleanup reports an error.
            _run(
                [
                    "msibuild",
                    str(package),
                    "-q",
                    f"UPDATE `CustomAction` SET `Type` = {flags} WHERE `Action` = '{action_name}'",
                ]
            )
        for table in _user_interface(directory, name, monitoring, service):
            _run(["msibuild", str(package), "-i", str(table)])
        for dialog, condition_text, order in (
            ("Welcome", "NOT Installed", 1290),
            ("Maintenance", "Installed", 1290),
            ("Progress", "1", 1299),
            ("Complete", "1", -1),
            ("Cancelled", "1", -2),
            ("Failed", "1", -3),
        ):
            _run(
                [
                    "msibuild",
                    str(package),
                    "-q",
                    f"INSERT INTO `InstallUISequence` (`Action`, `Condition`, `Sequence`) VALUES ('{dialog}', '{condition_text}', {order})",
                ]
            )
        if package.read_bytes()[:8] != MSI_MAGIC:
            raise BuildError("The builder did not return a Windows Installer package.")
        # Copy only the completed database, never an intermediate build.
        with tempfile.NamedTemporaryFile(
            dir=destination.parent, delete=False
        ) as output:
            staged = Path(output.name)
            with package.open("rb") as stream:
                shutil.copyfileobj(stream, output)
        try:
            os.replace(staged, destination)
        finally:
            staged.unlink(missing_ok=True)
    return destination
