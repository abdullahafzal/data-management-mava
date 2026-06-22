#!/usr/bin/env python
# coding: utf-8

# ## Instant Checkmate — phone search (dashboard)
# 
# Automates [Instant Checkmate dashboard](https://app.instantcheckmate.com/dashboard): **Phone** tab → enter number → **Search** → **View** on the match → scrape **name**, **phone numbers**, **emails** → Excel.
# 
# ### Chrome profile (logged-in account)
# 
# Selenium must use the **same Chrome user data** where you are already signed in.
# 
# **Important:** The Chrome **card title** (*Abubakar*, *abubakar*) is **not** always the folder name (folders are `Default`, `Profile 1`, …). Two cards *Abubakar* vs *abubakar* are different profiles — use **`CHROME_PROFILE_EXACT_DISPLAY_NAME = "Abubakar"`** or **`CHROME_PROFILE_EMAIL`** so the script picks the right one. Run **`print_chrome_profiles()`** to see folder ↔ name.
# 
# **Three ways to run Chrome:**
# 
# 1. **Real profile (default)** — `USE_ISOLATED_CHROME_USER_DATA = False`. Selenium uses your normal `User Data` + **`CHROME_PROFILE_EXACT_DISPLAY_NAME` / email** so you get **Abubakar** with ICM already logged in. You must **quit every Chrome window** first (Task Manager → end all `chrome.exe`) or you get `SessionNotCreated`.
# 
# 2. **`USE_REMOTE_DEBUGGING = True`** — you start Chrome manually with `--remote-debugging-port=9222` and your profile; the notebook **attaches**. Use this if you cannot close normal Chrome.
# 
# 3. **`USE_ISOLATED_CHROME_USER_DATA = True`** — a **separate** folder under `D:\\automation\\...`. It is **never** your Abubakar profile; ICM will always show login until you sign in once in that automation browser only.
# 
# **`SessionNotCreatedException`:** Real profile + Chrome still open → close all Chrome, or switch to remote debugging (2). If **`FALLBACK_SPAWN_CHROME_WITH_DEBUG_PORT = True`** (default), the notebook will try launching **Chrome.exe** with **`--remote-debugging-port`** and attach — that only works if **no** other `chrome.exe` is holding the same User Data folder.

# In[1]:


from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import List, Optional
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import pandas as pd
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.keys import Keys


# In[3]:


# ===== CONFIG =====
_PROJECT_ROOT = Path(__file__).resolve().parent
INPUT_XLSX = str(_PROJECT_ROOT / "file" / "dummy.xlsx")
INPUT_PHONE_COLUMN = "phone"
OUTPUT_XLSX = str(_PROJECT_ROOT / "instantcheckmate_output.xlsx")

START_URL = "https://app.instantcheckmate.com/dashboard"

WAIT_SEC = 40
HEADLESS = False  # must be False when using a logged-in Chrome profile
PAUSE_BETWEEN_NUMBERS = 2.0
MAX_NUMBERS = 2  # None = all

# Owner History modal often mounts *after* the report shell (tabs / sidebar). Do not treat
# "Personal" / "Email" on the page as "ready" until this modal-wait window has passed.
OWNER_HISTORY_APPEAR_WAIT_SEC = 22.0
# On each opened report, open the Locations tab and capture address-like lines.
SCRAPE_LOCATIONS_TAB = True
# Owner History modal: close it and use the main report "Possible Owners" carousel (tabs + 1/N pager).
# More reliable than reopening the modal between owners. Falls back to in-modal "View" loop if needed.
OWNER_HISTORY_DISMISS_THEN_INLINE_CAROUSEL = True

# Chrome profile (Instant Checkmate must already be logged in there).
# Option A: internal folder from print_chrome_profiles() or chrome://version (e.g. "Default", "Profile 1").
# Leave "" to auto-pick via email/display name below, then fall back to Default if the folder exists.
CHROME_PROFILE_DIRECTORY = ""
# Option B: auto-pick — use EXACT Chrome card name OR email (avoids wrong "abubakar" vs "Abubakar").
CHROME_PROFILE_EXACT_DISPLAY_NAME = ""  # Chrome UI name, case-sensitive; "" to skip
CHROME_PROFILE_EMAIL = ""  # e.g. your Google account on this machine; "" = skip email match
CHROME_PROFILE_NAME_CONTAINS = ""  # substring fallback only; leave "" if using name/email above
USE_CHROME_PROFILE = True
# Default Chrome user-data root: macOS / Windows / Linux (override if your install is non-standard).
_HOME = Path.home()
if sys.platform == "darwin":
    _CHROME_USER_DATA_DEFAULT = str(_HOME / "Library/Application Support/Google/Chrome")
    _CLONED_CHROME_DEFAULT = str(_HOME / "Library/Caches/icm_selenium_chrome_clone")
    _REMOTE_DEBUG_FB_DEFAULT = str(_HOME / "Library/Caches/chrome-debug-fallback")
elif sys.platform.startswith("win") or os.name == "nt":
    _la = os.environ.get("LOCALAPPDATA", "")
    _CHROME_USER_DATA_DEFAULT = (
        os.path.join(_la, "Google", "Chrome", "User Data")
        if _la
        else r"C:\Users\Admin\AppData\Local\Google\Chrome\User Data"
    )
    _CLONED_CHROME_DEFAULT = r"C:\temp\abubakar-selenium-user-data"
    _REMOTE_DEBUG_FB_DEFAULT = r"C:\temp\chrome-debug-test"
else:
    _CHROME_USER_DATA_DEFAULT = str(_HOME / ".config/google-chrome")
    _CLONED_CHROME_DEFAULT = str(_HOME / ".cache/icm_selenium_chrome_clone")
    _REMOTE_DEBUG_FB_DEFAULT = str(_HOME / ".cache/chrome-debug-fallback")

CHROME_USER_DATA_DIR = _CHROME_USER_DATA_DEFAULT

# --- Option 1: clone your real profile to a selenium-safe folder ---
# This avoids profile-lock issues because Selenium uses the CLONED folder, not your daily Chrome folder.
# If the clone keeps your ICM session, it will behave like your "Abubakar" profile without needing remote debugging.
USE_CLONED_CHROME_PROFILE = True
CLONED_CHROME_USER_DATA_DIR = _CLONED_CHROME_DEFAULT
# "" = same resolved folder as CHROME_PROFILE_DIRECTORY / email / Default fallback
CLONED_PROFILE_DIRECTORY = ""
# If True: refresh clone on every run (only works when ALL Chrome is closed).
REFRESH_CLONE_EACH_RUN = False

# --- Optional: isolated folder (only if SessionNotCreated and you cannot close Chrome) ---
# When True, you do NOT get Abubakar / saved ICM — separate empty profile.
# True = separate folder (NOT Abubakar; always looks like empty/guest Chrome). False = real profile below.
USE_ISOLATED_CHROME_USER_DATA = False
ISOLATED_CHROME_USER_DATA_DIR = str(_PROJECT_ROOT / "chrome_icm_selenium_profile")

# Attach to Chrome YOU started manually (real profile + ICM already logged in). See markdown above.
USE_REMOTE_DEBUGGING = False
REMOTE_DEBUGGING_ADDRESS = "127.0.0.1:9222"
# If port is down, auto-start Chrome: tries real CHROME_USER_DATA_DIR first, then FALLBACK path (often unlocks DevTools).
AUTO_START_CHROME_FOR_REMOTE_DEBUGGING = True
# Folder that already binds :9222 on your machine when real User Data cannot (log in to ICM once here).
CHROME_REMOTE_DEBUG_FALLBACK_USER_DATA = _REMOTE_DEBUG_FB_DEFAULT
CHROME_REMOTE_DEBUG_FALLBACK_PROFILE_DIRECTORY = "Default"
# Short wait on real profile (often locked — then script tries fallback).
CHROME_REMOTE_DEBUG_PRIMARY_WAIT_SEC = 12.0

# ===== ICM Login (auto) =====
# If the site redirects to /login, automation can sign in using these credentials.
# NOTE: These are sensitive. Prefer setting them as environment variables:
#   ICM_LOGIN_EMAIL, ICM_LOGIN_PASSWORD
ICM_LOGIN_EMAIL = os.environ.get("ICM_LOGIN_EMAIL") or ""
ICM_LOGIN_PASSWORD = os.environ.get("ICM_LOGIN_PASSWORD") or ""
USE_AUTO_LOGIN_ON_ICM_LOGIN_PAGE = True
# Admin verification may take time. After login submit, we keep waiting until dashboard becomes available.
WAIT_FOR_ICM_ADMIN_VERIFICATION_SEC = 6 * 60 * 60  # 6 hours
USE_AUTO_SEND_VERIFICATION_EMAIL = True
# If True: after SessionNotCreated on normal start, spawn chrome.exe with --remote-debugging-port then attach (needs ALL Chrome closed).
FALLBACK_SPAWN_CHROME_WITH_DEBUG_PORT = True
DEBUG_PORT_WAIT_SEC = 90
# If True (Windows): skip 90s wait when tasklist shows chrome.exe — fallback cannot bind debug port.
ABORT_CHROME_FALLBACK_WHEN_CHROME_IN_TASKLIST = True
CHROME_BINARY_PATH = ""  # optional full path to chrome.exe if not found automatically
# Last-resort: if real-profile startup and debug-port fallback fail, use isolated profile automatically.
AUTO_USE_ISOLATED_PROFILE_ON_CHROME_START_FAILURE = False
# If Chrome flashes then SessionNotCreated: profile lock / race — retry before failing.
CHROME_START_ATTEMPTS = 3
CHROME_START_RETRY_SLEEP_SEC = 4.0


def print_chrome_profiles(user_data_dir: str = None) -> None:
    """Print Chrome internal profile folder -> display name (pick one for CONFIG)."""
    base = Path(user_data_dir or CHROME_USER_DATA_DIR)
    ls = base / "Local State"
    if not ls.exists():
        print("Not found:", ls)
        return
    data = json.loads(ls.read_text(encoding="utf-8"))
    cache = data.get("profile", {}).get("info_cache", {})
    for key in sorted(cache.keys(), key=lambda x: (x != "Default", x)):
        meta = cache[key]
        disp = meta.get("name") or ""
        user = meta.get("user_name") or meta.get("username") or ""
        print(f"  {key:14}  name={disp!r}  user_name={user!r}")


def _read_profile_cache(user_data_dir: str) -> dict:
    ls = Path(user_data_dir) / "Local State"
    if not ls.exists():
        return {}
    data = json.loads(ls.read_text(encoding="utf-8"))
    return data.get("profile", {}).get("info_cache", {}) or {}


def _resolve_chrome_profile_dir(user_data_dir: str, contains: str) -> Optional[str]:
    if not contains or not str(contains).strip():
        return None
    cache = _read_profile_cache(user_data_dir)
    needle = str(contains).strip().lower()
    for dir_key, meta in cache.items():
        blob = json.dumps(meta).lower()
        disp = (meta.get("name") or "").lower()
        if needle in disp or needle in blob:
            return dir_key
    return None


def _profile_identity_text(meta: dict) -> str:
    """Strings Chrome uses for account / display (for email + name matching)."""
    parts: List[str] = []
    for k in ("user_name", "username", "name", "gaia_name", "given_name"):
        v = meta.get(k)
        if isinstance(v, str) and v.strip():
            parts.append(v.strip().lower())
    for v in meta.values():
        if isinstance(v, str) and "@" in v:
            parts.append(v.strip().lower())
    return " | ".join(parts)


def _profile_matches_email(meta: dict, email: str) -> bool:
    blob = _profile_identity_text(meta)
    if email in blob:
        return True
    return email in json.dumps(meta).lower()


def _effective_chrome_profile_directory() -> str:
    explicit = (CHROME_PROFILE_DIRECTORY or "").strip()
    if explicit:
        return explicit
    cache = _read_profile_cache(CHROME_USER_DATA_DIR)
    if not cache:
        return ""
    exact = (CHROME_PROFILE_EXACT_DISPLAY_NAME or "").strip()
    email = (CHROME_PROFILE_EMAIL or "").strip().lower()
    # Prefer email first — display "name" in Local State often differs from the card (e.g. "Abubakar" vs "Abubakar Mahmood").
    if email:
        hits = [k for k, m in cache.items() if _profile_matches_email(m, email)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            non_def = [h for h in hits if h.lower() != "default"]
            if len(non_def) == 1:
                return non_def[0]
            if exact:
                exl = exact.lower()
                by_name = [
                    h
                    for h in hits
                    if (cache[h].get("name") or "").strip().lower() == exl
                    or exl in _profile_identity_text(cache[h])
                ]
                if len(by_name) == 1:
                    return by_name[0]
            raise RuntimeError(
                f"Email {email!r} matches multiple Chrome profiles {hits!r}. "
                "Set CHROME_PROFILE_DIRECTORY to the correct folder from print_chrome_profiles()."
            )
    if exact:
        sens = [k for k, m in cache.items() if (m.get("name") or "").strip() == exact]
        if len(sens) == 1:
            return sens[0]
        if len(sens) > 1:
            raise RuntimeError(
                f"Display name {exact!r} matches multiple folders {sens!r}. Set CHROME_PROFILE_DIRECTORY."
            )
        ins = [
            k
            for k, m in cache.items()
            if (m.get("name") or "").strip().lower() == exact.lower()
        ]
        if len(ins) == 1:
            return ins[0]
        if len(ins) > 1:
            raise RuntimeError(
                f"Same display name (case-insensitive) on multiple profiles {ins!r} — "
                "Chrome cards 'Abubakar' vs 'abubakar' can be different. Set CHROME_PROFILE_DIRECTORY or CHROME_PROFILE_EMAIL."
            )
    sub = (CHROME_PROFILE_NAME_CONTAINS or "").strip()
    if sub:
        hit = _resolve_chrome_profile_dir(CHROME_USER_DATA_DIR, sub)
        if hit:
            return hit
    return ""

# If not using profile: seconds to sign in manually before first search (0 = skip)
LOGIN_PAUSE_SEC = 0
# After opening dashboard URL, wait this long for you to finish ICM login if redirected to /login
WAIT_FOR_ICM_LOGIN_SEC = 180


def clean_phone(v: object) -> str:
    """Normalize to 10 digits. Excel/pandas often loads phones as float (e.g. 7189379391.0);
    str() becomes '7189379391.0' which would otherwise produce a bogus 11-digit string."""
    if v is None:
        return ""
    try:
        if pd.isna(v):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    s = str(v).strip()
    if re.fullmatch(r"\d+\.0+", s):
        s = s.split(".", 1)[0]
    digits = re.sub(r"\D", "", s)
    if len(digits) >= 10:
        return digits[-10:]
    return digits


def format_phone_display(d10: str) -> str:
    if len(d10) == 10:
        return f"({d10[:3]}) {d10[3:6]}-{d10[6:]}"
    return d10


# In[4]:


@dataclass
class ResultRow:
    source_phone: str
    record_index: int = 1
    owner_from_results: Optional[str] = None  # Possible owners line before View
    report_name: Optional[str] = None
    phone_numbers: Optional[str] = None  # | separated
    emails: Optional[str] = None  # | separated
    locations: Optional[str] = None  # | separated (from Locations tab when enabled)
    page_url: Optional[str] = None
    status: str = "ok"
    error: Optional[str] = None


# In[5]:


import subprocess
import socket
import urllib.request
import shutil


def _find_chrome_executable() -> str:
    p = (globals().get("CHROME_BINARY_PATH") or "").strip()
    if p and os.path.isfile(p):
        return p
    if sys.platform == "darwin":
        mac_candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            str(Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        ]
        for c in mac_candidates:
            if os.path.isfile(c):
                return c
        raise FileNotFoundError(
            "Google Chrome.app not found — install Chrome or set CHROME_BINARY_PATH in CONFIG"
        )
    pf = os.environ.get("ProgramFiles", r"C:\\Program Files")
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\\Program Files (x86)")
    local = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        os.path.join(pf, "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(pf86, "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(local, "Google", "Chrome", "Application", "chrome.exe") if local else "",
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    raise FileNotFoundError(
        "chrome.exe not found — install Chrome or set CHROME_BINARY_PATH in CONFIG"
    )


def _pick_free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _chrome_seems_running() -> bool:
    """True if a normal Chrome session is likely still open (blocks profile clone on all OSes)."""
    if os.name == "nt" or sys.platform.startswith("win"):
        return _windows_chrome_task_count() > 0
    if sys.platform == "darwin":
        try:
            r = subprocess.run(
                ["pgrep", "-x", "Google Chrome"],
                capture_output=True,
                timeout=10,
            )
            return r.returncode == 0
        except Exception:
            return False
    try:
        r = subprocess.run(
            ["pgrep", "-c", "-f", r"[/]chrome(|\s)"],
            capture_output=True,
            timeout=10,
        )
        return r.returncode == 0 and int((r.stdout or b"0").decode().strip() or "0") > 0
    except Exception:
        return False


def _windows_chrome_task_count() -> int:
    """How many chrome.exe lines tasklist reports (0 = none)."""
    if os.name != "nt":
        return 0
    try:
        cr = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        r = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq chrome.exe", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=25,
            creationflags=cr,
        )
        out = (r.stdout or "").strip()
        if not out:
            return 0
        low = out.lower()
        if "no tasks" in low:
            return 0
        return len([ln for ln in out.splitlines() if ln.strip()])
    except Exception:
        return 0


def _list_chrome_profile_folders(user_data_dir: str) -> list[str]:
    """Profile subfolders that exist under User Data (Default, Profile 1, …)."""
    root = Path(user_data_dir)
    if not root.is_dir():
        return []
    names: list[str] = []
    for p in sorted(root.iterdir()):
        if not p.is_dir():
            continue
        if p.name in ("System Profile", "Guest Profile", "Crash Reports", "GrShaderCache"):
            continue
        if (p / "Preferences").is_file() or (p / "Cookies").exists() or (p / "Login Data").exists():
            names.append(p.name)
    return names


def _resolve_source_profile_name() -> str:
    """
    Pick a Chrome profile folder that exists on disk.
    Order: _effective_chrome_profile_directory → CLONED/CHROME_PROFILE_DIRECTORY → Default.
    """
    root = Path(CHROME_USER_DATA_DIR)
    if not root.is_dir():
        raise RuntimeError(f"Chrome User Data not found: {root}")

    eff = ""
    if USE_CHROME_PROFILE:
        eff = (_effective_chrome_profile_directory() or "").strip()
    if eff and (root / eff).is_dir():
        return eff

    for candidate in (
        (globals().get("CLONED_PROFILE_DIRECTORY") or "").strip(),
        (CHROME_PROFILE_DIRECTORY or "").strip(),
    ):
        if candidate and (root / candidate).is_dir():
            if eff and candidate != eff:
                print(
                    f"[ICM] Configured profile {candidate!r} exists; "
                    f"email/name resolved {eff!r} but that folder is missing."
                )
            return candidate

    if (root / "Default").is_dir():
        if eff or (CHROME_PROFILE_DIRECTORY or "").strip():
            print(
                "[ICM] Resolved/configured profile folder missing; "
                "using Chrome 'Default' profile."
            )
        return "Default"

    available = _list_chrome_profile_folders(str(root))
    cache = _read_profile_cache(str(root))
    hint_lines = []
    for folder in available[:12]:
        meta = cache.get(folder, {})
        hint_lines.append(
            f"  {folder!r}  name={meta.get('name')!r}  user={meta.get('user_name')!r}"
        )
    raise RuntimeError(
        f"No Chrome profile folder found under {root}. "
        f"Set CHROME_PROFILE_DIRECTORY to one of: {available!r}\n"
        "Run: python3 -c \"import second_site_automation as m; m.print_chrome_profiles()\"\n"
        + ("\n".join(hint_lines) if hint_lines else "")
    )


def _clone_chrome_profile_if_needed() -> tuple[str, str]:
    """
    Copy CHROME_USER_DATA_DIR + profile folder into CLONED_CHROME_USER_DATA_DIR.

    Returns (user_data_dir, profile_directory) that Selenium should use.
    """
    if not bool(globals().get("USE_CLONED_CHROME_PROFILE", False)):
        prof = _resolve_source_profile_name() if USE_CHROME_PROFILE else (
            (CHROME_PROFILE_DIRECTORY or "").strip() or "Default"
        )
        return (CHROME_USER_DATA_DIR, prof)

    src_root = Path(CHROME_USER_DATA_DIR)
    src_profile = _resolve_source_profile_name()
    # Where we store the cloned profile. On Windows, users often set an explicit temp path.
    # On Linux/macOS, default to a folder inside this repo so it's writable & predictable.
    _cfg_dst = (globals().get("CLONED_CHROME_USER_DATA_DIR") or "").strip()
    if _cfg_dst:
        dst_root = Path(_cfg_dst)
    else:
        dst_root = Path(__file__).resolve().parent / ".cloned_chrome_user_data"

    if not src_root.exists():
        raise RuntimeError(f"Chrome User Data not found: {src_root}")

    refresh = bool(globals().get("REFRESH_CLONE_EACH_RUN", False))
    dst_profile_dir = dst_root / src_profile
    dst_local_state = dst_root / "Local State"
    dst_first_run = dst_root / "First Run"

    print("[ICM] Clone source profile:", src_profile)
    print("[ICM] Clone target root:", str(dst_root))

    # If we already have a cloned profile and we're not refreshing, we can reuse it even if
    # normal Chrome is currently open (no file copying needed).
    if not refresh and dst_profile_dir.exists() and dst_local_state.exists():
        print("[ICM] Using existing CLONED Chrome profile (no refresh).")
        print("      dst:", str(dst_profile_dir))
        return (str(dst_root), src_profile)

    # Must be fully closed to copy reliably (Cookies, Network, etc).
    if _chrome_seems_running():
        # Help the user understand where the clone will be created.
        try:
            print("[ICM] Clone target root:", str(dst_root))
            print("[ICM] Clone target profile:", str(dst_profile_dir))
        except Exception:
            pass
        raise RuntimeError(
            "Chrome is still running. Quit ALL Chrome windows before cloning the profile "
            "(Linux: close Chrome AND run `pkill -f chrome` or `pkill -f chromium`, then re-run; "
            "Windows: Task Manager → chrome.exe; macOS: Cmd+Q Chrome). "
            "After cloning once, you can reopen Chrome; Selenium will use the cloned folder."
        )

    if refresh and dst_root.exists():
        shutil.rmtree(dst_root, ignore_errors=True)

    dst_root.mkdir(parents=True, exist_ok=True)

    # Copy Local State (contains profile map + encrypted cookie keys). Refresh it whenever we
    # are (re)creating a profile subfolder so switching CHROME_PROFILE_DIRECTORY (e.g. Profile 8
    # → Profile 1) does not leave a stale Local State from an older clone.
    src_local_state = src_root / "Local State"
    if src_local_state.exists() and (
        refresh
        or not dst_local_state.exists()
        or not dst_profile_dir.exists()
    ):
        shutil.copy2(src_local_state, dst_local_state)

    # Copy the profile directory (Default / Profile N)
    src_profile_dir = src_root / src_profile
    if not src_profile_dir.exists():
        raise RuntimeError(f"Chrome profile folder not found: {src_profile_dir}")

    if refresh or not dst_profile_dir.exists():
        # Copy only the important state; skip heavy caches.
        skip_names = {
            "Cache",
            "Code Cache",
            "GPUCache",
            "GrShaderCache",
            "ShaderCache",
            "Service Worker",
            "Storage",
            "OptimizationGuidePredictionModels",
            "DawnCache",
            "Crashpad",
            "Crash Reports",
        }

        def _ignore(dir_path: str, names: list[str]):
            base = os.path.basename(dir_path)
            if base in skip_names:
                return set(names)
            return {n for n in names if n in skip_names}

        if dst_profile_dir.exists():
            shutil.rmtree(dst_profile_dir, ignore_errors=True)
        shutil.copytree(src_profile_dir, dst_profile_dir, ignore=_ignore, dirs_exist_ok=False)

    # Avoid Chrome first-run screens as much as possible.
    if not dst_first_run.exists():
        try:
            dst_first_run.write_text("", encoding="utf-8")
        except Exception:
            pass

    print("[ICM] Using CLONED Chrome profile (selenium-safe):")
    print("      src:", str(src_root / src_profile))
    print("      dst:", str(dst_profile_dir))
    return (str(dst_root), src_profile)


def _wait_chrome_debugger_json(port: int, timeout_sec: float) -> str:
    """Poll DevTools /json/version; return host for debuggerAddress (127.0.0.1 or localhost)."""
    hosts = ("127.0.0.1", "localhost")
    end = time.time() + timeout_sec
    last_err: Optional[BaseException] = None
    while time.time() < end:
        for host in hosts:
            url = f"http://{host}:{port}/json/version"
            try:
                with urllib.request.urlopen(url, timeout=2.0) as resp:
                    if resp.status == 200:
                        return host
            except Exception as e:
                last_err = e
                continue
        time.sleep(0.35)
    raise TimeoutError(
        f"no response on port {port} from {hosts} within {timeout_sec}s. "
        "End every chrome.exe in Task Manager (same User Data = no debug port). "
        f"Last error: {last_err!r}"
    )


def _parse_host_port(addr: str) -> tuple[str, int]:
    s = (addr or "").strip()
    if not s:
        raise ValueError("Remote debugging address is empty")
    if ":" not in s:
        return s, 9222
    host, port_s = s.rsplit(":", 1)
    return (host.strip() or "127.0.0.1"), int(port_s.strip())


def _spawn_chrome_remote_debug(
    user_data_dir: str, profile_directory: str, port: int
) -> subprocess.Popen:
    exe = _find_chrome_executable()
    args = [
        exe,
        f"--remote-debugging-port={port}",
        "--remote-debugging-address=127.0.0.1",
        "--remote-allow-origins=*",
        f"--user-data-dir={user_data_dir}",
        f"--profile-directory={profile_directory}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    cflags = 0
    start = (globals().get("START_URL") or "").strip()
    if start:
        args.append(start)
    return subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        close_fds=False,
        creationflags=cflags,
    )


def _driver_prepare_for_icm_navigation(driver) -> None:
    """Session restore + debug attach can leave many tabs; drive the one that should load ICM."""
    needle = "instantcheckmate"
    try:
        handles = list(driver.window_handles)
    except Exception:
        return
    if not handles:
        return
    for h in handles:
        try:
            driver.switch_to.window(h)
            cur = (driver.current_url or "").lower()
            if needle in cur:
                return
        except Exception:
            continue
    try:
        driver.switch_to.window(handles[-1])
    except Exception:
        try:
            driver.switch_to.window(handles[0])
        except Exception:
            pass


def _drv_finalize_session(driver) -> None:
    """Right after WebDriver connects: focus tab + open dashboard if still on NTP / blank."""
    _driver_prepare_for_icm_navigation(driver)
    start = (globals().get("START_URL") or "").strip()
    if not start:
        return
    try:
        if "instantcheckmate.com" in (driver.current_url or "").lower():
            return
    except Exception:
        pass
    try:
        print("[ICM] Opening dashboard in the automation-focused tab...")
        driver.get(start)
    except Exception as e:
        print("[ICM] Note: initial dashboard load:", type(e).__name__, e)


def build_driver(headless: bool = False):
    options = Options()
    if headless and not globals().get("USE_REMOTE_DEBUGGING", False):
        options.add_argument("--headless=new")
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--remote-allow-origins=*")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    try:
        options.page_load_strategy = "eager"
    except Exception:
        pass
    if USE_REMOTE_DEBUGGING:
        addr = (REMOTE_DEBUGGING_ADDRESS or "127.0.0.1:9222").strip()
        host, port = _parse_host_port(addr)
        if bool(globals().get("AUTO_START_CHROME_FOR_REMOTE_DEBUGGING", True)):
            try:
                _wait_chrome_debugger_json(port, 1.5)
            except Exception:
                prof_main = (
                    _effective_chrome_profile_directory()
                    if USE_CHROME_PROFILE
                    else (CHROME_PROFILE_DIRECTORY or "Default").strip()
                )
                if not prof_main:
                    prof_main = "Default"
                primary_wait = float(
                    globals().get("CHROME_REMOTE_DEBUG_PRIMARY_WAIT_SEC", 12.0)
                )
                full_wait = float(globals().get("DEBUG_PORT_WAIT_SEC", 90))
                fb_dir = (
                    globals().get("CHROME_REMOTE_DEBUG_FALLBACK_USER_DATA") or ""
                ).strip()
                fb_prof = (
                    globals().get("CHROME_REMOTE_DEBUG_FALLBACK_PROFILE_DIRECTORY")
                    or "Default"
                ).strip()
                chain: list[tuple[str, str, float]] = []
                if CHROME_USER_DATA_DIR:
                    chain.append((CHROME_USER_DATA_DIR, prof_main, primary_wait))
                if fb_dir:
                    chain.append((fb_dir, fb_prof or "Default", full_wait))
                if not chain:
                    raise RuntimeError(
                        "AUTO_START_CHROME_FOR_REMOTE_DEBUGGING needs CHROME_USER_DATA_DIR "
                        "or CHROME_REMOTE_DEBUG_FALLBACK_USER_DATA."
                    )
                proc: Optional[subprocess.Popen] = None
                started = False
                for ud, pd, wait_sec in chain:
                    print(
                        f"[ICM] Remote debug {host}:{port} not up. "
                        f"Starting Chrome -> user-data-dir={ud}  profile={pd!r} ..."
                    )
                    try:
                        if proc is not None:
                            try:
                                proc.terminate()
                            except Exception:
                                pass
                            proc = None
                        proc = _spawn_chrome_remote_debug(ud, pd, port)
                        live_host = _wait_chrome_debugger_json(port, wait_sec)
                        addr = f"{live_host}:{port}"
                        started = True
                        break
                    except Exception as e:
                        print(f"[ICM] That user-data-dir did not open debug port in time: {e!r}")
                        continue
                if not started:
                    raise RuntimeError(
                        "Could not start Chrome with remote debugging on port "
                        f"{port}. Real User Data may be locked; use temp folder "
                        f"{fb_dir!r} or start Chrome manually with --remote-debugging-port."
                    )
        options.add_experimental_option("debuggerAddress", addr)
        print("[ICM] Attaching to existing Chrome (remote debugging):", addr)
        try:
            drv = webdriver.Chrome(options=options)
            pl = min(int(globals().get("WAIT_SEC", 40)), 90)
            drv.set_page_load_timeout(pl)
            drv.set_script_timeout(45)
            print("[ICM] Attached; page load timeout:", pl, "s")
            _drv_finalize_session(drv)
            return drv
        except Exception as e:
            raise RuntimeError(
                "Could not attach. Start Chrome FIRST with remote debugging, e.g.\n"
                '  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" '
                '--remote-debugging-port=9222 '
                '--user-data-dir="C:\\Users\\Admin\\AppData\\Local\\Google\\Chrome\\User Data" '
                '--profile-directory="Profile 1"\n'
                "(use your real Profile N from print_chrome_profiles). Then set USE_REMOTE_DEBUGGING = True.\n"
                f"Error: {type(e).__name__}: {e}"
            ) from e
    # If enabled, clone the real profile into a selenium-safe folder (requires Chrome closed once).
    if bool(globals().get("USE_CLONED_CHROME_PROFILE", False)):
        ud, pd = _clone_chrome_profile_if_needed()
        # Override for this run (do not mutate global config).
        clone_user_data_dir = ud
        clone_profile_dir = pd
    else:
        clone_user_data_dir = ""
        clone_profile_dir = ""

    prof = _effective_chrome_profile_directory() if USE_CHROME_PROFILE else ""
    print(
        "[ICM] Driver config:",
        "USE_CLONED_CHROME_PROFILE=",
        bool(globals().get("USE_CLONED_CHROME_PROFILE", False)),
        "USE_CHROME_PROFILE=",
        USE_CHROME_PROFILE,
        "effective_prof=",
        prof or "(empty)",
    )
    if bool(globals().get("USE_CLONED_CHROME_PROFILE", False)):
        print("[ICM] Driver will use cloned user-data-dir:", clone_user_data_dir or "(empty)")
        print("[ICM] Driver will use cloned profile-directory:", clone_profile_dir or "(empty)")
    if USE_ISOLATED_CHROME_USER_DATA:
        Path(ISOLATED_CHROME_USER_DATA_DIR).mkdir(parents=True, exist_ok=True)
        options.add_argument(f"--user-data-dir={ISOLATED_CHROME_USER_DATA_DIR}")
        print("[ICM] Using isolated user-data-dir (no lock with your daily Chrome):")
        print("     ", ISOLATED_CHROME_USER_DATA_DIR)
    elif bool(globals().get("USE_CLONED_CHROME_PROFILE", False)) and clone_user_data_dir and clone_profile_dir:
        options.add_argument(f"--user-data-dir={clone_user_data_dir}")
        options.add_argument(f"--profile-directory={clone_profile_dir}")
        print("Using CLONED Chrome profile directory:", clone_profile_dir)
    elif USE_CHROME_PROFILE and CHROME_USER_DATA_DIR and prof:
        options.add_argument(f"--user-data-dir={CHROME_USER_DATA_DIR}")
        options.add_argument(f"--profile-directory={prof}")
        print("Using Chrome profile directory:", prof)
    elif USE_CHROME_PROFILE:
        raise RuntimeError(
            "Set CHROME_PROFILE_DIRECTORY, or CHROME_PROFILE_EXACT_DISPLAY_NAME / CHROME_PROFILE_EMAIL, then run print_chrome_profiles()"
        )
    attempts = max(1, int(globals().get("CHROME_START_ATTEMPTS", 3)))
    pause = float(globals().get("CHROME_START_RETRY_SLEEP_SEC", 4.0))
    last: Optional[BaseException] = None
    for attempt in range(1, attempts + 1):
        try:
            drv = webdriver.Chrome(options=options)
            pl = min(int(globals().get("WAIT_SEC", 40)), 90)
            drv.set_page_load_timeout(pl)
            drv.set_script_timeout(45)
            print("[ICM] Chrome session OK; page load timeout:", pl, "s")
            _drv_finalize_session(drv)
            return drv
        except WebDriverException as e:
            last = e
            msg = str(e).lower()
            retryable = "session not created" in msg or "chrome instance exited" in msg
            if retryable and attempt < attempts:
                print(
                    f"[ICM] Chrome start failed ({type(e).__name__}), retry {attempt}/{attempts} in {pause}s..."
                )
                time.sleep(pause)
                continue
            break
        except Exception as e:
            last = e
            break
    assert last is not None
    sn = str(last).lower()
    want_fb = (
        bool(globals().get("FALLBACK_SPAWN_CHROME_WITH_DEBUG_PORT", True))
        and USE_CHROME_PROFILE
        and not USE_ISOLATED_CHROME_USER_DATA
        and CHROME_USER_DATA_DIR
        and prof
        and not USE_REMOTE_DEBUGGING
        and ("session not created" in sn or "chrome instance exited" in sn)
    )
    if want_fb:
        print(
            "[ICM] Fallback: launching Chrome.exe with --remote-debugging-port (then Selenium attaches). "
            "Task Manager -> end ALL chrome.exe first, or this often times out."
        )
        port = _pick_free_tcp_port()
        if bool(globals().get("ABORT_CHROME_FALLBACK_WHEN_CHROME_IN_TASKLIST", True)):
            nchr = _windows_chrome_task_count()
            if nchr > 0:
                exe = _find_chrome_executable()
                raise RuntimeError(
                    f"tasklist shows {nchr} chrome.exe process line(s). While ANY Chrome is running, "
                    "this profile's User Data is locked — the spawned Chrome cannot open remote debugging on port "
                    f"{port} (you still see ICM in a window, but Selenium never attaches, so the Phone tab is never clicked).\n"
                    "Fix: Task Manager -> end ALL chrome.exe (and 'Google Chrome' background), wait 10s, rerun.\n"
                    "OR start Chrome yourself with debugging, then set USE_REMOTE_DEBUGGING = True:\n"
                    f'  "{exe}" --remote-debugging-port=9222 '
                    f'--user-data-dir="{CHROME_USER_DATA_DIR}" --profile-directory="{prof}"\n'
                    'Then REMOTE_DEBUGGING_ADDRESS = "127.0.0.1:9222". '
                    "Or set ABORT_CHROME_FALLBACK_WHEN_CHROME_IN_TASKLIST = False to retry the long wait."
                )
        proc: Optional[subprocess.Popen] = None
        dbg_host = "127.0.0.1"
        try:
            proc = _spawn_chrome_remote_debug(CHROME_USER_DATA_DIR, prof, port)
            wait_dbg = float(globals().get("DEBUG_PORT_WAIT_SEC", 90))
            dbg_host = _wait_chrome_debugger_json(port, wait_dbg)
        except Exception as fb_err:
            if proc is not None:
                try:
                    proc.terminate()
                except Exception:
                    pass
            if bool(globals().get("AUTO_USE_ISOLATED_PROFILE_ON_CHROME_START_FAILURE", True)):
                print(
                    "[ICM] Real profile failed (DevToolsActivePort/profile lock). "
                    "Auto-fallback: trying isolated Chrome profile..."
                )
                try:
                    Path(ISOLATED_CHROME_USER_DATA_DIR).mkdir(parents=True, exist_ok=True)
                    iso = Options()
                    if headless:
                        iso.add_argument("--headless=new")
                    iso.add_argument("--start-maximized")
                    iso.add_argument("--disable-blink-features=AutomationControlled")
                    iso.add_argument("--remote-allow-origins=*")
                    iso.add_argument("--no-first-run")
                    iso.add_argument("--no-default-browser-check")
                    iso.add_argument(f"--user-data-dir={ISOLATED_CHROME_USER_DATA_DIR}")
                    try:
                        iso.page_load_strategy = "eager"
                    except Exception:
                        pass
                    drv = webdriver.Chrome(options=iso)
                    pl = min(int(globals().get("WAIT_SEC", 40)), 90)
                    drv.set_page_load_timeout(pl)
                    drv.set_script_timeout(45)
                    print(
                        "[ICM] Isolated profile started at:",
                        ISOLATED_CHROME_USER_DATA_DIR,
                    )
                    print(
                        "[ICM] Sign in to ICM once in this automation window if prompted; "
                        "this isolated profile is reused next runs."
                    )
                    _drv_finalize_session(drv)
                    return drv
                except Exception as iso_err:
                    raise RuntimeError(
                        "Chrome fallback (debug port) failed, and isolated profile fallback also failed. "
                        "Close every chrome.exe/chromedriver.exe, rerun, or use USE_REMOTE_DEBUGGING=True. "
                        f"Original: {type(last).__name__}: {last}. "
                        f"Debug fallback: {type(fb_err).__name__}: {fb_err}. "
                        f"Isolated fallback: {type(iso_err).__name__}: {iso_err}"
                    ) from iso_err
            raise RuntimeError(
                "Chrome fallback (debug port) failed. Close every chrome.exe and chromedriver.exe, wait 5s, rerun. "
                "Or set USE_REMOTE_DEBUGGING = True and start Chrome manually with your profile + --remote-debugging-port. "
                f"Original: {type(last).__name__}: {last}. Fallback: {type(fb_err).__name__}: {fb_err}"
            ) from fb_err
        att = Options()
        att.add_experimental_option("debuggerAddress", f"{dbg_host}:{port}")
        if headless:
            att.add_argument("--headless=new")
        att.add_argument("--start-maximized")
        att.add_argument("--disable-blink-features=AutomationControlled")
        try:
            att.page_load_strategy = "eager"
        except Exception:
            pass
        try:
            drv = webdriver.Chrome(options=att)
            pl = min(int(globals().get("WAIT_SEC", 40)), 90)
            drv.set_page_load_timeout(pl)
            drv.set_script_timeout(45)
            print(
                "[ICM] Attached to Chrome we spawned on 127.0.0.1:%s (leave this window open for the run)."
                % port,
            )
            print("[ICM] Chrome session OK; page load timeout:", pl, "s")
            _drv_finalize_session(drv)
            return drv
        except Exception as e2:
            raise RuntimeError(
                "Spawned Chrome with debug port but Selenium could not attach. "
                f"{type(e2).__name__}: {e2}"
            ) from e2
    raise RuntimeError(
        "Chrome failed to start — no WebDriver session, so the script never navigates to ICM (New Tab only). "
        "Fix: Task Manager -> end every chrome.exe and chromedriver.exe, wait a few seconds, rerun. "
        "Or set USE_REMOTE_DEBUGGING = True and attach to Chrome you launch manually with --remote-debugging-port. "
        f"Details: {type(last).__name__}: {last}"
    ) from last


def wait_clickable(driver, by, value, timeout=WAIT_SEC):
    return WebDriverWait(driver, timeout).until(EC.element_to_be_clickable((by, value)))


def js_click(driver, el):
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", el)
    driver.execute_script("arguments[0].click();", el)


def safe_text(el) -> str:
    try:
        return (el.text or "").strip()
    except Exception:
        return ""


def load_phone_values(path: str, phone_col: str) -> list:
    target = phone_col.strip().lower()
    df = pd.read_excel(path)
    normalized = {str(c).strip().lower(): c for c in df.columns}
    if target in normalized:
        return df[normalized[target]].tolist()
    raw = pd.read_excel(path, header=None)
    for r in range(min(30, len(raw.index))):
        for c in range(len(raw.columns)):
            val = raw.iat[r, c]
            if str(val).strip().lower() == target:
                return raw.iloc[r + 1 :, c].tolist()
    raise ValueError(f"Column '{phone_col}' not found. Columns: {list(df.columns)}")


# ### Flow implementation (Instant Checkmate)

# In[6]:


def dismiss_overlays_if_any(driver):
    for xp in [
        "//button[contains(.,'Accept')]",
        "//button[contains(.,'Got it')]",
        "//button[contains(.,'Agree')]",
        "//button[contains(.,'Maybe later')]",
        "//button[contains(.,'No thanks')]",
        "//button[contains(.,'Not now')]",
        "//*[@aria-label='Close' or @aria-label='Dismiss']",
        "//button[(contains(@aria-label,'Close') or contains(@aria-label,'close'))][ancestor::*[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'dark web')]]",
        "//*[contains(.,'View dark web report')]/ancestor::*[.//button][1]//button[not(contains(.,'View'))]",
    ]:
        for el in driver.find_elements(By.XPATH, xp):
            try:
                if el.is_displayed():
                    js_click(driver, el)
                    time.sleep(0.3)
            except Exception:
                pass
    try:
        b = driver.find_element(By.TAG_NAME, "body")
        for _ in range(2):
            b.send_keys(Keys.ESCAPE)
            time.sleep(0.15)
    except Exception:
        pass


def _icm_is_login_page(driver) -> bool:
    try:
        u = (driver.current_url or "").lower()
        if "/login" in u:
            return True
    except Exception:
        pass
    # Fallback heuristic: look for an email input on page.
    try:
        return len(driver.find_elements(By.CSS_SELECTOR, "input[type='email'], input[name*='email' i]")) > 0
    except Exception:
        return False


def _icm_try_login(driver) -> bool:
    """
    Attempt to submit ICM login form.
    Returns True if it found inputs and submitted.
    """
    if not USE_AUTO_LOGIN_ON_ICM_LOGIN_PAGE:
        return False

    email = (ICM_LOGIN_EMAIL or "").strip()
    password = (ICM_LOGIN_PASSWORD or "").strip()
    if not email or not password:
        print("[ICM] Auto-login disabled: ICM_LOGIN_EMAIL / ICM_LOGIN_PASSWORD are empty.")
        return False

    # Locate email/password fields (use multiple selectors to be robust).
    email_selectors = [
        (By.CSS_SELECTOR, "input[type='email']"),
        (By.CSS_SELECTOR, "input[name='email']"),
        (By.CSS_SELECTOR, "input[id*='email' i]"),
        (By.XPATH, "//input[contains(translate(@name,'EMAIL','email'),'email')]"),
        (By.XPATH, "//input[contains(translate(@id,'EMAIL','email'),'email')]"),
        (By.XPATH, "//input[contains(translate(@placeholder,'EMAIL','email'),'email')]"),
        (By.XPATH, "//label[contains(translate(.,'EMAIL','email'),'email')]/following::input[1]"),
    ]
    password_selectors = [
        (By.CSS_SELECTOR, "input[type='password']"),
        (By.XPATH, "//input[contains(translate(@name,'PASSWORD','password'),'password')]"),
        (By.XPATH, "//input[contains(translate(@id,'PASSWORD','password'),'password')]"),
    ]
    submit_selectors = [
        (By.XPATH, "//button[contains(.,'Sign in') or contains(.,'Sign In') or contains(.,'Log in') or contains(.,'Login') or contains(.,'Continue')]"),
        (By.XPATH, "//input[@type='submit' and (contains(@value,'Sign') or contains(@value,'Login') or contains(@value,'Continue'))]"),
        (By.CSS_SELECTOR, "button[type='submit']"),
    ]

    def _find_first(candidates):
        for by, sel in candidates:
            els = driver.find_elements(by, sel)
            for el in els:
                try:
                    if el.is_displayed():
                        return el
                except Exception:
                    continue
        return None

    inp_email = _find_first(email_selectors)
    inp_pass = _find_first(password_selectors)
    if inp_email is None or inp_pass is None:
        try:
            WebDriverWait(driver, 12).until(
                lambda d: _find_first(email_selectors) is not None and _find_first(password_selectors) is not None
            )
            inp_email = _find_first(email_selectors)
            inp_pass = _find_first(password_selectors)
        except TimeoutException:
            pass
    if inp_email is None or inp_pass is None:
        print("[ICM] Login page open but email/password fields were not found.")
        return False

    print("[ICM] Login page detected; submitting credentials...")
    inp_email.click()
    inp_email.clear()
    inp_email.send_keys(email)
    time.sleep(0.2)
    inp_pass.click()
    inp_pass.clear()
    inp_pass.send_keys(password)
    time.sleep(0.2)

    # Click submit.
    for by, sel in submit_selectors:
        for btn in driver.find_elements(by, sel):
            try:
                if btn.is_displayed():
                    js_click(driver, btn)
                    time.sleep(1.0)
                    return True
            except Exception:
                continue

    # If no submit button matched, attempt ENTER on password field.
    try:
        inp_pass.send_keys(Keys.ENTER)
        time.sleep(1.0)
        return True
    except Exception:
        return True


def _icm_is_verification_page(driver) -> bool:
    try:
        u = (driver.current_url or "").lower()
        if "/verification" in u:
            return True
    except Exception:
        pass
    # Fallback heuristic: look for the button label
    try:
        return (
            len(
                driver.find_elements(
                    By.XPATH,
                    "//button[contains(.,'Send Verification Email') or contains(.,'Send verification')]",
                )
            )
            > 0
        )
    except Exception:
        return False


def _icm_try_send_verification_email(driver) -> bool:
    if not USE_AUTO_SEND_VERIFICATION_EMAIL:
        return False
    btn_xps = [
        "//button[contains(.,'Send Verification Email')]",
        "//button[contains(.,'Send verification')]",
        "//button[contains(.,'verification email')]",
        "//input[@type='submit' and (contains(@value,'Verification') or contains(@value,'verification'))]",
    ]
    for xp in btn_xps:
        for el in driver.find_elements(By.XPATH, xp):
            try:
                if el.is_displayed():
                    print("[ICM] Verification page detected; clicking 'Send Verification Email'...")
                    js_click(driver, el)
                    time.sleep(2.0)
                    return True
            except Exception:
                continue
    return False


def _icm_dashboard_search_ui_present(driver) -> bool:
    """True when ICM dashboard search UI is visible (Phone or Name tab / search form)."""
    try:
        if driver.find_elements(
            By.XPATH,
            "//li[@role='tab' and contains(@aria-label,'Phone')] | //button[contains(.,'Phone')] | //*[@role='tab' and contains(.,'Phone')]",
        ):
            return True
        if driver.find_elements(
            By.CSS_SELECTOR,
            "li[role='tab'][aria-label='Name search tab']",
        ):
            return True
        if driver.find_elements(By.XPATH, "//label[contains(.,'First Name')]/following::input[1]"):
            return True
        if driver.find_elements(By.XPATH, "//*[contains(.,'Start Your Search')]"):
            return True
    except Exception:
        pass
    return False


def _wait_icm_dashboard_ready(driver, timeout_sec: int = None) -> None:
    """Wait for real dashboard search UI (Phone / Name tab / Start Your Search).

    If admin verification is pending, ICM may keep you out of the dashboard; we keep polling until UI appears or timeout.
    On /login we retry auto-login instead of only sleeping.
    """
    sec = int(timeout_sec if timeout_sec is not None else globals().get("WAIT_FOR_ICM_LOGIN_SEC", 180))
    print(f"[ICM] Waiting up to {sec}s for dashboard search UI (Phone / Name tab)...")
    end = time.time() + sec
    last_login_attempt = 0.0
    while time.time() < end:
        if _icm_dashboard_search_ui_present(driver):
            print("[ICM] Dashboard search UI found.")
            return
        try:
            u = (driver.current_url or "").lower()
        except Exception:
            time.sleep(1)
            continue
        if "/login" in u or _icm_is_login_page(driver):
            if time.time() - last_login_attempt > 8.0:
                print("[ICM] Still on /login; retrying auto-login...")
                _icm_try_login(driver)
                last_login_attempt = time.time()
            time.sleep(1.5)
            continue
        if _icm_is_verification_page(driver):
            _icm_try_send_verification_email(driver)
            time.sleep(2.0)
            continue
        time.sleep(1)
    raise TimeoutException(
        "Instant Checkmate never showed the dashboard search box. "
        "Log in on /login in this window, or fix Chrome profile (run print_chrome_profiles — avoid wrong Default)."
    )


def ensure_icm_session(driver) -> None:
    """Open dashboard and handle login/verification until search UI is ready."""
    try:
        if _icm_dashboard_search_ui_present(driver):
            dismiss_overlays_if_any(driver)
            return
    except Exception:
        pass
    step_open_site(driver)


def step_open_site(driver):
    _driver_prepare_for_icm_navigation(driver)
    print("[ICM] Navigating to", START_URL)
    try:
        driver.get(START_URL)
    except TimeoutException:
        print("[ICM] Page load timed out; stopping network and continuing.")
        try:
            driver.execute_script("window.stop();")
        except Exception:
            pass
    dismiss_overlays_if_any(driver)
    if LOGIN_PAUSE_SEC and not USE_CHROME_PROFILE:
        print(f"[ICM] Sign in if needed; waiting {LOGIN_PAUSE_SEC}s...")
        time.sleep(LOGIN_PAUSE_SEC)
    WebDriverWait(driver, min(WAIT_SEC, 15)).until(lambda d: d.find_elements(By.TAG_NAME, "body"))
    time.sleep(1.0)
    cur = driver.current_url
    print("[ICM] Current URL:", cur)
    if "profile-picker" in cur or cur.startswith("chrome://"):
        print(
            "[ICM] Still on Chrome profile/system page — close Chrome, uncheck 'Show on startup', pick profile once, then rerun."
        )
    # Handle Instant Checkmate auth/verification flow:
    # - /login (email+password)
    # - /verification?redirect=/dashboard (device verification -> send email)
    if _icm_is_login_page(driver):
        print("[ICM] Detected /login; attempting auto-login...")
        did = _icm_try_login(driver)
        admin_timeout = int(globals().get("WAIT_FOR_ICM_ADMIN_VERIFICATION_SEC", 6 * 60 * 60))
        if did:
            # After password submit the page may redirect to /verification first.
            time.sleep(1.5)
        _wait_icm_dashboard_ready(driver, timeout_sec=admin_timeout)
    elif _icm_is_verification_page(driver):
        print("[ICM] Detected /verification; attempting to send verification email...")
        _icm_try_send_verification_email(driver)
        admin_timeout = int(globals().get("WAIT_FOR_ICM_ADMIN_VERIFICATION_SEC", 6 * 60 * 60))
        _wait_icm_dashboard_ready(driver, timeout_sec=admin_timeout)
    else:
        _wait_icm_dashboard_ready(driver)
    dismiss_overlays_if_any(driver)
    print("[ICM] Dashboard step done; next: Phone tab + search.")


def _icm_phone_tab_active(driver) -> bool:
    try:
        for el in driver.find_elements(
            By.CSS_SELECTOR,
            "li[role='tab'][aria-label='Phone search tab'][aria-selected='true']",
        ):
            if el.is_displayed():
                return True
    except Exception:
        pass
    try:
        for inp in driver.find_elements(By.CSS_SELECTOR, "input[type='tel']"):
            if inp.is_displayed():
                return True
    except Exception:
        pass
    return False


def _click_phone_tab(driver):
    """ICM: <li role='tab' aria-label='Phone search tab'>; verify aria-selected / tel input after click."""
    print("[ICM] Looking for Phone tab...")
    dismiss_overlays_if_any(driver)
    deadline = time.time() + float(min(WAIT_SEC, 55))
    last: Optional[BaseException] = None
    while time.time() < deadline:
        if _icm_phone_tab_active(driver):
            print("[ICM] Phone tab already active (or phone field visible).")
            return
        dismiss_overlays_if_any(driver)
        try:
            b = driver.find_element(By.TAG_NAME, "body")
            b.send_keys(Keys.ESCAPE)
            time.sleep(0.1)
        except Exception:
            pass
        try:
            li = WebDriverWait(driver, 8).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "li[role='tab'][aria-label='Phone search tab']")
                )
            )
            driver.execute_script(
                "var e=arguments[0]; e.scrollIntoView({block:'center'});"
                "e.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true,view:window}));",
                li,
            )
            time.sleep(0.35)
            try:
                li.click()
            except Exception:
                js_click(driver, li)
            time.sleep(0.55)
            if _icm_phone_tab_active(driver):
                print("[ICM] Phone tab activated.")
                return
        except Exception as e:
            last = e
        try:
            for el in driver.find_elements(
                By.XPATH,
                "//ul[.//li[@aria-label='Phone search tab']]//span[normalize-space()='Phone']",
            ):
                if el.is_displayed():
                    driver.execute_script(
                        "arguments[0].scrollIntoView({block:'center'});", el
                    )
                    js_click(driver, el)
                    time.sleep(0.55)
                    if _icm_phone_tab_active(driver):
                        print("[ICM] Phone tab activated (label span).")
                        return
        except Exception as e:
            last = e
        try:
            ok = driver.execute_script(
                "var e=document.querySelector(\"li[role='tab'][aria-label='Phone search tab']\");"
                "if(!e)return false; e.scrollIntoView({block:'center'}); e.click(); return true;"
            )
            if ok:
                time.sleep(0.55)
                if _icm_phone_tab_active(driver):
                    print("[ICM] Phone tab activated (querySelector).")
                    return
        except Exception as e:
            last = e
        time.sleep(0.4)
    print("[ICM] Phone tab never became active. Title:", driver.title)
    raise TimeoutException(f"Phone tab did not activate: {last}")


def _find_phone_input(driver):
    for by, sel in [
        (By.CSS_SELECTOR, "input[type='tel']"),
        (By.XPATH, "//input[contains(@placeholder,'555')]"),
        (By.XPATH, "//label[contains(.,'Phone Number')]/following::input[1]"),
        (By.XPATH, "//*[contains(.,'Phone Number')]/following::input[1]"),
    ]:
        els = driver.find_elements(by, sel)
        for el in els:
            if el.is_displayed():
                return el
    raise TimeoutException("Phone number input not found")


def _click_search_button(driver):
    xps = [
        "//button[normalize-space()='Search']",
        "//button[contains(.,'Search')]",
        "//input[@type='submit' and contains(@value,'Search')]",
        "//button[contains(@class,'search') and contains(.,'Search')]",
    ]
    for xp in xps:
        for el in driver.find_elements(By.XPATH, xp):
            try:
                if el.is_displayed() and safe_text(el).lower().count("search") >= 1:
                    js_click(driver, el)
                    return
            except Exception:
                pass
    raise TimeoutException("Search button not found")


def step_enter_phone_and_search(driver, phone: str):
    _click_phone_tab(driver)
    print("[ICM] Typing phone", phone)
    inp = _find_phone_input(driver)
    inp.click()
    inp.clear()
    text = format_phone_display(phone)
    inp.send_keys(text)
    time.sleep(0.3)
    print("[ICM] Clicking Search...")
    _click_search_button(driver)
    time.sleep(1.5)
    print("[ICM] Search submitted. URL:", driver.current_url)


def _possible_owners_snippet(driver) -> Optional[str]:
    # Search-results page has a "Possible Owners" label; grab the nearby name, not the whole page.
    xps = [
        # Label then nearest following text node
        "//*[normalize-space()='Possible Owners']/following::*[normalize-space(.)!=''][1]",
        "//*[contains(normalize-space(.),'Possible Owners')]/following::*[normalize-space(.)!=''][1]",
    ]
    for xp in xps:
        for el in driver.find_elements(By.XPATH, xp)[:10]:
            t = safe_text(el)
            if t:
                t = re.sub(r"\s+", " ", t).strip()
                return t[:120]
    return None


def _main_text(driver) -> str:
    """Prefer main content text (avoids sidebar/header noise)."""
    try:
        mains = driver.find_elements(By.TAG_NAME, "main")
        for m in mains:
            if m.is_displayed():
                t = (m.text or "").strip()
                if t:
                    return t
    except Exception:
        pass
    try:
        body = driver.find_element(By.TAG_NAME, "body")
        return (body.text or "").strip()
    except Exception:
        return ""


def _click_view_for_current_search(driver, digits10: str):
    print("[ICM] Waiting for results / View...")
    WebDriverWait(driver, WAIT_SEC).until(
        lambda d: len(d.find_elements(By.XPATH, "//a[contains(.,'View')]")) > 0
        or len(d.find_elements(By.XPATH, "//button[contains(.,'View')]")) > 0
        or len(d.find_elements(By.XPATH, "//*[contains(.,'Possible Owners')]")) > 0
    )
    time.sleep(0.8)
    owners_line = _possible_owners_snippet(driver)
    candidates = driver.find_elements(
        By.XPATH,
        "//a[contains(.,'View')] | //button[contains(.,'View')]",
    )
    clicked = False
    for el in candidates:
        try:
            if not el.is_displayed():
                continue
            lab = safe_text(el)
            if not lab:
                continue
            low = lab.lower()
            if "review" in low or "overview" in low:
                continue
            if "view" not in low and "->" not in lab:
                continue
            js_click(driver, el)
            clicked = True
            break
        except Exception:
            continue
    if not clicked and candidates:
        for el in candidates:
            if el.is_displayed():
                js_click(driver, el)
                clicked = True
                break
    if not clicked:
        raise TimeoutException("View button not found after search")
    time.sleep(1.5)
    print("[ICM] View clicked. Report URL:", driver.current_url)
    return owners_line


def _extract_emails_from_page(driver) -> List[str]:
    body = _main_text(driver)
    found = re.findall(r"[A-Za-z0-9_.+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", body)
    out, seen = [], set()
    for e in found:
        el = e.lower()
        if el in seen or "instantcheckmate" in el or "example.com" in el:
            continue
        seen.add(el)
        out.append(e)
    return out


def _extract_phones_from_page(driver) -> List[str]:
    body = _main_text(driver)
    pat = re.compile(r"\(?\d{3}\)?[\s.-]*\d{3}[\s.-]*\d{4}")
    raw = pat.findall(body)
    norm = []
    seen = set()
    # Known ICM support number seen in header/sidebar
    block = {"8775643003"}
    for p in raw:
        d = re.sub(r"\D", "", p)
        if len(d) == 11 and d.startswith("1"):
            d = d[1:]
        if len(d) != 10:
            continue
        if d in block:
            continue
        if d in seen:
            continue
        seen.add(d)
        norm.append(format_phone_display(d))
    return norm


_ICM_NAME_FALSE_POSITIVES = frozenset(
    {
        "personal",
        "name",
        "contact",
        "contact information",
        "email addresses",
        "phone numbers",
        "locations",
        "location history",
        "address history",
        "current address",
        "social media",
        "jobs & businesses",
        "user comments",
    }
)


def _looks_like_icm_section_header(t: str) -> bool:
    low = re.sub(r"\s+", " ", (t or "").strip().lower())
    if low in _ICM_NAME_FALSE_POSITIVES:
        return True
    return any(
        x in low
        for x in (
            "information",
            "addresses",
            "comments",
            "education",
            "dark web",
            "download",
            "location history",
        )
    )


def _looks_like_concatenated_names(t: str, max_words: int = 5) -> bool:
    """ICM sometimes surfaces multiple owner names in one string; not a single person name."""
    parts = re.sub(r"\s+", " ", (t or "").strip()).split()
    return len(parts) > max_words


def _extract_report_name(driver) -> Optional[str]:
    xps = [
        "//main//*[normalize-space()='Name']/following::*[string-length(normalize-space(.))>2][1]",
        "//*[contains(@class,'name') or contains(@class,'Name')]",
        "//main//h1",
        "//main//h2",
        "//h1",
        "//h2",
        "//*[normalize-space()='Name']/following::*[string-length(normalize-space(.))>5][1]",
    ]
    for xp in xps:
        for el in driver.find_elements(By.XPATH, xp)[:40]:
            t = safe_text(el)
            if not t or len(t) > 80:
                continue
            if not re.match(r"^[A-Za-z][A-Za-z\s,'.-]+$", t):
                continue
            if " " not in t:
                continue
            tl = re.sub(r"\s+", " ", t.strip().lower())
            if tl in _ICM_NAME_FALSE_POSITIVES or _looks_like_icm_section_header(t):
                continue
            if _looks_like_concatenated_names(t):
                continue
            return t
    return None


def _wait_phone_report_url(driver, timeout: Optional[float] = None) -> None:
    WebDriverWait(driver, float(timeout if timeout is not None else WAIT_SEC)).until(
        lambda d: "/dashboard/reports/" in (d.current_url or "").lower()
    )


def _poll_for_owner_history_modal(driver, seconds: float) -> bool:
    """Return True if the Owner History modal is visible within the time window."""
    deadline = time.time() + max(0.0, float(seconds))
    while time.time() < deadline:
        if _owner_history_popup_present(driver):
            return True
        time.sleep(0.35)
    return bool(_owner_history_popup_present(driver))


def _owner_history_modal_ready_again(driver, phone: str, report_url: str) -> bool:
    """
    After one owner's View, ICM often leaves the full report open without the Owner History modal.
    Reload alone usually does not bring it back; use View All or search -> View again.
    """
    if _owner_history_popup_present(driver):
        return True

    def poll(sec: float) -> bool:
        return _poll_for_owner_history_modal(driver, sec)

    try:
        driver.get(report_url)
        time.sleep(0.9)
        try:
            _wait_phone_report_url(driver)
        except TimeoutException:
            pass
        dismiss_overlays_if_any(driver)
        if poll(5.0):
            return True
    except Exception as e:
        print(f"[ICM] Reopen modal (reload report): {e}")

    dismiss_overlays_if_any(driver)
    for xp in (
        "//a[contains(translate(.,'VIEWALL','viewall'),'view all')]",
        "//button[contains(translate(.,'VIEWALL','viewall'),'view all')]",
        "//*[contains(.,'Possible Owners')]//a[contains(.,'View All')]",
        "//*[contains(.,'Possible Owners')]//button[contains(.,'View All')]",
    ):
        for el in driver.find_elements(By.XPATH, xp):
            try:
                if not el.is_displayed():
                    continue
                if "view all" not in safe_text(el).lower():
                    continue
                js_click(driver, el)
                time.sleep(1.4)
                dismiss_overlays_if_any(driver)
                if poll(14.0):
                    print("[ICM] Owner History reopened via View All")
                    return True
            except Exception:
                continue

    search_url = f"https://app.instantcheckmate.com/dashboard/search?phone={phone}"
    try:
        print("[ICM] Reopening Owner History via search results -> View...")
        driver.get(search_url)
        time.sleep(1.5)
        dismiss_overlays_if_any(driver)
        _click_view_for_current_search(driver, phone)
        _wait_phone_report_url(driver)
        dismiss_overlays_if_any(driver)
        if poll(float(OWNER_HISTORY_APPEAR_WAIT_SEC)):
            print("[ICM] Owner History reopened via search -> View")
            return True
    except Exception as e:
        print(f"[ICM] Reopen modal via search failed: {e}")
    return False


def _click_report_nav_tab(driver, *labels: str) -> bool:
    """Click a report sub-nav tab (Phone Numbers, Email Addresses, etc.)."""
    dismiss_overlays_if_any(driver)
    for label in labels:
        if not label:
            continue
        xps = [
            f"//main//nav//button[normalize-space()='{label}']",
            f"//main//button[normalize-space()='{label}']",
            f"//main//a[normalize-space()='{label}']",
            f"//*[@role='tab' and normalize-space()='{label}']",
            f"//*[@role='tab' and contains(normalize-space(.),'{label}')]",
            f"//nav//button[contains(normalize-space(.),'{label}')]",
        ]
        for xp in xps:
            for el in driver.find_elements(By.XPATH, xp):
                try:
                    if not el.is_displayed():
                        continue
                    js_click(driver, el)
                    time.sleep(0.85)
                    return True
                except Exception:
                    continue
    return False


def _dedupe_phones_list(phones: List[str]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    block = {"8775643003"}
    for p in phones:
        d = re.sub(r"\D", "", p or "")
        if len(d) == 11 and d.startswith("1"):
            d = d[1:]
        if len(d) != 10 or d in block or d in seen:
            continue
        seen.add(d)
        out.append(format_phone_display(d))
    return out


def _dedupe_emails_list(emails: List[str]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for e in emails:
        el = (e or "").strip().lower()
        if not el or el in seen or "instantcheckmate" in el or "example.com" in el:
            continue
        seen.add(el)
        out.append(e.strip())
    return out


def _scroll_to_report_heading(driver, heading_fragment: str) -> None:
    """Scroll ICM report subsection into view (e.g. h2 'phone numbers')."""
    frag = heading_fragment.lower()
    for h in driver.find_elements(
        By.XPATH,
        f"//*[self::h1 or self::h2][contains(translate(.,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'{frag}')]",
    ):
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", h)
            time.sleep(0.45)
            return
        except Exception:
            continue


def _phone_digits_from_text(t: str) -> Optional[str]:
    pat = re.compile(r"\(?\d{3}\)?[\s.-]*\d{3}[\s.-]*\d{4}")
    m = pat.search(t or "")
    if not m:
        return None
    d = re.sub(r"\D", "", m.group(0))
    if len(d) == 11 and d.startswith("1"):
        d = d[1:]
    return d if len(d) == 10 else None


def _extract_phones_from_icm_subsection(driver) -> List[str]:
    """
    ICM report cards: div.phonesSubsectionItem with (914) 636-3563 in p._text_d3dxc_1._medium_...
    and /dashboard/search?phone=9146363563 links.
    """
    out: List[str] = []
    seen: set[str] = set()
    block = {"8775643003"}

    def _add_digits(d: str) -> None:
        if len(d) == 11 and d.startswith("1"):
            d = d[1:]
        if len(d) != 10 or d in block or d in seen:
            return
        seen.add(d)
        out.append(format_phone_display(d))

    _scroll_to_report_heading(driver, "phone")
    dismiss_overlays_if_any(driver)

    for a in driver.find_elements(By.XPATH, "//a[contains(@href,'phone=')]"):
        try:
            href = a.get_attribute("href") or ""
            q = parse_qs(urlparse(href).query)
            raw = (q.get("phone") or [""])[0]
            d = re.sub(r"\D", "", unquote(str(raw)))
            if d:
                _add_digits(d)
        except Exception:
            continue

    card_xps = (
        "//div[contains(@class,'phonesSubsectionItem')]",
        "//div[contains(@class,'_phonesSubsectionItem')]",
    )
    for xp in card_xps:
        for card in driver.find_elements(By.XPATH, xp):
            for p in card.find_elements(
                By.XPATH,
                ".//p[contains(@class,'d3dxc') or contains(@class,'_medium_')]",
            ):
                d = _phone_digits_from_text(safe_text(p))
                if d:
                    _add_digits(d)

    subsection_xp = (
        "//div[contains(@class,'recordSubsection')]"
        "[.//*[self::h2][contains(translate(.,'PHONE','phone'),'phone')]]"
        "//p[contains(@class,'medium') or contains(@class,'d3dxc')]"
    )
    for p in driver.find_elements(By.XPATH, subsection_xp):
        d = _phone_digits_from_text(safe_text(p))
        if d:
            _add_digits(d)

    if out:
        print(f"[ICM] Phone subsection: {len(out)} number(s)")
    return out


def _extract_emails_from_icm_subsection(driver) -> List[str]:
    """ICM email cards: div.emailsSubsectionItem with email in clipboard <p> and ?email= links."""
    out: List[str] = []
    seen: set[str] = set()
    email_re = re.compile(r"[A-Za-z0-9_.+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

    def _add_email(addr: str) -> None:
        el = (addr or "").strip().lower()
        if not el or el in seen or "instantcheckmate" in el or "example.com" in el:
            return
        seen.add(el)
        out.append(addr.strip())

    _scroll_to_report_heading(driver, "email")
    dismiss_overlays_if_any(driver)

    for a in driver.find_elements(By.XPATH, "//a[contains(@href,'email=')]"):
        try:
            href = a.get_attribute("href") or ""
            q = parse_qs(urlparse(href).query)
            raw = unquote((q.get("email") or [""])[0]).replace("+", " ")
            if raw and email_re.fullmatch(raw.strip()):
                _add_email(raw.strip())
        except Exception:
            continue

    for xp in (
        "//div[contains(@class,'emailsSubsectionItem')]",
        "//div[contains(@class,'_emailsSubsectionItem')]",
    ):
        for card in driver.find_elements(By.XPATH, xp):
            for p in card.find_elements(
                By.XPATH,
                ".//p[contains(@class,'d3dxc') or contains(@class,'_medium_')]",
            ):
                t = safe_text(p)
                for m in email_re.findall(t or ""):
                    _add_email(m)

    if out:
        print(f"[ICM] Email subsection: {len(out)} address(es)")
    return out


def _collect_phones_and_emails_from_report(driver) -> tuple[List[str], List[str]]:
    """ICM Personal report: phone/email subsection cards, then page fallback + nav tabs."""
    phones: List[str] = []
    emails: List[str] = []
    _click_personal_tab(driver)
    time.sleep(0.8)
    dismiss_overlays_if_any(driver)

    phones.extend(_extract_phones_from_icm_subsection(driver))
    emails.extend(_extract_emails_from_icm_subsection(driver))

    phones.extend(_extract_phones_from_page(driver))
    emails.extend(_extract_emails_from_page(driver))

    if _click_report_nav_tab(driver, "Phone Numbers", "Phones", "Phone"):
        time.sleep(0.6)
        phones.extend(_extract_phones_from_icm_subsection(driver))
        phones.extend(_extract_phones_from_page(driver))
    if _click_report_nav_tab(driver, "Email Addresses", "Emails", "Email"):
        time.sleep(0.6)
        emails.extend(_extract_emails_from_icm_subsection(driver))
        emails.extend(_extract_emails_from_page(driver))

    _click_personal_tab(driver)
    phones = _dedupe_phones_list(phones)
    emails = _dedupe_emails_list(emails)
    if phones or emails:
        print(f"[ICM] Contact scrape total: {len(phones)} phone(s), {len(emails)} email(s)")
    return phones, emails


def _click_personal_tab(driver) -> bool:
    """Report sub-nav: ensure Personal is active before reading name/contact from <main>."""
    dismiss_overlays_if_any(driver)
    xps = [
        "//nav//button[normalize-space()='Personal']",
        "//button[normalize-space()='Personal']",
        "//a[normalize-space()='Personal']",
        "//*[@role='tab' and normalize-space()='Personal']",
        "//header//button[contains(normalize-space(.),'Personal')]",
    ]
    for xp in xps:
        for el in driver.find_elements(By.XPATH, xp):
            try:
                if not el.is_displayed():
                    continue
                lab = safe_text(el).strip().lower()
                if lab != "personal":
                    continue
                js_click(driver, el)
                time.sleep(0.85)
                return True
            except Exception:
                continue
    return False


def _tab_label_blob(el) -> str:
    """Visible label + aria-label + innerText (ICM often puts the word in a child span)."""
    parts: List[str] = []
    try:
        parts.append((el.text or "").strip())
        parts.append((el.get_attribute("aria-label") or "").strip())
        parts.append((el.get_attribute("title") or "").strip())
        if not parts[0]:
            parts.append((el.get_attribute("innerText") or "").strip())
    except Exception:
        pass
    return " ".join(p for p in parts if p).lower()


def _is_locations_tab_label(blob: str) -> bool:
    """True for 'Location' / 'Locations' tabs; false for carrier / unrelated matches."""
    b = (blob or "").lower()
    if "carrier location" in b or "current carrier" in b:
        return False
    return "location" in b


def _click_locations_tab(driver) -> bool:
    if not bool(globals().get("SCRAPE_LOCATIONS_TAB", True)):
        return False
    dismiss_overlays_if_any(driver)
    # ICM may use 'Location' (singular); label text may live only on a child node.
    xps = [
        "//main//nav//button[normalize-space()='Locations']",
        "//main//nav//button[normalize-space()='Location']",
        "//main//button[normalize-space()='Locations']",
        "//main//button[normalize-space()='Location']",
        "//main//*[@role='tab' and contains(normalize-space(.),'Locations')]",
        "//main//*[@role='tab' and contains(normalize-space(.),'Location')]",
        "//nav//button[normalize-space()='Locations']",
        "//nav//button[normalize-space()='Location']",
        "//button[normalize-space()='Locations']",
        "//button[normalize-space()='Location']",
        "//a[normalize-space()='Locations']",
        "//a[normalize-space()='Location']",
        "//*[@role='tab' and contains(normalize-space(.),'Locations')]",
        "//*[@role='tab' and contains(normalize-space(.),'Location')]",
        "//*[@role='tab' and contains(translate(@aria-label,'LOCATION','location'),'location')]",
        "//header//button[contains(normalize-space(.),'Locations')]",
        "//header//button[contains(normalize-space(.),'Location')]",
        "//*[contains(@class,'tab')][contains(normalize-space(.),'Locations')]",
        "//*[contains(@class,'tab')][contains(normalize-space(.),'Location')]",
        "//*[@role='tablist']//*[@role='tab'][contains(translate(normalize-space(.),'LOCATION','location'),'location')]",
        "//div[contains(@class,'tab')]//*[self::button or self::a][contains(translate(normalize-space(.),'LOCATION','location'),'location')]",
    ]

    def try_click_elements(elements, require_visible: bool) -> bool:
        seen_id = set()
        for el in elements:
            try:
                eid = id(el)
                if eid in seen_id:
                    continue
                seen_id.add(eid)
                if require_visible and not el.is_displayed():
                    continue
                blob = _tab_label_blob(el)
                if not _is_locations_tab_label(blob):
                    continue
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                time.sleep(0.25)
                js_click(driver, el)
                time.sleep(1.1)
                return True
            except Exception:
                continue
        return False

    candidates: List = []
    for xp in xps:
        try:
            candidates.extend(driver.find_elements(By.XPATH, xp))
        except Exception:
            continue
    if try_click_elements(candidates, require_visible=True):
        return True
    # Overflow / horizontal tab strip: element exists but not "displayed" until scrolled.
    if try_click_elements(candidates, require_visible=False):
        return True

    # Last resort: any role=tab in report whose visible/aria text contains "location" (not carrier).
    try:
        for el in driver.find_elements(
            By.XPATH,
            "//*[@role='tab'][contains(translate(@aria-label,'LOCATION','location'),'location') or "
            "contains(translate(normalize-space(.),'LOCATION','location'),'location')]",
        ):
            blob = _tab_label_blob(el)
            if "carrier" in blob:
                continue
            if "location" not in blob:
                continue
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            time.sleep(0.25)
            js_click(driver, el)
            time.sleep(1.1)
            return True
    except Exception:
        pass

    print("[ICM] Could not find a clickable Locations tab.")
    return False


_SKIP_LOCATION_UI_LABELS = frozenset(
    {
        "address",
        "usage",
        "deliverable",
        "receiving mail",
        "last known",
        "yes",
        "no",
        "residential",
        "commercial",
        "business",
        "locations",
        "location history",
    }
)


def _line_looks_like_city_state(t: str) -> bool:
    """ICM often lists 'Brooklyn, New York' without street or ZIP."""
    s = re.sub(r"\s+", " ", (t or "").strip())
    if "," not in s or len(s) < 6 or len(s) > 80:
        return False
    sl = s.lower()
    if sl in _SKIP_LOCATION_UI_LABELS:
        return False
    left, right = [p.strip() for p in s.split(",", 1)]
    if len(left) < 2 or len(right) < 2:
        return False
    if re.search(r"\d", left):
        return False
    if re.fullmatch(r"[A-Za-z]{2}\.?", right):
        return True
    if re.search(r"[A-Za-z]{3,}", right) and not re.search(r"\d{5}", s):
        return True
    return False


def _line_looks_like_address_component(t: str) -> bool:
    s = re.sub(r"\s+", " ", (t or "").strip())
    if len(s) < 6:
        return False
    sl = s.lower()
    if sl in _SKIP_LOCATION_UI_LABELS:
        return False
    if "find location report" in sl or "leaflet" in sl:
        return False
    if _line_looks_like_city_state(s):
        return True
    if re.search(r"\b\d{5}(-\d{4})?\b", s):
        return True
    if "," in s and re.search(r"[A-Za-z]{2}\s+\d{5}", s):
        return True
    if "," in s and re.search(r",\s*[A-Z]{2}\s*\d", s, re.I):
        return True
    # Street-style: number + … + St/Ave/Rd/Ln/Dr/Way/Blvd/Ct/Pkwy/Hwy
    if re.search(r"\d", s) and re.search(
        r"\b(st|street|ave|avenue|rd|road|dr|drive|ln|lane|blvd|boulevard|ct|court|hwy|pkwy|way|pl|place|circle|cir)\b\.?$",
        sl,
        re.I,
    ):
        return True
    return False


def _paragraphs_matching_address(driver, xpath: str) -> List[str]:
    out: List[str] = []
    seen: set = set()
    try:
        for p in driver.find_elements(By.XPATH, xpath):
            t = safe_text(p)
            if not t or t in seen:
                continue
            if _line_looks_like_address_component(t):
                seen.add(t)
                out.append(t)
    except Exception:
        pass
    return out


def _lines_from_visible_text(blob: str) -> List[str]:
    out: List[str] = []
    seen: set = set()
    for raw in (blob or "").splitlines():
        s = re.sub(r"\s+", " ", raw.strip())
        if not s or s in seen:
            continue
        if _line_looks_like_address_component(s):
            seen.add(s)
            out.append(s)
    return out


def _extract_locations_from_find_location_report_hrefs(driver) -> List[str]:
    """
    Location cards include <a class='..._openReport_...' href='/dashboard/search?...street=...'>.
    ICM address <p> classes use _text_d3dxc_* (no '_text_' substring), so parsing these links is robust.
    """
    out: List[str] = []
    seen: set = set()
    xps = (
        "//section[.//*[self::h1 or self::h2][contains(.,'Location History')]]"
        "//a[contains(@href,'street=')]",
        "//*[contains(@class,'recordSection')][.//*[contains(.,'Location History')]]"
        "//a[contains(@href,'street=')]",
        "//a[contains(@class,'openReport')][contains(@href,'street=')]",
    )
    for xp in xps:
        try:
            for a in driver.find_elements(By.XPATH, xp):
                href = (a.get_attribute("href") or "").strip()
                if not href or "street=" not in href:
                    continue
                q = parse_qs(urlparse(href).query)
                st = (q.get("street") or [""])[0]
                city = (q.get("city") or [""])[0]
                state = (q.get("state") or [""])[0]
                zipc = (q.get("zip") or [""])[0]
                st = unquote(st.replace("+", " ")).strip()
                city = unquote(city.replace("+", " ")).strip()
                state = unquote(state).strip()
                zipc = unquote(zipc).strip()
                if not st and not zipc:
                    continue
                tail = f"{city}, {state} {zipc}".strip()
                combined = f"{st}, {tail}" if st else tail
                combined = re.sub(r"\s+", " ", combined).strip()
                if combined and combined not in seen:
                    seen.add(combined)
                    out.append(combined)
        except Exception:
            continue
        if out:
            break
    return out[:80]


def _extract_locations_from_location_history_section(driver) -> List[str]:
    """
    ICM renders addresses as <p class="_text_..."> under Location History (h1/h2) and in
    clipboard cards. Include recordSection and copyToClipboard blocks.
    """
    out: List[str] = []
    seen: set = set()
    roots = driver.find_elements(
        By.XPATH,
        "//section[.//*[self::h1 or self::h2][contains(.,'Location History')]] | "
        "//section[contains(@class,'recordSection')][.//*[contains(.,'Location History')]] | "
        "//*[normalize-space()='Location History']/ancestor::section[1] | "
        "//*[contains(normalize-space(.),'Location History')]/ancestor::div[contains(@class,'recordSection')][1]",
    )
    for root in roots:
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", root)
        except Exception:
            pass
        # ICM uses _text_d3dxc_* — does NOT match contains(@class,'_text_') ('_text_d' ≠ '_text_').
        xps = (
            ".//p[contains(@class,'d3dxc')]",
            ".//p[contains(@class,'text_d3dxc')]",
            ".//p[contains(@class,'_larger_')]",
            ".//p[contains(@style,'font-weight: 600')]",
            ".//div[contains(@class,'clipBoard') or contains(@class,'copyToClipboard')]//p",
            ".//p[contains(@class,'_text_')]",
        )
        for xp in xps:
            for p in root.find_elements(By.XPATH, xp):
                t = safe_text(p)
                if not t or t in seen:
                    continue
                if _line_looks_like_address_component(t):
                    seen.add(t)
                    out.append(t)
    return out[:80]


def _extract_location_lines_from_main(driver) -> List[str]:
    body = _main_text(driver)
    out: List[str] = []
    seen: set = set()
    noise_sub = (
        "instant checkmate",
        "terms of use",
        "privacy policy",
        "dark web",
        "download pdf",
        "unlock",
        "subscribe",
        "order history",
    )
    street_re = re.compile(
        r"^\d+\s+[\w\s.'-]+(?:st|street|ave|avenue|rd|road|dr|drive|ln|lane|blvd|ct|court|hwy|pkwy)\b",
        re.I,
    )
    email_re = re.compile(r"[A-Za-z0-9_.+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
    for raw in body.splitlines():
        s = re.sub(r"\s+", " ", raw.strip())
        if len(s) < 6:
            continue
        if "@" in s or email_re.search(s):
            continue
        sl = s.lower()
        if any(n in sl for n in noise_sub):
            continue
        if sl in ("locations", "location", "current address", "address history", "location history"):
            continue
        if sl in _SKIP_LOCATION_UI_LABELS:
            continue
        ok = False
        if "," in s and re.search(r"\d", s):
            ok = True
        if re.search(r"\b\d{5}(-\d{4})?\b", s):
            ok = True
        if street_re.search(s):
            ok = True
        if not ok and _line_looks_like_address_component(s):
            ok = True
        if ok and s not in seen:
            seen.add(s)
            out.append(s)
    return out[:50]


def _scrape_locations_tab(driver) -> Optional[str]:
    if not bool(globals().get("SCRAPE_LOCATIONS_TAB", True)):
        return None
    try:
        try:
            report_root = driver.find_elements(By.CSS_SELECTOR, "div#report, div[class*='_report_']")
            if report_root:
                driver.execute_script(
                    "arguments[0].scrollIntoView({block:'start'});", report_root[0]
                )
                time.sleep(0.35)
        except Exception:
            pass
        tab_ok = _click_locations_tab(driver)
        if not tab_ok:
            has_heading = bool(
                driver.find_elements(
                    By.XPATH,
                    "//*[self::h1 or self::h2][contains(normalize-space(.),'Location History')]",
                )
            )
            if not has_heading:
                return None
            print("[ICM] Locations tab not found; Location History section present — scraping anyway.")
        try:
            WebDriverWait(driver, min(18.0, float(WAIT_SEC))).until(
                lambda d: len(d.find_elements(By.XPATH, "//*[contains(.,'Location History')]")) > 0
                or len(d.find_elements(By.XPATH, "//main//p[contains(@class,'d3dxc')]")) > 0
                or len(d.find_elements(By.XPATH, "//a[contains(@href,'street=')]")) > 0
            )
        except TimeoutException:
            print("[ICM] Locations panel slow to render; continuing scrape anyway.")
        time.sleep(1.0)
        dismiss_overlays_if_any(driver)
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight / 3);")
        time.sleep(0.4)

        lines = _extract_locations_from_location_history_section(driver)
        if not lines:
            # City/state lines under Location History (no street / ZIP)
            try:
                seen_cs: set = set()
                for p in driver.find_elements(
                    By.XPATH,
                    "//*[self::h1 or self::h2][contains(.,'Location')]/ancestor::section[1]//p | "
                    "//div[@id='report']//p | //div[contains(@class,'_report_')]//p",
                ):
                    t = safe_text(p)
                    if t and _line_looks_like_city_state(t) and t not in seen_cs:
                        seen_cs.add(t)
                        lines.append(t)
            except Exception:
                pass
        if not lines:
            lines = _extract_locations_from_find_location_report_hrefs(driver)
        if not lines:
            lines = _paragraphs_matching_address(
                driver,
                "//div[contains(@class,'clipBoard') or contains(@class,'copyToClipboard')]//p",
            )
        if not lines:
            lines = _paragraphs_matching_address(driver, "//main//p[contains(@class,'d3dxc')]")
        if not lines:
            lines = _paragraphs_matching_address(driver, "//main//p[contains(@class,'_text_')]")
        if not lines:
            lines = _paragraphs_matching_address(driver, "//main//p[contains(@class,'_larger_')]")
        if not lines:
            lines = _paragraphs_matching_address(driver, "//main//p")
        if not lines:
            lines = _extract_location_lines_from_main(driver)
        if not lines:
            try:
                body_txt = driver.find_element(By.TAG_NAME, "body").text
                lines = _lines_from_visible_text(body_txt)
            except Exception:
                pass
        if not lines:
            print("[ICM] No address lines extracted after Locations tab.")
            return None
        return " | ".join(lines)
    except Exception as e:
        print(f"[ICM] Locations scrape error: {type(e).__name__}: {e}")
        return None


def _collect_current_report_row(
    driver, phone: str, record_index: int, owner_hint: Optional[str] = None
) -> ResultRow:
    _click_personal_tab(driver)
    dismiss_overlays_if_any(driver)
    name = _extract_report_name(driver)
    if name and (
        _looks_like_icm_section_header(name)
        or _looks_like_concatenated_names(name)
    ):
        name = None
    if owner_hint and (not name or _looks_like_icm_section_header(name)):
        hint = re.sub(r"\s+", " ", owner_hint.strip())
        if hint and not _looks_like_concatenated_names(hint):
            name = hint
    phones, emails = _collect_phones_and_emails_from_report(driver)
    loc = _scrape_locations_tab(driver) if SCRAPE_LOCATIONS_TAB else None
    return ResultRow(
        source_phone=phone,
        record_index=record_index,
        owner_from_results=owner_hint,
        report_name=name,
        phone_numbers=" | ".join(phones) if phones else None,
        emails=" | ".join(emails) if emails else None,
        locations=loc,
        page_url=driver.current_url,
    )


def _owner_history_modal_root(driver):
    """Visible Owner History modal (scoped so we do not use the main report pager 1/4)."""
    try:
        u = (driver.current_url or "").lower()
        if "/dashboard/reports/" not in u:
            return None
    except Exception:
        return None
    for sel in [
        "div[class*='_ownerHistoryModal_']",
        "div[class*='ownerHistoryModal']",
        "[role='dialog']",
    ]:
        try:
            for m in driver.find_elements(By.CSS_SELECTOR, sel):
                try:
                    if not m.is_displayed():
                        continue
                    blob = (m.text or "").lower()
                    if "owner history" in blob and "possible owner" in blob:
                        return m
                except Exception:
                    continue
        except Exception:
            continue
    try:
        for el in driver.find_elements(
            By.XPATH,
            "//*[contains(.,'Owner History')][.//*[contains(.,'Possible Owner')]]",
        ):
            try:
                if el.is_displayed() and _has_view_in(el):
                    return el
            except Exception:
                continue
    except Exception:
        pass
    return None


def _has_view_in(el) -> bool:
    try:
        for x in el.find_elements(By.XPATH, ".//*[contains(.,'View')]"):
            t = (safe_text(x) or "").lower()
            if "view all" in t:
                continue
            if "view" in t:
                return True
    except Exception:
        pass
    return False


def _owner_name_from_owner_card(card) -> Optional[str]:
    parts: List[str] = []
    for n in card.find_elements(By.XPATH, ".//h2 | .//h3 | .//h4 | .//h5 | .//h6"):
        t = safe_text(n)
        if not t:
            continue
        tl = t.lower()
        if "possible owner" in tl:
            continue
        if tl in ("view", "view >") or (tl.startswith("view") and len(tl) < 12):
            continue
        parts.append(t)
    if parts:
        name = re.sub(r"\s+", " ", " ".join(parts)).strip()
        return name or None
    blob = safe_text(card)
    if blob:
        for ln in blob.splitlines():
            ln = ln.strip()
            if not ln:
                continue
            ll = ln.lower()
            if "possible owner" in ll or ll.startswith("view"):
                continue
            if re.match(r"^[A-Za-z][A-Za-z\s.'-]{1,}$", ln):
                return ln
    return None


def _owner_history_page_token_from_modal(modal) -> str:
    """Pagination like '1/2' inside the Owner History modal only."""
    try:
        for s in modal.find_elements(
            By.XPATH,
            ".//div[contains(@class,'slideTracker')]//span | .//*[contains(@class,'SlideTracker')]//span",
        ):
            t = safe_text(s)
            if re.match(r"^\d+\s*/\s*\d+$", t):
                return re.sub(r"\s+", "", t)
        for s in modal.find_elements(By.TAG_NAME, "span"):
            t = safe_text(s)
            if re.match(r"^\d+\s*/\s*\d+$", t):
                return re.sub(r"\s+", "", t)
    except Exception:
        pass
    return ""


def _js_click_object_or_el(driver, el) -> None:
    """ICM uses <object> for carousel chevrons; plain WebDriver clicks often miss."""
    driver.execute_script(
        """
        var el = arguments[0];
        try { el.scrollIntoView({block:'center'}); } catch (e) {}
        try {
          if (typeof el.click === 'function') { el.click(); return; }
        } catch (e) {}
        try {
          el.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true, view:window}));
        } catch (e2) {}
        """,
        el,
    )


def _owner_history_next_page_in_modal(driver, modal) -> bool:
    """Click 'next' in the modal pager only (not the main report's Possible Owners pager).

    ICM DOM (your snapshot): slideTracker holds
    <object class='..._chevron_... _disabled_...'/> <span>1/2</span> <object class='..._chevron_...'/>
    so the next control is the object *after* the page span.
    """
    try:
        trackers = modal.find_elements(
            By.XPATH,
            ".//div[contains(@class,'_slideTracker_')] | .//div[contains(@class,'slideTracker')]",
        )
        for tr in trackers:
            if not tr.is_displayed():
                continue
            # Preferred: span "1/2" then following-sibling object (next chevron).
            for span in tr.find_elements(By.TAG_NAME, "span"):
                t = safe_text(span)
                if not re.match(r"^\d+\s*/\s*\d+$", t):
                    continue
                for nxt in span.find_elements(
                    By.XPATH,
                    "following-sibling::object[contains(@class,'chevron') or contains(@class,'_chevron_')]",
                ):
                    cls = (nxt.get_attribute("class") or "").lower()
                    if "disabled" in cls:
                        continue
                    _js_click_object_or_el(driver, nxt)
                    time.sleep(1.0)
                    return True
            # Fallback: last chevron <object> in row that is not disabled (prev is first, next is last).
            chev = tr.find_elements(
                By.XPATH,
                ".//object[contains(@class,'chevron') or contains(@class,'_chevron_')]",
            )
            for nxt in reversed(chev):
                cls = (nxt.get_attribute("class") or "").lower()
                if "disabled" in cls:
                    continue
                _js_click_object_or_el(driver, nxt)
                time.sleep(1.0)
                return True
        for xp in (
            ".//button[contains(translate(@aria-label,'NEXT','next'),'next')]",
            ".//*[@role='button'][contains(translate(@aria-label,'NEXT','next'),'next')]",
        ):
            for el in modal.find_elements(By.XPATH, xp):
                if el.is_displayed():
                    dis = (el.get_attribute("disabled") or el.get_attribute("aria-disabled") or "").lower()
                    if dis in ("true", "disabled"):
                        continue
                    js_click(driver, el)
                    time.sleep(1.0)
                    return True
    except Exception as e:
        print(f"[ICM] Owner History next page failed: {e}")
    return False


def _wait_report_after_owner_modal_view(driver) -> None:
    """After clicking View on a modal row, wait until full report is shown (modal gone)."""

    def ready(d):
        if _owner_history_modal_root(d) is not None:
            return False
        u = (d.current_url or "").lower()
        if "/dashboard/reports/" not in u:
            return False
        m = _main_text(d)
        if len(m) < 100:
            return False
        ml = m.lower()
        return any(
            x in ml
            for x in (
                "phone numbers",
                "email",
                "contact information",
                "email addresses",
                "personal",
                "carrier",
                "line type",
                "locations",
            )
        )

    WebDriverWait(driver, WAIT_SEC).until(ready)
    time.sleep(0.4)


def _owner_history_entries(driver, modal=None):
    """
    Return visible owner-history choices from popup modal:
    [(owner_name, view_button_element, item_index_on_page), ...]
    """
    entries: List[tuple] = []
    idx = 0
    if modal is None:
        modal = _owner_history_modal_root(driver)
    if modal is None:
        return entries

    cards: List = []
    try:
        cards = modal.find_elements(By.CSS_SELECTOR, "div[class*='_ownerItem_']")
    except Exception:
        cards = []
    if not cards:
        try:
            cards = modal.find_elements(By.CSS_SELECTOR, "[class*='ownerItem']")
        except Exception:
            cards = []
    if not cards:
        try:
            cards = modal.find_elements(
                By.XPATH,
                ".//div[.//*[contains(.,'Possible Owner')] and (.//button[contains(.,'View')] or .//a[contains(.,'View')])]",
            )
        except Exception:
            cards = []

    for card in cards:
        try:
            if not card.is_displayed():
                continue
            idx += 1
            owner_name = _owner_name_from_owner_card(card)
            btn = None
            for xp in (
                ".//button[contains(.,'View')]",
                ".//a[contains(.,'View')]",
                ".//*[@role='button'][contains(.,'View')]",
                ".//button[.//div[normalize-space()='View']]",
            ):
                for b in card.find_elements(By.XPATH, xp):
                    if not b.is_displayed():
                        continue
                    lab = (safe_text(b) or b.get_attribute("textContent") or "").strip().lower()
                    if "view all" in lab:
                        continue
                    if "view" in lab or lab in ("", "view >"):
                        btn = b
                        break
                if btn is not None:
                    break
            if btn is not None:
                entries.append((owner_name or None, btn, idx))
        except Exception:
            continue

    if entries:
        return entries

    # Fallback: each View control in modal order (handles DOM changes without ownerItem cards).
    idx = 0
    for b in modal.find_elements(
        By.XPATH,
        ".//button[contains(.,'View')] | .//a[contains(.,'View')] | .//*[@role='button'][contains(.,'View')]",
    ):
        try:
            if not b.is_displayed():
                continue
            lab = (safe_text(b) or b.get_attribute("textContent") or "").strip().lower()
            if "view all" in lab:
                continue
            if "view" not in lab and lab != "view >":
                continue
            idx += 1
            card = b
            for _ in range(10):
                try:
                    parent = card.find_element(By.XPATH, "..")
                except Exception:
                    break
                card = parent
                pt = safe_text(card)
                if pt and "Possible Owner" in pt:
                    break
            owner_name = _owner_name_from_owner_card(card)
            entries.append((owner_name or None, b, idx))
        except Exception:
            continue
    return entries


def _owner_history_popup_present(driver) -> bool:
    return _owner_history_modal_root(driver) is not None


def _owner_history_page_token(driver) -> str:
    m = _owner_history_modal_root(driver)
    if not m:
        return ""
    return _owner_history_page_token_from_modal(m)


def _owner_history_next_page(driver) -> bool:
    m = _owner_history_modal_root(driver)
    if not m:
        return False
    return _owner_history_next_page_in_modal(driver, m)


def _dismiss_owner_history_modal_if_present(driver) -> bool:
    """Close the Owner History overlay so the inline Possible Owners section is usable."""
    m = _owner_history_modal_root(driver)
    if not m:
        return False
    try:
        for xp in (
            ".//div[contains(@class,'close')][@title='Dismiss']",
            ".//div[@title='Dismiss']",
            ".//*[@aria-label='Close']",
            ".//button[@aria-label='Close']",
        ):
            for el in m.find_elements(By.XPATH, xp):
                try:
                    if el.is_displayed():
                        js_click(driver, el)
                        time.sleep(1.0)
                        if _owner_history_modal_root(driver) is None:
                            print("[ICM] Owner History modal dismissed.")
                            return True
                except Exception:
                    continue
        try:
            driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
            time.sleep(0.6)
        except Exception:
            pass
        return _owner_history_modal_root(driver) is None
    except Exception as e:
        print(f"[ICM] Dismiss Owner History modal failed: {e}")
        return False


def _possible_owners_section_root(driver):
    """Main report block with h2 'Possible Owners' (not the modal)."""
    for h2 in driver.find_elements(By.XPATH, "//h2[normalize-space()='Possible Owners']"):
        try:
            if not h2.is_displayed():
                continue
            root = h2.find_element(
                By.XPATH,
                "./ancestor::div[contains(@class,'recordSubsection')][1]",
            )
            if root.is_displayed():
                return root
        except Exception:
            continue
    return None


def _name_from_inline_owner_card(card) -> Optional[str]:
    parts: List[str] = []
    for tag in ("h3", "h4", "h5"):
        for el in card.find_elements(By.TAG_NAME, tag):
            t = safe_text(el)
            if t and t.lower() not in ("possible owner", "currently viewing"):
                parts.append(t)
    name = re.sub(r"\s+", " ", " ".join(parts)).strip()
    return name or None


def _inline_possible_owner_card_elements(section_root):
    """Owner 'tab' cards inside the Possible Owners horizontal carousel."""
    out: List = []
    try:
        for sc in section_root.find_elements(
            By.XPATH,
            ".//div[contains(@class,'scrollContainer')]",
        ):
            if not sc.is_displayed():
                continue
            for div in sc.find_elements(By.XPATH, "./div[contains(@class,'possibleOwners')]"):
                try:
                    if div.is_displayed():
                        out.append(div)
                except Exception:
                    continue
    except Exception:
        pass
    return out


def _collect_via_inline_possible_owners_carousel(
    driver, phone: str, owners_hint: Optional[str]
) -> List[ResultRow]:
    """Click each visible Possible Owners tab once; do not advance the 1/N pager (same owners repeat)."""
    rows: List[ResultRow] = []
    seen = set()
    rec = 1
    section = _possible_owners_section_root(driver)
    if not section:
        print("[ICM] Inline carousel: no Possible Owners section.")
        return rows
    cards = _inline_possible_owner_card_elements(section)
    if not cards:
        print("[ICM] Inline carousel: no owner tab cards in view.")
        return rows
    for idx, card in enumerate(cards, start=1):
        name = _name_from_inline_owner_card(card)
        norm = re.sub(r"\s+", " ", (name or "").strip().lower())
        key = f"{idx}|{norm or 'blank'}"
        if key in seen:
            continue
        try:
            print(f"[ICM] Inline owner tab -> idx={idx} name={name!r}")
            js_click(driver, card)
            time.sleep(1.2)
            dismiss_overlays_if_any(driver)
            # Only the tab label — never fall back to search-snippet (concatenates all owners).
            rows.append(_collect_current_report_row(driver, phone, rec, name))
            rec += 1
            seen.add(key)
            print(f"[ICM] Inline owner captured key={key} total_rows={len(rows)}")
        except Exception as e:
            print(f"[ICM] Inline owner failed {key}: {type(e).__name__}: {e}")
    return rows


def step_collect_results_for_one_number(driver, phone: str) -> List[ResultRow]:
    owners = _click_view_for_current_search(driver, phone)
    # Wait for report route. Do NOT use "Personal"/"Email" in the DOM as readiness: the shell
    # often paints before the Owner History modal, which caused false "no modal" and one-row scrapes.
    try:
        _wait_phone_report_url(driver)
    except TimeoutException:
        print("[ICM] Warning: still not on /dashboard/reports/ after View:", driver.current_url)
    report_url = driver.current_url

    modal_here = _poll_for_owner_history_modal(driver, OWNER_HISTORY_APPEAR_WAIT_SEC)
    if not modal_here:
        time.sleep(1.8)
        modal_here = _owner_history_popup_present(driver)
        if modal_here:
            print("[ICM] Owner History modal appeared after extra delay.")
    if modal_here:
        print("[ICM] Owner History modal detected within wait window.")
    else:
        print("[ICM] No Owner History modal in first", OWNER_HISTORY_APPEAR_WAIT_SEC, "s; single-owner flow.")
    time.sleep(0.5)

    if _owner_history_popup_present(driver) and OWNER_HISTORY_DISMISS_THEN_INLINE_CAROUSEL:
        print("[ICM] Owner History modal open -> try dismiss + inline Possible Owners tabs...")
        _dismiss_owner_history_modal_if_present(driver)
        time.sleep(0.6)
        dismiss_overlays_if_any(driver)
        inline_rows = _collect_via_inline_possible_owners_carousel(driver, phone, owners)
        if inline_rows:
            print(f"[ICM] Inline Possible Owners carousel done: {len(inline_rows)} row(s).")
            return inline_rows
        print("[ICM] Inline carousel empty; reloading report to restore modal for View loop...")
        try:
            driver.get(report_url)
            time.sleep(1.2)
            _wait_phone_report_url(driver)
            _poll_for_owner_history_modal(driver, min(18.0, float(OWNER_HISTORY_APPEAR_WAIT_SEC)))
        except Exception as e:
            print("[ICM] Reload after failed inline:", e)
        dismiss_overlays_if_any(driver)

    if _owner_history_popup_present(driver):
        print("[ICM] Owner History popup detected; collecting each owner (modal View loop)...")
        rows: List[ResultRow] = []
        seen = set()
        rec = 1
        guard = 0
        same_page_misses = 0
        while guard < 100:
            guard += 1
            modal = _owner_history_modal_root(driver)
            if modal is None:
                break
            dismiss_overlays_if_any(driver)
            page_tok = _owner_history_page_token_from_modal(modal) or "1/1"
            entries = _owner_history_entries(driver, modal)
            if not entries:
                if _owner_history_next_page_in_modal(driver, modal):
                    same_page_misses = 0
                    continue
                break

            def entry_key(owner_name, item_idx: int) -> str:
                norm_name = re.sub(r"\s+", " ", (owner_name or "").strip().lower())
                return f"{page_tok}|{item_idx}|{norm_name or 'blank'}"

            unseen = [(o, b, i) for o, b, i in entries if entry_key(o, i) not in seen]
            if not unseen:
                same_page_misses = 0
                if _owner_history_next_page_in_modal(driver, modal):
                    continue
                break

            picked = False
            for owner_name, btn, item_idx in unseen:
                key = entry_key(owner_name, item_idx)
                print(f"[ICM] Owner popup -> page={page_tok} idx={item_idx} name={owner_name!r}")
                try:
                    js_click(driver, btn)
                    _wait_report_after_owner_modal_view(driver)
                    rows.append(_collect_current_report_row(driver, phone, rec, owner_name))
                    rec += 1
                    seen.add(key)
                    picked = True
                    print(f"[ICM] Owner popup captured -> key={key} total_rows={len(rows)}")
                    if not _owner_history_modal_ready_again(driver, phone, report_url):
                        print("[ICM] Could not reopen Owner History; stopping multi-owner loop.")
                        break
                    break
                except Exception as e:
                    print(f"[ICM] Owner History View failed ({key}): {type(e).__name__}: {e}")
                    continue

            if not picked:
                same_page_misses += 1
                if same_page_misses >= 3:
                    print("[ICM] Owner History: repeated click failures; trying next modal page.")
                    same_page_misses = 0
                    if _owner_history_next_page_in_modal(driver, modal):
                        continue
                    break
                time.sleep(1.5)
                dismiss_overlays_if_any(driver)
                continue

            same_page_misses = 0

        if rows:
            return rows

    if OWNER_HISTORY_DISMISS_THEN_INLINE_CAROUSEL:
        sec = _possible_owners_section_root(driver)
        if sec:
            cards = _inline_possible_owner_card_elements(sec)
            if len(cards) > 1:
                ir = _collect_via_inline_possible_owners_carousel(driver, phone, owners)
                if ir:
                    print(f"[ICM] Inline-only Possible Owners: {len(ir)} row(s).")
                    return ir

    print("[ICM] No Owner History popup handling needed; scraping current report.")
    return [_collect_current_report_row(driver, phone, 1, owners)]


# In[7]:


def process_one_number(driver, phone: str) -> List[ResultRow]:
    try:
        print("[ICM] --- Start number", phone, "---")
        step_open_site(driver)
        step_enter_phone_and_search(driver, phone)
        rows = step_collect_results_for_one_number(driver, phone)
        print("[ICM] --- Done number", phone, "rows=", len(rows), "---")
        return rows
    except TimeoutException as e:
        try:
            url = driver.current_url
        except Exception:
            url = None
        return [
            ResultRow(
                source_phone=phone,
                record_index=0,
                status="timeout",
                error=str(e),
                page_url=url,
            )
        ]
    except Exception as e:
        try:
            url = driver.current_url
        except Exception:
            url = None
        return [
            ResultRow(
                source_phone=phone,
                record_index=0,
                status="error",
                error=str(e),
                page_url=url,
            )
        ]


# In[ ]:


from pathlib import Path
from datetime import datetime
from typing import List


def save_excel(df: pd.DataFrame, path: str) -> str:
    try:
        df.to_excel(path, index=False)
        return path
    except PermissionError:
        p = Path(path)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        alt = p.with_name(f"{p.stem}_{stamp}{p.suffix}")
        df.to_excel(str(alt), index=False)
        print(f"Locked {path}; wrote {alt}")
        return str(alt)


def run_instant_checkmate_batch(
    input_xlsx: str | None = None,
    phone_column: str | None = None,
    output_xlsx: str | None = None,
    max_numbers: int | None = None,
    pause_between_numbers: float | None = None,
    quit_driver_when_done: bool = False,
) -> tuple[pd.DataFrame, str]:
    """
    Run the Instant Checkmate scrape loop. Parameters default to module CONFIG constants.

    quit_driver_when_done: set True when chaining after another Selenium script so Chrome is released.
    """
    ix = INPUT_XLSX if input_xlsx is None else input_xlsx
    col = INPUT_PHONE_COLUMN if phone_column is None else phone_column
    ox = OUTPUT_XLSX if output_xlsx is None else output_xlsx
    mx = MAX_NUMBERS if max_numbers is None else max_numbers
    pause = PAUSE_BETWEEN_NUMBERS if pause_between_numbers is None else pause_between_numbers

    phone_values = load_phone_values(ix, col)
    phones = [clean_phone(v) for v in phone_values if clean_phone(v)]
    if mx is not None:
        phones = phones[:mx]

    print(f"Phones to process: {len(phones)}")
    print("[ICM] Starting main loop (open dashboard -> search each phone)...")
    if USE_REMOTE_DEBUGGING:
        print("[ICM] Mode: attach to existing Chrome @", REMOTE_DEBUGGING_ADDRESS)
    elif USE_ISOLATED_CHROME_USER_DATA:
        print("[ICM] Chrome user data (isolated — NOT Abubakar):", ISOLATED_CHROME_USER_DATA_DIR)
        print(
            "[ICM] WARNING: Isolated folder ≠ your real profile. Set USE_ISOLATED_CHROME_USER_DATA=False "
            "and close all chrome.exe to use Abubakar."
        )
    elif USE_CHROME_PROFILE:
        print(
            "[ICM] Real profile --profile-directory:",
            _effective_chrome_profile_directory() or "(empty—fix CONFIG)",
        )
        print("[ICM] Quit ALL Chrome first or you get SessionNotCreated.")

    driver = build_driver(headless=HEADLESS)
    all_rows: List[ResultRow] = []

    try:
        for idx, phone in enumerate(phones, start=1):
            print(f"[{idx}/{len(phones)}] {phone}")
            all_rows.extend(process_one_number(driver, phone))
            time.sleep(pause)
    finally:
        if quit_driver_when_done:
            try:
                driver.quit()
            except Exception:
                pass

    out_df = pd.DataFrame([asdict(r) for r in all_rows])
    saved = save_excel(out_df, ox)
    print(f"Saved: {saved}")
    return out_df, saved


if __name__ == "__main__":
    out_df, saved = run_instant_checkmate_batch()
    print(saved)
    print(out_df.head(20))

