# Download the Windows x64 build linked from https://ffmpeg.org/download.html.
# Run from any directory; only this checkout's tools/ffmpeg folder is changed.
$ErrorActionPreference = 'Stop'
$ffmpegVersion = '9.0.1'
$archiveSha256 = 'fec81ae03971d9dd4be3ebe02e263bd2ec1d789483f931bdba5f5715e65da2e9'
$downloadUrl = "https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-$ffmpegVersion-essentials_build.zip"
$projectRoot = Split-Path -Parent $PSScriptRoot
$installRoot = [System.IO.Path]::GetFullPath((Join-Path $projectRoot 'tools\ffmpeg'))
$metadataPath = Join-Path $installRoot 'download-info.json'

if (Test-Path -LiteralPath $metadataPath) {
    $installed = Get-Content -LiteralPath $metadataPath -Raw | ConvertFrom-Json
    $installationMatches = $installed.archive_sha256 -eq $archiveSha256
    foreach ($name in @('ffmpeg', 'ffprobe')) {
        $binaryPath = Join-Path $installRoot "bin\$name.exe"
        if (-not (Test-Path -LiteralPath $binaryPath)) {
            $installationMatches = $false
        } elseif ((Get-FileHash -LiteralPath $binaryPath -Algorithm SHA256).Hash -ne $installed.binaries.$name) {
            $installationMatches = $false
        }
    }
    if ($installationMatches) {
        Write-Output "FFmpeg $ffmpegVersion already installed and verified: $installRoot"
        return
    }
}

New-Item -ItemType Directory -Path $installRoot -Force | Out-Null
$stagingRoot = Join-Path $installRoot ('.setup-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $stagingRoot | Out-Null
try {
    $archivePath = Join-Path $stagingRoot 'ffmpeg.zip'
    Write-Output "Downloading FFmpeg $ffmpegVersion from gyan.dev..."
    $previousProgressPreference = $ProgressPreference
    try {
        $ProgressPreference = 'SilentlyContinue'
        Invoke-WebRequest -Uri $downloadUrl -OutFile $archivePath -UseBasicParsing
    } finally {
        $ProgressPreference = $previousProgressPreference
    }
    if ((Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash -ne $archiveSha256) {
        throw 'FFmpeg archive checksum mismatch. Installation stopped.'
    }
    Expand-Archive -LiteralPath $archivePath -DestinationPath $stagingRoot
    $extractedRoot = Join-Path $stagingRoot "ffmpeg-$ffmpegVersion-essentials_build"
    foreach ($name in @('ffmpeg', 'ffprobe')) {
        if (-not (Test-Path -LiteralPath (Join-Path $extractedRoot "bin\$name.exe"))) {
            throw "The downloaded archive does not contain $name.exe."
        }
    }
    # Preserve the upstream license, readme and documentation along with the tools.
    Get-ChildItem -LiteralPath $extractedRoot | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $installRoot -Recurse -Force
    }
    $binaryHashes = @{}
    foreach ($name in @('ffmpeg', 'ffprobe')) {
        $binaryPath = Join-Path $installRoot "bin\$name.exe"
        # Consume all native output before selecting a line, so PowerShell does
        # not close the pipe early and make a healthy executable report failure.
        $versionOutput = & $binaryPath -version
        if ($LASTEXITCODE -ne 0) { throw "$name could not start." }
        Write-Output $versionOutput[0]
        $binaryHashes[$name] = (Get-FileHash -LiteralPath $binaryPath -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    @{
        version = $ffmpegVersion
        url = $downloadUrl
        archive_sha256 = $archiveSha256
        installed_at = [DateTime]::UtcNow.ToString('o')
        binaries = $binaryHashes
    } | ConvertTo-Json | Set-Content -LiteralPath $metadataPath -Encoding UTF8
    Write-Output "Installed and verified: $installRoot"
} finally {
    # Check the absolute target before recursively removing our temporary files.
    $resolvedStaging = [System.IO.Path]::GetFullPath($stagingRoot)
    $expectedPrefix = $installRoot + [System.IO.Path]::DirectorySeparatorChar + '.setup-'
    if (-not $resolvedStaging.StartsWith($expectedPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Refusing to remove a staging directory outside tools/ffmpeg.'
    }
    if (Test-Path -LiteralPath $resolvedStaging) {
        Remove-Item -LiteralPath $resolvedStaging -Recurse -Force
    }
}
