import os
import gzip
import re
import xml.etree.ElementTree as ET
import requests

try:
    from lxml import etree as lxml_etree
    HAS_LXML = True
except ImportError:
    HAS_LXML = False

# Settings
NAME = "fast"
# Get URL from GitHub Secret
M3U_URL = os.getenv("M3U_URL")

# Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "epgs")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# We only define the .gz path to stay under GitHub's 100MB limit
OUTPUT_FILE_GZ = os.path.join(OUTPUT_DIR, f"{NAME}-epg.xml.gz")

URLS = [
    'https://github.com/BuddyChewChew/tcl-playlist-generator/raw/refs/heads/main/tcl_epg.xml',
    'https://github.com/BuddyChewChew/xumo-playlist-generator/raw/refs/heads/main/playlists/xumo_epg.xml.gz',
    'https://github.com/matthuisman/i.mjh.nz/raw/refs/heads/master/PlutoTV/all.xml.gz',
    'https://github.com/matthuisman/i.mjh.nz/raw/refs/heads/master/Roku/all.xml.gz',
    'https://github.com/matthuisman/i.mjh.nz/raw/refs/heads/master/SamsungTVPlus/all.xml.gz',
]

def get_tvg_ids_from_remote_m3u():
    """Downloads M3U from GitFlic and extracts tvg-id values."""
    tvg_ids = set()
    if not M3U_URL:
        print("CRITICAL: No M3U_URL secret found.")
        return None

    print(f"Downloading M3U from GitFlic...")
    try:
        response = requests.get(M3U_URL, timeout=30)
        if response.status_code != 200:
            print(f"Failed to download M3U: {response.status_code}")
            return None
        
        # Extract tvg-id="value"
        pattern = re.compile(r'tvg-id="([^"]+)"')
        matches = pattern.findall(response.text)
        for val in matches:
            tvg_ids.add(val)
            
        print(f"Successfully mapped {len(tvg_ids)} channels from your playlist.")
        return tvg_ids
    except Exception as e:
        print(f"Error fetching M3U: {e}")
        return None

def sanitize_xml_bytes(content):
    """Strip bytes that are illegal in XML 1.0 but keep valid whitespace."""
    return re.sub(rb'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', b'', content)

def parse_xml(content, url):
    """Try strict stdlib parse first, fall back to lxml recovery, then sanitize + retry."""
    # 1. Fast path: strict stdlib parse
    try:
        return ET.fromstring(content)
    except ET.ParseError:
        pass

    # 2. lxml with recover=True (handles most real-world malformed EPGs)
    if HAS_LXML:
        try:
            root_lxml = lxml_etree.fromstring(content, parser=lxml_etree.XMLParser(recover=True))
            # Convert lxml element back to stdlib ET so the rest of the script is unchanged
            return ET.fromstring(lxml_etree.tostring(root_lxml))
        except Exception:
            pass

    # 3. Strip illegal control characters and retry stdlib
    try:
        return ET.fromstring(sanitize_xml_bytes(content))
    except ET.ParseError as e:
        print(f"  ! Error: {e}")
        return None

def fetch_and_parse(url):
    try:
        print(f"Fetching EPG: {url.split('/')[-1]}")
        response = requests.get(url, timeout=60)
        if response.status_code != 200: return None
        content = response.content
        if url.endswith('.gz'):
            content = gzip.decompress(content)
        return parse_xml(content, url)
    except Exception as e:
        print(f"  ! Error: {e}")
        return None

def main():
    valid_ids = get_tvg_ids_from_remote_m3u()
    
    # SAFETY CHECK: Stop if M3U fails to prevent pulling ~400MB of data to GitHub
    if not valid_ids:
        print("Stopping process: M3U filter is required to stay under GitHub file size limits.")
        return

    master_root = ET.Element('tv', {"generator-info-name": "BuddyChewChew-Light-GZ-Only"})

    for url in URLS:
        epg_data = fetch_and_parse(url)
        if epg_data is None: continue

        for channel in epg_data.findall('channel'):
            if channel.get('id') in valid_ids:
                master_root.append(channel)

        for prog in epg_data.findall('programme'):
            if prog.get('channel') in valid_ids:
                title = prog.find('title')
                if title is not None and title.text in ['NHL Hockey', 'Live: NFL Football']:
                    sub = prog.find('sub-title')
                    if sub is not None and sub.text:
                        title.text = f"{title.text} {sub.text}"
                master_root.append(prog)

    # Write ONLY the .gz file to save space and stay under 100MB
    print(f"Saving compressed EPG to {OUTPUT_FILE_GZ}...")
    tree = ET.ElementTree(master_root)
    with gzip.open(OUTPUT_FILE_GZ, 'wb') as f:
        tree.write(f, encoding='utf-8', xml_declaration=True)
    
    print("M3U-Filtered EPG (.gz only) generation complete.")

if __name__ == "__main__":
    main()
