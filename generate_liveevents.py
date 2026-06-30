import os
import sys
from contextlib import closing
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from urllib.parse import urlparse

# ====================================================================
# CONFIGURATION
# ====================================================================
TIMEOUT = 8
MAX_WORKERS = 32
OUTPUT_DIR = Path("playlists")

# Cukup masukkan raw URL m3u/m3u8 di sini.
# Output file akan otomatis disamakan dengan nama file di URL-nya.
PLAYLIST_SOURCES = [
    "https://github.com/doms9/iptv/raw/refs/heads/default/M3U8/events.m3u8",
    "https://github.com/sm-monirulislam/SM-Live-TV/raw/refs/heads/main/World_Cup.m3u",
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

def is_stream_playable(session: requests.Session, url: str, headers: dict = None) -> bool:
    headers = headers or {}
    headers.setdefault("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")

    # Ambil ekstensi untuk deteksi tipe DASH
    parsed_url = urlparse(url)
    is_dash = parsed_url.path.endswith(".mpd")

    # 1. HEAD Request (Fast Path) - Dilewati jika DASH karena DASH wajib body-sniff
    if not is_dash:
        try:
            response = session.head(url, headers=headers, timeout=TIMEOUT, allow_redirects=True)
            if 200 <= response.status_code < 300:
                content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
                if content_type in VALID_CONTENT_TYPES and content_type != "application/octet-stream":
                    return True
        except requests.exceptions.Timeout:
            # Jika timeout pada HEAD, coba berikan kesempatan kedua di bawah (GET)
            pass
        except requests.RequestException:
            return False

    # 2. Fallback ke GET Stream + Body-sniff (Dengan 1x Retry khusus Timeout)
    for attempt in range(2):
        try:
            with closing(
                session.get(url, headers=headers, timeout=TIMEOUT, stream=True, allow_redirects=True)
            ) as response:
                if response.status_code >= 400:
                    return False

                content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
                
                # Baca chunk pertama (8KB agar lebih aman mencakup signature DRM/HTML)
                try:
                    chunk = next(response.iter_content(chunk_size=8192), b"")
                except (StopIteration, requests.RequestException):
                    return False

                if not chunk:
                    return False

                preview = chunk.decode("utf-8", errors="ignore").lstrip()
                preview_lower = preview.lower()

                # Filter HTML page (Anti False Positive dari web error/geo-block)
                if preview_lower.startswith("<html") or "<html" in preview_lower[:200]:
                    return False

                # Validasi DASH Manifest & Deteksi DRM
                if is_dash or "application/dash+xml" in content_type:
                    if "<ContentProtection" in preview:  # Terproteksi DRM
                        return False
                    return "<MPD" in preview or "main.mpd" in url

                # Validasi M3U8/HLS Manifest & Deteksi DRM
                if preview.startswith("#EXTM3U") or preview.startswith("#EXT-X-"):
                    if "#EXT-X-KEY:METHOD=" in preview and "METHOD=NONE" not in preview:
                        return False  # DRM Protected HLS
                    return True

                # Binary stream signatures
                if chunk[:1] == b"\x47":  # MPEG-TS sync byte
                    return True
                if b"ftyp" in chunk[:32]:  # MP4 container
                    return True
                if chunk[:3] == b"ID3" or chunk[:2] == b"\xff\xfb":  # MP3/ID3
                    return True

                # Jika content-type valid tapi signature tidak dikenal, loloskan untuk hindari false-negative
                if content_type in VALID_CONTENT_TYPES:
                    return True

                return False

        except requests.exceptions.Timeout:
            if attempt == 1:
                return False  # Gagal setelah retry kedua
        except requests.RequestException:
            return False
            
    return False

def parse_m3u(lines: list[str]) -> list[dict]:
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
        elif stripped.startswith("#EXTM3U") or not stripped:
            continue
        elif stripped.startswith("#"):
            buffer_other.append(line)
        else:
            url = stripped
            headers = {}
            for opt in buffer_vlcopt:
                if opt.startswith("#EXTVLCOPT:"):
                    key_value = opt[len("#EXTVLCOPT:"):].split("=", 1)
                    if len(key_value) == 2:
                        key, value = key_value[0].lower(), key_value[1]
                        if key == "http-referrer": headers["Referer"] = value
                        elif key == "http-origin": headers["Origin"] = value
                        elif key == "http-user-agent": headers["User-Agent"] = value

            entries.append({
                "extinf": buffer_extinf,
                "other": buffer_other,
                "vlcopt": buffer_vlcopt,
                "url": url,
                "headers": headers,
            })
            buffer_extinf, buffer_other, buffer_vlcopt = [], [], []

    return entries

def process_source(session: requests.Session, url: str) -> bool:
    # Otomatis ambil nama file asli dari URL sebagai nama output
    filename = os.path.basename(urlparse(url).path)
    if not filename or not filename.endswith((".m3u", ".m3u8")):
        filename = "output_playlist.m3u"

    print(f"\n{'=' * 60}\nProcessing : {filename}\nSource URL : {url}\n{'=' * 60}")

    try:
        response = session.get(url, timeout=30)
        response.raise_for_status()
        lines = [line.rstrip() for line in response.text.splitlines()]
    except requests.RequestException as e:
        print(f"[ERROR] Gagal mengunduh source: {e}")
        return False

    entries = parse_m3u(lines)
    if not entries:
        print("[SKIP] Tidak ada entri channel yang valid.")
        return False

    # Deduplikasi berdasarkan URL (case-insensitive)
    seen_urls = set()
    unique_entries = []
    for entry in entries:
        lowered_url = entry["url"].lower()
        if lowered_url not in seen_urls:
            seen_urls.add(lowered_url)
            unique_entries.append(entry)

    print(f"Total: {len(entries)} | Unique: {len(unique_entries)}")
    print(f"Checking streams dengan {MAX_WORKERS} parallel workers...\n")

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_entry = {
            executor.submit(is_stream_playable, session, entry["url"], entry["headers"]): entry
            for entry in unique_entries
        }

        done = 0
        playable_count = 0
        total = len(unique_entries)
        
        for future in as_completed(future_to_entry):
            entry = future_to_entry[future]
            try:
                entry["playable"] = future.result()
            except Exception:
                entry["playable"] = False

            done += 1
            if entry["playable"]:
                playable_count += 1
                status = "OK "
            else:
                status = "DEAD"
            print(f"[{done}/{total}] {status} -> {entry['url']}")

    # Build output m3u (Tanpa paksaan tag EPG custom)
    output_lines = ["#EXTM3U"]
    for entry in unique_entries:
        if entry["playable"]:
            output_lines.extend(entry["extinf"])
            output_lines.extend(entry["other"])
            output_lines.extend(entry["vlcopt"])
            output_lines.append(entry["url"])

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / filename

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(output_lines) + "\n")

    print(f"\nSaved -> {output_path} ({playable_count}/{total} Alive)")
    return True

def main():
    if not PLAYLIST_SOURCES:
        print("[ERROR] PLAYLIST_SOURCES kosong.")
        sys.exit(1)

    results = {}
    
    # Inisialisasi Session untuk Connection Pooling secara global
    with requests.Session() as session:
        adapter = requests.adapters.HTTPAdapter(pool_connections=MAX_WORKERS, pool_maxsize=MAX_WORKERS)
        session.mount("http://", adapter)
        session.mount("https://", adapter)

        for url in PLAYLIST_SOURCES:
            filename = os.path.basename(urlparse(url).path) or url
            results[filename] = process_source(session, url)

    print(f"\n{'=' * 60}\nSUMMARY\n{'=' * 60}")
    for name, success in results.items():
        print(f"  {name}: {'SUCCESS' if success else 'FAILED/SKIPPED'}")

    if not any(results.values()):
        sys.exit(1)

if __name__ == "__main__":
    main()