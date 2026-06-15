import sys
from contextlib import closing
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

TIMEOUT = 10
MAX_WORKERS = 16
OUTPUT_DIR = Path("playlists")

EPG_URL = "https://epgshare01.online/epgshare01/epg_ripper_DUMMY_CHANNELS.xml.gz"

# ====================================================================
# DAFTAR SUMBER PLAYLIST
# Tambah/ubah entry di sini untuk custom source.
#   name   -> nama file output (tanpa ekstensi), hasil: playlists/<name>.m3u
#   url    -> link raw M3U/M3U8 source
# ====================================================================
SOURCES = [
    {
        "name": "live_events",
        "url": "https://github.com/doms9/iptv/raw/refs/heads/default/M3U8/events.m3u8",
    },
    {
        "name": "wc2026",
        "url": "https://github.com/sm-monirulislam/SM-Live-TV/raw/refs/heads/main/World_Cup.m3u",
    },
]

VALID_CONTENT_TYPES = {
    "application/dash+xml",
    "application/vnd.apple.mpegurl",
    "application/x-mpegurl",
    "video/m4s",
    "video/mp2t",
    "video/mp4",
    "video/mpeg",
    "video/ogg",
    "video/ts",
    "video/webm",
    "video/x-flv",
}


def is_stream_playable(url: str, headers: dict = None) -> bool:
    headers = headers or {}

    # 1. Coba HEAD request dulu (efisien)
    try:
        response = requests.head(
            url, headers=headers, timeout=TIMEOUT, allow_redirects=True
        )
        if response.status_code < 400:
            content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
            if content_type in VALID_CONTENT_TYPES:
                return True
    except requests.RequestException:
        pass

    # 2. Fallback ke GET stream, body-sniff untuk validasi konten
    try:
        with closing(
            requests.get(
                url, headers=headers, timeout=TIMEOUT, stream=True, allow_redirects=True
            )
        ) as response:
            if response.status_code >= 400:
                return False

            content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
            if content_type in VALID_CONTENT_TYPES:
                return True

            # Body-sniff: baca chunk pertama, cek apakah manifest/stream valid
            try:
                chunk = next(response.iter_content(chunk_size=2048), b"")
            except (StopIteration, requests.RequestException):
                return False

            if not chunk:
                return False

            preview = chunk.decode("utf-8", errors="ignore").lstrip()
            preview_lower = preview.lower()

            # HTML page (error/geo-block) -> not playable
            if preview_lower.startswith("<html") or "<html" in preview_lower[:200]:
                return False

            # Valid M3U8 manifest
            if preview.startswith("#EXTM3U") or preview.startswith("#EXT-X-"):
                return True

            # Binary stream signatures (MPEG-TS sync byte, MP4 ftyp box, ID3 tag)
            if chunk[:1] == b"\x47":  # MPEG-TS sync byte
                return True
            if b"ftyp" in chunk[:32]:  # MP4 container
                return True
            if chunk[:3] == b"ID3" or chunk[:2] == b"\xff\xfb":  # MP3/ID3
                return True

            return False

    except requests.RequestException:
        return False


def parse_m3u(lines: list[str]) -> list[dict]:
    """Parse M3U lines into structured entries, preserving all tag types."""
    entries = []
    buffer_extinf = []
    buffer_other = []
    buffer_vlcopt = []

    for line in lines:
        stripped = line.strip()

        if line.startswith("#EXTINF"):
            buffer_extinf.append(line)
        elif line.startswith("#EXTVLCOPT"):
            buffer_vlcopt.append(line)
        elif stripped.startswith("#"):
            buffer_other.append(line)
        elif stripped and not stripped.startswith("#"):
            url = stripped

            headers = {}
            for opt in buffer_vlcopt:
                if opt.startswith("#EXTVLCOPT:"):
                    key_value = opt[len("#EXTVLCOPT:"):].split("=", 1)
                    if len(key_value) == 2:
                        key, value = key_value
                        key = key.lower()
                        if key == "http-referrer":
                            headers["Referer"] = value
                        elif key == "http-origin":
                            headers["Origin"] = value
                        elif key == "http-user-agent":
                            headers["User-Agent"] = value

            entries.append({
                "extinf": buffer_extinf,
                "other": buffer_other,
                "vlcopt": buffer_vlcopt,
                "url": url,
                "headers": headers,
            })

            buffer_extinf = []
            buffer_other = []
            buffer_vlcopt = []
        # baris kosong: skip, buffer tetap (handle case kosong di antara tags)

    return entries


def dedup_entries(entries: list[dict]) -> tuple[list[dict], int]:
    """Remove entries with duplicate URL, keep first occurrence."""
    seen_urls = set()
    unique_entries = []

    for entry in entries:
        url = entry["url"]
        if url not in seen_urls:
            seen_urls.add(url)
            unique_entries.append(entry)

    removed = len(entries) - len(unique_entries)
    return unique_entries, removed


def fetch_playlist(url: str) -> list[str] | None:
    """Download playlist source, return list of lines or None on failure."""
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        return [line.rstrip() for line in response.text.splitlines()]
    except requests.RequestException as e:
        print(f"  [ERROR] Gagal fetch source: {e}")
        return None


def process_source(name: str, url: str) -> bool:
    """Process single source: fetch -> dedup -> check -> write output. Return True if success."""
    print(f"\n{'=' * 60}")
    print(f"Source: {name}")
    print(f"URL   : {url}")
    print(f"{'=' * 60}")

    lines = fetch_playlist(url)
    if lines is None:
        print(f"[SKIP] {name}: source tidak bisa di-fetch")
        return False

    entries = parse_m3u(lines)
    print(f"Total entries: {len(entries)}")

    if not entries:
        print(f"[SKIP] {name}: tidak ada entry valid")
        return False

    # Dedup by URL sebelum check (hemat request)
    entries, dup_removed = dedup_entries(entries)
    if dup_removed > 0:
        print(f"Duplikat      : {dup_removed} dihapus (berdasarkan URL)")
    print(f"Unique entries: {len(entries)}")

    print(f"Checking with {MAX_WORKERS} parallel workers...\n")

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_entry = {
            executor.submit(is_stream_playable, entry["url"], entry["headers"]): entry
            for entry in entries
        }

        done = 0
        total = len(entries)
        for future in as_completed(future_to_entry):
            entry = future_to_entry[future]
            try:
                entry["playable"] = future.result()
            except Exception:
                entry["playable"] = False

            done += 1
            status = "OK " if entry["playable"] else "DEAD"
            print(f"[{done}/{total}] {status} {entry['url']}")

    output_lines = [f'#EXTM3U url-tvg="{EPG_URL}"']
    playable_count = 0
    for entry in entries:
        if entry["playable"]:
            output_lines.extend(entry["extinf"])
            output_lines.extend(entry["other"])
            output_lines.extend(entry["vlcopt"])
            output_lines.append(entry["url"])
            playable_count += 1

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / f"{name}.m3u"

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(output_lines) + "\n")

    print(f"\nPlayable: {playable_count}/{len(entries)}")
    print(f"Saved -> {output_path}")
    return True


def main():
    if not SOURCES:
        print("[ERROR] SOURCES kosong. Tambahkan minimal satu source di SOURCES.")
        sys.exit(1)

    results = {}
    for source in SOURCES:
        name = source["name"]
        url = source["url"]
        results[name] = process_source(name, url)

    print(f"\n{'=' * 60}")
    print("SUMMARY")
    print(f"{'=' * 60}")
    for name, success in results.items():
        status = "OK" if success else "FAILED/SKIPPED"
        print(f"  {name}: {status}")

    # Exit 1 hanya kalau SEMUA source gagal
    if not any(results.values()):
        print("\n[ERROR] Semua source gagal diproses.")
        sys.exit(1)


if __name__ == "__main__":
    main()