"""Record provenance only after platform-native signature verification succeeds."""

import argparse
import hashlib
import json
import re
from pathlib import Path


def write_record(directory, platform, version, signer="", unsigned=False):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+_-]{0,49}", version):
        raise ValueError(
            "Use a version of at most 50 letters, numbers, dots, hyphens, underscores or plus signs"
        )
    if not unsigned and (not signer.strip() or len(signer) > 255):
        raise ValueError("Missing or invalid verified signing identity")
    root = Path(directory)
    if platform not in ("windows", "macos"):
        raise ValueError("Unsupported platform")
    suffix = "pkg" if platform == "macos" else "msi"
    path = root / f"TacticalSupport-{platform}.{suffix}"
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    record = {
        "platform": platform,
        "filename": path.name,
        "sha256": digest,
        "size": path.stat().st_size,
        "generated": True,
        "format": suffix,
        "signature": {
            "provider": (
                "unsigned"
                if unsigned
                else (
                    "azure-artifact-signing"
                    if platform != "macos"
                    else "apple-developer-id"
                )
            ),
            "signer": signer.strip(),
            "verified": not unsigned,
            **({"notarized": not unsigned} if platform == "macos" else {}),
        },
    }
    (root / f"{platform}.json").write_text(
        json.dumps({"schema": 2, "version": version, "installer": record}, indent=2)
        + "\n",
        encoding="utf-8",
    )


def combine(directory):
    root = Path(directory)
    records = [
        json.loads((root / f"{p}.json").read_text(encoding="utf-8"))
        for p in ("windows", "macos")
        if (root / f"{p}.json").is_file()
    ]
    if not records:
        raise ValueError("No verified platform records found")
    if any(record["version"] != records[0]["version"] for record in records):
        raise ValueError("Platform versions must match")
    for record in records:
        installer = record["installer"]
        with (root / installer["filename"]).open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != installer["sha256"]:
                raise ValueError("Installer changed after signing")
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "schema": 2,
                "version": records[0]["version"],
                "installers": [r["installer"] for r in records],
                "payloads": [
                    {
                        "filename": f"payloads/{p.name}",
                        "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                        "size": p.stat().st_size,
                    }
                    for p in sorted((root / "payloads").glob("*.exe"))
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--combine", action="store_true")
    parser.add_argument("--platform", choices=["windows", "macos"])
    parser.add_argument("--version")
    parser.add_argument("--signer")
    parser.add_argument("--unsigned", action="store_true")
    args = parser.parse_args()
    if args.combine:
        combine(args.directory)
    elif args.platform and args.version and (args.signer or args.unsigned):
        write_record(
            args.directory,
            args.platform,
            args.version,
            args.signer or "",
            args.unsigned,
        )
    else:
        parser.error("Specify --combine or --platform, --version and --signer")
