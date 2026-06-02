import os
import gzip
import re
import xml.etree.ElementTree as ET
import requests
import io
from typing import Optional

# ─────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────
M3U_URL = os.getenv("M3U_URL")

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "epgs")
OUTPUT_XML = os.path.join(OUTPUT_DIR, "guide.xml")
OUTPUT_GZ  = os.path.join(OUTPUT_DIR, "guide.xml.gz")

os.makedirs(OUTPUT_DIR, exist_ok=True)

TITLE_REWRITE_RULES: set[str] = {
    "NHL Hockey",
    "Live: NFL Football",
}

REMOTE_EPG_URLS = [
    "https://github.com/BuddyChewChew/tcl-playlist-generator/raw/refs/heads/main/tcl_epg.xml",
    "https://github.com/BuddyChewChew/xumo-playlist-generator/raw/refs/heads/main/playlists/xumo_epg.xml.gz",
    "https://github.com/matthuisman/i.mjh.nz/raw/refs/heads/master/PlutoTV/all.xml.gz",
    "https://github.com/matthuisman/i.mjh.nz/raw/refs/heads/master/Roku/all.xml.gz",
]


# ─────────────────────────────────────────────
# Step 1: Fetch valid tvg-ids from M3U
# ─────────────────────────────────────────────
def get_tvg_ids_from_m3u() -> Optional[set[str]]:
    if not M3U_URL:
        print("CRITICAL: M3U_URL secret not set.")
        return None

    print("Downloading M3U playlist...")
    try:
        r = requests.get(M3U_URL, timeout=30)
        r.raise_for_status()
        ids = set(re.findall(r'tvg-id="([^"]+)"', r.text))
        print(f"  → {len(ids)} unique tvg-ids found.")
        return ids
    except Exception as e:
        print(f"  ! Failed to fetch M3U: {e}")
        return None


# ─────────────────────────────────────────────
# Step 2: Load base EPG (output dari Fungsi 1 / iptv-org)
# ─────────────────────────────────────────────
def load_base_epg() -> ET.Element:
    if os.path.exists(OUTPUT_XML):
        print("Found existing guide.xml (Fungsi 1). Loading...")
        try:
            root = ET.parse(OUTPUT_XML).getroot()
            print(f"  → Loaded. Channels: {len(root.findall('channel'))}, "
                  f"Programmes: {len(root.findall('programme'))}")
            return root
        except Exception as e:
            print(f"  ! Failed to parse guide.xml: {e}. Starting fresh.")

    print("No base guide.xml found. Creating empty EPG.")
    return ET.Element("tv", {"generator-info-name": "BuddyChewChew-Combined-EPG"})


# ─────────────────────────────────────────────
# Step 3: Parse satu remote EPG, return (channels, programmes)
# ─────────────────────────────────────────────
def fetch_epg_elements(
    url: str,
    valid_ids: set[str],
) -> tuple[list[ET.Element], list[ET.Element]]:
    filename = url.split("/")[-1]
    print(f"Processing: {filename}")

    channels: list[ET.Element]   = []
    programmes: list[ET.Element] = []

    try:
        r = requests.get(url, stream=True, timeout=60)
        r.raise_for_status()

        bio = io.BytesIO()
        for chunk in r.iter_content(chunk_size=65536):
            bio.write(chunk)
        bio.seek(0)

        stream = gzip.GzipFile(fileobj=bio) if url.endswith(".gz") else bio

        # Menggunakan iterparse secara aman & membersihkan tree asal demi stabilitas data
        context = ET.iterparse(stream, events=("start", "end"))
        event, root = next(context)
        
        for event, elem in context:
            if event == "end":
                if elem.tag == "channel":
                    cid = elem.get("id")
                    if cid and cid in valid_ids:
                        channels.append(elem)
                    else:
                        elem.clear()

                elif elem.tag == "programme":
                    cname = elem.get("channel")
                    if cname and cname in valid_ids:
                        _apply_title_rewrite(elem)
                        programmes.append(elem)
                    else:
                        elem.clear()

        print(f"  → +{len(channels)} channels, +{len(programmes)} programmes")

    except Exception as e:
        print(f"  ! Error processing {filename}: {e}")

    return channels, programmes


# ─────────────────────────────────────────────
# Helper: title rewrite (subtitle append)
# ─────────────────────────────────────────────
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


# ─────────────────────────────────────────────
# Step 4: Merge ke master_root dengan dedup channel
# ─────────────────────────────────────────────
def merge_into_root(
    master_root: ET.Element,
    channels: list[ET.Element],
    programmes: list[ET.Element],
    seen_channel_ids: set[str],
) -> None:
    for ch in channels:
        cid = ch.get("id")
        if cid and cid not in seen_channel_ids:
            seen_channel_ids.add(cid)
            master_root.append(ch)

    for prog in programmes:
        master_root.append(prog)


# ─────────────────────────────────────────────
# Step 5: Simpan XML + GZ
# ─────────────────────────────────────────────
def save_epg(root: ET.Element) -> None:
    tree = ET.ElementTree(root)

    print(f"Saving {OUTPUT_XML}...")
    with open(OUTPUT_XML, "wb") as f:
        tree.write(f, encoding="utf-8", xml_declaration=True)

    print(f"Saving {OUTPUT_GZ}...")
    with gzip.open(OUTPUT_GZ, "wb") as f:
        tree.write(f, encoding="utf-8", xml_declaration=True)


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────
def main() -> None:
    valid_ids = get_tvg_ids_from_m3u()
    if not valid_ids:
        print("Aborting: valid_ids required for filtering.")
        return

    master_root = load_base_epg()

    seen_channel_ids: set[str] = {
        ch.get("id") for ch in master_root.findall("channel") if ch.get("id")
    }
    print(f"Base channel IDs tracked: {len(seen_channel_ids)}")

    print("\nInjecting remote EPG sources...")
    for url in REMOTE_EPG_URLS:
        channels, programmes = fetch_epg_elements(url, valid_ids)
        merge_into_root(master_root, channels, programmes, seen_channel_ids)

    print("\nFinalizing...")
    save_epg(master_root)

    total_ch   = len(master_root.findall("channel"))
    total_prog = len(master_root.findall("programme"))
    print(f"\n✅ Done. Channels: {total_ch} | Programmes: {total_prog}")


if __name__ == "__main__":
    main()