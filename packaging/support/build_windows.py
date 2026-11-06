"""Package the signed Windows client with its separate MeshCentral configuration."""

import os
import re
import subprocess
from pathlib import Path


def build():
    version = os.environ["SUPPORT_VERSION"]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+_-]{0,49}", version):
        raise ValueError("Invalid support version")
    match = re.match(r"v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:\.(\d+))?", version)
    numbers = [int(n or 0) for n in match.groups()] if match else [0, 0, 0, 0]
    if any(n > 65535 for n in numbers):
        raise ValueError("Version components must be at most 65535")
    numeric_version = ".".join(str(n) for n in numbers)
    compiler = Path(os.environ["ProgramFiles(x86)"]) / "Inno Setup 6" / "ISCC.exe"
    if not compiler.is_file():
        raise ValueError("Install Inno Setup 6 before building the Windows installer")
    subprocess.run(
        [
            str(compiler),
            f"/DNumericVersion={numeric_version}",
            str(Path(__file__).with_name("windows.iss")),
        ],
        check=True,
    )


if __name__ == "__main__":
    build()
