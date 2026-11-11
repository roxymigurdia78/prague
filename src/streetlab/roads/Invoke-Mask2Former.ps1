[CmdletBinding(DefaultParameterSetName='Single')]
param(
    [Parameter(Mandatory, ParameterSetName='Single')]
    [string]$ImagePath,
    [Parameter(Mandatory, ParameterSetName='Batch')]
    [string]$BatchDirectory,
    [Parameter(Mandatory, ParameterSetName='Batch')]
    [string]$OutputDirectory
)

$ErrorActionPreference = 'Stop'
$worker = Join-Path $PSScriptRoot 'detect_ground.py'
$target = 'hpc-cloudflare'
$runId = [Guid]::NewGuid().ToString('N')
$remoteRoot = 'oneformer/mask2former-transfer'
$remoteRun = "$remoteRoot/$runId"
$output = Join-Path $PSScriptRoot "results/$runId"
$batch = $PSCmdlet.ParameterSetName -eq 'Batch'
if ($batch) {
    $images = @(Get-ChildItem -LiteralPath $BatchDirectory -File -Filter '*.png')
    if (!$images.Count -or ($images.Name -match '[^a-zA-Z0-9_.-]')) { throw 'Expected PNGs with simple filenames.' }
    $output = $OutputDirectory
    $remoteInput = 'images'
} else {
    $images = @(Get-Item -LiteralPath $ImagePath)
    $remoteInput = 'input.png'
}
$connectionOptions = @('-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=15', '-o', 'ServerAliveInterval=30', '-o', 'ServerAliveCountMax=4')
$portalConfig = Join-Path $env:USERPROFILE '.config/hpc-portal/hpc-ssh.json'
$transport = Join-Path $PSScriptRoot 'hpc_transport.py'
$transportPython = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../../.venv/Scripts/python.exe'))

function Invoke-OpenSsh {
    param([string]$Program, [string[]]$Arguments, [string]$Stage, [int]$Timeout = 120)
    $options = $connectionOptions
    if ($Program -eq 'ssh.exe') { $options = @('-n', '-T') + $options }
    & $transportPython -X utf8 $transport --timeout $Timeout --label $Stage -- $Program @options @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Program failed with exit code $LASTEXITCODE (run $runId)."
    }
}

$credentials = Get-Content -Raw -LiteralPath $portalConfig | ConvertFrom-Json
$previousId = $env:TUNNEL_SERVICE_TOKEN_ID
$previousSecret = $env:TUNNEL_SERVICE_TOKEN_SECRET
try {
    $env:TUNNEL_SERVICE_TOKEN_ID = $credentials.client_id
    $env:TUNNEL_SERVICE_TOKEN_SECRET = $credentials.client_secret

    Write-Host "HPC run: ~/$remoteRun"
    Invoke-OpenSsh ssh.exe @($target, ('umask 077; mkdir -p -- "$HOME/{0}" "$HOME/{0}/cache" "$HOME/{0}/tmp" && mkdir -- "$HOME/{1}"' -f $remoteRoot, $remoteRun)) -Stage 'create-run-directory'
    if ($batch) {
        Invoke-OpenSsh ssh.exe @($target, ('umask 077; mkdir -- "$HOME/{0}/images"' -f $remoteRun)) -Stage 'create-image-directory'
        Invoke-OpenSsh scp.exe (@($images.FullName) + @("${target}:$remoteRun/images/")) -Stage 'upload-images' -Timeout 300
    } else {
        Invoke-OpenSsh scp.exe @($images[0].FullName, "${target}:$remoteRun/input.png") -Stage 'upload-image' -Timeout 300
    }
    Invoke-OpenSsh scp.exe @($worker, "${target}:$remoteRun/detect_ground.py") -Stage 'upload-worker'

    $runCommand = 'umask 077; export APPTAINER_CACHEDIR="$HOME/{0}/cache/apptainer" APPTAINER_TMPDIR="$HOME/{0}/tmp"; printf "HPC container START\n"; timeout --signal=TERM --kill-after=30s 1200s apptainer exec --nv /opt/images/jupyter-cpu.sif env HF_HOME="$HOME/{0}/cache/huggingface" XDG_CACHE_HOME="$HOME/{0}/cache" TMPDIR="$HOME/{0}/tmp" PYTHONDONTWRITEBYTECODE=1 "$HOME/oneformer/bin/python" -u "$HOME/{1}/detect_ground.py" "$HOME/{1}/{2}"' -f $remoteRoot, $remoteRun, $remoteInput
    Invoke-OpenSsh ssh.exe @($target, $runCommand) -Stage 'container-and-inference' -Timeout 1260

    New-Item -ItemType Directory -Path $output -Force | Out-Null
    if ($batch) {
        Invoke-OpenSsh scp.exe @('-r', "${target}:$remoteRun/output", $output) -Stage 'download-results' -Timeout 300
    } else {
        $remoteResults = @('mask.png', 'overlay.png', 'result.json') | ForEach-Object { "${target}:$remoteRun/$_" }
        Invoke-OpenSsh scp.exe ($remoteResults + @($output)) -Stage 'download-results' -Timeout 300
    }
    Write-Host "Windows results: $output"
}
finally {
    [Environment]::SetEnvironmentVariable('TUNNEL_SERVICE_TOKEN_ID', $previousId, 'Process')
    [Environment]::SetEnvironmentVariable('TUNNEL_SERVICE_TOKEN_SECRET', $previousSecret, 'Process')
}
