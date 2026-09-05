#!/usr/bin/env python3
"""
Simple IPTV Playlist Validator
- Fetch M3U from sources
- Dedup by URL
- Check playability (HEAD + body-sniff)
- Save valid streams
"""

import sys
import os
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

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0"
    )
}

SOURCES = [
    "https://github.com/BuddyChewChew/sports/raw/refs/heads/main/liveeventsfilter.m3u8",
]

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
    """Get (or create) a thread-local Session with a sized connection pool."""
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
    """Check if URL points to a valid stream."""
    req_headers = dict(headers) if headers else {}
    sess = get_session()
    timeout_tuple = (3.0, TIMEOUT)

    # 1. HEAD request (fast path)
    try:
        resp = sess.head(url, headers=req_headers, timeout=timeout_tuple, allow_redirects=True)
        if resp.status_code < 400:
            ct = resp.headers.get("Content-Type", "").split(";")[0].strip().lower()
            if ct in VALID_CONTENT_TYPES:
                return True
    except requests.RequestException:
        pass

    # 2. GET with body sniffing (fallback)
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

            if chunk[0:1] == b"\x47":  # MPEG-TS sync byte
                return True
            if b"ftyp" in chunk[:32]:  # MP4 container
                return True
            if chunk[:3] == b"ID3" or chunk[:2] == b"\xff\xfb":  # MP3/ID3
                return True

            return False

    except requests.RequestException:
        return False


def parse_m3u(lines: list[str]) -> tuple[list[str], list[dict]]:
    """
    Parse M3U file into:
    - headers: list of header lines (including #EXTM3U and its attributes)
    - entries: list of entry dicts with metadata
    """
    headers = []
    entries = []
    extinf = []
    other_tags = []
    vlcopts = []
    is_header = True

    for line in lines:
        line = line.strip()

        if not line:
            continue

        if is_header and line.startswith("#"):
            headers.append(line)
            if line.startswith("#EXTINF"):
                is_header = False
                extinf.append(line)
            continue

        if not line.startswith("#") and not is_header:
            url = line

            entry_headers = {}
            for opt in vlcopts:
                if opt.startswith("#EXTVLCOPT:"):
                    kv = opt[len("#EXTVLCOPT:") :].split("=", 1)
                    if len(kv) == 2:
                        key, val = kv
                        if key.lower() == "http-referrer":
                            entry_headers["Referer"] = val
                        elif key.lower() == "http-origin":
                            entry_headers["Origin"] = val
                        elif key.lower() == "http-user-agent":
                            entry_headers["User-Agent"] = val

            entries.append(
                {
                    "extinf": extinf[:],
                    "other": other_tags[:],
                    "vlcopt": vlcopts[:],
                    "url": url,
                    "headers": entry_headers,
                }
            )

            extinf.clear()
            other_tags.clear()
            vlcopts.clear()
            continue

        if line.startswith("#"):
            if line.startswith("#EXTINF"):
                extinf.append(line)
            elif line.startswith("#EXTVLCOPT"):
                vlcopts.append(line)
            else:
                other_tags.append(line)

    return headers, entries


def dedup_by_url(entries: list[dict]) -> tuple[list[dict], int]:
    """Remove duplicate entries based on URL."""
    seen = set()
    unique = []
    for entry in entries:
        if entry["url"] not in seen:
            seen.add(entry["url"])
            unique.append(entry)

    return unique, len(entries) - len(unique)


def fetch_playlist(url: str) -> list[str] | None:
    """Download playlist content."""
    try:
        resp = get_session().get(url, timeout=FETCH_TIMEOUT)
        resp.raise_for_status()
        return resp.text.splitlines()
    except requests.RequestException as e:
        print(f"  [ERROR] Failed to fetch: {e}")
        return None


def get_filename_from_url(url: str) -> str:
    """Extract filename from URL."""
    parsed = urlparse(url)
    path = Path(parsed.path)
    filename = path.name

    if not filename or filename == "/":
        return "playlist.m3u"

    return filename


def process_source(url: str) -> bool:
    """Process single source: fetch, parse, validate, save."""
    filename = get_filename_from_url(url)

    print(f"\n{'=' * 60}")
    print(f"Processing: {filename}")
    print(f"URL: {url}")
    print(f"{'=' * 60}")

    # Fetch
    lines = fetch_playlist(url)
    if not lines:
        print(f"[SKIP] {filename}: cannot fetch")
        return False

    # Parse
    headers, entries = parse_m3u(lines)
    if not entries:
        print(f"[SKIP] {filename}: no entries found")
        return False
    
    print(f"Total entries: {len(entries)}")
    
    if headers:
        print(f"Headers preserved: {len(headers)} lines")
        for h in headers[:3]:  # Show first 3
            print(f"  {h[:80]}...")

    # Dedup before checking (save resources)
    entries, dup_count = dedup_by_url(entries)
    if dup_count:
        print(f"Duplicates removed: {dup_count}")
    print(f"Unique entries: {len(entries)}")

    # Check playability in parallel
    print(f"Checking {len(entries)} URLs with {MAX_WORKERS} workers...\n")

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
    
    playable_count = 0
    for entry in entries:
        if entry["playable"]:
            output.extend(entry["extinf"])
            output.extend(entry["other"])
            output.extend(entry["vlcopt"])
            output.append(entry["url"])
            playable_count += 1

    # Save with original filename
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / filename

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(output) + "\n")

    print(f"\nPlayable: {playable_count}/{len(entries)}")
    print(f"Saved: {out_path}")
    return True


def main():
    if not SOURCES:
        print("[ERROR] SOURCES is empty. Add at least one URL.")
        sys.exit(1)

    results = {}
    for url in SOURCES:
        filename = get_filename_from_url(url)
        results[filename] = process_source(url)

    print(f"\n{'=' * 60}")
    print("SUMMARY")
    print(f"{'=' * 60}")
    for name, success in results.items():
        print(f"  {name}: {'OK' if success else 'FAILED'}")

    if not any(results.values()):
        print("\n[ERROR] All sources failed.")
        sys.exit(1)


if __name__ == "__main__":
    main()