"""Reject Windows 7 payloads with incompatible PE headers or static imports.

This is a build guard, not a substitute for exercising remote control on Windows 7.
"""

import argparse
import struct
from pathlib import Path

POST_WINDOWS7_IMPORTS = {
    "CreateFile2",
    "GetOverlappedResultEx",
    "GetSystemTimePreciseAsFileTime",
    "WaitOnAddress",
    "WakeByAddressSingle",
    "WakeByAddressAll",
    "SetThreadDescription",
    "GetThreadDescription",
    "GetTempPath2A",
    "GetTempPath2W",
    "GetCurrentPackageFullName",
    "GetCurrentPackageId",
    "GetCurrentPackageFamilyName",
    "SetProcessMitigationPolicy",
    "GetProcessMitigationPolicy",
    "SetThreadInformation",
    "GetSystemCpuSetInformation",
    "VirtualAlloc2",
    "DiscardVirtualMemory",
    "PrefetchVirtualMemory",
}


def verify(path):
    data = Path(path).read_bytes()
    try:
        if data[:2] != b"MZ":
            raise ValueError("Expected a Windows executable")
        header = struct.unpack_from("<I", data, 60)[0]
        if data[header : header + 4] != b"PE\0\0":
            raise ValueError("Invalid Windows PE header")
        machine, count = struct.unpack_from("<HH", data, header + 4)
        optional_size = struct.unpack_from("<H", data, header + 20)[0]
        optional = header + 24
        if machine != 0x14C or struct.unpack_from("<H", data, optional)[0] != 0x10B:
            raise ValueError(
                "The Windows 7 payload must be x86 for 32-bit and 64-bit PCs"
            )
        for offset in (40, 48):
            if struct.unpack_from("<HH", data, optional + offset) > (6, 1):
                raise ValueError(
                    "The payload requires a newer Windows version than Windows 7"
                )
        sections = optional + optional_size

        def address(rva):
            for index in range(count):
                size, virtual, raw_size, raw = struct.unpack_from(
                    "<IIII", data, sections + index * 40 + 8
                )
                if virtual <= rva < virtual + max(size, raw_size):
                    offset = raw + rva - virtual
                    if offset >= len(data) or rva - virtual >= raw_size:
                        break
                    return offset
            raise ValueError("Invalid PE import address")

        imports = []
        import_rva, import_size = struct.unpack_from("<II", data, optional + 104)
        if not import_rva or not import_size:
            raise ValueError("Missing Windows import table")
        descriptor = address(import_rva)
        for descriptor in range(
            descriptor, min(descriptor + import_size, len(data)), 20
        ):
            original, _, _, name, first = struct.unpack_from("<IIIII", data, descriptor)
            if not any((original, name, first)):
                break
            thunk = address(original or first)
            while True:
                function = struct.unpack_from("<I", data, thunk)[0]
                if not function:
                    break
                thunk += 4
                if function & 0x80000000:
                    continue
                start = address(function) + 2
                end = data.find(b"\0", start, start + 512)
                if end == -1:
                    raise ValueError("Invalid PE import name")
                imports.append(data[start:end].decode("ascii"))
        incompatible = sorted(set(imports) & POST_WINDOWS7_IMPORTS)
        if incompatible:
            raise ValueError(
                "APIs unavailable on Windows 7: " + ", ".join(incompatible)
            )
        return {"architecture": "x86", "static_imports_checked": len(imports)}
    except (struct.error, UnicodeError) as exc:
        raise ValueError("Invalid Windows executable") from exc


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path")
    args = parser.parse_args()
    print(verify(args.path))
