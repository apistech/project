<#
.SYNOPSIS
    Convert & Clean IPTV Playlist - Optimized
.DESCRIPTION
    - Konversi TXT ke M3U
    - Bersihkan file M3U/M3U8
    - Periksa URL live (parallel) + ukur latency
    - Deteksi geo-block
    - Ranking CDN tercepat -> simpan ke file
    - Hapus duplikat berdasarkan URL
    - Sorting group/none
    - Backup & dead log
#>

#Requires -Version 7

param(
    [Parameter(Mandatory)]
    [string]$InputFile,
    
    [int]$TimeoutSec  = 8,
    [int]$MaxParallel = 32,
    [int]$DoCheck     = 1,
    [int]$ScanMode    = 1,
    
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
# CAPITALIZE NAMA GROUP
# =========================
function Convert-ToTitleCase {
    param([string]$Text)

    if ([string]::IsNullOrWhiteSpace($Text)) { return "Unknown" }

    $acronyms = @("TV", "IPTV", "VOD", "SD", "HD", "4K", "FHD", "UHD")
    $words = $Text.ToLower().Trim() -split '\s+'
    $result = [System.Collections.Generic.List[string]]::new()

    foreach ($word in $words) {
        if ([string]::IsNullOrWhiteSpace($word)) { continue }
        $wordUpper = $word.ToUpper()
        if ($acronyms -contains $wordUpper) {
            $result.Add($wordUpper)
        } else {
            $result.Add($word[0].ToString().ToUpper() + $word.Substring(1))
        }
    }
    return ($result -join ' ')
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
            $currentGroup = Convert-ToTitleCase -Text $Matches[1].Trim()
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
# HELPER
# =========================
function Get-RootDomain {
    param([string]$HostName)
    if ([string]::IsNullOrWhiteSpace($HostName)) { return "unknown" }
    $parts = $HostName.Split('.')
    if ($parts.Count -ge 2) { return "$($parts[-2]).$($parts[-1])" }
    return $HostName
}

function Get-DomainLatencyRanking {
    param([hashtable]$DomainLatencies)
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

# =========================
# PARSER M3U
# =========================
function Parse-M3U {
    param([string]$File)
    
    $entries = [System.Collections.Generic.List[object]]::new()
    $buffer = [System.Collections.Generic.List[string]]::new()
    $header = "#EXTM3U"
    $lines = Read-FileWithEncoding -Path $File
    
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
            Convert-ToTitleCase -Text $Matches[1].Trim()
        } else { "Unknown" }

        if ($info -match 'group-title="[^"]*"') {
            $info = $info -replace 'group-title="[^"]*"', "group-title=`"$group`""
        }

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
            $uri = [System.Uri]$urlClean
            $hostName = $uri.Host
            $root = Get-RootDomain $hostName
        }
        catch { $hostName = "unknown"; $root = "unknown" }
        
        $rawBlock = [System.Collections.Generic.List[string]]::new()
        $rawBlock.Add($info)
        foreach ($tag in $extraTags) { $rawBlock.Add($tag) }
        $rawBlock.Add($urlClean)
        
        $entries.Add([PSCustomObject]@{
            Url = $urlClean
            Title = $title
            Group = $group
            Referrer = $referrer
            Host = $hostName
            RootDomain = $root
            RawBlock = $rawBlock.ToArray()
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
        [int]$MaxParallel,
        [int]$ScanMode
    )
    
    $modeName = if ($ScanMode -eq 1) { "Normal" } else { "Fast Geo" }
    Write-Host "Memeriksa URL... (mode: $modeName, timeout: ${TimeoutSec}s, parallel: $MaxParallel)" -ForegroundColor Yellow

    $liveList = [System.Collections.Concurrent.ConcurrentBag[object]]::new()
    $deadList = [System.Collections.Concurrent.ConcurrentBag[object]]::new()
    $geoBlockedList = [System.Collections.Concurrent.ConcurrentBag[object]]::new()
    $domainLatencies = [System.Collections.Concurrent.ConcurrentDictionary[string, [System.Collections.Generic.List[long]]]]::new()

    $results = $Entries | ForEach-Object -Parallel {
        $entry = $_
        $timeoutSec = $using:TimeoutSec
        $scanMode = $using:ScanMode
        $alive = $false
        $isGeoBlocked = $false
        $latencyMs = -1L

        try {
            $handler = [System.Net.Http.SocketsHttpHandler]::new()
            $handler.AllowAutoRedirect = $true
            $handler.PooledConnectionLifetime = [TimeSpan]::FromMinutes(2)

            $client = [System.Net.Http.HttpClient]::new($handler)
            $client.Timeout = [TimeSpan]::FromSeconds($timeoutSec)
            $client.DefaultRequestHeaders.TryAddWithoutValidation("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)") | Out-Null

            if ($entry.Referrer) {
                $client.DefaultRequestHeaders.TryAddWithoutValidation("Referer", $entry.Referrer) | Out-Null
            }

            $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
            $req = [System.Net.Http.HttpRequestMessage]::new([System.Net.Http.HttpMethod]::Get, $entry.Url)
            $resp = $client.SendAsync($req, [System.Net.Http.HttpCompletionOption]::ResponseHeadersRead).GetAwaiter().GetResult()
            $stopwatch.Stop()
            $code = [int]$resp.StatusCode

            if ($code -ge 200 -and $code -lt 300) {
                $stream = $resp.Content.ReadAsStreamAsync().GetAwaiter().GetResult()
                $buf = New-Object byte[] 8192
                $bytesRead = $stream.Read($buf, 0, 8192)
                
                if ($bytesRead -gt 0) {
                    $previewText = [System.Text.Encoding]::UTF8.GetString($buf, 0, $bytesRead)
                    
                    # Geo-block detection
                    $geoKeywords = @(
                        'not available in your region', 'not available in your country',
                        'geo-block', 'geoblocked', 'geo restricted',
                        'only available in', 'outside your region', 'location restricted'
                    )
                    $isGeo = $false
                    foreach ($kw in $geoKeywords) {
                        if ($previewText.ToLower().Contains($kw)) {
                            $isGeo = $true
                            break
                        }
                    }
                    
                    if ($isGeo) {
                        $isGeoBlocked = $true
                    } else {
                        $alive = $true
                    }
                } else {
                    $alive = $true
                }
                if ($alive) { $latencyMs = $stopwatch.ElapsedMilliseconds }
                $resp.Dispose()
            }
            elseif ($code -eq 403 -or $code -eq 451) {
                $isGeoBlocked = $true
                $resp.Dispose()
            }
            else { 
                $resp.Dispose()
            }
            
            $client.Dispose()
            $handler.Dispose()
        }
        catch {
            # Fallback HEAD
            try {
                $handler = [System.Net.Http.SocketsHttpHandler]::new()
                $client = [System.Net.Http.HttpClient]::new($handler)
                $client.Timeout = [TimeSpan]::FromSeconds($timeoutSec)
                $req = [System.Net.Http.HttpRequestMessage]::new([System.Net.Http.HttpMethod]::Head, $entry.Url)
                $resp = $client.SendAsync($req).GetAwaiter().GetResult()
                if ([int]$resp.StatusCode -eq 200) { $alive = $true }
                $resp.Dispose()
                $client.Dispose()
                $handler.Dispose()
            } catch {}
        }

        [PSCustomObject]@{ 
            Entry = $entry
            Alive = $alive
            IsGeoBlocked = $isGeoBlocked
            LatencyMs = $latencyMs
            Domain = $entry.RootDomain
        }
    } -ThrottleLimit $MaxParallel

    foreach ($r in $results) {
        if ($r.Alive) { 
            $liveList.Add($r.Entry)
            $list = $domainLatencies.GetOrAdd($r.Domain, [System.Collections.Generic.List[long]]::new())
            if ($r.LatencyMs -gt 0) { 
                [System.Threading.Monitor]::Enter($list)
                try { $list.Add($r.LatencyMs) } finally { [System.Threading.Monitor]::Exit($list) }
            }
        } 
        elseif ($r.IsGeoBlocked) { $geoBlockedList.Add($r.Entry) }
        else { $deadList.Add($r.Entry) }
    }

    Write-Host "Aktif       : $($liveList.Count)" -ForegroundColor Green
    Write-Host "Geo-Blocked : $($geoBlockedList.Count)" -ForegroundColor Yellow
    Write-Host "Mati        : $($deadList.Count)" -ForegroundColor Red

    $normalHash = @{}
    foreach ($k in $domainLatencies.Keys) { $normalHash[$k] = $domainLatencies[$k] }

    return @($liveList.ToArray()), @($deadList.ToArray()), @($geoBlockedList.ToArray()), $normalHash
}

# =========================
# SIMPAN RANKING CDN
# =========================
function Save-CdnRanking {
    param([hashtable]$DomainLatencies, [string]$OutputPath)
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
# PROSES UTAMA
# =========================

if (-not (Test-Path $InputFile)) {
    Write-Host "ERROR: File tidak ditemukan: $InputFile" -ForegroundColor Red
    exit 1
}

$dir = [System.IO.Path]::GetDirectoryName((Resolve-Path $InputFile))
$baseName = [System.IO.Path]::GetFileNameWithoutExtension($InputFile)
$extension = [System.IO.Path]::GetExtension($InputFile).ToLowerInvariant()

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "     CONVERT & CLEAN - All in One" -ForegroundColor White
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Input file    : $([System.IO.Path]::GetFileName($InputFile))" -ForegroundColor Yellow

$m3uFile = $InputFile

if ($extension -eq ".txt") {
    $txtType = Get-TxtFileType -FilePath $InputFile
    switch ($txtType) {
        'm3u' { $m3uFile = $InputFile; Write-Host "Mode          : TXT (format M3U) -> Clean Only" -ForegroundColor Green }
        'txt-genre' {
            $m3uFile = Join-Path $dir "${baseName}.m3u"
            Write-Host "Mode          : TXT (channel/genre) -> Konversi M3U + Clean" -ForegroundColor Green
            if (Test-Path $m3uFile) { Copy-Item $m3uFile "$m3uFile.old" -Force }
            $channelCount = Convert-TxtToM3U -TxtFile $InputFile -OutM3u $m3uFile
            if ($channelCount -eq 0) { Write-Host "ERROR: Tidak ada channel valid" -ForegroundColor Red; exit 1 }
        }
        'url-list' {
            Write-Host "Mode          : TXT (daftar URL)" -ForegroundColor Green
            $urls = Get-Content $InputFile -Encoding UTF8 | Where-Object { $_ -match '^https?://' }
            $konfirmasi = Read-Host "Download dan proses semua URL? (Y/tidak) [default: Y]"
            if ($konfirmasi -ne "" -and $konfirmasi -notmatch '^[Yy]') { Write-Host "Dibatalkan." -ForegroundColor DarkYellow; exit 0 }
            foreach ($url in $urls) {
                $dlName = [System.IO.Path]::GetFileName(([System.Uri]$url).LocalPath)
                if ([string]::IsNullOrWhiteSpace($dlName) -or $dlName -notmatch '\.(m3u|m3u8|txt)$') { 
                    $dlName = "${baseName}_download.m3u"
                }
                $dlPath = Join-Path $dir $dlName
                try {
                    Invoke-WebRequest -Uri $url -OutFile $dlPath -TimeoutSec 30 -ErrorAction Stop
                    & $PSCommandPath -InputFile $dlPath -TimeoutSec $TimeoutSec -MaxParallel $MaxParallel -DoCheck $DoCheck -ScanMode $ScanMode -SortMode $SortMode
                } catch { continue }
            }
            exit 0
        }
    }
}
elseif ($extension -eq ".m3u") { Write-Host "Mode          : M3U Clean Only" -ForegroundColor Green }
elseif ($extension -eq ".m3u8") { Write-Host "Mode          : M3U8 Clean Only" -ForegroundColor Green }
else { Write-Host "ERROR: Tipe file tidak didukung" -ForegroundColor Red; exit 1 }

Write-Host ""
Write-Host "Memuat file M3U..." -ForegroundColor Yellow
$entries, $m3uHeader = Parse-M3U $m3uFile
Write-Host "Entry ditemukan : $($entries.Count)" -ForegroundColor Cyan

$liveEntries = $entries
$deadList = @()
$geoBlockedList = @()
$domainLatencies = @{}

if ($DoCheck -eq 1) {
    $liveEntries, $deadList, $geoBlockedList, $domainLatencies = Test-UrlsParallel -Entries $entries -TimeoutSec $TimeoutSec -MaxParallel $MaxParallel -ScanMode $ScanMode
    
    if ($domainLatencies.Count -gt 0) {
        $rankingFile = Join-Path $dir "${baseName}_cdn_ranking.txt"
        Save-CdnRanking -DomainLatencies $domainLatencies -OutputPath $rankingFile
        Write-Host ""
        Write-Host "5 CDN tercepat (median latency):" -ForegroundColor Yellow
        $domainRanking = Get-DomainLatencyRanking -DomainLatencies $domainLatencies
        $domainRanking.GetEnumerator() | Sort-Object Value | Select-Object -First 5 | ForEach-Object {
            $ms = $_.Value
            $display = if ($ms -ge 10000) { "> 10s" } else { "$ms ms" }
            Write-Host "  $($_.Key) : $display" -ForegroundColor DarkGray
        }
    }
    
    if ($geoBlockedList.Count -gt 0) {
        $geoFile = Join-Path $dir "${baseName}_geoblocked.log"
        [System.IO.File]::WriteAllLines($geoFile, ($geoBlockedList | ForEach-Object { "[$($_.Group)] $($_.Title) | $($_.Url)" }), [System.Text.Encoding]::UTF8)
        Write-Host "Geo-Blocked   : $geoFile" -ForegroundColor DarkGray
    }
}
else { Write-Host "Pemeriksaan URL : dilewati" -ForegroundColor DarkGray }

Write-Host ""
Write-Host "Menghapus duplikat..." -ForegroundColor Yellow

$seenUrls = [System.Collections.Generic.HashSet[string]]::new()
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
    $sorted = $uniqueEntries | Sort-Object Group, Title
    $sortLabel = "group -> then title"
    Write-Host "  Group kemudian title (alphabetical)" -ForegroundColor DarkGray
} else {
    $sorted = $uniqueEntries
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
$headerLine = if ($m3uHeader) { $m3uHeader } else { "#EXTM3U" }
$out.Add($headerLine)

foreach ($entry in $sorted) {
    foreach ($line in $entry.RawBlock) { $out.Add($line) }
}

$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[System.IO.File]::WriteAllLines($m3uFile, $out, $utf8NoBom)

if ($deadList.Count -gt 0) {
    $logFile = Join-Path $dir "${baseName}_dead.log"
    [System.IO.File]::WriteAllLines($logFile, ($deadList | ForEach-Object { "[$($_.Group)] $($_.Title) | $($_.Url)" }), [System.Text.Encoding]::UTF8)
    Write-Host "Log mati      : $logFile" -ForegroundColor DarkGray
}

Write-Host "Output file   : $m3uFile" -ForegroundColor Cyan

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "SELESAI" -ForegroundColor White
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Original      : $($entries.Count)"
if ($DoCheck -eq 1) { Write-Host "  Geo-Blocked   : $($geoBlockedList.Count)" }
if ($DoCheck -eq 1) { Write-Host "  Dead removed  : $($deadList.Count)" }
Write-Host "  Dup removed   : $dupRemoved"
Write-Host "  Final         : $($sorted.Count)"
Write-Host "  Sort mode     : $sortLabel"
Write-Host "========================================" -ForegroundColor DarkGray