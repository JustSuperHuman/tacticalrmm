import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from support.models import SupportInstaller
from support.views import installer_directory

PROVIDERS = {"windows": "azure-artifact-signing", "macos": "apple-developer-id"}
MAX_SIZE = 256 * 1024 * 1024


def validate_bundle(directory):
    root = Path(directory).resolve()
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        if manifest["schema"] != 1:
            raise ValueError("Unsupported manifest schema")
        version = manifest["version"]
        if not isinstance(version, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9.+_-]{0,49}", version
        ):
            raise ValueError("Invalid version")
        records = manifest["installers"]
        if not isinstance(records, list) or len(records) != 2:
            raise ValueError("A release must include Windows and Mac installers")
        validated = {}
        for record in records:
            platform = record["platform"]
            if platform not in PROVIDERS or platform in validated:
                raise ValueError("Invalid or duplicate platform")
            installer = SupportInstaller(platform=platform)
            if record["filename"] != installer.filename:
                raise ValueError("Unexpected installer filename")
            signature = record["signature"]
            if (
                signature["provider"] != PROVIDERS[platform]
                or signature["verified"] is not True
            ):
                raise ValueError("The release is missing verified signing provenance")
            if platform == "macos" and signature.get("notarized") is not True:
                raise ValueError("The Mac package must be notarized")
            signer = signature["signer"]
            if not isinstance(signer, str) or not 1 <= len(signer.strip()) <= 255:
                raise ValueError("Missing signing identity")
            digest = record["sha256"]
            if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
                raise ValueError("Invalid SHA-256 digest")
            path = (root / installer.filename).resolve()
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
                magic = stream.read(4)
                stream.seek(0)
                actual = hashlib.file_digest(stream, "sha256").hexdigest()
            if (platform == "windows" and magic[:2] != b"MZ") or (
                platform == "macos" and magic != b"xar!"
            ):
                raise ValueError("Unexpected installer format")
            if actual != digest:
                raise ValueError("Installer SHA-256 does not match the manifest")
            validated[platform] = (
                path,
                {
                    "version": version,
                    "sha256": digest,
                    "size": size,
                    "signer": signer.strip(),
                },
            )
        return validated
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CommandError(f"Invalid support release: {exc}") from exc


class Command(BaseCommand):
    help = "Publish the verified Windows and Mac bundle from the support-installers workflow."

    def add_arguments(self, parser):
        parser.add_argument(
            "directory", help="Extracted trusted CI bundle containing manifest.json"
        )

    def handle(self, *args, **options):
        # Validate the complete release before copying files or changing active records.
        records = validate_bundle(options["directory"])
        root = installer_directory()
        root.mkdir(parents=True, exist_ok=True)
        for platform, (source, fields) in records.items():
            installer = SupportInstaller(platform=platform, **fields)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=root, delete=False) as target:
                    temporary = Path(target.name)
                    with source.open("rb") as stream:
                        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                            target.write(chunk)
                    target.flush()
                    os.fsync(target.fileno())
                # Recheck copied bytes as well, including changes to the source during import.
                with temporary.open("rb") as stream:
                    if (
                        hashlib.file_digest(stream, "sha256").hexdigest()
                        != fields["sha256"]
                    ):
                        raise CommandError("The release changed during import")
                os.chmod(temporary, 0o640)
                os.replace(temporary, root / installer.storage_name)
            finally:
                if temporary and temporary.exists():
                    temporary.unlink()
        with transaction.atomic():
            for platform, (_, fields) in records.items():
                SupportInstaller.objects.update_or_create(
                    platform=platform, defaults=fields
                )
        self.stdout.write(
            self.style.SUCCESS("Published signed Windows and Mac support installers.")
        )
