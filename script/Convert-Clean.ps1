<#
.SYNOPSIS
    Convert & Clean IPTV Playlist
.DESCRIPTION
    - Konversi TXT ke M3U
    - Bersihkan file M3U/M3U8
    - Periksa URL live (parallel) + ukur latency
    - Deteksi geo-block & auth-block -> _blocked.log
    - Menjaga channel DRM tetap LIVE & mencatat _drm.log
    - Ranking CDN tercepat -> simpan ke file
    - Hapus duplikat berdasarkan URL
    - Sorting group/none (Urutan asli dipertahankan presisi)
#>

#Requires -Version 7

param(
    [Parameter(Mandatory)]
    [string]$InputFile,
    
    [int]$TimeoutSec  = 8,
    [int]$MaxParallel = 32,
    [int]$DoCheck     = 1,

    [ValidateSet("1", "2")]
    [string]$SortMode = "1"
)

# =========================
# DETEKSI ENCODING FILE
# =========================
function Get-FileEncoding {
    param([string]$Path)
    
    $bytes = [System.IO.File]::ReadAllBytes($Path)
    
    if ($bytes.Length -ge 4 -and $bytes[0] -eq 0x00 -and $bytes[1] -eq 0x00 -and $bytes[2] -eq 0xFE -and $bytes[3] -eq 0xFF) { return 'UTF-32BE' }
    if ($bytes.Length -ge 4 -and $bytes[0] -eq 0xFF -and $bytes[1] -eq 0xFE -and $bytes[2] -eq 0x00 -and $bytes[3] -eq 0x00) { return 'UTF-32LE' }
    if ($bytes.Length -ge 2 -and $bytes[0] -eq 0xFE -and $bytes[1] -eq 0xFF) { return 'UTF-16BE' }
    if ($bytes.Length -ge 2 -and $bytes[0] -eq 0xFF -and $bytes[1] -eq 0xFE) { return 'UTF-16LE' }
    if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) { return 'UTF8-BOM' }
    
    return 'UTF8'
}

function Read-FileWithEncoding {
    param([string]$Path)
    
    $enc = Get-FileEncoding -Path $Path
    Write-Host "Deteksi encoding: $enc" -ForegroundColor DarkGray
    
    switch ($enc) {
        'UTF-16LE' { return Get-Content $Path -Encoding Unicode }
        'UTF-16BE' { return Get-Content $Path -Encoding BigEndianUnicode }
        default    { return Get-Content $Path -Encoding UTF8 }
    }
}

# =========================
# DETEKSI FORMAT FILE TXT
# =========================
function Get-TxtFileType {
    param([string]$FilePath)

    $firstLines = Get-Content $FilePath -TotalCount 30 -Encoding UTF8 -ErrorAction SilentlyContinue |
                  Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
                  Select-Object -First 5

    foreach ($line in $firstLines) {
        $line = $line.Trim()
        if ($line -match '^#EXTM3U' -or $line -match '^#EXTINF') { return 'm3u' }
        if ($line -match '^https?://') { return 'url-list' }
        if ($line -match '^.+,.+') { return 'txt-genre' }
    }
    return 'txt-genre'
}

# =========================
# KONVERSI TXT KE M3U
# =========================
function Convert-TxtToM3U {
    param([string]$TxtFile, [string]$OutM3u)
    
    Write-Host "Mengkonversi TXT ke M3U..." -ForegroundColor Cyan
    
    $lines = Read-FileWithEncoding -Path $TxtFile
    $outLines = [System.Collections.Generic.List[string]]::new()
    $currentGroup = ""
    $countChannel = 0
    $countSkipped = 0
    
    $outLines.Add("#EXTM3U")
    
    foreach ($line in $lines) {
        $line = $line.Trim()
        if ($line -eq "") { continue }
        
        if ($line -match '^(.+),\s*#genre#\s*$') {
            $currentGroup = $Matches[1].Trim()
            continue
        }
        
        $parts = $line -split ',', 2
        if ($parts.Count -lt 2) { $countSkipped++; continue }
        
        $title = $parts[0].Trim() -replace '"', ''
        $url = ($parts[1].Trim() -split '#')[0].Trim()
        
        if ([string]::IsNullOrWhiteSpace($url) -or $url -notmatch '^(https?|rtmp|rtsp)://') {
            $countSkipped++
            continue
        }
        
        $extinf = '#EXTINF:-1'
        if ($currentGroup -ne "") { $extinf += " group-title=`"$currentGroup`"" }
        $extinf += ",$title"
        
        $outLines.Add($extinf)
        $outLines.Add($url)
        $countChannel++
    }
    
    $utf8NoBom = [System.Text.UTF8Encoding]::new($false)
    [System.IO.File]::WriteAllLines($OutM3u, $outLines, $utf8NoBom)
    
    Write-Host "Hasil konversi: $countChannel channel, $countSkipped skip" -ForegroundColor Green
    return $countChannel
}

# =========================
# HELPER LATENCY & CDN
# =========================
function Get-RootDomain {
    param([string]$HostName)
    if ([string]::IsNullOrWhiteSpace($HostName)) { return "unknown" }
    $parts = $HostName.Split('.')
    if ($parts.Count -ge 2) { return "$($parts[-2]).$($parts[-1])" }
    return $HostName
}

function Get-DomainLatencyRanking {
    param([System.Collections.IDictionary]$DomainLatencies)
    $result = @{}
    foreach ($domain in $DomainLatencies.Keys) {
        $latencies = $DomainLatencies[$domain] | Where-Object { $_ -gt 0 }
        if ($latencies.Count -gt 0) {
            $sorted = $latencies | Sort-Object
            $count = $sorted.Count
            if ($count % 2 -eq 0) {
                $median = [math]::Round(($sorted[$count/2 - 1] + $sorted[$count/2]) / 2)
            } else {
                $median = $sorted[([math]::Floor($count/2))]
            }
            $result[$domain] = $median
        } else { $result[$domain] = 99999 }
    }
    return $result
}

function Save-CdnRanking {
    param(
        [System.Collections.IDictionary]$DomainLatencies,
        [string]$OutputPath
    )
    $domainRanking = Get-DomainLatencyRanking -DomainLatencies $DomainLatencies
    $lines = [System.Collections.Generic.List[string]]::new()
    $lines.Add("# CDN Latency Ranking - $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')")
    $lines.Add("")
    $domainRanking.GetEnumerator() | Sort-Object Value | ForEach-Object {
        $lines.Add("$($_.Key) | $($_.Value) ms | $($DomainLatencies[$_.Key].Count) samples")
    }
    [System.IO.File]::WriteAllLines($OutputPath, $lines, [System.Text.UTF8Encoding]::new($false))
    Write-Host "CDN Ranking   : $OutputPath" -ForegroundColor DarkGray
}

# =========================
# PARSER M3U
# =========================
function Parse-M3U {
    param([string]$File)

    $indexCounter = 0
    $entries = [System.Collections.Generic.List[object]]::new()
    $buffer  = [System.Collections.Generic.List[string]]::new()
    $header  = "#EXTM3U"
    $lines   = Read-FileWithEncoding -Path $File
    
    foreach ($line in $lines) {
        $trim = $line.Trim()
        if ([string]::IsNullOrWhiteSpace($trim)) { continue }
        
        if ($trim -like '#EXTM3U*') {
            $header = $trim
            continue
        }
        
        if ($trim -notmatch '^https?://') {
            $buffer.Add($trim)
            continue
        }
        
        if ($buffer.Count -eq 0) { continue }
        
        $info = $buffer | Where-Object { $_ -like '#EXTINF*' } | Select-Object -Last 1
        if (-not $info) { $buffer.Clear(); continue }
        
        $extraTags = @($buffer | Where-Object { $_ -notlike '#EXTINF*' })
        
        $group = if ($info -match 'group-title="([^"]*)"') {
            $Matches[1].Trim()
        } else { "Unknown" }

        $title = if ($info -match ',(.+)$') { $Matches[1].Trim() } else { "Untitled" }

        $referrer = $null
        $vlcRef = $extraTags | Where-Object { $_ -match '^#EXTVLCOPT:http-referrer=(.+)$' } | Select-Object -First 1
        if ($vlcRef -and $vlcRef -match '^#EXTVLCOPT:http-referrer=(.+)$') {
            $referrer = $Matches[1].Trim()
        }
        
        $urlClean = $trim
        if ($trim -match '^(.+?)\|') {
            $urlClean = $Matches[1].Trim()
            if (-not $referrer -and $trim -match '\|Referer=(.+)$') {
                $referrer = $Matches[1].Trim()
                $extraTags += "#EXTVLCOPT:http-referrer=$referrer"
            }
        }
        
        try {
            $uri      = [System.Uri]$urlClean
            $hostName = $uri.Host
            $root     = Get-RootDomain $hostName
        }
        catch { $hostName = "unknown"; $root = "unknown" }
        
        $rawBlock = [System.Collections.Generic.List[string]]::new()
        $rawBlock.Add($info)
        foreach ($tag in $extraTags) { $rawBlock.Add($tag) }
        $rawBlock.Add($urlClean)

        $hasDrmKey = $false
        foreach ($tag in $extraTags) {
            if ($tag -match '#KODIPROP:inputstream\.adaptive\.license') {
                $hasDrmKey = $true
                break
            }
        }

        $entries.Add([PSCustomObject]@{
            Index      = $indexCounter++
            Url        = $urlClean
            Title      = $title
            Group      = $group
            Referrer   = $referrer
            Host       = $hostName
            RootDomain = $root
            RawBlock   = $rawBlock.ToArray()
            HasDRMKey  = $hasDrmKey
        })
        
        $buffer.Clear()
    }
    return $entries, $header
}

# =========================
# PEMERIKSA URL PARALLEL
# =========================
function Test-UrlsParallel {
    param(
        [array]$Entries,
        [int]$TimeoutSec,
        [int]$MaxParallel
    )

    Write-Host "Memeriksa URL... (timeout: ${TimeoutSec}s, parallel: $MaxParallel)" -ForegroundColor Yellow

    $liveList    = [System.Collections.Concurrent.ConcurrentBag[object]]::new()
    $deadList    = [System.Collections.Concurrent.ConcurrentBag[object]]::new()
    $blockedList = [System.Collections.Concurrent.ConcurrentBag[object]]::new()
    $drmList     = [System.Collections.Concurrent.ConcurrentBag[object]]::new()
    $domainLatencies = [System.Collections.Concurrent.ConcurrentDictionary[string, [System.Collections.Generic.List[long]]]]::new()

    $geoKeywords = @(
        'not available in your region',
        'not available in your country',
        'not available in this country',
        'geo-block', 'geoblocked', 'geo_block',
        'geo restricted', 'georestricted', 'region_blocked',
        'only available in',
        'outside your region',
        'location restricted',
        'unavailable in your area',
        'not available where you are',
        'not available in'
    )

    $results = $Entries | ForEach-Object -Parallel {
        $entry       = $_
        $timeoutSec  = $using:TimeoutSec
        $geoKeywords = $using:geoKeywords

        $alive       = $false
        $isDRM       = $false
        $blockReason = $null    # 'geo' | 'auth' | $null
        $latencyMs   = -1L

        function Invoke-LiveCheck {
            param(
                [string]$Url,
                [string]$Referrer,
                [int]$TimeoutSec
            )

            $handler = $null
            $client  = $null
            $req     = $null
            $resp    = $null

            try {
                $handler = [System.Net.Http.SocketsHttpHandler]::new()
                $handler.AllowAutoRedirect        = $true
                $handler.MaxAutomaticRedirections = 3
                $handler.PooledConnectionLifetime = [TimeSpan]::FromSeconds(30)
                $handler.ConnectTimeout           = [TimeSpan]::FromSeconds(5)

                $client = [System.Net.Http.HttpClient]::new($handler)
                $client.Timeout = [TimeSpan]::FromSeconds($TimeoutSec)
                $client.DefaultRequestHeaders.TryAddWithoutValidation("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)") | Out-Null

                if ($Referrer) {
                    $client.DefaultRequestHeaders.TryAddWithoutValidation("Referer", $Referrer) | Out-Null
                }

                # =========================
                # FAST PATH: coba HEAD dulu
                # =========================
                try {
                    $headReq  = [System.Net.Http.HttpRequestMessage]::new([System.Net.Http.HttpMethod]::Head, $Url)
                    $headSw   = [System.Diagnostics.Stopwatch]::StartNew()
                    $headResp = $client.SendAsync($headReq, [System.Net.Http.HttpCompletionOption]::ResponseHeadersRead).GetAwaiter().GetResult()
                    $headSw.Stop()
                    $headReq.Dispose()

                    $headCode = [int]$headResp.StatusCode
                    $headCT   = $headResp.Content.Headers.ContentType?.MediaType
                    $headResp.Dispose()

                    $isStreamCTHead = $headCT -match '^(application/vnd\.apple\.mpegurl|application/x-mpegurl|application/dash\+xml|audio/|video/|application/octet-stream)' `
                                       -or $Url -match '\.(m3u8|ts|aac|mp3|mp4|mpd)(\?|$)'

                    # HEAD sukses + content-type jelas + bukan DASH (DASH tetap perlu body-sniff utk DRM)
                    # dan bukan content-type ambigu (octet-stream tetap lanjut ke GET utk verifikasi)
                    if ($headCode -ge 200 -and $headCode -lt 300 -and $isStreamCTHead `
                        -and $headCT -notmatch 'application/dash\+xml' -and $headCT -notmatch 'application/octet-stream') {
                        $client.Dispose(); $handler.Dispose()
                        return @{
                            Success    = $true
                            FastPath   = $true
                            StatusCode = $headCode
                            LatencyMs  = $headSw.ElapsedMilliseconds
                        }
                    }
                    # HEAD ambigu/gagal/perlu body-sniff -> lanjut ke GET di bawah
                }
                catch {
                    # HEAD gak didukung server / gagal -> lanjut ke GET, jangan dianggap fatal
                }

                # =========================
                # GET stream (fallback / kasus butuh body-sniff)
                # =========================
                $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
                $req  = [System.Net.Http.HttpRequestMessage]::new([System.Net.Http.HttpMethod]::Get, $Url)
                $resp = $client.SendAsync($req, [System.Net.Http.HttpCompletionOption]::ResponseHeadersRead).GetAwaiter().GetResult()
                $stopwatch.Stop()

                # Sukses: kembalikan response (jangan dispose, biar caller bisa baca body)
                return @{
                    Success    = $true
                    FastPath   = $false
                    Response   = $resp
                    StatusCode = [int]$resp.StatusCode
                    LatencyMs  = $stopwatch.ElapsedMilliseconds
                    Transient  = $false
                    Client     = $client   # perlu tetap hidup selama body dibaca
                    Handler    = $handler
                }
            }
            catch [System.Threading.Tasks.TaskCanceledException] {
                # Timeout -> transient, layak diretry
                if ($null -ne $client)  { $client.Dispose() }
                if ($null -ne $handler) { $handler.Dispose() }
                return @{ Success = $false; Transient = $true }
            }
            catch {
                # DNS failure, connection refused, dll -> permanent, jangan retry
                if ($null -ne $client)  { $client.Dispose() }
                if ($null -ne $handler) { $handler.Dispose() }
                return @{ Success = $false; Transient = $false }
            }
            finally {
                if ($null -ne $req) { $req.Dispose() }
            }
        }

        # =========================
        # Eksekusi dengan retry selektif (hanya untuk timeout)
        # =========================
        $result = Invoke-LiveCheck -Url $entry.Url -Referrer $entry.Referrer -TimeoutSec $timeoutSec

        if (-not $result.Success -and $result.Transient) {
            # Retry sekali untuk timeout
            $result = Invoke-LiveCheck -Url $entry.Url -Referrer $entry.Referrer -TimeoutSec $timeoutSec
        }

        if ($result.Success -and $result.FastPath) {
            # =========================
            # HEAD fast-path: content-type sudah definitif, skip body-sniff
            # =========================
            $alive     = $true
            $latencyMs = $result.LatencyMs
        }
        elseif ($result.Success) {
            $resp      = $result.Response
            $code      = $result.StatusCode
            $latencyMs = $result.LatencyMs
            $client    = $result.Client
            $handler   = $result.Handler

            try {
                if ($code -eq 451) {
                    $blockReason = 'geo'
                }
                elseif ($code -eq 401) {
                    $blockReason = 'auth'
                }
                elseif ($code -eq 403) {
                    # Baca body: bedakan geo vs auth
                    $stream = $null
                    try {
                        $stream = $resp.Content.ReadAsStreamAsync().GetAwaiter().GetResult()
                        $buf = New-Object byte[] 4096
                        $bytesRead = $stream.Read($buf, 0, 4096)
                        if ($bytesRead -gt 0) {
                            $previewLower = ([System.Text.Encoding]::UTF8.GetString($buf, 0, $bytesRead)).ToLower()
                            $isGeo = $false
                            foreach ($kw in $geoKeywords) {
                                if ($previewLower.Contains($kw)) { $isGeo = $true; break }
                            }
                            $blockReason = if ($isGeo) { 'geo' } else { 'auth' }
                        }
                        else { $blockReason = 'auth' }
                    }
                    catch { $blockReason = 'auth' }
                    finally { if ($null -ne $stream) { $stream.Dispose() } }
                }
                elseif ($code -ge 200 -and $code -lt 300) {
                    $contentType = $resp.Content.Headers.ContentType?.MediaType

                    $isDashUrl  = $entry.Url -match '\.mpd(\?|$)'
                    $isDashCT   = $contentType -match 'application/dash\+xml'
                    $isStreamCT = $contentType -match '^(application/vnd\.apple\.mpegurl|application/x-mpegurl|audio/|video/|application/octet-stream)' `
                                  -or $entry.Url -match '\.(m3u8|ts|aac|mp3|mp4)(\?|$)'

                    if ($isDashUrl -or $isDashCT) {
                        # DASH: baca body untuk cek ContentProtection
                        $stream = $null
                        try {
                            $stream = $resp.Content.ReadAsStreamAsync().GetAwaiter().GetResult()
                            $buf = New-Object byte[] 8192
                            $bytesRead = $stream.Read($buf, 0, 8192)
                            if ($bytesRead -gt 0) {
                                $preview = [System.Text.Encoding]::UTF8.GetString($buf, 0, $bytesRead)
                                if ($preview -match '<ContentProtection') { $isDRM = $true }
                            }
                            $alive = $true
                        }
                        catch { $alive = $false }
                        finally { if ($null -ne $stream) { $stream.Dispose() } }
                    }
                    elseif ($isStreamCT) {
                        # Content-type sudah jelas stream, tidak perlu baca body
                        $alive = $true
                    }
                    else {
                        # Content-type ambigu: baca body untuk validasi
                        $stream = $null
                        try {
                            $stream = $resp.Content.ReadAsStreamAsync().GetAwaiter().GetResult()
                            $buf = New-Object byte[] 8192
                            $bytesRead = $stream.Read($buf, 0, 8192)

                            if ($bytesRead -gt 0) {
                                $preview      = [System.Text.Encoding]::UTF8.GetString($buf, 0, $bytesRead)
                                $previewLower = $preview.ToLower()
                                $isHtml       = $previewLower.Contains('<html') -or $contentType -match 'text/html'

                                # DRM markers HLS
                                if ($preview -match '#EXT-X-KEY:METHOD=(?!NONE)' -or
                                    $preview -match 'KEYFORMAT="urn:uuid:edef8ba9' -or
                                    $previewLower.Contains('skd://')) {
                                    $isDRM = $true
                                }

                                if ($isHtml) {
                                    $isGeo = $false
                                    foreach ($kw in $geoKeywords) {
                                        if ($previewLower.Contains($kw)) { $isGeo = $true; break }
                                    }
                                    if ($isGeo) { $blockReason = 'geo' }
                                    # HTML tanpa geo keyword -> dead (alive tetap false)
                                }
                                elseif ($buf[0] -eq 0x47) {
                                    # MPEG-TS sync byte
                                    $alive = $true
                                }
                                elseif ($bytesRead -ge 12 -and [System.Text.Encoding]::ASCII.GetString($buf, 4, 4) -eq 'ftyp') {
                                    # MP4 container (ftyp box di offset 4)
                                    $alive = $true
                                }
                                elseif (($buf[0] -eq 0x49 -and $buf[1] -eq 0x44 -and $buf[2] -eq 0x33) -or
                                        ($buf[0] -eq 0xFF -and $buf[1] -eq 0xFB)) {
                                    # ID3 tag / MP3 frame sync
                                    $alive = $true
                                }
                                else {
                                    # Bukan HTML, gak match signature dikenal -> tetap anggap alive
                                    # (signature unknown != dead; hindari false negative)
                                    $alive = $true
                                }
                            }
                        }
                        catch { $alive = $false }
                        finally { if ($null -ne $stream) { $stream.Dispose() } }
                    }
                }
                # Semua status lain (4xx selain 401/403/451, 5xx) -> dead (default)
            }
            finally {
                if ($null -ne $resp)    { $resp.Dispose() }
                if ($null -ne $client)  { $client.Dispose() }
                if ($null -ne $handler) { $handler.Dispose() }
            }
        }
        # Kalau $result.Success = $false (permanent error / timeout setelah retry) -> alive tetap false, dead

        [PSCustomObject]@{
            Entry       = $entry
            Alive       = $alive
            IsDRM       = $isDRM -or $entry.HasDRMKey
            BlockReason = $blockReason
            LatencyMs   = $latencyMs
            Domain      = $entry.RootDomain
        }
    } -ThrottleLimit $MaxParallel

    foreach ($r in $results) {
        if ($r.Alive) {
            $liveList.Add($r.Entry)
            if ($r.IsDRM) { $drmList.Add($r.Entry) }

            $list = $domainLatencies.GetOrAdd($r.Domain, [System.Collections.Generic.List[long]]::new())
            if ($r.LatencyMs -gt 0) {
                [System.Threading.Monitor]::Enter($list)
                try { $list.Add($r.LatencyMs) } finally { [System.Threading.Monitor]::Exit($list) }
            }
        }
        elseif ($null -ne $r.BlockReason) {
            $blockedList.Add([PSCustomObject]@{ Entry = $r.Entry; Reason = $r.BlockReason })
        }
        else {
            $deadList.Add($r.Entry)
        }
    }

    $geoCount  = @($blockedList | Where-Object { $_.Reason -eq 'geo'  }).Count
    $authCount = @($blockedList | Where-Object { $_.Reason -eq 'auth' }).Count

    Write-Host "Aktif          : $($liveList.Count)"  -ForegroundColor Green
    Write-Host "Blocked (geo)  : $geoCount"            -ForegroundColor Yellow
    Write-Host "Blocked (auth) : $authCount"            -ForegroundColor DarkYellow
    Write-Host "DRM protected  : $($drmList.Count)"   -ForegroundColor Magenta
    Write-Host "Mati           : $($deadList.Count)"  -ForegroundColor Red

    $normalHash = @{}
    foreach ($k in $domainLatencies.Keys) { $normalHash[$k] = $domainLatencies[$k] }

    return [PSCustomObject]@{
        Live      = @($liveList.ToArray())
        Dead      = @($deadList.ToArray())
        Blocked   = @($blockedList.ToArray())
        Drm       = @($drmList.ToArray())
        Latencies = $normalHash
    }
}

# =========================
# PROSES UTAMA
# =========================

if (-not (Test-Path $InputFile)) {
    Write-Host "ERROR: File tidak ditemukan: $InputFile" -ForegroundColor Red
    exit 1
}

$dir       = [System.IO.Path]::GetDirectoryName((Resolve-Path $InputFile))
$baseName  = [System.IO.Path]::GetFileNameWithoutExtension($InputFile)
$extension = [System.IO.Path]::GetExtension($InputFile).ToLowerInvariant()

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "     CONVERT & CLEAN - All in One"       -ForegroundColor White
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Input file    : $([System.IO.Path]::GetFileName($InputFile))" -ForegroundColor Yellow

$m3uFile = $InputFile

if ($extension -eq ".txt") {
    $txtType = Get-TxtFileType -FilePath $InputFile
    switch ($txtType) {
        'm3u' {
            $m3uFile = $InputFile
            Write-Host "Mode          : TXT (format M3U) -> Clean Only" -ForegroundColor Green
        }
        'txt-genre' {
            $m3uFile = Join-Path $dir "${baseName}.m3u"
            Write-Host "Mode          : TXT (channel/genre) -> Konversi M3U + Clean" -ForegroundColor Green
            if (Test-Path $m3uFile) { Copy-Item $m3uFile "$m3uFile.old" -Force }
            $channelCount = Convert-TxtToM3U -TxtFile $InputFile -OutM3u $m3uFile
            if ($channelCount -eq 0) {
                Write-Host "ERROR: Tidak ada channel valid" -ForegroundColor Red
                exit 1
            }
        }
        'url-list' {
            Write-Host "Mode          : TXT (daftar URL)" -ForegroundColor Green
            $urls = Get-Content $InputFile -Encoding UTF8 | Where-Object { $_ -match '^https?://' }
            $konfirmasi = Read-Host "Download dan proses semua URL? (Y/tidak) [default: Y]"
            if ($konfirmasi -ne "" -and $konfirmasi -notmatch '^[Yy]') {
                Write-Host "Dibatalkan." -ForegroundColor DarkYellow
                exit 0
            }
            foreach ($url in $urls) {
                $dlName = [System.IO.Path]::GetFileName(([System.Uri]$url).LocalPath)
                if ([string]::IsNullOrWhiteSpace($dlName) -or $dlName -notmatch '\.(m3u|m3u8|txt)$') {
                    $dlName = "${baseName}_download.m3u"
                }
                $dlPath = Join-Path $dir $dlName
                try {
                    Invoke-WebRequest -Uri $url -OutFile $dlPath -TimeoutSec 30 -ErrorAction Stop
                    & $PSCommandPath -InputFile $dlPath -TimeoutSec $TimeoutSec -MaxParallel $MaxParallel -DoCheck $DoCheck -SortMode $SortMode
                } catch { continue }
            }
            exit 0
        }
    }
}
elseif ($extension -eq ".m3u")  { Write-Host "Mode          : M3U Clean Only"  -ForegroundColor Green }
elseif ($extension -eq ".m3u8") { Write-Host "Mode          : M3U8 Clean Only" -ForegroundColor Green }
else {
    Write-Host "ERROR: Tipe file tidak didukung" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "Memuat file M3U..." -ForegroundColor Yellow
$entries, $m3uHeader = Parse-M3U $m3uFile
Write-Host "Entry ditemukan : $($entries.Count)" -ForegroundColor Cyan

# Init semua variabel sebelum branching
$liveEntries     = @()
$deadList        = @()
$blockedList     = @()
$drmList         = @()
$domainLatencies = @{}

if ($DoCheck -eq 1) {
    $checkResult = Test-UrlsParallel -Entries $entries -TimeoutSec $TimeoutSec -MaxParallel $MaxParallel

    $liveEntries     = $checkResult.Live
    $deadList        = $checkResult.Dead
    $blockedList     = $checkResult.Blocked
    $drmList         = $checkResult.Drm
    $domainLatencies = $checkResult.Latencies

    if ($domainLatencies.Count -gt 0) {
        $rankingFile = Join-Path $dir "${baseName}_cdn_ranking.txt"
        Save-CdnRanking -DomainLatencies $domainLatencies -OutputPath $rankingFile
        Write-Host ""
        Write-Host "5 CDN tercepat (median latency):" -ForegroundColor Yellow
        $domainRanking = Get-DomainLatencyRanking -DomainLatencies $domainLatencies
        $domainRanking.GetEnumerator() | Sort-Object Value | Select-Object -First 5 | ForEach-Object {
            $ms      = $_.Value
            $display = if ($ms -ge 10000) { "> 10s" } else { "$ms ms" }
            Write-Host "  $($_.Key) : $display" -ForegroundColor DarkGray
        }
    }

    if ($blockedList.Count -gt 0) {
        $blockedFile  = Join-Path $dir "${baseName}_blocked.log"
        $blockedLines = $blockedList | ForEach-Object {
            $tag = if ($_.Reason -eq 'geo') { 'GEO' } else { 'AUTH' }
            "[$tag] [$($_.Entry.Group)] $($_.Entry.Title) | $($_.Entry.Url)"
        }
        [System.IO.File]::WriteAllLines($blockedFile, $blockedLines, [System.Text.Encoding]::UTF8)
        Write-Host "Log blocked   : $blockedFile" -ForegroundColor DarkGray
    }

    if ($drmList.Count -gt 0) {
        $drmLogFile = Join-Path $dir "${baseName}_drm.log"
        [System.IO.File]::WriteAllLines($drmLogFile, ($drmList | ForEach-Object {
            "[$($_.Group)] $($_.Title) | $($_.Url)"
        }), [System.Text.Encoding]::UTF8)
        Write-Host "Log DRM       : $drmLogFile" -ForegroundColor DarkGray
    }
}
else {
    $liveEntries = $entries
    Write-Host "Pemeriksaan URL : dilewati" -ForegroundColor DarkGray
}

# =========================
# DEDUP
# =========================
Write-Host ""
Write-Host "Menghapus duplikat..." -ForegroundColor Yellow

$seenUrls     = [System.Collections.Generic.HashSet[string]]::new()
$uniqueEntries = [System.Collections.Generic.List[object]]::new()
foreach ($entry in $liveEntries) {
    if ($seenUrls.Add($entry.Url)) { $uniqueEntries.Add($entry) }
}
$dupRemoved = $liveEntries.Count - $uniqueEntries.Count
Write-Host "Duplikat      : $dupRemoved dihapus (berdasarkan URL)" -ForegroundColor Cyan

# =========================
# SORTING
# =========================
Write-Host ""
Write-Host "Mode sorting    :" -ForegroundColor Yellow

if ($SortMode -eq "1") {
    $naturalSort = {
        param($a, $b)
        $groupCompare = [System.String]::Compare($a.Group, $b.Group, [System.StringComparison]::OrdinalIgnoreCase)
        if ($groupCompare -ne 0) { return $groupCompare }

        $chunksA = [regex]::Matches($a.Title, '\d+|\D+') | ForEach-Object { $_.Value }
        $chunksB = [regex]::Matches($b.Title, '\d+|\D+') | ForEach-Object { $_.Value }
        $count   = [math]::Min($chunksA.Count, $chunksB.Count)

        for ($i = 0; $i -lt $count; $i++) {
            $numA = 0; $numB = 0
            if ([int]::TryParse($chunksA[$i], [ref]$numA) -and [int]::TryParse($chunksB[$i], [ref]$numB)) {
                $cmp = $numA.CompareTo($numB)
                if ($cmp -ne 0) { return $cmp }
            }
            else {
                $cmp = [System.String]::Compare($chunksA[$i], $chunksB[$i], [System.StringComparison]::OrdinalIgnoreCase)
                if ($cmp -ne 0) { return $cmp }
            }
        }
        return $chunksA.Count.CompareTo($chunksB.Count)
    }

    $sorted    = [System.Collections.Generic.List[object]]::new($uniqueEntries)
    $sorted.Sort($naturalSort)
    $sortLabel = "group -> title (natural alphanumeric)"
    Write-Host "  Group kemudian title (1, 2, 3... 100)" -ForegroundColor DarkGray
}
else {
    $sorted    = $uniqueEntries | Sort-Object Index
    $sortLabel = "none (original order)"
    Write-Host "  Tanpa sorting - urutan asli dipertahankan" -ForegroundColor DarkGray
}
Write-Host "Sorting diterapkan: $sortLabel" -ForegroundColor Cyan

# =========================
# OUTPUT
# =========================
Write-Host ""
Write-Host "Menulis output..." -ForegroundColor Yellow

$out = [System.Collections.Generic.List[string]]::new()
$out.Add($(if ($m3uHeader) { $m3uHeader } else { "#EXTM3U" }))

foreach ($entry in $sorted) {
    foreach ($line in $entry.RawBlock) { $out.Add($line) }
}

[System.IO.File]::WriteAllLines($m3uFile, $out, [System.Text.UTF8Encoding]::new($false))
Write-Host "Output file   : $m3uFile" -ForegroundColor Cyan

if ($deadList.Count -gt 0) {
    $logFile = Join-Path $dir "${baseName}_dead.log"
    [System.IO.File]::WriteAllLines($logFile, ($deadList | ForEach-Object {
        "[$($_.Group)] $($_.Title) | $($_.Url)"
    }), [System.Text.Encoding]::UTF8)
    Write-Host "Log mati      : $logFile" -ForegroundColor DarkGray
}

# =========================
# SUMMARY
# =========================
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "SELESAI"                                  -ForegroundColor White
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Original      : $($entries.Count)"
if ($DoCheck -eq 1) {
    $geoCount  = @($blockedList | Where-Object { $_.Reason -eq 'geo'  }).Count
    $authCount = @($blockedList | Where-Object { $_.Reason -eq 'auth' }).Count
    Write-Host "  Blocked (geo) : $geoCount"
    Write-Host "  Blocked (auth): $authCount"
    Write-Host "  Dead removed  : $($deadList.Count)"
    Write-Host "  DRM (in live) : $($drmList.Count)"
}
Write-Host "  Dup removed   : $dupRemoved"
Write-Host "  Final         : $($sorted.Count)"
Write-Host "  Sort mode     : $sortLabel"
Write-Host "========================================" -ForegroundColor DarkGray