"""
Update checker for SC Rock ID.

Asks the GitHub Releases API for the newest release, downloads RockID.exe over HTTPS,
checks its size and SHA-256 against what GitHub reports, then swaps the exe with a
tiny batch script once the running app has closed. Nothing else is downloaded or run.
"""
import hashlib
import json
import os
import re
import subprocess
import tempfile
import urllib.request

REPO = "KuzuCevirme/SC-RockID"
LATEST_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
ASSET_NAME = "RockID.exe"
USER_AGENT = "SC-RockID-updater"


def parse_version(text):
    """'v1.2' / 'SC Rock ID v1.2.3' -> (1, 2) / (1, 2, 3); None if no number."""
    m = re.search(r"(\d+(?:\.\d+)*)", text or "")
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def is_newer(remote, local):
    if not remote or not local:
        return False
    n = max(len(remote), len(local))
    pad = lambda v: tuple(v) + (0,) * (n - len(v))  # noqa: E731
    return pad(remote) > pad(local)


def _get(url, timeout):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(req, timeout=timeout)


def fetch_latest(url=LATEST_API, timeout=6):
    with _get(url, timeout) as r:
        rel = json.load(r)
    # Version comes from the tag (e.g. "v1.2"); if the tag has no number, from the title.
    ver = parse_version(rel.get("tag_name")) or parse_version(rel.get("name"))
    assets = rel.get("assets") or []
    asset = next((a for a in assets if a.get("name", "").lower() == ASSET_NAME.lower()), None)
    if asset is None:
        asset = next((a for a in assets if a.get("name", "").lower().endswith(".exe")), None)
    digest = (asset or {}).get("digest") or ""
    return {
        "version": ver,
        "version_str": ".".join(map(str, ver)) if ver else "?",
        "title": rel.get("name") or rel.get("tag_name") or "",
        "notes": (rel.get("body") or "").strip(),
        "page": rel.get("html_url") or RELEASES_PAGE,
        "url": (asset or {}).get("browser_download_url"),
        "size": (asset or {}).get("size"),
        "sha256": digest[7:].lower() if digest.startswith("sha256:") else None,
    }


def download(info, dest, progress=None, timeout=20):
    """Download the release exe to dest, verifying size and SHA-256. progress(done, total)."""
    if not info.get("url"):
        raise RuntimeError("This release has no RockID.exe attached.")
    part = dest + ".part"
    h = hashlib.sha256()
    done = 0
    total = info.get("size") or 0
    try:
        with _get(info["url"], timeout) as r, open(part, "wb") as f:
            total = total or int(r.headers.get("Content-Length") or 0)
            while True:
                chunk = r.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
        if info.get("size") and done != info["size"]:
            raise RuntimeError(f"Download incomplete ({done} of {info['size']} bytes).")
        if info.get("sha256") and h.hexdigest() != info["sha256"]:
            raise RuntimeError("Downloaded file failed the SHA-256 check - not installed.")
        os.replace(part, dest)
    except Exception:
        try:
            os.remove(part)
        except OSError:
            pass
        raise
    return dest


_SWAP_BAT = r"""@echo off
rem SC Rock ID updater: waits for the app to close, swaps the exe, restarts it.
set n=0
:wait
"%SystemRoot%\System32\tasklist.exe" /NH /FI "PID eq {pid}" 2>nul | "%SystemRoot%\System32\find.exe" " {pid} " >nul
if errorlevel 1 goto swap
set /a n+=1
if %n% geq 60 goto swap
"%SystemRoot%\System32\PING.EXE" -n 2 127.0.0.1 >nul
goto wait
:swap
set t=0
:retry
move /y "{new}" "{exe}" >nul 2>&1
if not errorlevel 1 goto run
set /a t+=1
if %t% geq 15 goto run
"%SystemRoot%\System32\PING.EXE" -n 2 127.0.0.1 >nul
goto retry
:run
start "" "{exe}"
del "%~f0"
"""


def install_and_restart(new_exe, exe, pid):
    """Start a hidden script that replaces exe with new_exe after process pid exits."""
    bat = os.path.join(tempfile.gettempdir(), f"rockid_update_{pid}.bat")
    with open(bat, "w", encoding="mbcs" if os.name == "nt" else "utf-8") as f:
        f.write(_SWAP_BAT.format(pid=pid, new=new_exe, exe=exe))
    env = dict(os.environ)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"  # new exe must unpack fresh, not reuse ours
    for k in [k for k in env if k.startswith("_PYI_") or k == "_MEIPASS2"]:
        env.pop(k)
    cmd = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "cmd.exe")
    subprocess.Popen([cmd, "/c", bat], env=env, close_fds=True,
                     creationflags=0x08000000 | 0x00000200)  # no window, own process group
    return bat
