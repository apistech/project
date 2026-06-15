import sys
from contextlib import closing
from pathlib import Path
import requests

TIMEOUT = 10
VALID_CONTENT_TYPES = {
    "application/vnd.apple.mpegurl",
    "application/x-mpegURL",
    "video/mp4",
    "audio/mpeg",
    "video/ts",
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
            content_type = response.headers.get("Content-Type", "").split(";")[0]
            if content_type in VALID_CONTENT_TYPES:
                return True
    except requests.RequestException:
        pass

    # 2. Fallback ke GET stream, tapi langsung close setelah dapet headers
    try:
        with closing(
            requests.get(
                url, headers=headers, timeout=TIMEOUT, stream=True, allow_redirects=True
            )
        ) as response:
            if response.status_code < 400:
                content_type = response.headers.get("Content-Type", "").split(";")[0]
                return content_type in VALID_CONTENT_TYPES
    except requests.RequestException:
        return False

    return False


def filter_m3u_playlist(input_path: str, output_path: str):
    with open(input_path, "r", encoding="utf-8") as f:
        lines = [line.rstrip() for line in f]

    output_lines = [f'#EXTM3U url-tvg="{EPG_URL}"']
    buffer_tags = []
    buffer_vlcopt = []

    for line in lines:
        if line.startswith("#EXTINF"):
            buffer_tags.append(line)
        elif line.startswith("#EXTVLCOPT"):
            buffer_vlcopt.append(line)
        elif line.strip() and not line.startswith("#"):
            url = line.strip()

            # Parsing headers dari VLC options
            headers = {}
            for opt in buffer_vlcopt:
                if opt.startswith("#EXTVLCOPT:"):
                    key_value = opt[len("#EXTVLCOPT:") :].split("=", 1)
                    if len(key_value) == 2:
                        key, value = key_value
                        key = key.lower()
                        if key == "http-referrer":
                            headers["Referer"] = value
                        elif key == "http-origin":
                            headers["Origin"] = value
                        elif key == "http-user-agent":
                            headers["User-Agent"] = value

            print(f"Checking: {url}")
            if is_stream_playable(url, headers=headers):
                print("  ✓ Playable")
                output_lines.extend(buffer_tags)
                output_lines.extend(buffer_vlcopt)
                output_lines.append(url)
            else:
                print("  ✗ Not playable")

            # Reset buffers wajib dilakukan setelah memproses URL
            buffer_tags = []
            buffer_vlcopt = []

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(output_lines) + "\n")

    print(f"\nSaved filtered playlist to: {output_path}")


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