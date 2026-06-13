# Convert & Clean IPTV

Toolset PowerShell + Batch untuk membersihkan, memvalidasi, dan mengorganisir playlist IPTV format M3U/M3U8/TXT.

## Requirements

- Windows 10/11
- PowerShell 7+ (`pwsh`) — [Download](https://github.com/PowerShell/PowerShell/releases)

---

## File

| File | Fungsi |
|---|---|
| `Convert-Clean.bat` | Launcher utama, drag-and-drop interface |
| `Convert-Clean.ps1` | Core engine: parse, validasi URL, dedup, sort, output |
| `Combine-M3U.ps1` | Menggabungkan beberapa file playlist menjadi satu |

---

## Cara Penggunaan

### Drag & Drop (Recommended)

1. Pilih satu atau beberapa file `.m3u`, `.m3u8`, atau `.txt`
2. Drag ke `Convert-Clean.bat`
3. Ikuti menu interaktif di terminal

### Via PowerShell langsung

```powershell
pwsh -File Convert-Clean.ps1 -InputFile "playlist.m3u"
```

Parameter opsional:

```powershell
pwsh -File Convert-Clean.ps1 `
    -InputFile   "playlist.m3u" `
    -DoCheck     1  `
    -SortMode    1  `
    -TimeoutSec  8  `
    -MaxParallel 32
```

| Parameter | Default | Keterangan |
|---|---|---|
| `-InputFile` | *(wajib)* | Path file input |
| `-DoCheck` | `1` | `1` = periksa URL live, `0` = skip |
| `-SortMode` | `1` | `1` = sort by group+title, `2` = urutan asli |
| `-TimeoutSec` | `8` | Timeout per URL check (detik) |
| `-MaxParallel` | `32` | Jumlah worker paralel |

---

## Format Input yang Didukung

### M3U / M3U8
Format playlist standar IPTV:
```
#EXTM3U
#EXTINF:-1 group-title="News",CNN International
http://example.com/stream/cnn.m3u8
```

### TXT (channel/genre)
Format daftar channel dengan penanda group:
```
News,#genre#
CNN International,http://example.com/stream/cnn.m3u8
BBC World News,http://example.com/stream/bbc.m3u8

Sports,#genre#
ESPN,http://example.com/stream/espn.m3u8
```

### TXT (daftar URL)
Daftar URL playlist yang akan didownload lalu diproses:
```
http://example.com/playlist1.m3u
http://example.com/playlist2.m3u8
```

---

## Fitur

### Validasi URL Live
Setiap URL dicek secara paralel dengan logika klasifikasi:

| Status | Klasifikasi | Keterangan |
|---|---|---|
| HTTP 2xx + stream content | **Live** | Masuk output |
| HTTP 2xx + DRM markers | **Live + DRM** | Masuk output, dicatat di log |
| HTTP 200 + HTML + geo keyword | **Blocked (geo)** | Dibuang, dicatat di log |
| HTTP 403 + geo keyword di body | **Blocked (geo)** | Dibuang, dicatat di log |
| HTTP 403 tanpa geo keyword | **Blocked (auth)** | Dibuang, dicatat di log |
| HTTP 401 | **Blocked (auth)** | Dibuang, dicatat di log |
| HTTP 451 | **Blocked (geo)** | Dibuang, dicatat di log |
| Timeout / connection error / 5xx | **Dead** | Dibuang, dicatat di log |

### Deteksi DRM
Channel dengan proteksi DRM tetap dipertahankan di output (tidak dibuang), hanya dicatat di log terpisah. Deteksi dilakukan via:
- `#EXT-X-KEY:METHOD=` (HLS, kecuali `METHOD=NONE`)
- `KEYFORMAT="urn:uuid:edef8ba9..."` (Widevine UUID)
- `skd://` (FairPlay key URI)
- `<ContentProtection>` di DASH MPD
- `#KODIPROP:inputstream.adaptive.license` di metadata playlist

### Deduplication
URL identik dihapus, channel pertama yang ditemukan dipertahankan.

### Sorting
- **Mode 1** — Sort by group (A-Z), lalu title dengan natural alphanumeric sort (misal: Ch 1, Ch 2, ... Ch 10, bukan Ch 1, Ch 10, Ch 2)
- **Mode 2** — Urutan asli dipertahankan

### CDN Latency Ranking
Setelah URL check, median latency per domain dihitung dan diranking. Berguna untuk mengetahui CDN mana yang paling responsif dari lokasi kamu.

### Encoding Detection
File input dideteksi encoding-nya secara otomatis: UTF-8, UTF-8 BOM, UTF-16 LE/BE, UTF-32 LE/BE.

### Gabung Playlist
Jika drag lebih dari satu file, tersedia opsi untuk menggabungkan semua menjadi satu file `playlist_combined.m3u` sebelum diproses.

---

## Output

Semua file output disimpan di folder yang sama dengan file input.

| File | Keterangan |
|---|---|
| `<nama>.m3u` | Playlist hasil (overwrite file input) |
| `<nama>_dead.log` | URL yang tidak aktif |
| `<nama>_blocked.log` | URL yang diblokir, dengan tag `[GEO]` atau `[AUTH]` |
| `<nama>_drm.log` | Channel yang terdeteksi DRM (tetap ada di playlist) |
| `<nama>_cdn_ranking.txt` | Ranking domain berdasarkan median latency |
| `<nama>.m3u.bak` | Backup file asli (jika opsi backup diaktifkan) |

### Contoh `_blocked.log`
```
[GEO]  [News] BBC World News | http://example.com/stream/bbc.m3u8
[AUTH] [Sports] ESPN Premium  | http://example.com/stream/espn.m3u8
```

### Contoh `_cdn_ranking.txt`
```
# CDN Latency Ranking - 2025-01-15 14:32:00

akamai.net      | 112 ms  | 45 samples
cloudfront.net  | 158 ms  | 30 samples
fastly.net      | 203 ms  | 12 samples
```

---

## Tips

- **Playlist besar** — naikkan `-MaxParallel` ke 50-64 untuk mempercepat, tapi perhatikan beban CPU dan network
- **Koneksi lambat** — naikkan `-TimeoutSec` ke 15-20 supaya channel dengan stream lambat tidak salah diklasifikasikan sebagai dead
- **Skip validasi** — gunakan `-DoCheck 0` kalau hanya ingin convert format atau gabung file tanpa cek URL
- **File TXT gabungan** — kalau drag campuran `.txt` dan `.m3u` dengan opsi gabung, semua akan di-merge dulu lalu diproses sebagai satu playlist
