<#
Gabungkan beberapa file M3U/TXT menjadi satu playlist

Requires: PowerShell 7+
#>

#Requires -Version 7

param(
    [Parameter(Mandatory)]
    [string]$FileList,   # path file dipisah | (pipe)

    [Parameter(Mandatory)]
    [string]$OutFile
)

$files = $FileList -split '\|' | Where-Object { $_ -ne "" -and (Test-Path $_) }

if ($files.Count -eq 0) {
    Write-Host "[ERROR] Tidak ada file valid untuk digabungkan." -ForegroundColor Red
    exit 1
}

Write-Host "Menggabungkan $($files.Count) file..." -ForegroundColor Yellow

$writer = [System.IO.StreamWriter]::new($OutFile, $false, [System.Text.UTF8Encoding]::new($false))
$writer.WriteLine('#EXTM3U')

foreach ($f in $files) {
    Write-Host "  + $(Split-Path $f -Leaf)" -ForegroundColor DarkGray
    $lines = Get-Content $f -Encoding UTF8 -ErrorAction SilentlyContinue
    foreach ($line in $lines) {
        if ($line -match '^#EXTM3U') { continue }
		if ($line -match '^#\s*$') { continue }
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        $writer.WriteLine($line)
    }
}

$writer.Flush()
$writer.Close()
Write-Host "[GABUNG] Selesai -> $(Split-Path $OutFile -Leaf)" -ForegroundColor Green