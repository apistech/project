#!/usr/bin/env python3
"""
Samsung TV Plus M3U and EPG Generator for TiviMate
Generates static .m3u playlist and .xml EPG files for use with TiviMate IPTV player
"""

import os
import json
import gzip
import requests
from datetime import datetime
from io import BytesIO
from urllib.parse import quote

# Configuration
APP_URL = 'https://i.mjh.nz/SamsungTVPlus/.channels.json.gz'
EPG_URL = 'https://i.mjh.nz/SamsungTVPlus/{region}.xml.gz'
PLAYBACK_URL = 'https://jmp2.uk/{slug}'
TIMEOUT = (10, 30)  # connect, read timeouts
OUTPUT_DIR = 'playlists'
PLAYLIST_FILE = 'samsung_tvplus.m3u'
EPG_FILE = 'samsung_tvplus.xml'

# Default settings - can be overridden by environment variables
DEFAULT_REGIONS = os.getenv('REGIONS', 'us,ca,gb')  # comma-separated regions or 'all'
DEFAULT_GROUPS = os.getenv('GROUPS', 'Anime,Anime & Gaming,Kids,Movies,Sci-fi & Horror')  # comma-separated groups to include (empty = all)
INCLUDE_DRM = os.getenv('INCLUDE_DRM', 'false').lower() == 'true'
START_CHNO = int(os.getenv('START_CHNO', '1'))  # Starting channel number
SORT_BY = os.getenv('SORT_BY', 'chno')  # 'chno' or 'name'
SKIP_EPG = os.getenv('SKIP_EPG', 'false').lower() == 'true'

def download_and_decompress(url):
    """Download and decompress gzipped content from URL"""
    print(f"Downloading {url}...")
    response = requests.get(url, timeout=TIMEOUT)
    response.raise_for_status()
    
    # Decompress gzipped content
    with gzip.GzipFile(fileobj=BytesIO(response.content)) as gz:
        return gz.read()

def get_channel_data():
    """Download and parse Samsung TV Plus channel data"""
    json_data = download_and_decompress(APP_URL)
    return json.loads(json_data)

def get_epg_data(region='all'):
    """Download EPG data for specified region"""
    url = EPG_URL.format(region=region)
    return download_and_decompress(url)

def filter_channels(data, regions, groups):
    """Filter channels based on regions and groups"""
    if regions == ['all']:
        regions = list(data['regions'].keys())
    
    channels = {}
    for region in regions:
        if region in data['regions']:
            region_channels = data['regions'][region].get('channels', {})
            channels.update(region_channels)
    
    # Filter by groups if specified
    if groups:
        filtered_channels = {}
        for channel_id, channel in channels.items():
            if channel.get('group', '').lower() in [g.lower() for g in groups]:
                filtered_channels[channel_id] = channel
        channels = filtered_channels
    
    return channels

def generate_m3u_playlist(data, channels):
    """Generate M3U playlist content"""
    lines = ['#EXTM3U']
    
    # Sort channels
    if SORT_BY == 'name':
        sorted_channels = sorted(channels.items(), key=lambda x: (x[1].get('group', '').lower(), x[1]['name'].strip().lower()))
    else:
        sorted_channels = sorted(channels.items(), key=lambda x: (x[1].get('group', '').lower(), x[1].get('chno', 999999)))
    
    current_chno = START_CHNO
    
    for channel_id, channel in sorted_channels:
        # Skip DRM channels if not including them
        if channel.get('license_url') and not INCLUDE_DRM:
            continue
        
        name = channel['name']
        logo = channel['logo']
        group = channel['group']
        url = PLAYBACK_URL.format(slug=data['slug'].format(id=channel_id))
        
        # Add DRM indicator if applicable
        if channel.get('license_url'):
            name = f"{name} [DRM]"
        
        # Use original channel number or sequential numbering
        if channel.get('chno') and START_CHNO == 1:
            chno = channel['chno']
        else:
            chno = current_chno
            current_chno += 1
        
        # Generate M3U entry
        extinf_line = f'#EXTINF:-1 tvg-id="{channel_id}" tvg-name="{name}" tvg-logo="{logo}" group-title="{group}" tvg-chno="{chno}"'
        
        # Add DRM attributes if applicable
        if channel.get('license_url'):
            extinf_line += f' drm="true" license-url="{channel["license_url"]}"'
        
        extinf_line += f',{name}'
        
        lines.extend([extinf_line, url])
    
    return '\n'.join(lines)

def save_files(playlist_content, epg_content):
    """Save M3U and EPG files to output directory"""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    # Save M3U playlist
    playlist_path = os.path.join(OUTPUT_DIR, PLAYLIST_FILE)
    with open(playlist_path, 'w', encoding='utf-8') as f:
        f.write(playlist_content)
    print(f"Saved playlist: {playlist_path}")
    
    # Save EPG XML (only if available)
    if epg_content:
        epg_path = os.path.join(OUTPUT_DIR, EPG_FILE)
        with open(epg_path, 'wb') as f:
            f.write(epg_content)
        print(f"Saved EPG: {epg_path}")
    else:
        # Hapus file EPG lama kalo ada, biar gak dipake TiviMate
        epg_path = os.path.join(OUTPUT_DIR, EPG_FILE)
        if os.path.exists(epg_path):
            os.remove(epg_path)
            print(f"Removed old EPG file: {epg_path}")
    
    return playlist_path, epg_path if epg_content else None

def main():
    """Main execution function"""
    print("Samsung TV Plus M3U/EPG Generator for TiviMate")
    print("=" * 50)
    
    try:
        # Parse configuration
        regions = [r.strip() for r in DEFAULT_REGIONS.split(',') if r.strip()]
        groups = [g.strip() for g in DEFAULT_GROUPS.split(',') if g.strip()] if DEFAULT_GROUPS else []
        
        print(f"Regions: {regions}")
        print(f"Groups: {groups if groups else 'All'}")
        print(f"Include DRM: {INCLUDE_DRM}")
        print(f"Sort by: {SORT_BY}")
        print(f"Skip EPG: {SKIP_EPG}")
        print()
        
        # Get channel data
        data = get_channel_data()
        print(f"Downloaded channel data with {len(data.get('regions', {}))} regions")
        
        # Filter channels
        channels = filter_channels(data, regions, groups)
        print(f"Filtered to {len(channels)} channels")
        
        # Generate M3U playlist
        playlist_content = generate_m3u_playlist(data, channels)
        
        # Get EPG data (skip if SKIP_EPG=True)
        epg_content = None
        if not SKIP_EPG:
            try:
                epg_region = regions[0] if len(regions) == 1 and regions[0] != 'all' else 'all'
                epg_content = get_epg_data(epg_region)
                print(f"Downloaded EPG data for region: {epg_region}")
            except Exception as e:
                print(f"Warning: Failed to download EPG: {e}")
                epg_content = None
        else:
            print("Skipping EPG download (SKIP_EPG=true)")
        
        # Save files
        playlist_path, epg_path = save_files(playlist_content, epg_content)

        print("\nGeneration completed successfully!")
        print(f"Files saved in '{OUTPUT_DIR}' directory")
        
        # Print statistics
        playlist_lines = playlist_content.count('\n')
        channel_count = playlist_content.count('#EXTINF')
        drm_count = playlist_content.count('[DRM]')
        
        print(f"\nStatistics:")
        print(f"- Total channels: {channel_count}")
        print(f"- DRM channels: {drm_count}")
        print(f"- Playlist lines: {playlist_lines}")
        if epg_content:
            print(f"- EPG size: {len(epg_content):,} bytes")
        else:
            print(f"- EPG: Skipped/Not available")
        
    except Exception as e:
        print(f"Error: {e}")
        raise

if __name__ == '__main__':
    main()
