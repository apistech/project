import os
import requests
import sys
import threading
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv
from pathlib import Path
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib.parse import unquote, urlparse

load_dotenv()

# =========================
# CONFIGURATION
# =========================
TIMEOUT = 10
FETCH_TIMEOUT = 30
MAX_WORKERS = min(32, (os.cpu_count() or 2) * 2)
OUTPUT_DIR = Path("playlists")
MIN_PLAYABLE_RATIO = 0.10


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
        "Chrome/124.0.0.0 Safari/537.36"
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
        retry = Retry(
            total=2,
            connect=2,
            read=2,
            backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET"],
        )
        adapter = HTTPAdapter(
            pool_connections=MAX_WORKERS,
            pool_maxsize=MAX_WORKERS,
            max_retries=retry,
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


def get_group_title(entry: dict) -> str:
    """Extract group-title from the first EXTINF line."""
    for line in entry.get("extinf", []):
        if line.startswith("#EXTINF"):
            match = re.search(r"(?i)\bgroup-title\s*=\s*[\"']([^\"']*)[\"']", line)
            if match:
                return match.group(1).strip()
    return ""


def get_entry_filename(entry: dict) -> str:
    """Use the URL path basename as the secondary filename sort key."""
    parsed = urlparse(entry.get("url", ""))
    filename = unquote(Path(parsed.path).name).strip()
    return filename or entry.get("url", "").strip()


def natural_sort_key(value: str) -> list[object]:
    """Case-insensitive natural sort: Channel 2 < Channel 10."""
    return [
        int(part) if part.isdigit() else part.casefold()
        for part in re.split(r"(\d+)", value)
    ]


def sort_playlist_entries(entries: list[dict]) -> list[dict]:
    """Stable natural sort: group-title, then filename."""
    return sorted(
        entries,
        key=lambda entry: (
            natural_sort_key(get_group_title(entry)),
            natural_sort_key(get_entry_filename(entry)),
        ),
    )


def _atomic_write_text(path: Path, content: str) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp_path.write_text(content, encoding="utf-8")
        tmp_path.replace(path)  # atomic on POSIX & Windows NTFS same-volume
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise


def process_source(url: str) -> bool:
    filename = get_filename_from_url(url)
    print(f"\n{'=' * 60}\nProcessing: {filename}\nURL: {url}\n{'=' * 60}")

    try:
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

        playable_entries = sort_playlist_entries(
            [entry for entry in entries if entry["playable"]]
        )
        playable_count = len(playable_entries)

        ratio = playable_count / len(entries) if entries else 0
        if ratio < MIN_PLAYABLE_RATIO:
            print(
                f"[SANITY CHECK FAILED] {filename}: only {playable_count}/{len(entries)} "
                f"playable ({ratio:.0%}, threshold {MIN_PLAYABLE_RATIO:.0%}). "
                f"Refusing to overwrite existing output."
            )
            return False

        for entry in playable_entries:
            output.extend(entry["extinf"])
            output.extend(entry["vlcopt"])
            output.extend(entry["other"])
            output.append(entry["url"])

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        out_path = OUTPUT_DIR / filename
        _atomic_write_text(out_path, "\n".join(output) + "\n")

        print(f"\nPlayable: {playable_count}/{len(entries)}")
        print("Sorted: group-title -> filename")
        print(f"Saved: {out_path}")
        return True

    except Exception as e:
        print(f"[ERROR] {filename}: unhandled exception: {e}")
        return False


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