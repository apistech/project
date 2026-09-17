import gzip
import io
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests
from defusedxml import ElementTree as DefusedET
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

load_dotenv()

try:
    from lxml import etree as lxml_etree
    HAS_LXML = True
except ImportError:
    HAS_LXML = False

M3U_URL = os.getenv("M3U_URL")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "epgs")
OUTPUT_XML = os.path.join(OUTPUT_DIR, "guide.xml")
OUTPUT_GZ = os.path.join(OUTPUT_DIR, "guide.xml.gz")

os.makedirs(OUTPUT_DIR, exist_ok=True)

TITLE_REWRITE_RULES = {"NHL Hockey", "Live: NFL Football"}
REMOTE_EPG_URLS = [
    "https://github.com/BuddyChewChew/tcl-playlist-generator/raw/refs/heads/main/tcl_epg.xml",
    "https://github.com/BuddyChewChew/xumo-playlist-generator/raw/refs/heads/main/playlists/xumo_epg.xml.gz",
    "https://github.com/matthuisman/i.mjh.nz/raw/refs/heads/master/PlutoTV/all.xml.gz",
    "https://github.com/matthuisman/i.mjh.nz/raw/refs/heads/master/Roku/all.xml.gz",
    "https://github.com/matthuisman/i.mjh.nz/raw/refs/heads/master/SamsungTVPlus/all.xml.gz",
]

MIN_PROGRAMME_SANITY_THRESHOLD = 50
MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024
MAX_DECOMPRESSED_BYTES = 200 * 1024 * 1024
REQUEST_TIMEOUT = 60
CHUNK_SIZE = 256 * 1024

_session: Optional[requests.Session] = None


def get_session() -> requests.Session:
    global _session
    if _session is not None:
        return _session
    s = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    _session = s
    return s


def get_tvg_ids_from_m3u() -> Optional[set[str]]:
    if not M3U_URL:
        print("CRITICAL: M3U_URL secret not set.")
        return None
    print("Downloading M3U playlist...")
    try:
        r = get_session().get(M3U_URL, timeout=30)
        r.raise_for_status()
        r.encoding = r.apparent_encoding or "utf-8"
        ids = set(re.findall(r'tvg-id="([^"]+)"', r.text))
        ids.discard("")
        print(f"  -> {len(ids)} unique tvg-ids found.")
        return ids
    except Exception as e:
        print(f"  ! Failed to fetch M3U: {e}")
        return None


def _parse_xmltv_time(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        value = value.strip()
        dt_part, _, tz_part = value.partition(" ")
        dt = datetime.strptime(dt_part, "%Y%m%d%H%M%S")
        if tz_part:
            sign = 1 if tz_part[0] == "+" else -1
            hours = int(tz_part[1:3])
            minutes = int(tz_part[3:5])
            offset = sign * (hours * 3600 + minutes * 60)
            dt = dt - timedelta(seconds=offset)
        return dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def sanitize_xml_bytes(content: bytes) -> bytes:
    return re.sub(rb'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', b'', content)


def parse_xml(content: bytes, label: str) -> Optional[ET.Element]:
    try:
        return DefusedET.fromstring(content)
    except Exception:
        pass

    if HAS_LXML:
        try:
            safe_parser = lxml_etree.XMLParser(
                recover=True,
                resolve_entities=False,
                no_network=True,
                huge_tree=False,
            )
            root_lxml = lxml_etree.fromstring(content, parser=safe_parser)
            if root_lxml is not None:
                return ET.fromstring(lxml_etree.tostring(root_lxml))
        except Exception:
            pass

    try:
        return ET.fromstring(sanitize_xml_bytes(content))
    except ET.ParseError as e:
        print(f"  ! Parse failed for {label}: {e}")
        return None


def _download(url: str, label: str) -> Optional[bytes]:
    try:
        r = get_session().get(url, timeout=REQUEST_TIMEOUT, stream=True)
        r.raise_for_status()

        content_length = r.headers.get("Content-Length")
        if content_length and int(content_length) > MAX_DOWNLOAD_BYTES:
            print(f"  ! {label}: Content-Length {content_length} exceeds "
                  f"{MAX_DOWNLOAD_BYTES}-byte cap. Skipping.")
            return None

        buf = io.BytesIO()
        for chunk in r.iter_content(CHUNK_SIZE):
            buf.write(chunk)
            if buf.tell() > MAX_DOWNLOAD_BYTES:
                print(f"  ! {label}: exceeds {MAX_DOWNLOAD_BYTES}-byte cap "
                      f"mid-download. Skipping.")
                return None
        return buf.getvalue()
    except Exception as e:
        print(f"  ! {label}: download failed: {e}")
        return None


def _safe_gzip_decompress(raw: bytes, label: str) -> Optional[bytes]:
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as gz:
            content = gz.read(MAX_DECOMPRESSED_BYTES + 1)
        if len(content) > MAX_DECOMPRESSED_BYTES:
            print(f"  ! {label}: decompressed size exceeds "
                  f"{MAX_DECOMPRESSED_BYTES} bytes cap. Skipping.")
            return None
        return content
    except Exception as e:
        print(f"  ! {label}: gzip decompress failed: {e}")
        return None


def _apply_title_rewrite(elem: ET.Element) -> None:
    title = elem.find("title")
    if title is None or not title.text:
        return
    cleaned_title = title.text.strip()
    if cleaned_title not in TITLE_REWRITE_RULES:
        return
    sub = elem.find("sub-title")
    if sub is not None and sub.text and sub.text.strip():
        title.text = f"{cleaned_title} {sub.text.strip()}"


def fetch_epg_elements(url: str, valid_ids: set[str]) -> tuple[list[ET.Element], list[ET.Element]]:
    filename = url.split("/")[-1]
    print(f"Processing: {filename}")

    channels: list[ET.Element] = []
    programmes: list[ET.Element] = []

    content = _download(url, filename)
    if content is None:
        return channels, programmes

    if url.endswith(".gz"):
        content = _safe_gzip_decompress(content, filename)
        if content is None:
            return channels, programmes

    epg_root = parse_xml(content, filename)
    if epg_root is None:
        print(f"  ! Skipping {filename}: unparseable after all fallbacks.")
        return channels, programmes

    for channel in epg_root.findall("channel"):
        cid = channel.get("id")
        if cid and cid in valid_ids:
            channels.append(channel)

    for prog in epg_root.findall("programme"):
        cname = prog.get("channel")
        if cname and cname in valid_ids:
            _apply_title_rewrite(prog)
            programmes.append(prog)

    print(f"  -> +{len(channels)} channels, +{len(programmes)} programmes")
    return channels, programmes


def _programme_key(prog: ET.Element) -> tuple[str, str, str]:
    ch = prog.get("channel", "")
    start = _parse_xmltv_time(prog.get("start", ""))
    stop = _parse_xmltv_time(prog.get("stop", ""))
    if start and stop:
        return (ch, start.isoformat(), stop.isoformat())
    return (ch, prog.get("start", ""), prog.get("stop", ""))


def merge_into_root(
    master_root: ET.Element,
    channels: list[ET.Element],
    programmes: list[ET.Element],
    seen_channel_ids: set[str],
    seen_programme_keys: set[tuple[str, str, str]],
) -> None:
    for ch in channels:
        cid = ch.get("id")
        if cid and cid not in seen_channel_ids:
            seen_channel_ids.add(cid)
            master_root.append(ch)

    for prog in programmes:
        key = _programme_key(prog)
        if key in seen_programme_keys:
            continue
        seen_programme_keys.add(key)
        master_root.append(prog)


def _atomic_write(path: str, write_fn) -> None:
    tmp_path = f"{path}.tmp"
    try:
        with open(tmp_path, "wb") as f:
            write_fn(f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def save_epg(root: ET.Element) -> None:
    tree = ET.ElementTree(root)
    print(f"Saving {OUTPUT_XML}...")
    _atomic_write(OUTPUT_XML, lambda f: tree.write(f, encoding="utf-8", xml_declaration=True))

    print(f"Saving {OUTPUT_GZ}...")
    def _write_gz(f) -> None:
        with gzip.GzipFile(fileobj=f, mode="wb", mtime=0) as gz:
            tree.write(gz, encoding="utf-8", xml_declaration=True)

    _atomic_write(OUTPUT_GZ, _write_gz)


def main() -> None:
    valid_ids = get_tvg_ids_from_m3u()
    if not valid_ids:
        print("Aborting: valid_ids required for filtering.")
        sys.exit(1)

    master_root = ET.Element("tv", {"generator-info-name": "BuddyChewChew-Combined-EPG"})
    seen_channel_ids: set[str] = set()
    seen_programme_keys: set[tuple[str, str, str]] = set()

    print("\nInjecting remote EPG sources...")
    for url in REMOTE_EPG_URLS:
        channels, programmes = fetch_epg_elements(url, valid_ids)
        merge_into_root(master_root, channels, programmes, seen_channel_ids, seen_programme_keys)
        time.sleep(1)

    final_channels = len(master_root.findall("channel"))
    final_programmes = len(master_root.findall("programme"))

    print("\nFinalizing...")
    if final_programmes < MIN_PROGRAMME_SANITY_THRESHOLD:
        print(f"  ! SANITY CHECK FAILED: only {final_programmes} programmes "
              f"(threshold {MIN_PROGRAMME_SANITY_THRESHOLD}). Aborting save.")
        sys.exit(1)

    save_epg(master_root)
    print(f"\nDone. Channels: {final_channels} | Programmes: {final_programmes}")


if __name__ == "__main__":
    main()
