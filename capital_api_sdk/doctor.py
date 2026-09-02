"""SKCOM environment preflight: DLL / COM registration / comtypes cache checks.

Diagnoses the classic setup failures before any login:
  1. Python bitness vs SKCOM.dll bitness mismatch (REGDB_E_CLASSNOTREG source #1).
  2. DLL not registered, or a DIFFERENT copy registered than the configured one.
  3. Stale comtypes generated cache: after upgrading SKCOM.dll the module under
     comtypes.gen still reflects the old typelib and API calls fail obscurely.

Run:
    python -m capital_api_sdk.doctor            # report only
    python -m capital_api_sdk.doctor --clean    # also delete a stale comtypes cache
"""
from __future__ import annotations

import os
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

# SKCOM.dll TypeLib GUID (stable across versions).
SKCOM_TYPELIB_GUID = "{75AAD71C-8F4F-4F1F-9AEE-3D41A8C9BA5E}"


@dataclass(slots=True)
class DoctorReport:
    checks: list[tuple[str, str, str]] = field(default_factory=list)  # (level, name, detail)

    def add(self, level: str, name: str, detail: str) -> None:
        self.checks.append((level, name, detail))

    @property
    def ok(self) -> bool:
        return all(level != "FAIL" for level, _, _ in self.checks)

    def render(self) -> str:
        width = max((len(name) for _, name, _ in self.checks), default=0)
        return "\n".join(
            f"[{level:<4}] {name:<{width}}  {detail}"
            for level, name, detail in self.checks
        )


def python_bitness() -> int:
    return struct.calcsize("P") * 8


def dll_bitness(path: Path) -> int | None:
    """Read the PE machine field: 0x8664 -> 64-bit, 0x14c -> 32-bit."""
    try:
        with path.open("rb") as fh:
            if fh.read(2) != b"MZ":
                return None
            fh.seek(0x3C)
            pe_offset = struct.unpack("<I", fh.read(4))[0]
            fh.seek(pe_offset)
            if fh.read(4) != b"PE\x00\x00":
                return None
            machine = struct.unpack("<H", fh.read(2))[0]
    except OSError:
        return None
    return {0x8664: 64, 0x14C: 32, 0xAA64: 64}.get(machine)


def dll_file_version(path: Path) -> str:
    try:
        import win32api  # type: ignore

        info = win32api.GetFileVersionInfo(str(path), "\\")
        ms, ls = info["FileVersionMS"], info["FileVersionLS"]
        return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
    except Exception:
        return ""


def registered_typelib_paths() -> dict[str, str]:
    """Registered SKCOM typelib paths keyed by hive/view, e.g. {'HKCU/win64': path}.

    Checks HKCU (per-user registration, takes precedence in the merged HKCR
    view) plus both HKLM registry views and both win32/win64 subkeys. Per-user
    entries are how non-admin users register (regsvr32 /n /i:user).
    """
    import winreg

    out: dict[str, str] = {}
    subkey = rf"SOFTWARE\Classes\TypeLib\{SKCOM_TYPELIB_GUID}\1.0\0"
    hives = [
        ("HKCU", winreg.HKEY_CURRENT_USER, 0),
        ("HKLM64", winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_64KEY),
        ("HKLM32", winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY),
    ]
    for hive_name, hive, view_flag in hives:
        for arch in ("win32", "win64"):
            try:
                with winreg.OpenKey(hive, rf"{subkey}\{arch}", 0, winreg.KEY_READ | view_flag) as key:
                    value, _ = winreg.QueryValueEx(key, "")
            except OSError:
                continue
            if value:
                out[f"{hive_name}/{arch}"] = str(value)
    return out


def comtypes_cache_origin() -> tuple[Path | None, str]:
    """(generated module path, typelib_path it was built from) for SKCOMLib."""
    try:
        import comtypes.client  # type: ignore

        gen_dir = Path(comtypes.client.gen_dir)
    except Exception:
        return None, ""
    stem = SKCOM_TYPELIB_GUID.strip("{}").replace("-", "_")
    for module in sorted(gen_dir.glob(f"_{stem}_*_*_*.py")):
        text = module.read_text(encoding="mbcs", errors="replace")
        for line in text.splitlines():
            if line.startswith("typelib_path"):
                _, _, value = line.partition("=")
                value = value.strip()
                try:
                    import ast

                    return module, str(ast.literal_eval(value))
                except (ValueError, SyntaxError):
                    return module, value.strip("'\"")
        return module, ""
    return None, ""


def clean_comtypes_cache() -> list[Path]:
    """Delete the SKCOMLib comtypes cache files; they regenerate on next load()."""
    import comtypes.client  # type: ignore

    gen_dir = Path(comtypes.client.gen_dir)
    stem = SKCOM_TYPELIB_GUID.strip("{}").replace("-", "_")
    removed: list[Path] = []
    for pattern in (f"_{stem}_*", "SKCOMLib.py"):
        for item in gen_dir.glob(pattern):
            if item.is_dir():
                continue
            item.unlink(missing_ok=True)
            removed.append(item)
    for cache in gen_dir.glob("__pycache__"):
        for item in cache.glob(f"*{stem}*"):
            item.unlink(missing_ok=True)
            removed.append(item)
        for item in cache.glob("SKCOMLib*"):
            item.unlink(missing_ok=True)
            removed.append(item)
    return removed


def run_doctor(dll_path: str | None = None) -> DoctorReport:
    report = DoctorReport()
    py_bits = python_bitness()
    report.add("OK", "python", f"{sys.version.split()[0]} ({py_bits}-bit)")

    if sys.platform != "win32":
        report.add("FAIL", "platform", "SKCOM.dll needs Windows COM; this is not Windows.")
        return report

    # 1. configured DLL
    if dll_path is None:
        try:
            from .com_client import load_dotenv_config

            load_dotenv_config()
            dll_path = os.getenv("CAPITAL_SKCOM_DLL", "SKCOM.dll")
        except Exception:
            dll_path = "SKCOM.dll"
    configured = Path(dll_path)
    if configured.is_file():
        bits = dll_bitness(configured)
        version = dll_file_version(configured)
        level = "OK" if bits == py_bits else "FAIL"
        detail = f"{configured} (version {version or '?'}, {bits or '?'}-bit)"
        if bits != py_bits:
            detail += f" — bitness mismatch: Python is {py_bits}-bit"
        report.add(level, "configured dll", detail)
    else:
        report.add("WARN", "configured dll", f"{configured} not found (CAPITAL_SKCOM_DLL)")

    # 2. COM registration. HKCU (per-user) precedes HKLM in the merged HKCR
    #    view, so a valid HKCU entry supersedes stale machine-wide entries.
    #    WITHOUT admin rights, use per-user registration; running plain
    #    regsvr32 unelevated is DESTRUCTIVE: the ATL script deletes existing
    #    (HKCU-backed) keys first, then fails to recreate them in HKLM.
    per_user_cmd = f'regsvr32 /n /i:user "{configured}"' if configured.is_file() else "regsvr32 /n /i:user <SKCOM.dll>"
    registered = registered_typelib_paths()
    valid_views = {
        view for view, path in registered.items()
        if Path(path).is_file() and dll_bitness(Path(path)) in (None, py_bits)
    }
    if not registered:
        report.add("FAIL", "com registration",
                   f"SKCOM typelib not registered. Without admin rights run: {per_user_cmd} ; "
                   "with admin rights run install.bat / regsvr32 as Administrator.")
    else:
        for view, path in registered.items():
            reg_path = Path(path)
            exists = reg_path.is_file()
            bits = dll_bitness(reg_path) if exists else None
            version = dll_file_version(reg_path) if exists else ""
            same = configured.is_file() and exists and reg_path.resolve() == configured.resolve()
            level = "OK"
            detail = f"[{view}] {path} (version {version or '?'})"
            if not exists:
                if valid_views:
                    detail += " — stale entry, superseded by a valid registration; harmless"
                else:
                    level, detail = "FAIL", detail + f" — registered file is missing; re-register: {per_user_cmd}"
            elif bits is not None and bits != py_bits:
                level, detail = "WARN", detail + f" — {bits}-bit, Python is {py_bits}-bit"
            elif configured.is_file() and not same:
                level, detail = "WARN", detail + " — differs from CAPITAL_SKCOM_DLL; keep them identical"
            elif view.startswith("HKCU"):
                detail += " — per-user registration, takes precedence"
            report.add(level, "com registration", detail)

    # 3. comtypes cache
    module, origin = comtypes_cache_origin()
    if module is None:
        report.add("OK", "comtypes cache", "no cached SKCOMLib module (generated on first load())")
    else:
        origin_path = Path(origin) if origin else None
        origin_version = dll_file_version(origin_path) if origin_path and origin_path.is_file() else ""
        reg_versions = {
            dll_file_version(Path(p)) for p in registered.values() if Path(p).is_file()
        } - {""}
        detail = f"{module.name} built from {origin or '?'} (version {origin_version or '?'})"
        if origin_path is not None and not origin_path.is_file():
            report.add("FAIL", "comtypes cache",
                       detail + " — source DLL gone; run --clean and reload")
        elif reg_versions and origin_version and origin_version not in reg_versions:
            report.add("FAIL", "comtypes cache",
                       detail + f" — registered version is {sorted(reg_versions)}; STALE cache, run --clean")
        else:
            report.add("OK", "comtypes cache", detail)

    # 4. live COM check: the definitive test — generate/reuse the typelib module
    #    and instantiate SKCenterLib (no login, no network side effects).
    if configured.is_file():
        try:
            import comtypes.client  # type: ignore

            comtypes.client.GetModule(str(configured))
            import comtypes.gen.SKCOMLib as sk  # type: ignore

            center = comtypes.client.CreateObject(sk.SKCenterLib, interface=sk.ISKCenterLib)
            report.add("OK", "com objects", f"SKCenterLib created (comtypes {getattr(__import__('comtypes'), '__version__', '?')})")
            del center
        except Exception as exc:
            report.add("FAIL", "com objects", f"CreateObject failed: {exc!r} — check registration/bitness, or run --clean")

    return report


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    clean = "--clean" in args
    dll_args = [a for a in args if not a.startswith("--")]
    report = run_doctor(dll_args[0] if dll_args else None)
    print(report.render())
    if clean:
        removed = clean_comtypes_cache()
        print(f"\ncleaned {len(removed)} comtypes cache file(s); regenerated on next client.load()")
        for item in removed:
            print(f"  removed {item}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
