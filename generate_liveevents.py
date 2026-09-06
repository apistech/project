#!/usr/bin/env python3
"""
Simple IPTV Playlist Validator
- Fetch M3U from sources
- Dedup by URL
- Check playability (HEAD + body-sniff)
- Save valid streams
"""

import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter

# =========================
# CONFIGURATION
# =========================
TIMEOUT = 10
FETCH_TIMEOUT = 30
MAX_WORKERS = min(32, (os.cpu_count() or 2) * 2)
OUTPUT_DIR = Path("playlists")

env_file = Path(".env")
if env_file.exists():
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"\''))

sources_raw = os.getenv("PLAYLIST_SOURCES", "")

SOURCES = [
    url.strip() 
    for url in sources_raw.replace("\n", ",").split(",") 
    if url.strip()
]

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0"
    ),
    "Accept": "*/*",
}

VALID_CONTENT_TYPES = {
    "application/dash+xml",
    "application/vnd.apple.mpegurl",
    "application/x-mpegurl",
    "video/mp2t",
    "video/mp4",
    "video/mpeg",
    "video/ogg",
    "video/webm",
    "video/x-flv",
}

# =========================
# SESSION POOLING
# =========================
_thread_local = threading.local()


def get_session() -> requests.Session:
    sess = getattr(_thread_local, "session", None)
    if sess is None:
        sess = requests.Session()
        adapter = HTTPAdapter(
            pool_connections=MAX_WORKERS,
            pool_maxsize=MAX_WORKERS,
            max_retries=0,
        )
        sess.mount("http://", adapter)
        sess.mount("https://", adapter)
        sess.headers.update(DEFAULT_HEADERS)
        _thread_local.session = sess
    return sess


# =========================
# CORE FUNCTIONS
# =========================
def is_playable(url: str, headers: dict = None) -> bool:
    req_headers = dict(headers) if headers else {}
    sess = get_session()
    timeout_tuple = (3.0, TIMEOUT)

    # 1. HEAD request
    try:
        resp = sess.head(url, headers=req_headers, timeout=timeout_tuple, allow_redirects=True)
        if resp.status_code < 400:
            ct = resp.headers.get("Content-Type", "").split(";")[0].strip().lower()
            if ct in VALID_CONTENT_TYPES:
                return True
    except requests.RequestException:
        pass

    # 2. GET fallback
    try:
        with sess.get(
            url, headers=req_headers, timeout=timeout_tuple, stream=True, allow_redirects=True
        ) as resp:
            if resp.status_code >= 400:
                return False

            ct = resp.headers.get("Content-Type", "").split(";")[0].strip().lower()
            if ct in VALID_CONTENT_TYPES:
                return True

            chunk = next(resp.iter_content(chunk_size=2048), b"")
            if not chunk:
                return False

            preview = chunk.decode("utf-8", errors="ignore").strip()

            if preview.lower().startswith("<html") or "<html" in preview.lower()[:200]:
                return False

            if preview.startswith("#EXTM3U") or preview.startswith("#EXT-X-"):
                return True

            if chunk[0:1] == b"\x47" or b"ftyp" in chunk[:32] or chunk[:3] == b"ID3" or chunk[:2] == b"\xff\xfb":
                return True

            return False
    except requests.RequestException:
        return False


def parse_m3u(lines: list[str]) -> tuple[list[str], list[dict]]:
    headers = []
    entries = []
    
    current_extinf = []
    current_other = []
    current_vlc = []
    
    in_global_header = True

    for line in lines:
        line = line.strip()
        if not line:
            continue

        if in_global_header:
            if line.startswith("#EXTINF"):
                in_global_header = False
            elif line.startswith("#"):
                headers.append(line)
                continue

        if line.startswith("#EXTINF"):
            current_extinf.append(line)
        elif line.startswith("#EXTVLCOPT"):
            current_vlc.append(line)
        elif line.startswith("#"):
            current_other.append(line)
        else:
            # Reached a URL line
            entry_headers = {}
            for opt in current_vlc:
                if opt.startswith("#EXTVLCOPT:"):
                    kv = opt[len("#EXTVLCOPT:") :].split("=", 1)
                    if len(kv) == 2:
                        key, val = kv[0].strip(), kv[1].strip()
                        key_lower = key.lower()
                        if key_lower == "http-referrer":
                            entry_headers["Referer"] = val
                        elif key_lower == "http-origin":
                            entry_headers["Origin"] = val
                        elif key_lower == "http-user-agent":
                            entry_headers["User-Agent"] = val

            entries.append({
                "extinf": current_extinf[:],
                "vlcopt": current_vlc[:],
                "other": current_other[:],
                "url": line,
                "headers": entry_headers,
            })

            current_extinf.clear()
            current_vlc.clear()
            current_other.clear()

    return headers, entries


def dedup_by_url(entries: list[dict]) -> tuple[list[dict], int]:
    seen = set()
    unique = []
    for entry in entries:
        if entry["url"] not in seen:
            seen.add(entry["url"])
            unique.append(entry)
    return unique, len(entries) - len(unique)


def fetch_playlist(url: str) -> list[str] | None:
    try:
        resp = get_session().get(url, timeout=FETCH_TIMEOUT)
        resp.raise_for_status()
        return resp.text.splitlines()
    except requests.RequestException as e:
        print(f"  [ERROR] Failed to fetch: {e}")
        return None


def get_filename_from_url(url: str) -> str:
    parsed = urlparse(url)
    filename = Path(parsed.path).name
    return filename if filename and filename != "/" else "playlist.m3u"


def process_source(url: str) -> bool:
    filename = get_filename_from_url(url)
    print(f"\n{'=' * 60}\nProcessing: {filename}\nURL: {url}\n{'=' * 60}")

    lines = fetch_playlist(url)
    if not lines:
        return False

    headers, entries = parse_m3u(lines)
    if not entries:
        print(f"[SKIP] {filename}: no entries found")
        return False

    entries, dup_count = dedup_by_url(entries)
    print(f"Unique entries to test: {len(entries)} (Duplicates: {dup_count})")

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(is_playable, entry["url"], entry["headers"]): entry
            for entry in entries
        }

        for i, future in enumerate(as_completed(futures), 1):
            entry = futures[future]
            try:
                entry["playable"] = future.result()
            except Exception:
                entry["playable"] = False
            status = "OK" if entry["playable"] else "DEAD"
            print(f"[{i:>3}/{len(entries)}] {status} {entry['url'][:60]}")

    output = headers.copy()
    if not output:
        output.append("#EXTM3U")

    playable_count = 0
    for entry in entries:
        if entry["playable"]:
            output.extend(entry["extinf"])
            output.extend(entry["vlcopt"])
            output.extend(entry["other"])
            output.append(entry["url"])
            playable_count += 1

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / filename
    out_path.write_text("\n".join(output) + "\n", encoding="utf-8")

    print(f"\nPlayable: {playable_count}/{len(entries)}\nSaved: {out_path}")
    return True


def main():
    if not SOURCES:
        sys.exit(1)

    results = {get_filename_from_url(url): process_source(url) for url in SOURCES}

    print(f"\n{'=' * 60}\nSUMMARY\n{'=' * 60}")
    for name, success in results.items():
        print(f"  {name}: {'OK' if success else 'FAILED'}")

    if not any(results.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()