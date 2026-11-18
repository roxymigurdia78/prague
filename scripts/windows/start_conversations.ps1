param([switch]$Generate)
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$pythonPath = Join-Path $projectRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { $pythonPath = (Get-Command python).Source }
$settingsPath = Join-Path $projectRoot 'config/conversation_receiver.local.json'
if (-not (Test-Path -LiteralPath $settingsPath)) {
    throw 'Run: python run.py conversation init-receiver --host YOUR_TAILSCALE_IP'
}
$receiverSettings = Get-Content -LiteralPath $settingsPath -Raw | ConvertFrom-Json
$receiverHost = $receiverSettings.host
$receiverPort = $receiverSettings.port
$existingListener = Get-NetTCPConnection -LocalPort $receiverPort -State Listen -ErrorAction SilentlyContinue
if ($existingListener) {
    Write-Output "Port $receiverPort already has a listener. Check http://${receiverHost}:$receiverPort/health"
    exit 0
}
$logDirectory = Join-Path $projectRoot 'outputs/conversation_receiver'
New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
$receiverArguments = @('-u', '-X', 'utf8', 'run.py', 'conversation', 'serve')
if ($Generate) { $receiverArguments += '--generate' }
$receiverProcess = Start-Process -FilePath $pythonPath -ArgumentList $receiverArguments -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDirectory 'stdout.log') -RedirectStandardError (Join-Path $logDirectory 'stderr.log') -PassThru
$receiverProcess.Id | Set-Content -LiteralPath (Join-Path $logDirectory 'server.pid')
Write-Output "Started receiver PID $($receiverProcess.Id): http://${receiverHost}:$receiverPort/conversations"
Write-Output "Automatic Marble generation: $Generate"
Write-Output "Logs: $logDirectory"
