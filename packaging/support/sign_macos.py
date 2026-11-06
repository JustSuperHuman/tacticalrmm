"""Sign nested MeshCentral executables, then rebuild and sign the Mac installer."""

import argparse
import os
import subprocess
import tempfile
from pathlib import Path

MACHO = {
    b"\xfe\xed\xfa\xce",
    b"\xce\xfa\xed\xfe",
    b"\xfe\xed\xfa\xcf",
    b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe",
    b"\xbe\xba\xfe\xca",
    b"\xca\xfe\xba\xbf",
    b"\xbf\xba\xfe\xca",
}


def run(*args):
    subprocess.run(args, check=True)


def sign(source, output):
    application_id = os.environ["APPLE_APPLICATION_IDENTITY"]
    installer_id = os.environ["APPLE_INSTALLER_IDENTITY"]
    keychain = os.environ["SUPPORT_KEYCHAIN"]
    entitlements = Path(__file__).with_name("support.entitlements.plist")
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        expanded = root / "expanded"
        run("pkgutil", "--expand-full", str(source), str(expanded))
        signed = []
        for path in expanded.rglob("*"):
            if path.is_symlink() or not path.is_file():
                continue
            with path.open("rb") as stream:
                magic = stream.read(4)
            if magic not in MACHO:
                continue
            run(
                "codesign",
                "--force",
                "--timestamp",
                "--options",
                "runtime",
                "--keychain",
                keychain,
                "--entitlements",
                str(entitlements),
                "--sign",
                application_id,
                str(path),
            )
            signed.append(path)
        if not signed:
            raise ValueError("No Mach-O executables found in the support package")
        bundles = sorted(
            (p for p in expanded.rglob("*.app") if p.is_dir()),
            key=lambda p: len(p.parts),
            reverse=True,
        )
        for bundle in bundles:
            run(
                "codesign",
                "--force",
                "--timestamp",
                "--options",
                "runtime",
                "--keychain",
                keychain,
                "--entitlements",
                str(entitlements),
                "--sign",
                application_id,
                str(bundle),
            )
        for path in signed + bundles:
            run("codesign", "--verify", "--strict", "--verbose=2", str(path))
        unsigned = root / "unsigned.pkg"
        run("pkgutil", "--flatten-full", str(expanded), str(unsigned))
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        run(
            "productsign",
            "--timestamp",
            "--keychain",
            keychain,
            "--sign",
            installer_id,
            str(unsigned),
            str(output),
        )
        run("pkgutil", "--check-signature", str(output))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    sign(args.source, args.output)
