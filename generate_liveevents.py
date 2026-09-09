#!/usr/bin/env python3
import os
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# =========================
# CONFIG
# =========================
TIMEOUT = 10
FETCH_TIMEOUT = 30
MAX_WORKERS = min(32, (os.cpu_count() or 2) * 2)
OUTPUT_DIR = Path("playlists")
MIN_PLAYABLE_RATIO = 0.10

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
}

VALID_CT = {
    "application/dash+xml",
    "application/vnd.apple.mpegurl",
    "application/x-mpegurl",
    "video/mp2t", "video/mp4", "video/mpeg",
    "video/ogg", "video/webm", "video/x-flv",
}

# Load .env sederhana
env_path = Path(".env")
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("'\""))

SOURCES = [
    u.strip()
    for u in os.getenv("PLAYLIST_SOURCES", "").replace("\n", ",").split(",")
    if u.strip()
]

# =========================
# SESSION
# =========================
_local = threading.local()

def get_session() -> requests.Session:
    if not getattr(_local, "session", None):
        s = requests.Session()
        retry = Retry(total=2, backoff_factor=0.5,
                      status_forcelist=[429, 500, 502, 503, 504],
                      allowed_methods=["HEAD", "GET"])
        adapter = HTTPAdapter(pool_connections=MAX_WORKERS,
                              pool_maxsize=MAX_WORKERS, max_retries=retry)
        s.mount("http://", adapter)
        s.mount("https://", adapter)
        s.headers.update(DEFAULT_HEADERS)
        _local.session = s
    return _local.session

# =========================
# LIVE CHECK
# =========================
def is_playable(url: str, headers: dict | None = None) -> bool:
    sess = get_session()
    h = headers or {}
    to = (3.0, TIMEOUT)

    # HEAD fast-path
    try:
        r = sess.head(url, headers=h, timeout=to, allow_redirects=True)
        if r.status_code < 400:
            ct = r.headers.get("Content-Type", "").split(";")[0].strip().lower()
            if ct in VALID_CT:
                return True
    except requests.RequestException:
        pass

    # GET + sniff
    try:
        with sess.get(url, headers=h, timeout=to, stream=True, allow_redirects=True) as r:
            if r.status_code >= 400:
                return False
            ct = r.headers.get("Content-Type", "").split(";")[0].strip().lower()
            if ct in VALID_CT:
                return True

            chunk = next(r.iter_content(2048), b"")
            if not chunk:
                return False

            preview = chunk.decode("utf-8", errors="ignore").strip()
            if "<html" in preview[:200].lower():
                return False
            if preview.startswith(("#EXTM3U", "#EXT-X-")):
                return True
            if chunk[0:1] == b"\x47" or b"ftyp" in chunk[:32] or chunk[:3] == b"ID3" or chunk[:2] == b"\xff\xfb":
                return True
            return False
    except requests.RequestException:
        return False

# =========================
# PARSER & HELPERS
# =========================
def parse_m3u(lines: list[str]) -> tuple[list[str], list[dict]]:
    headers, entries = [], []
    extinf, vlc, other = [], [], []
    in_header = True

    for line in lines:
        line = line.strip()
        if not line:
            continue

        if in_header:
            if line.startswith("#EXTINF"):
                in_header = False
            elif line.startswith("#"):
                headers.append(line)
                continue

        if line.startswith("#EXTINF"):
            extinf.append(line)
        elif line.startswith("#EXTVLCOPT"):
            vlc.append(line)
        elif line.startswith("#"):
            other.append(line)
        else:
            hdrs = {}
            for opt in vlc:
                if opt.startswith("#EXTVLCOPT:"):
                    kv = opt[11:].split("=", 1)
                    if len(kv) == 2:
                        k, v = kv[0].strip().lower(), kv[1].strip()
                        if k == "http-referrer":
                            hdrs["Referer"] = v
                        elif k == "http-origin":
                            hdrs["Origin"] = v
                        elif k == "http-user-agent":
                            hdrs["User-Agent"] = v

            entries.append({
                "extinf": extinf[:], "vlcopt": vlc[:], "other": other[:],
                "url": line, "headers": hdrs
            })
            extinf.clear(); vlc.clear(); other.clear()

    return headers, entries


def natural_key(s: str) -> list:
    return [int(p) if p.isdigit() else p.casefold() for p in re.split(r"(\d+)", s)]


def get_group(entry: dict) -> str:
    for line in entry.get("extinf", []):
        m = re.search(r'(?i)group-title\s*=\s*["\']([^"\']*)["\']', line)
        if m:
            return m.group(1).strip()
    return ""


def get_filename(entry: dict) -> str:
    name = unquote(Path(urlparse(entry.get("url", "")).path).name).strip()
    return name or entry.get("url", "")


def sort_entries(entries: list[dict]) -> list[dict]:
    return sorted(entries, key=lambda e: (natural_key(get_group(e)), natural_key(get_filename(e))))


def atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    except Exception:
        if tmp.exists():
            tmp.unlink()
        raise

# =========================
# PROCESS
# =========================
def process_source(url: str) -> bool:
    filename = Path(urlparse(url).path).name or "playlist.m3u"
    print(f"\n{'='*60}\nProcessing: {filename}\n{url}\n{'='*60}")

    try:
        resp = get_session().get(url, timeout=FETCH_TIMEOUT)
        resp.raise_for_status()
        lines = resp.text.splitlines()
    except requests.RequestException as e:
        print(f"  [ERROR] Fetch failed: {e}")
        return False

    headers, entries = parse_m3u(lines)
    if not entries:
        print(f"[SKIP] {filename}: no entries")
        return False

    # Dedup
    seen, unique = set(), []
    for e in entries:
        if e["url"] not in seen:
            seen.add(e["url"])
            unique.append(e)
    print(f"Unique: {len(unique)} (dup: {len(entries)-len(unique)})")

    # Parallel check
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futs = {pool.submit(is_playable, e["url"], e["headers"]): e for e in unique}
        for i, fut in enumerate(as_completed(futs), 1):
            e = futs[fut]
            try:
                e["playable"] = fut.result()
            except Exception:
                e["playable"] = False
            print(f"[{i:>3}/{len(unique)}] {'OK' if e['playable'] else 'DEAD'} {e['url'][:60]}")

    playable = sort_entries([e for e in unique if e.get("playable")])
    ratio = len(playable) / len(unique) if unique else 0

    if ratio < MIN_PLAYABLE_RATIO:
        print(f"[SANITY] {filename}: {len(playable)}/{len(unique)} ({ratio:.0%}) < {MIN_PLAYABLE_RATIO:.0%} → skip write")
        return False

    out = headers or ["#EXTM3U"]
    for e in playable:
        out.extend(e["extinf"] + e["vlcopt"] + e["other"] + [e["url"]])

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / filename
    atomic_write(out_path, "\n".join(out) + "\n")

    print(f"Playable: {len(playable)}/{len(unique)} | Saved: {out_path}")
    return True


def main():
    if not SOURCES:
        sys.exit(1)

    results = {Path(urlparse(u).path).name or u: process_source(u) for u in SOURCES}

    print(f"\n{'='*60}\nSUMMARY\n{'='*60}")
    for name, ok in results.items():
        print(f"  {name}: {'OK' if ok else 'FAILED'}")

    if not any(results.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()