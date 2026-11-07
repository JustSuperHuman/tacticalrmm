import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from support.installer.builder import MSI_MAGIC
from support.models import SupportInstaller, SupportPayload
from support.views import installer_directory

PROVIDERS = {"windows": "azure-artifact-signing", "macos": "apple-developer-id"}
MAX_SIZE = 256 * 1024 * 1024


def msi_properties(path):
    try:
        result = subprocess.run(
            ["msiinfo", "export", str(path), "Property"],
            check=True,
            capture_output=True,
            timeout=30,
        )
        return dict(
            line.split("\t", 1)
            for line in result.stdout.decode("utf-8").splitlines()[3:]
            if "\t" in line
        )
    except (OSError, UnicodeError, subprocess.SubprocessError) as exc:
        raise ValueError("A generated Windows MSI could not be inspected") from exc


def validate_file(root, filename, record, magic):
    digest = record["sha256"]
    if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise ValueError("Invalid SHA-256 digest")
    path = (root / filename).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Installer must be inside the release directory")
    size = path.stat().st_size
    if (
        type(record["size"]) is not int
        or not 0 < size <= MAX_SIZE
        or size != record["size"]
    ):
        raise ValueError("Installer size does not match the manifest")
    with path.open("rb") as stream:
        if stream.read(len(magic)) != magic:
            raise ValueError("Unexpected installer format")
        stream.seek(0)
        if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
            raise ValueError("Installer SHA-256 does not match the manifest")
    return path


def validate_bundle(directory, allow_unsigned=False):
    root = Path(directory).resolve()
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        if manifest["schema"] != 2:
            raise ValueError(
                "Regenerate this release with the MSI installer workflow (schema 2)"
            )
        version = manifest["version"]
        if not isinstance(version, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9.+_-]{0,49}", version
        ):
            raise ValueError("Invalid version")
        records = manifest["installers"]
        if not isinstance(records, list) or not 1 <= len(records) <= 2:
            raise ValueError("A release must include one or more generated installers")
        payloads = {}
        if (
            not isinstance(manifest.get("payloads", []), list)
            or len(manifest.get("payloads", [])) > 8
        ):
            raise ValueError("Invalid client payload list")
        for record in manifest.get("payloads", []):
            digest = record["sha256"]
            if record["filename"] != f"payloads/{digest}.exe" or digest in payloads:
                raise ValueError("Unexpected or duplicate client payload")
            path = validate_file(root, record["filename"], record, b"MZ")
            payloads[digest] = (path, record["size"])
        validated = {}
        for record in records:
            platform = record["platform"]
            if platform not in PROVIDERS or platform in validated:
                raise ValueError("Invalid or duplicate platform")
            installer = SupportInstaller(platform=platform)
            expected_format = "msi" if platform == "windows" else "pkg"
            if (
                record["filename"] != installer.filename
                or record.get("format") != expected_format
                or record.get("generated") is not True
            ):
                raise ValueError("Only generated MSI and Mac packages can be published")
            signature = record["signature"]
            unsigned = (
                signature.get("provider") == "unsigned"
                and signature.get("verified") is False
            )
            if unsigned:
                if not allow_unsigned:
                    raise ValueError(
                        "This generated package is unsigned; use --allow-unsigned to publish it"
                    )
                signer = ""
            else:
                if (
                    signature["provider"] != PROVIDERS[platform]
                    or signature["verified"] is not True
                ):
                    raise ValueError(
                        "The release is missing verified signing provenance"
                    )
                if platform == "macos" and signature.get("notarized") is not True:
                    raise ValueError("The Mac package must be notarized")
                signer = signature["signer"]
                if not isinstance(signer, str) or not 1 <= len(signer.strip()) <= 255:
                    raise ValueError("Missing signing identity")
            path = validate_file(
                root,
                installer.filename,
                record,
                MSI_MAGIC if platform == "windows" else b"xar!",
            )
            if platform == "windows":
                properties = msi_properties(path)
                if (
                    properties.get("GeneratedInstaller") != "1"
                    or properties.get("PackageMode") != "support"
                ):
                    raise ValueError(
                        "The Windows package is not a generated support MSI"
                    )
                if (
                    not {properties.get("LegacySha256"), properties.get("ModernSha256")}
                    <= payloads.keys()
                ):
                    raise ValueError(
                        "The release is missing the MSI's verified client payloads"
                    )
            validated[platform] = (
                path,
                {
                    "version": version,
                    "sha256": record["sha256"],
                    "size": record["size"],
                    "signer": signer.strip(),
                    "signed": not unsigned,
                    "generated": True,
                },
            )
        return validated, payloads
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CommandError(f"Invalid support release: {exc}") from exc


def copy_verified(source, destination, digest):
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination.parent, delete=False
        ) as target:
            temporary = Path(target.name)
            with source.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
        with temporary.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                raise CommandError("The release changed during import")
        temporary.chmod(0o640)
        temporary.replace(destination)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


class Command(BaseCommand):
    help = "Publish generated MSI/Mac packages and their verified, unconfigured client payloads."

    def add_arguments(self, parser):
        parser.add_argument(
            "directory", help="Extracted trusted CI bundle containing manifest.json"
        )
        parser.add_argument(
            "--allow-unsigned",
            action="store_true",
            help="Allow generated packages that have not been code-signed yet.",
        )

    def handle(self, *args, **options):
        records, payloads = validate_bundle(
            options["directory"], options["allow_unsigned"]
        )
        root = installer_directory()
        for digest, (source, _) in payloads.items():
            copy_verified(source, root / "payloads" / f"{digest}.exe", digest)
        for platform, (source, fields) in records.items():
            installer = SupportInstaller(platform=platform, **fields)
            copy_verified(source, root / installer.storage_name, fields["sha256"])
        with transaction.atomic():
            for digest, (_, size) in payloads.items():
                SupportPayload.objects.update_or_create(
                    sha256=digest, defaults={"size": size}
                )
            for platform, (_, fields) in records.items():
                SupportInstaller.objects.update_or_create(
                    platform=platform, defaults=fields
                )
        self.stdout.write(
            self.style.SUCCESS(
                f"Published generated support installers: {', '.join(records)}."
            )
        )
