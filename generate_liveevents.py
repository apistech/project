import sys
from contextlib import closing
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

TIMEOUT = 10
MAX_WORKERS = 16

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

EPG_URL = "https://epgshare01.online/epgshare01/epg_ripper_DUMMY_CHANNELS.xml.gz"


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


def filter_m3u_playlist(input_path: str, output_path: str):
    with open(input_path, "r", encoding="utf-8") as f:
        lines = [line.rstrip() for line in f]

    entries = parse_m3u(lines)
    print(f"Total entries: {len(entries)}")
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

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(output_lines) + "\n")

    print(f"\nPlayable: {playable_count}/{len(entries)}")
    print(f"Saved filtered playlist to: {output_path}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python generate_liveevents.py input.m3u output.m3u")
        sys.exit(1)

    input_m3u = sys.argv[1]
    output_m3u = sys.argv[2]

    if not Path(input_m3u).exists():
        print(f"Input file {input_m3u} does not exist.")
        sys.exit(1)

    filter_m3u_playlist(input_m3u, output_m3u)