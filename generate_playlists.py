import requests
import gzip
import json
import os
import logging
import re
import time
import random
import xml.etree.ElementTree as ET
from io import BytesIO
from datetime import datetime, timezone, timedelta

# --- Configuration ---
OUTPUT_DIR = "playlists"
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'
REQUEST_TIMEOUT = 30 

# Filter groups dari environment variable
# Format: "Anime,Kids,Movies" atau "all" (default)
ROKU_GROUP_FILTER = os.getenv('ROKU_GROUP_FILTER', 'all').strip()
if ROKU_GROUP_FILTER != 'all':
    ROKU_GROUP_FILTER = [g.strip() for g in ROKU_GROUP_FILTER.split(',')]

# Metode: 'api', 'chno', atau 'hybrid' (default)
ROKU_GROUP_METHOD = os.getenv('ROKU_GROUP_METHOD', 'hybrid').strip()

TCL_CATEGORY_FILTER = os.getenv('TCL_CATEGORY_FILTER', 'all').strip()
if TCL_CATEGORY_FILTER != 'all':
    TCL_CATEGORY_FILTER = [c.strip() for c in TCL_CATEGORY_FILTER.split(',')]

# Format: "us" / "us,gb,ca" / "all" (default: "us")
PLUTO_REGION_FILTER = os.getenv('PLUTO_REGION_FILTER', 'us').strip()
if PLUTO_REGION_FILTER != 'all':
    PLUTO_REGION_FILTER = [r.strip().lower() for r in PLUTO_REGION_FILTER.split(',')]

# Format: "Movies,Kids" / "all" (default: "all")
PLUTO_GROUP_FILTER = os.getenv('PLUTO_GROUP_FILTER', 'all').strip()
if PLUTO_GROUP_FILTER != 'all':
    PLUTO_GROUP_FILTER = [g.strip() for g in PLUTO_GROUP_FILTER.split(',')]

# Format: "us" / "us,gb,ca" / "all" (default: "us")
SAMSUNG_REGION_FILTER = os.getenv('SAMSUNG_REGION_FILTER', 'us').strip()
if SAMSUNG_REGION_FILTER != 'all':
    SAMSUNG_REGION_FILTER = [r.strip().lower() for r in SAMSUNG_REGION_FILTER.split(',')]

# Format: "Movies,Kids" / "all" (default: "all")
SAMSUNG_GROUP_FILTER = os.getenv('SAMSUNG_GROUP_FILTER', 'all').strip()
if SAMSUNG_GROUP_FILTER != 'all':
    SAMSUNG_GROUP_FILTER = [g.strip() for g in SAMSUNG_GROUP_FILTER.split(',')]

# TCL Specific Config
TCL_COUNTRY_CODE = 'US'
TCL_STATE_CODE = 'OH'
TCL_DEVICE_ID = '1776786148042-4c4uc'
TCL_BASE_URL = "https://gateway-prod.ideonow.com"
TCL_IMAGE_BASE = "https://tcl-channel-cdn.ideonow.com"
TCL_ORIGIN = "https://tcltv.plus"
TCL_EPG_URL = "https://github.com/apistech/project/raw/refs/heads/main/playlists/tcl_epg.xml"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

session = requests.Session()
session.headers.update({
    "Accept": "application/json, text/plain, */*",
    "Origin": TCL_ORIGIN,
    "Referer": f"{TCL_ORIGIN}/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
})

# --- Helper Functions ---
def fetch_url(url, is_json=True, is_gzipped=False, headers=None, stream=False, retries=3):
    headers = headers or {'User-Agent': USER_AGENT}
    for i in range(retries):
        try:
            response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT, stream=stream)
            if response.status_code == 429:
                time.sleep((i + 1) * 10 + random.uniform(0, 5))
                continue
            response.raise_for_status()
            content = response.content
            if is_gzipped:
                try:
                    with gzip.GzipFile(fileobj=BytesIO(content), mode='rb') as f:
                        content = f.read()
                    content = content.decode('utf-8')
                except:
                    content = content.decode('utf-8')
            else:
                content = content.decode('utf-8')
            return json.loads(content) if is_json else content
        except Exception as e:
            logger.warning(f"Fetch failed (attempt {i+1}): {e}")
            if i < retries - 1: time.sleep(5)
    return None

def write_m3u_file(filename, content):
    filepath = os.path.join(OUTPUT_DIR, filename)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)

def format_extinf(channel_id, tvg_id, tvg_chno, tvg_name, tvg_logo, group_title, display_name):
    chno_str = str(tvg_chno) if tvg_chno and str(tvg_chno).isdigit() else ""
    return (f'#EXTINF:-1 channel-id="{channel_id}" tvg-id="{tvg_id}" tvg-chno="{chno_str}" '
            f'tvg-name="{tvg_name.replace(chr(34), chr(39))}" tvg-logo="{tvg_logo}" '
            f'group-title="{group_title.replace(chr(34), chr(39))}",{display_name.replace(",", "")}\n')

# --- Roku ---
def generate_roku_m3u():
    data = fetch_url('https://i.mjh.nz/Roku/.channels.json', is_json=True)
    if not data: 
        logger.error("Failed to fetch Roku channel data")
        return

    # Manual override untuk channel yang sering salah
    ROKU_GROUP_OVERRIDE = {
    #    '82bd10ceb52152a7adb6bdc5d776e794': 'Sports', # NHRA TV
    #    '2deae9b4e83550f88f6776c45df08315': 'Movies', # Hi-YAH!
    #    '5e73572a69bcac238ce1a9a705050a30': 'Movies', # Hong Kong Fight Club
    #    '834e2a09799752b3be7ecaab726b7242': 'Movies', # Maverick Black Cinema
    #    '40d73ba5be775428a377908b02033b4c': 'Kids',   # BABY SHARK TV
    #    'c0de867f29485305b9197b14cd08240f': 'Kids',   # Like Nastya
    #    'b5cde121f98257329346020e2a60295a': 'Kids',   # Moonbug Kids
    #    '8e0ba996e9985beb9c5e7f7f994ddc2e': 'Kids',   # Toony Planet
    }
    
    # ROKU_GROUP_MAP harus didefinisikan SEBELUM dipake
    ROKU_GROUP_MAP = {
        # Movies
        'Action': 'Movies',
        'Fantasy': 'Movies',
        'Science Fiction': 'Movies',
        'Western': 'Western',

        # Horror
        'Dark Comedy': 'Horror',
        'Horror': 'Horror',
        'Mystery': 'Horror',
        'Paranormal': 'Horror',
        'Suspense': 'Horror',
        'Thriller': 'Horror',

        # Kids
        'Animated': 'Kids',
        'Anime': 'Kids',
        'Children-Music': 'Kids',
        'Family': 'Kids',
        'Kids': 'Kids',
        'Preschool': 'Kids',
        
        # Sports
        'Action Sports': 'Sports',
        'Artistic Gymnastics': 'Sports',
        'Auto Racing': 'Sports',
        'Auto': 'Sports',
        'Baseball': 'Sports',
        'Basketball': 'Sports',
        'Bicycle': 'Sports',
        'Billiards': 'Sports',
        'Bmx Racing': 'Sports',
        'Boat Racing': 'Sports',
        'Bodybuilding': 'Sports',
        'Boxing': 'Sports',
        'Bullfighting': 'Sports',
        'Card Games': 'Sports',
        'Drag Racing': 'Sports',
        'Football': 'Sports',
        'Golf': 'Sports',
        'Gymnastics': 'Sports',
        'Hockey': 'Sports',
        'Indoor Soccer': 'Sports',
        'Intl Soccer': 'Sports',
        'Judo': 'Sports',
        'Karate': 'Sports',
        'Martial Arts': 'Sports',
        'Mixed Martial Arts': 'Sports',
        'Motorcycle Racing': 'Sports',
        'Motorcycle': 'Sports',
        'Motorsports': 'Sports',
        'Mountain Biking': 'Sports',
        'Olympics': 'Sports',
        'Rodeo': 'Sports',
        'Rugby': 'Sports',
        'Skateboarding': 'Sports',
        'Skiing': 'Sports',
        'Snowboarding': 'Sports',
        'Soccer': 'Sports',
        'Sports Talk': 'Sports',
        'Sports': 'Sports',
        'Surfing': 'Sports',
        'Swimming': 'Sports',
        'Tennis': 'Sports',
        'Volleyball': 'Sports',
        'Wrestling': 'Sports',

        # Special
        'Special': 'Special',
        
        # Drama
        'Comedy Drama': 'Drama',
        'Crime Drama': 'Drama',
        'Docudrama': 'Drama',
        'Drama': 'Drama',
        'Romance': 'Drama',
        'Romantic Comedy': 'Drama',

        # News
        'Crime': 'Crime',
        'Law': 'Crime',
        'News': 'News',
        'Newsmagazine': 'News',
        'Politics': 'News',
        'Weather': 'Weather',
        
        # Documentary
        'Adventure': 'Documentary',
        'Animals': 'Documentary',
        'Biography': 'Documentary',
        'Computers': 'Documentary',
        'Documentary': 'Documentary',
        'Fishing': 'Documentary',
        'Gaming': 'Documentary',
        'History': 'Documentary',
        'Hunting': 'Documentary',
        'Nature': 'Documentary',
        'Outdoors': 'Documentary',
        'Science': 'Documentary',
        'Technology': 'Documentary',

        # Lifestyle
        'Art': 'Lifestyle',
        'Auction': 'Lifestyle',
        'Bus./Financial': 'Lifestyle',
        'Cooking': 'Lifestyle',
        'Educational': 'Lifestyle',
        'Environment': 'Lifestyle',
        'Fashion': 'Lifestyle',
        'Food': 'Lifestyle',
        'Health': 'Lifestyle',
        'Home Improvement': 'Lifestyle',
        'House/Garden': 'Lifestyle',
        'How-To': 'Lifestyle',
        'Medical': 'Lifestyle',
        'Shopping': 'Lifestyle',
        'Travel': 'Lifestyle',

        # Music
        'Dance': 'Music',
        'Music Talk': 'Music',
        'Music': 'Music',
        
        # Faith
        'Faith': 'Faith & Family',
        'Religious': 'Faith & Family',
        
        # Entertainment
        'Comedy': 'Entertainment',
        'Entertainment': 'Entertainment',
        'Game Show': 'Entertainment',
        'Interview': 'Entertainment',
        'Reality': 'Entertainment',
        'Sitcom': 'Entertainment',
        'Soap': 'Entertainment',
        'Standup': 'Entertainment',
        'Talk': 'Entertainment',
    }
    
    # Mapping range channel number ke kategori (fallback)
    CHNO_RANGE_MAP = [
        (100, 199, 'News'),
        (200, 270, 'Sports'),
        (271, 280, 'Entertainment'),
        (281, 325, 'Movies'),
        (326, 335, 'Entertainment'),
        (336, 361, 'Entertainment'),
        (362, 368, 'Kids'),
        (369, 390, 'Entertainment'),
        (391, 400, 'Entertainment'),
        (401, 436, 'Kids'),
        (437, 467, 'Entertainment'),
        (469, 499, 'Documentary'),
        (500, 599, 'Entertainment'),
        (600, 734, 'Entertainment'),
        (744, 799, 'Horror'),
        (800, 899, 'Entertainment'),
        (900, 999, 'Latino'),
        (1000, 1199, 'Music'),
        (4000, 4999, 'News'),
        (5000, 5999, 'Special'),
    ]
    
    def get_group_by_chno(chno):
        if not chno or not str(chno).isdigit():
            return None
        chno_int = int(chno)
        for start, end, group in CHNO_RANGE_MAP:
            if start <= chno_int <= end:
                return group
        return None
    
    def get_channel_group(ch_id, ch_name, raw_groups, chno):
        # 1. Manual override
        if ch_id in ROKU_GROUP_OVERRIDE:
            return ROKU_GROUP_OVERRIDE[ch_id]
        
        # 2. Mode CHNO
        if ROKU_GROUP_METHOD == 'chno':
            result = get_group_by_chno(chno)
            return result if result else 'Special'
        
        # 3. Mode API
        if ROKU_GROUP_METHOD == 'api':
            raw_group = raw_groups[0] if raw_groups else ''
            return ROKU_GROUP_MAP.get(raw_group, 'Special')
        
        # 4. Mode Hybrid (default)
        if ROKU_GROUP_METHOD == 'hybrid':
            raw_group = raw_groups[0] if raw_groups else ''
            api_group = ROKU_GROUP_MAP.get(raw_group, 'Special')
            
            if api_group == 'Special' or api_group == 'Entertainment':
                chno_group = get_group_by_chno(chno)
                if chno_group:
                    return chno_group
            
            return api_group if api_group != 'Special' else 'Special'
        
        return 'Special'

    channels = data.get('channels', {})

    # === DEBUG: Tampilkan semua group dari Roku API ===
    all_groups = set()
    for ch in channels.values():
        all_groups.update(ch.get('groups', []))
    logger.info(f"All Roku API groups ({len(all_groups)} unique):")
    for group in sorted(all_groups):
        logger.info(f"  '{group}'")
    # =================================================

    group_map = {}
    
    for c_id, ch in channels.items():
        raw_groups = ch.get('groups', [])
        chno = ch.get('chno')
        group = get_channel_group(c_id, ch.get('name', ''), raw_groups, chno)
        group_map.setdefault(group, []).append((c_id, ch))

    output_lines = ['#EXTM3U url-tvg="https://github.com/matthuisman/i.mjh.nz/raw/master/Roku/all.xml.gz"\n']
    
    for group in sorted(group_map.keys()):
        if ROKU_GROUP_FILTER != 'all' and group not in ROKU_GROUP_FILTER:
            continue
        
        for c_id, ch in sorted(group_map[group], key=lambda x: (int(x[1].get('chno', 0)) if str(x[1].get('chno', '')).isdigit() else 99999, x[1].get('name', '').lower())):
            output_lines.extend([
                format_extinf(c_id, c_id, ch.get('chno'), ch['name'], ch['logo'], group, ch['name']),
                f"https://jmp2.uk/rok-{c_id}.m3u8\n"
            ])
    
    if ROKU_GROUP_FILTER == 'all':
        write_m3u_file("roku_all.m3u", "".join(output_lines))
    else:
        filter_slug = "_".join(ROKU_GROUP_FILTER).lower()
        write_m3u_file(f"roku_{filter_slug}.m3u", "".join(output_lines))
    
    logger.info(f"Roku: generated {len(output_lines)-1} lines")
    logger.info(f"  Method: {ROKU_GROUP_METHOD}, Filter: {ROKU_GROUP_FILTER}")

# --- TCL Scraping Logic ---
_TCL_COLON_RE = re.compile(r'^(.+?)\s+S(\d+):\s+(.+)$', re.IGNORECASE)
_TCL_TRAILING_CODE = re.compile(r'\s+\d+$')
_TCL_DASH_RE = re.compile(r'^(.+?)\s+S(\d+)(?:\s+E(\d+))?(?:\s*[-–]\s*"?(.+?)"?\s*)?$', re.IGNORECASE)
_TCL_PLAIN_DASH_RE = re.compile(r'^(.+?)\s{1,2}-\s+(.+)$')

def parse_tcl_title(raw, api_season, api_episode):
    if not raw: return raw, api_season, api_episode, None
    s = raw.strip()
    m = _TCL_COLON_RE.match(s)
    if m: return m.group(1).strip(), int(m.group(2)), api_episode, _TCL_TRAILING_CODE.sub('', m.group(3)).strip() or None
    m = _TCL_DASH_RE.match(s)
    if m: return (m.group(1).strip(), int(m.group(2)) if m.group(2) else api_season, int(m.group(3)) if m.group(3) else api_episode, m.group(4).strip().strip('"') if m.group(4) else None)
    if api_season is None and api_episode is None:
        m = _TCL_PLAIN_DASH_RE.match(s)
        if m: return m.group(1).strip(), None, None, m.group(2).strip() or None
    return s, api_season, api_episode, None

def get_tcl_common_params():
    return {"userId": TCL_DEVICE_ID, "device_type": "web", "device_model": "web", "device_id": TCL_DEVICE_ID, "app_version": "1.0", "country_code": TCL_COUNTRY_CODE, "state_code": TCL_STATE_CODE}

def resolve_tcl_stream(bundle_id, source, media):
    payload = {"type": "channel", "bundle_id": bundle_id, "device_id": TCL_DEVICE_ID, "source": source, "stream_url": media}
    params = {"country_code": TCL_COUNTRY_CODE, "app_version": "3.2.7"}
    try:
        resp = session.post(f"{TCL_BASE_URL}/api/metadata/v1/format-stream-url", params=params, json=payload, timeout=20)
        return resp.json().get("stream_url") or media
    except: return media

def generate_tcl_m3u():
    logger.info("=== Starting TCL API scrape ===")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    try:
        livetab = session.get(f"{TCL_BASE_URL}/api/metadata/v2/livetab", params=get_tcl_common_params(), timeout=20).json()
    except Exception as e:
        logger.error(f"Failed to fetch TCL live tab: {e}")
        return

    channels_map, program_map, stubs = {}, {}, []
    now = datetime.now(timezone.utc)
    range_params = {"start": (now - timedelta(hours=4)).strftime("%Y-%m-%dT%H:%M:%SZ"), "end": (now + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")}

    for line in livetab.get("lines", []):
        cat_id, cat_name = line["id"], line.get("name", "General")
        params = get_tcl_common_params()
        params.update({"category_id": cat_id, **range_params})
        try:
            data = session.get(f"{TCL_BASE_URL}/api/metadata/v1/epg/programlist/by/category", params=params, timeout=30).json()
            for ch in data.get("channels", []):
                bid = str(ch.get("bundle_id") or ch.get("id"))
                if bid not in channels_map:
                    stream = resolve_tcl_stream(bid, ch.get("source"), ch.get("media", ""))
                    channels_map[bid] = {
                        "id": bid, "name": ch.get("name"),
                        "logo": f"{TCL_IMAGE_BASE}{ch.get('logo_color')}" if ch.get('logo_color') else "",
                        "stream": stream, "category": cat_name, "description": ch.get("description", "").strip()
                    }
                for prog in ch.get("programs", []):
                    if prog.get("id"): stubs.append((bid, prog))
        except Exception as e: 
            logger.warning(f"TCL Category {cat_id} failed: {e}")

    if stubs:
        unique_ids = set()
        for _, p in stubs:
            pid = p.get("id")
            if pid:
                pid_str = str(pid)
                unique_ids.add(pid_str)
                if ':' in pid_str:
                    parts = pid_str.split(':')
                    for length in range(1, len(parts) + 1): 
                        unique_ids.add(':'.join(parts[:length]))
        
        unique_ids = list(unique_ids)
        batch_size = 40
        for i in range(0, len(unique_ids), batch_size):
            batch = unique_ids[i:i + batch_size]
            params = get_tcl_common_params()
            params["ids"] = ",".join(batch)
            try:
                detail_resp = session.get(f"{TCL_BASE_URL}/api/metadata/v1/epg/program/detail", params=params, timeout=30).json()
                details_list = detail_resp if isinstance(detail_resp, list) else [detail_resp] if isinstance(detail_resp, dict) else []
                for det in details_list:
                    if isinstance(det, dict) and "id" in det:
                        det_id = str(det["id"])
                        program_map[det_id] = det
                        if ':' in det_id:
                            parts = det_id.split(':')
                            for length in range(1, len(parts) + 1):
                                variant = ':'.join(parts[:length])
                                program_map[variant] = det
            except Exception as e:
                logger.warning(f"Failed to fetch details for batch: {e}")

    # Filter channels berdasarkan kategori
    all_channels = list(channels_map.values())
    if TCL_CATEGORY_FILTER == 'all':
        filtered_channels = all_channels
        filter_slug = "all"
    else:
        filtered_channels = [ch for ch in all_channels if ch['category'] in TCL_CATEGORY_FILTER]
        filter_slug = "_".join(TCL_CATEGORY_FILTER).lower()
        logger.info(f"TCL: filtered {len(filtered_channels)} from {len(all_channels)} channels, categories: {TCL_CATEGORY_FILTER}")

    # Write M3U8 (filtered)
    sorted_channels = sorted(filtered_channels, key=lambda x: (x["category"].lower(), x["name"].lower()))
    m3u_filename = f"tcl_{filter_slug}.m3u8" if filter_slug != "all" else "tcl.m3u8"
    
    with open(os.path.join(OUTPUT_DIR, m3u_filename), "w", encoding="utf-8") as f:
        f.write(f'#EXTM3U x-tvg-url="{TCL_EPG_URL}"\n')
        for ch in sorted_channels:
            f.write(f'#EXTINF:-1 tvg-id="{ch["id"]}" tvg-logo="{ch["logo"]}" group-title="{ch["category"]}",{ch["name"]}\n{ch["stream"]}\n')

    # Write EPG (FULL, tidak difilter)
    root = ET.Element("tv")
    for ch in channels_map.values():  # pake full channels_map, bukan filtered
        channel_el = ET.SubElement(root, "channel", id=ch["id"])
        ET.SubElement(channel_el, "display-name").text = ch["name"]
        if ch["logo"]: 
            ET.SubElement(channel_el, "icon", src=ch["logo"])

    for bid, p in stubs:
        prog_id = str(p.get("id")) if p.get("id") else None
        detail = program_map.get(prog_id) if prog_id else None
        if not detail and prog_id and ':' in prog_id:
            parts = prog_id.split(':')
            for length in range(1, len(parts) + 1):
                variant = ':'.join(parts[:length])
                if variant in program_map:
                    detail = program_map[variant]
                    break

        start_str = p["start"].replace("-", "").replace("T", "").replace(":", "").replace("Z", " +0000")
        stop_str = p["end"].replace("-", "").replace("T", "").replace(":", "").replace("Z", " +0000")
        prog_el = ET.SubElement(root, "programme", start=start_str, stop=stop_str, channel=bid)
        
        title = p.get("title", "No Title")
        clean_title, season, episode, subtitle = parse_tcl_title(title, p.get("season"), p.get("episode"))
        ET.SubElement(prog_el, "title").text = clean_title
        if subtitle or p.get("subtitle"): 
            ET.SubElement(prog_el, "sub-title").text = subtitle or p.get("subtitle")
        
        desc = ""
        if detail and isinstance(detail.get("desc"), str) and detail["desc"].strip(): 
            desc = detail["desc"].strip()
        elif isinstance(p.get("desc"), str) and p["desc"].strip(): 
            desc = p["desc"].strip()
        elif channels_map.get(bid, {}).get("description"): 
            desc = channels_map[bid]["description"].strip()
        
        if desc:
            try: 
                ET.SubElement(prog_el, "desc").text = desc
            except: 
                pass
        if season or episode:
            ep_num = ET.SubElement(prog_el, "episode-num", system="onscreen")
            ep_num.text = f"S{season or 0:02d}E{episode or 0:02d}"
        
        rating = detail.get("rating") if detail else p.get("rating", "TV-NR")
        rating_el = ET.SubElement(prog_el, "rating", system="VCHIP")
        ET.SubElement(rating_el, "value").text = rating

    ET.ElementTree(root).write(os.path.join(OUTPUT_DIR, "tcl_epg.xml"), encoding="utf-8", xml_declaration=True)
    logger.info("=== TCL Scraper completed successfully ===")

# --- Pluto Scraping Logic ---
def generate_pluto_m3u():
    data = fetch_url('https://github.com/matthuisman/i.mjh.nz/raw/refs/heads/master/PlutoTV/.channels.json.gz', is_json=True, is_gzipped=True)
    if not data or 'regions' not in data: return

    available_regions = list(data['regions'].keys())

    # Tentukan region yang akan diproses
    if PLUTO_REGION_FILTER == 'all':
        selected_regions = available_regions
    else:
        selected_regions = [r for r in PLUTO_REGION_FILTER if r in data['regions']]
        invalid = [r for r in PLUTO_REGION_FILTER if r not in data['regions']]
        if invalid:
            logger.warning(f"Pluto: region tidak ditemukan dan dilewati: {invalid}")
        if not selected_regions:
            logger.error("Pluto: tidak ada region valid, skip.")
            return

    # Dedupe by c_id, US diprioritaskan
    # Iterasi region: US duluan jika ada, sisanya alfabetis
    us_first = sorted(selected_regions, key=lambda r: (0 if r == 'us' else 1, r))

    channels = {}  # c_id -> channel dict (deduped, US wins)
    for r_code in us_first:
        for c_id, c_info in data['regions'][r_code].get('channels', {}).items():
            if c_id not in channels:  # US sudah masuk duluan, skip duplikat
                channels[c_id] = {
                    **c_info,
                    'original_id': c_id,
                    'service_group': c_info.get('group', 'Other')
                }

    # Filter group
    if PLUTO_GROUP_FILTER != 'all':
        before = len(channels)
        channels = {k: v for k, v in channels.items() if v['service_group'] in PLUTO_GROUP_FILTER}
        logger.info(f"Pluto: group filter '{PLUTO_GROUP_FILTER}' — {len(channels)} dari {before} channel")

    # Sort: group name → channel name
    sorted_channels = sorted(
        channels.items(),
        key=lambda x: (x[1]['service_group'].lower(), x[1].get('name', '').lower())
    )

    # Tentukan nama file
    if len(selected_regions) == 1:
        region_slug = selected_regions[0]
    else:
        region_slug = 'all'

    filename = f"pluto_{region_slug}.m3u"

    # EPG URL: pakai region pertama jika single, 'all' jika multi
    epg_region = selected_regions[0] if len(selected_regions) == 1 else 'all'
    output_lines = [f'#EXTM3U url-tvg="https://github.com/matthuisman/i.mjh.nz/raw/master/PlutoTV/{epg_region}.xml.gz"\n']

    for c_id, ch in sorted_channels:
        output_lines.extend([
            format_extinf(
                c_id,
                ch['original_id'],
                ch.get('chno'),
                ch['name'],
                ch['logo'],
                ch['service_group'],
                ch['name']
            ),
            f"https://jmp2.uk/plu-{ch['original_id']}.m3u8\n"
        ])

    write_m3u_file(filename, "".join(output_lines))
    logger.info(f"Pluto: {len(sorted_channels)} channels → {filename}")
    logger.info(f"  Regions: {selected_regions}, Group filter: {PLUTO_GROUP_FILTER}")

# --- SamsungTV+ Scraping Logic ---
def generate_samsungtvplus_m3u():
    data = fetch_url('https://github.com/matthuisman/i.mjh.nz/raw/refs/heads/master/SamsungTVPlus/.channels.json.gz', is_json=True, is_gzipped=True)
    if not data or 'regions' not in data: return
    slug_template = data.get('slug', '{id}.m3u8')

    available_regions = list(data['regions'].keys())

    if SAMSUNG_REGION_FILTER == 'all':
        selected_regions = available_regions
    else:
        selected_regions = [r for r in SAMSUNG_REGION_FILTER if r in data['regions']]
        invalid = [r for r in SAMSUNG_REGION_FILTER if r not in data['regions']]
        if invalid:
            logger.warning(f"Samsung: region tidak ditemukan dan dilewati: {invalid}")
        if not selected_regions:
            logger.error("Samsung: tidak ada region valid, skip.")
            return

    # US duluan, sisanya alfabetis
    us_first = sorted(selected_regions, key=lambda r: (0 if r == 'us' else 1, r))

    channels = {}  # c_id -> channel dict (deduped, US wins)
    for r_code in us_first:
        for c_id, c_info in data['regions'][r_code].get('channels', {}).items():
            if c_id not in channels:
                channels[c_id] = {
                    **c_info,
                    'original_id': c_id,
                    'service_group': c_info.get('group', 'Other')
                }

    if SAMSUNG_GROUP_FILTER != 'all':
        before = len(channels)
        channels = {k: v for k, v in channels.items() if v['service_group'] in SAMSUNG_GROUP_FILTER}
        logger.info(f"Samsung: group filter '{SAMSUNG_GROUP_FILTER}' — {len(channels)} dari {before} channel")

    sorted_channels = sorted(
        channels.items(),
        key=lambda x: (x[1]['service_group'].lower(), x[1].get('name', '').lower())
    )

    region_slug = selected_regions[0] if len(selected_regions) == 1 else 'all'
    filename = f"samsungtvplus_{region_slug}.m3u"

    epg_region = selected_regions[0] if len(selected_regions) == 1 else 'all'
    output_lines = [f'#EXTM3U url-tvg="https://github.com/matthuisman/i.mjh.nz/raw/master/SamsungTVPlus/{epg_region}.xml.gz"\n']

    for c_id, ch in sorted_channels:
        output_lines.extend([
            format_extinf(
                c_id,
                ch['original_id'],
                ch.get('chno'),
                ch['name'],
                ch['logo'],
                ch['service_group'],
                ch['name']
            ),
            f"https://jmp2.uk/{slug_template.replace('{id}', ch['original_id'])}\n"
        ])

    write_m3u_file(filename, "".join(output_lines))
    logger.info(f"Samsung: {len(sorted_channels)} channels → {filename}")
    logger.info(f"  Regions: {selected_regions}, Group filter: {SAMSUNG_GROUP_FILTER}")

# --- Execution ---
if __name__ == "__main__":
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    generate_roku_m3u()
    generate_tcl_m3u()
    generate_pluto_m3u()
    generate_samsungtvplus_m3u()