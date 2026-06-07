> **DISCLAIMER:** Script dan link yang tersedia di repo ini hanya untuk keperluan informasi dan edukasi pribadi. Tidak ada jaminan ketersediaan, akurasi, atau kesesuaian untuk tujuan tertentu. Gunakan dengan risiko sendiri.

---

# 📺 ApisTECH Personal Project

Koleksi playlist IPTV, EPG guide, dan script otomasi untuk keperluan pribadi.

---

## 🔗 Playlist & EPG

| Jenis | URL |
|---|---|
| **IndihomeTV** | `https://github.com/apistech/project/raw/refs/heads/main/IndihomeTV.m3u` |
| **Shortlink** | `https://bit.ly/IndihomeTV` |

> Paste URL di atas langsung ke aplikasi IPTV Player (TiviMate, Kodi, VLC, dll).

---

## ⚙️ Script

### `generate_playlists.py` FAST Service playlists generator

Generate playlists pada FAST services (Pluto TV, TCL, SAMSUNG TVPLUS, ROKU, dll)

### `generate_epg.py` — EPG Aggregator

Mengambil EPG dari beberapa sumber, filter berdasarkan `tvg-id` yang ada di playlist aktif, lalu merge menjadi satu file `guide.xml`.

**Sumber EPG:**
- iptv-org — Indonesia channel
- BuddyChewChew — FAST Service channel
- matthuisman/i.mjh.nz — FAST Service channel

---

### `script/` — Convert & Clean IPTV (Windows)

Tool lokal untuk membersihkan, mengkonversi, dan memvalidasi playlist IPTV secara manual — drag-and-drop ready, berbasis PowerShell 7.

**Requirement:** PowerShell 7+ (`pwsh`) harus ada di PATH.

#### Cara Pakai

**Drag & Drop** — seret file `.m3u`, `.m3u8`, atau `.txt` ke `Convert-Clean.bat`.

**Command line:**
```bat
Convert-Clean.bat "playlist.m3u"
Convert-Clean.bat "file1.m3u" "file2.m3u8"   ← multi-file
```

**Langsung via PowerShell:**
```powershell
pwsh -File Convert-Clean.ps1 -InputFile "playlist.m3u" -DoCheck 1 -ScanMode 1 -SortMode 1 -TimeoutSec 8 -MaxParallel 32
```

#### Fitur

| Fitur | Keterangan |
|---|---|
| Konversi TXT → M3U | Format channel/genre atau daftar URL |
| Cleaning | Hapus duplikat URL, normalisasi nama grup |
| URL checker parallel | Validasi live/mati via HTTP GET + fallback HEAD |
| Deteksi geo-block | HTTP 403/451 + keyword body scan |
| CDN latency ranking | Median latency per domain, simpan ke file |
| Gabung playlist | Merge beberapa M3U sebelum diproses |
| Backup otomatis | Simpan salinan file asli sebelum dimodifikasi |
| Dead log | Catat URL mati ke file `.log` terpisah |

#### Parameter `Convert-Clean.ps1`

| Parameter | Default | Keterangan |
|---|---|---|
| `-InputFile` | *(wajib)* | Path file input (.m3u/.m3u8/.txt) |
| `-DoCheck` | `1` | `1` = periksa URL, `0` = skip |
| `-ScanMode` | `1` | `1` = Normal, `2` = Fast Geo |
| `-SortMode` | `1` | `1` = sort group+title, `2` = urutan asli |
| `-TimeoutSec` | `8` | Timeout per URL (detik) |
| `-MaxParallel` | `32` | Jumlah worker parallel |

#### Format Input yang Didukung

**TXT — channel/genre:**
```
Indonesia,#genre#
RCTI,https://stream.example.com/rcti.m3u8
SCTV,https://stream.example.com/sctv.m3u8
```

**TXT — daftar URL** (satu URL per baris, akan diunduh lalu diproses):
```
https://example.com/playlist1.m3u
https://example.com/playlist2.m3u8
```

#### Output yang Dihasilkan

| File | Keterangan |
|---|---|
| `(nama).m3u` | Playlist bersih hasil proses |
| `(nama).m3u.bak` | Backup file asli |
| `(nama)_dead.log` | Daftar URL tidak aktif |
| `(nama)_geoblocked.log` | Daftar URL geo-blocked |
| `(nama)_cdn_ranking.txt` | Ranking CDN by median latency |
| `playlist_combined.m3u` | Hasil gabungan (mode combine) |

---

## 📡 Sumber Playlist & EPG

- [CubMu](https://www.cubmu.com/live-tv)
- [DENS TV](https://www.dens.tv/tv-local)
- [MaxStream](https://maxstream.tv/tv-channels)
- [Vidio](https://www.vidio.com/live)
- [VisionPlus](https://www.visionplus.id/webclient/)
- [iptv-org](https://github.com/iptv-org)
- [matthuisman/i.mjh.nz](https://github.com/matthuisman/i.mjh.nz)
- [BuddyChewChew](https://github.com/BuddyChewChew)

---

Kalau repo ini berguna, kasih ⭐ ya!
