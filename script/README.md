# 📺 IPTV Convert & Clean

Tool all-in-one untuk membersihkan, mengkonversi, dan memvalidasi playlist IPTV — drag-and-drop ready, berbasis PowerShell 7.

---

## ✨ Fitur

- **Konversi otomatis** — TXT (format channel/genre) → M3U yang valid
- **Cleaning** — hapus duplikat URL, normalisasi grup, strip komentar & baris kosong
- **URL checker parallel** — validasi live/mati dengan HTTP GET + fallback HEAD
- **Deteksi geo-block** — identifikasi channel yang diblokir berdasarkan region (HTTP 403/451 + keyword body)
- **CDN latency ranking** — ukur median latency per domain, simpan ke file ranking
- **Gabung playlist** — merge beberapa file M3U/M3U8 menjadi satu sebelum diproses
- **Sorting fleksibel** — urutkan berdasarkan group+title, atau pertahankan urutan asli
- **Backup otomatis** — simpan salinan file asli sebelum dimodifikasi
- **Dead log** — catat semua URL mati ke file `.log` terpisah
- **Encoding-aware** — deteksi dan baca UTF-8, UTF-16LE/BE, UTF-32

---

## 📁 Struktur File

```
Convert-Clean.bat     ← Launcher utama (drag-and-drop di sini)
Convert-Clean.ps1     ← Engine utama (konversi, clean, check)
Combine-M3U.ps1       ← Helper penggabung file M3U
```

---

## ⚙️ Requirement

- **PowerShell 7+** (`pwsh`) — wajib, harus ada di PATH
- Windows (tested), bisa jalan di Linux/macOS via pwsh langsung

---

## 🚀 Cara Pakai

### Cara 1 — Drag & Drop (termudah)

Seret satu atau lebih file `.m3u`, `.m3u8`, atau `.txt` ke atas `Convert-Clean.bat`.

### Cara 2 — Command Line

```bat
Convert-Clean.bat "C:\playlist\channels.m3u"
```

Multi-file:
```bat
Convert-Clean.bat "file1.m3u" "file2.m3u8" "file3.txt"
```

### Cara 3 — Langsung via PowerShell

```powershell
pwsh -File Convert-Clean.ps1 -InputFile "playlist.m3u"
```

Dengan semua parameter:
```powershell
pwsh -File Convert-Clean.ps1 `
    -InputFile "playlist.m3u" `
    -DoCheck 1 `
    -ScanMode 2 `
    -SortMode 1 `
    -TimeoutSec 8 `
    -MaxParallel 32
```

---

## 🎛️ Parameter `Convert-Clean.ps1`

| Parameter | Default | Keterangan |
|---|---|---|
| `-InputFile` | *(wajib)* | Path ke file input (.m3u/.m3u8/.txt) |
| `-DoCheck` | `1` | `1` = periksa URL, `0` = skip |
| `-ScanMode` | `1` | `1` = Normal, `2` = Fast Geo (deteksi geo-block) |
| `-SortMode` | `1` | `1` = Sort by group+title, `2` = urutan asli |
| `-TimeoutSec` | `8` | Timeout per URL (detik) |
| `-MaxParallel` | `32` | Jumlah worker parallel untuk URL checking |

---

## 📋 Format Input yang Didukung

### M3U / M3U8
Format standar IPTV, langsung dibersihkan.

### TXT — format channel/genre
```
Indonesia,#genre#
RCTI,https://stream.example.com/rcti.m3u8
SCTV,https://stream.example.com/sctv.m3u8

Sports,#genre#
beIN Sports 1,https://stream.example.com/bein1.m3u8
```

### TXT — daftar URL
Satu URL per baris. Script akan mengunduh tiap URL lalu memprosesnya.
```
https://example.com/playlist1.m3u
https://example.com/playlist2.m3u8
```

---

## 📂 Output yang Dihasilkan

| File | Keterangan |
|---|---|
| `(nama_file).m3u` | Playlist bersih hasil proses |
| `(nama_file).m3u.bak` | Backup file asli (jika backup aktif) |
| `(nama_file)_dead.log` | Daftar URL yang tidak aktif |
| `(nama_file)_geoblocked.log` | Daftar URL yang terdeteksi geo-block |
| `(nama_file)_cdn_ranking.txt` | Ranking CDN berdasarkan median latency |
| `playlist_combined.m3u` | Hasil gabungan (mode combine) |

---

## 🔄 Mode Gabung File

Kalau drag lebih dari satu file `.m3u`/`.m3u8`, script akan menawarkan opsi untuk menggabungkannya dulu menjadi satu playlist sebelum dibersihkan.

```
[2 file M3U/M3U8 terdeteksi]
Gabungkan semua file menjadi satu playlist? (1=ya / 0=tidak) [default: 0]:
```

---

## 🌍 Deteksi Geo-Block

**Mode Normal** — cek HTTP status code:
- 403 / 451 → dianggap geo-blocked

**Mode Fast Geo** (`ScanMode=2`) — baca 8KB pertama dari response body, cari keyword:
- `not available in your region`, `geo-block`, `only available in`, `location restricted`, dll.

Channel geo-blocked **tidak dihapus dari playlist**, tapi dicatat terpisah di `_geoblocked.log`.

---

## 📊 CDN Ranking

Setelah URL check selesai, script menghitung **median latency** per root domain dan menyimpannya ke `_cdn_ranking.txt`:

```
# CDN Latency Ranking - 2025-06-15 14:23:01

akamaized.net   | 142 ms  | 38 samples
fastly.net      | 187 ms  | 12 samples
cdnstream.io    | 320 ms  | 7 samples
```

---

## ⚡ Tips

- Naikkan `-MaxParallel` (misal 64) untuk playlist besar agar lebih cepat
- Turunkan `-TimeoutSec` (misal 5) kalau banyak URL lambat yang ingin cepat-cepat dianggap mati
- Gunakan `ScanMode=2` hanya kalau perlu tahu mana yang geo-blocked; lebih lambat 2-3x
- `SortMode=2` (no sort) berguna kalau urutan channel sudah diatur manual
