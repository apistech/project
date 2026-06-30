#!/usr/bin/env python3
"""
Simple IPTV Playlist Validator
- Fetch M3U from sources
- Dedup by URL
- Check playability (HEAD + body-sniff)
- Save valid streams
"""

import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse

import requests

# =========================
# CONFIGURATION
# =========================
TIMEOUT = 10
MAX_WORKERS = 32
OUTPUT_DIR = Path("playlists")

# Add your M3U URLs here
SOURCES = [
    "https://github.com/doms9/iptv/raw/refs/heads/default/M3U8/events.m3u8",
    "https://github.com/sm-monirulislam/SM-Live-TV/raw/refs/heads/main/World_Cup.m3u",
]

# Stream content types (whitelist)
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
# CORE FUNCTIONS
# =========================
def is_playable(url: str, headers: dict = None) -> bool:
    """Check if URL points to a valid stream."""
    headers = headers or {}

    # 1. HEAD request (fast path)
    try:
        resp = requests.head(url, headers=headers, timeout=TIMEOUT, allow_redirects=True)
        if resp.status_code < 400:
            ct = resp.headers.get("Content-Type", "").split(";")[0].strip().lower()
            if ct in VALID_CONTENT_TYPES:
                return True
    except requests.RequestException:
        pass

    # 2. GET with body sniffing (fallback)
    try:
        with requests.get(
            url, headers=headers, timeout=TIMEOUT, stream=True, allow_redirects=True
        ) as resp:
            if resp.status_code >= 400:
                return False

            # Check Content-Type header
            ct = resp.headers.get("Content-Type", "").split(";")[0].strip().lower()
            if ct in VALID_CONTENT_TYPES:
                return True

            # Read first chunk for validation
            chunk = next(resp.iter_content(chunk_size=2048), b"")
            if not chunk:
                return False

            preview = chunk.decode("utf-8", errors="ignore").strip()

            # HTML page -> not a stream
            if preview.lower().startswith("<html") or "<html" in preview.lower()[:200]:
                return False

            # M3U8 manifest
            if preview.startswith("#EXTM3U") or preview.startswith("#EXT-X-"):
                return True

            # Binary stream signatures
            if chunk[0:1] == b"\x47":  # MPEG-TS sync byte
                return True
            if b"ftyp" in chunk[:32]:  # MP4 container
                return True
            if chunk[:3] == b"ID3" or chunk[:2] == b"\xff\xfb":  # MP3/ID3
                return True

            return False

    except requests.RequestException:
        return False


def parse_m3u(lines: list[str]) -> list[dict]:
    """Parse M3U file into entries with metadata."""
    entries = []
    extinf = []
    other_tags = []
    vlcopts = []

    for line in lines:
        line = line.strip()

        if not line:
            continue

        if line.startswith("#EXTM3U"):
            continue

        if line.startswith("#EXTINF"):
            extinf.append(line)
            continue

        if line.startswith("#EXTVLCOPT"):
            vlcopts.append(line)
            continue

        if line.startswith("#"):
            other_tags.append(line)
            continue

        # This is a URL
        url = line

        # Extract headers from VLC options
        headers = {}
        for opt in vlcopts:
            if opt.startswith("#EXTVLCOPT:"):
                kv = opt[len("#EXTVLCOPT:") :].split("=", 1)
                if len(kv) == 2:
                    key, val = kv
                    if key.lower() == "http-referrer":
                        headers["Referer"] = val
                    elif key.lower() == "http-origin":
                        headers["Origin"] = val
                    elif key.lower() == "http-user-agent":
                        headers["User-Agent"] = val

        entries.append(
            {
                "extinf": extinf[:],
                "other": other_tags[:],
                "vlcopt": vlcopts[:],
                "url": url,
                "headers": headers,
            }
        )

        extinf.clear()
        other_tags.clear()
        vlcopts.clear()

    return entries


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
        resp = requests.get(url, timeout=30)
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
    entries = parse_m3u(lines)
    if not entries:
        print(f"[SKIP] {filename}: no entries found")
        return False
    print(f"Total entries: {len(entries)}")

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

    # Build output
    output = ["#EXTM3U"]
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