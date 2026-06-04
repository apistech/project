import requests
import json
import re
import os
import sys
import uuid
import logging
from datetime import datetime

# ========== KONFIGURASI ==========
OUTPUT_DIR = "playlists"
OUTPUT_FILE = "rctiplus.m3u"
USER_AGENT = 'Mozilla/5.0'
API_KEY = 'jFFhGYfZzrEgaPIGmFOVttQzCNbvqJHb'
BASE_URL = 'https://m.rctiplus.com'
API_URL = 'https://toutatis.rctiplus.com/video/live/api/v1/live/{}/url'

# Token fallback (expired, cuma buat darurat)
FALLBACK_TOKEN = 'eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJ2aWQiOjAsInRva2VuIjoiMjM0OTM2NGE5ZTgzMjQ1NyIsInBsIjoibXdlYiIsImRldmljZV9pZCI6IjJkYmQwZDJiLWRjMTYtNGIwOS1iYTA1LWUwYjQzNzc5NDhkOSIsImx0eXBlIjoiIiwiaWF0IjoxNzcyMTU5NDMyfQ.F_CwnDc1Bpen9o7uJNTP1lCqwcHMbY48rZOftlRYLC0'

CHANNELS = [
    {"api_id": 1, "name": "RCTI", "logo": "https://static.rctiplus.id/media/300/files/fta_rcti/Channel_Logo/RCTI.png"},
    {"api_id": 2, "name": "MNCTV", "logo": "https://static.rctiplus.id/media/300/files/fta_rcti/Channel_Logo/MNCTV.png"},
    {"api_id": 3, "name": "GTV", "logo": "https://static.rctiplus.id/media/300/files/fta_rcti/Channel_Logo/GTV.png"},
    {"api_id": 4, "name": "iNews", "logo": "https://static.rctiplus.id/media/300/files/fta_rcti/Channel_Logo/iNews.png"}
]

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ========== FUNGSI ==========

def get_jwt_token(session):
    """Ambil JWT token dari cookie visitor_token"""
    try:
        resp = session.get(BASE_URL, timeout=15)
        token = session.cookies.get('visitor_token')
        
        if token:
            logger.info("✅ Mendapat token JWT baru")
            return token
        else:
            logger.warning("⚠️ Token tidak ditemukan, pakai fallback")
            return FALLBACK_TOKEN
    except Exception as e:
        logger.error(f"Gagal ambil token: {e}")
        return FALLBACK_TOKEN


def extract_stream_url(data):
    """Extract stream URL dari response JSON"""
    # Cek struktur response yang mungkin
    if isinstance(data, dict):
        # Langsung di field url
        if 'url' in data and data['url']:
            return data['url']
        # Di dalam data.url
        if 'data' in data and isinstance(data['data'], dict):
            if 'url' in data['data'] and data['data']['url']:
                return data['data']['url']
    
    # Fallback: regex
    json_str = json.dumps(data)
    match = re.search(r'https?://[^"\']+\.m3u8[^"\']*', json_str)
    if match:
        return match.group(0).replace('\\/', '/')
    
    return None


def fetch_stream_url(session, api_headers, channel, device_id):
    """Fetch stream URL untuk satu channel"""
    url = API_URL.format(channel['api_id'])
    params = {'appierid': device_id}
    
    try:
        resp = session.get(url, headers=api_headers, params=params, timeout=15)
        
        if resp.status_code != 200:
            logger.warning(f"{channel['name']}: HTTP {resp.status_code}")
            return None
        
        data = resp.json()
        stream_url = extract_stream_url(data)
        
        if stream_url:
            logger.info(f"✅ {channel['name']}: Mendapat stream")
            return stream_url
        else:
            logger.warning(f"⚠️ {channel['name']}: URL tidak ditemukan di response")
            return None
            
    except Exception as e:
        logger.error(f"❌ {channel['name']}: Error - {e}")
        return None


def generate_m3u_content(channels_data):
    """Generate konten M3U dari list channel yang berhasil"""
    lines = ['#EXTM3U']
    lines.append(f'# Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    lines.append('')
    
    for ch in channels_data:
        lines.append(f'#EXTINF:-1 tvg-id="{ch["name"]}" group-title="Nasional" tvg-logo="{ch["logo"]}",{ch["name"]}')
        lines.append(f'#EXTVLCOPT:http-referrer={BASE_URL}/')
        lines.append(f'#EXTVLCOPT:http-user-agent={USER_AGENT}')
        lines.append(ch["stream_url"])
        lines.append('')  # empty line for readability
    
    return '\n'.join(lines)


def save_m3u_file(content):
    """Simpan file M3U ke disk"""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    filepath = os.path.join(OUTPUT_DIR, OUTPUT_FILE)
    
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
    
    logger.info(f"💾 File disimpan: {filepath}")
    return filepath


def update_m3u_file():
    """Main function"""
    logger.info("🚀 Memulai RCTI+ M3U Generator")
    logger.info("=" * 50)
    
    session = requests.Session()
    
    # Step 1: Dapatkan token
    jwt_token = get_jwt_token(session)
    
    # Step 2: Siapkan headers API
    api_headers = {
        'User-Agent': USER_AGENT,
        'Accept': 'application/json, text/plain, */*',
        'Origin': BASE_URL,
        'Referer': f'{BASE_URL}/',
        'apikey': API_KEY,
        'authorization': jwt_token
    }
    
    # Step 3: Generate device ID unik
    device_id = str(uuid.uuid4())
    logger.info(f"📱 Device ID: {device_id}")
    
    # Step 4: Fetch stream untuk tiap channel
    successful_channels = []
    
    for channel in CHANNELS:
        logger.info(f"➡️ Memproses {channel['name']} (ID: {channel['api_id']})...")
        stream_url = fetch_stream_url(session, api_headers, channel, device_id)
        
        if stream_url:
            successful_channels.append({
                **channel,
                "stream_url": stream_url
            })
    
    # Step 5: Simpan hasil
    if successful_channels:
        m3u_content = generate_m3u_content(successful_channels)
        save_m3u_file(m3u_content)
        
        logger.info("=" * 50)
        logger.info(f"✅ SUKSES! {len(successful_channels)}/{len(CHANNELS)} channel berhasil")
        for ch in successful_channels:
            logger.info(f"   - {ch['name']}")
    else:
        logger.error("❌ GAGAL! Tidak ada channel yang berhasil")
        sys.exit(1)


if __name__ == "__main__":
    update_m3u_file()