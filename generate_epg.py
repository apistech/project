import os
import gzip
import re
import sys
import time
import xml.etree.ElementTree as ET
import requests
from datetime import datetime, timezone, timedelta
from typing import Optional

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

PRUNE_OLDER_THAN_HOURS = 6
MIN_PROGRAMME_SANITY_THRESHOLD = 50


def get_tvg_ids_from_m3u() -> Optional[set[str]]:
    if not M3U_URL:
        print("CRITICAL: M3U_URL secret not set.")
        return None
    print("Downloading M3U playlist...")
    try:
        r = requests.get(M3U_URL, timeout=30)
        r.raise_for_status()
        ids = set(re.findall(r'tvg-id="([^"]+)"', r.text))
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


def load_base_epg() -> ET.Element:
    if not os.path.exists(OUTPUT_XML):
        return ET.Element("tv", {"generator-info-name": "BuddyChewChew-Combined-EPG"})

    print("Found existing guide.xml. Loading...")
    try:
        root = ET.parse(OUTPUT_XML).getroot()
        before = len(root.findall("programme"))
        cutoff = datetime.now(timezone.utc) - timedelta(hours=PRUNE_OLDER_THAN_HOURS)

        for prog in list(root.findall("programme")):
            stop = _parse_xmltv_time(prog.get("stop", ""))
            if stop and stop < cutoff:
                root.remove(prog)

        after = len(root.findall("programme"))
        print(f"  -> Loaded. Channels: {len(root.findall('channel'))}, "
              f"Programmes: {after} (pruned {before - after} expired)")
        return root
    except Exception as e:
        print(f"  ! Failed to parse guide.xml: {e}. Starting fresh.")
        return ET.Element("tv", {"generator-info-name": "BuddyChewChew-Combined-EPG"})


def sanitize_xml_bytes(content: bytes) -> bytes:
    """Strip bytes yang ilegal di XML 1.0 tapi pertahankan whitespace valid."""
    return re.sub(rb'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', b'', content)


def parse_xml(content: bytes, label: str) -> Optional[ET.Element]:
    """3-tier fallback: strict stdlib -> lxml recover -> sanitize + retry stdlib.
    Sama seperti update_epg.py -- ini yang menyelamatkan source FAST channel
    (i.mjh.nz) yang sering punya XML kurang strict."""
    try:
        return ET.fromstring(content)
    except ET.ParseError:
        pass

    if HAS_LXML:
        try:
            root_lxml = lxml_etree.fromstring(content, parser=lxml_etree.XMLParser(recover=True))
            return ET.fromstring(lxml_etree.tostring(root_lxml))
        except Exception:
            pass

    try:
        return ET.fromstring(sanitize_xml_bytes(content))
    except ET.ParseError as e:
        print(f"  ! Parse failed for {label}: {e}")
        return None


def fetch_epg_elements(url: str, valid_ids: set[str]) -> tuple[list[ET.Element], list[ET.Element]]:
    filename = url.split("/")[-1]
    print(f"Processing: {filename}")

    channels: list[ET.Element] = []
    programmes: list[ET.Element] = []

    try:
        r = requests.get(url, timeout=60)
        r.raise_for_status()
        content = r.content
        if url.endswith(".gz"):
            content = gzip.decompress(content)

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
    except Exception as e:
        print(f"  ! Error processing {filename}: {e}")

    return channels, programmes


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
        key = (prog.get("channel", ""), prog.get("start", ""), prog.get("stop", ""))
        if key in seen_programme_keys:
            continue
        seen_programme_keys.add(key)
        master_root.append(prog)


def save_epg(root: ET.Element) -> None:
    tree = ET.ElementTree(root)
    print(f"Saving {OUTPUT_XML}...")
    with open(OUTPUT_XML, "wb") as f:
        tree.write(f, encoding="utf-8", xml_declaration=True)
    print(f"Saving {OUTPUT_GZ}...")
    with gzip.open(OUTPUT_GZ, "wb") as f:
        tree.write(f, encoding="utf-8", xml_declaration=True)


def main() -> None:
    valid_ids = get_tvg_ids_from_m3u()
    if not valid_ids:
        print("Aborting: valid_ids required for filtering.")
        sys.exit(1)

    master_root = load_base_epg()
    seen_channel_ids = {ch.get("id") for ch in master_root.findall("channel") if ch.get("id")}
    seen_programme_keys = {
        (p.get("channel", ""), p.get("start", ""), p.get("stop", ""))
        for p in master_root.findall("programme")
    }
    print(f"Base channel IDs tracked: {len(seen_channel_ids)}")
    print(f"Base programme keys tracked: {len(seen_programme_keys)}")

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