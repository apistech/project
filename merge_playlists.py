import os
import re
from datetime import datetime

OUTPUT_DIR = "playlists"
RCTI_FILE = os.path.join(OUTPUT_DIR, "rctiplus.m3u")
INDIHOME_FILE = "IndihomeTV.m3u"

def extract_rcti_streams():
    """Extract stream URLs dari file rctiplus.m3u"""
    if not os.path.exists(RCTI_FILE):
        print(f"❌ RCTI file not found: {RCTI_FILE}")
        return []
    
    with open(RCTI_FILE, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Regex buat extract #EXTINF + URL (handle multiple #EXTVLCOPT lines)
    streams = []
    lines = content.split('\n')
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith('#EXTINF'):
            extinf = line
            # Kumpulin #EXTVLCOPT lines
            opts = []
            i += 1
            while i < len(lines) and lines[i].startswith('#EXTVLCOPT'):
                opts.append(lines[i])
                i += 1
            # URL line
            if i < len(lines) and lines[i].startswith('http'):
                url = lines[i]
                streams.append((extinf, opts, url))
        i += 1
    
    return streams

def merge_to_indihome():
    print("🔗 Merging RCTI+ streams to IndihomeTV.m3u...")
    
    streams = extract_rcti_streams()
    if not streams:
        print("⚠️ No RCTI+ streams found, skipping merge")
        return
    
    print(f"📺 Found {len(streams)} RCTI+ streams")
    
    # Path di root repo (bukan di playlists/)
    indihome_path = INDIHOME_FILE
    
    # Baca file IndihomeTV.m3u yang ada (kalo ada)
    if os.path.exists(indihome_path):
        with open(indihome_path, 'r', encoding='utf-8') as f:
            content = f.read()
    else:
        content = "#EXTM3U\n\n"
        print(f"📝 Creating new {indihome_path}")
    
    # Hapus section RCTI+ lama
    marker_start = "# === RCTI+ SECTION ==="
    marker_end = "# === END RCTI+ SECTION ==="
    
    if marker_start in content and marker_end in content:
        pattern = rf'{re.escape(marker_start)}.*?{re.escape(marker_end)}'
        content = re.sub(pattern, '', content, flags=re.DOTALL)
        content = content.rstrip() + "\n\n"
        print("🗑️ Removed old RCTI+ section")
    
    # Tambah section baru
    new_section = [
        marker_start,
        f"# Updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        ""
    ]
    
    for extinf, opts, url in streams:
        new_section.append(extinf)
        new_section.extend(opts)
        new_section.append(url)
        new_section.append("")
    
    new_section.append(marker_end)
    
    new_content = content + "\n".join(new_section)
    
    # Simpan di root repo
    with open(indihome_path, 'w', encoding='utf-8') as f:
        f.write(new_content)
    
    print(f"✅ Merged {len(streams)} RCTI+ channels to {indihome_path}")

if __name__ == "__main__":
    merge_to_indihome()