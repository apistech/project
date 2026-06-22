import os
import gzip
import re
import sys
import time
import xml.etree.ElementTree as ET
import requests
import io
from datetime import datetime, timezone
from typing import Optional

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

# Programme dengan stop time lebih lama dari ini akan di-prune saat load base EPG
PRUNE_OLDER_THAN_HOURS = 6

# Minimum jumlah programme yang dianggap "sehat" pasca-merge.
# Kalau di bawah ini, anggap run gagal sebagian dan jangan overwrite output lama.
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
    """Parse format XMLTV: '20250622123000 +0000' -> aware datetime."""
    if not value:
        return None
    try:
        value = value.strip()
        # XMLTV time selalu punya offset di akhir, dipisah spasi
        dt_part, _, tz_part = value.partition(" ")
        dt = datetime.strptime(dt_part, "%Y%m%d%H%M%S")
        if tz_part:
            sign = 1 if tz_part[0] == "+" else -1
            hours = int(tz_part[1:3])
            minutes = int(tz_part[3:5])
            offset = sign * (hours * 3600 + minutes * 60)
            from datetime import timedelta
            dt = dt - timedelta(seconds=offset)
        return dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def load_base_epg() -> ET.Element:
    """Load guide.xml lama (kalau ada) dan prune programme yang sudah expired,
    supaya file tidak bertumbuh tanpa batas tiap run (cron 6 jam, jalan terus)."""
    if not os.path.exists(OUTPUT_XML):
        return ET.Element("tv", {"generator-info-name": "BuddyChewChew-Combined-EPG"})

    print("Found existing guide.xml. Loading...")
    try:
        root = ET.parse(OUTPUT_XML).getroot()
        before = len(root.findall("programme"))
        cutoff = datetime.now(timezone.utc)
        cutoff = cutoff.replace(hour=cutoff.hour) - __import__("datetime").timedelta(hours=PRUNE_OLDER_THAN_HOURS)

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


def fetch_epg_elements(url: str, valid_ids: set[str]) -> tuple[list[ET.Element], list[ET.Element]]:
    filename = url.split("/")[-1]
    print(f"Processing: {filename}")

    channels: list[ET.Element] = []
    programmes: list[ET.Element] = []

    try:
        with requests.get(url, stream=True, timeout=60) as r:
            r.raise_for_status()

            bio = io.BytesIO()
            for chunk in r.iter_content(chunk_size=65536):
                bio.write(chunk)
            bio.seek(0)

        stream = gzip.GzipFile(fileobj=bio) if url.endswith(".gz") else bio
        context = ET.iterparse(stream, events=("start", "end"))

        event, root = next(context)

        for event, elem in context:
            if event != "end":
                continue

            if elem.tag == "channel":
                cid = elem.get("id")
                if cid and cid in valid_ids:
                    channels.append(elem)
                    continue
            elif elem.tag == "programme":
                cname = elem.get("channel")
                if cname and cname in valid_ids:
                    _apply_title_rewrite(elem)
                    programmes.append(elem)
                    continue

            # Hanya clear elemen yang TIDAK terpakai. Jangan root.clear() --
            # itu akan mendetach elemen yang sudah di-append ke channels/programmes.
            elem.clear()

        print(f"  -> +{len(channels)} channels, +{len(programmes)} programmes")
    except StopIteration:
        print(f"  ! Empty or invalid XML stream: {filename}")
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
        title.text = f"{cleaned_title} - {sub.text.strip()}"


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
        time.sleep(1)  # sopan-sopan ke server remote, hindari rate-limit beruntun

    final_channels = len(master_root.findall("channel"))
    final_programmes = len(master_root.findall("programme"))

    print("\nFinalizing...")
    if final_programmes < MIN_PROGRAMME_SANITY_THRESHOLD:
        print(f"  ! SANITY CHECK FAILED: only {final_programmes} programmes "
              f"(threshold {MIN_PROGRAMME_SANITY_THRESHOLD}). Aborting save to avoid "
              f"committing a degraded guide.xml.")
        sys.exit(1)

    save_epg(master_root)
    print(f"\nDone. Channels: {final_channels} | Programmes: {final_programmes}")


if __name__ == "__main__":
    main()