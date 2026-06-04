import os
import re
from datetime import datetime

OUTPUT_DIR = "playlists"
RCTI_FILE = "playlists/rctiplus.m3u"
INDIHOME_FILE = "IndihomeTV.m3u"

def extract_rcti_streams():
    """Extract stream URLs dari file rctiplus.m3u"""
    if not os.path.exists(RCTI_FILE):
        return []
    
    with open(RCTI_FILE, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Regex buat extract #EXTINF + URL
    pattern = r'(#EXTINF[^\n]+)\n(#EXTVLCOPT[^\n]*\n)*(#EXTVLCOPT[^\n]*\n)?(https?://[^\n]+)'
    matches = re.findall(pattern, content, re.MULTILINE)
    
    streams = []
    for match in matches:
        extinf = match[0]
        url = match[3]
        streams.append((extinf, url))
    
    return streams

def merge_to_indihome():
    streams = extract_rcti_streams()
    if not streams:
        print("No RCTI+ streams found")
        return
    
    indihome_path = INDIHOME_FILE
    if not os.path.exists(indihome_path):
        with open(indihome_path, 'w', encoding='utf-8') as f:
            f.write("#EXTM3U\n\n")
    
    with open(indihome_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Hapus section RCTI+ lama
    marker_start = "# === RCTI+ SECTION ==="
    marker_end = "# === END RCTI+ SECTION ==="
    
    if marker_start in content and marker_end in content:
        pattern = rf'{re.escape(marker_start)}.*?{re.escape(marker_end)}'
        content = re.sub(pattern, '', content, flags=re.DOTALL)
        content = content.rstrip() + "\n\n"
    
    # Tambah section baru
    new_section = [marker_start, f"# Updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", ""]
    for extinf, url in streams:
        new_section.append(extinf)
        new_section.append(url)
        new_section.append("")
    new_section.append(marker_end)
    
    new_content = content + "\n".join(new_section)
    
    with open(indihome_path, 'w', encoding='utf-8') as f:
        f.write(new_content)
    
    print(f"✅ Merged {len(streams)} RCTI+ channels to {indihome_path}")

if __name__ == "__main__":
    merge_to_indihome()