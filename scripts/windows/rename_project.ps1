# Run from an external PowerShell after closing apps that use the project.
$ErrorActionPreference = 'Stop'
$taskSource = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
if ((Split-Path $taskSource -Leaf) -ne 'test1') { throw 'Expected test1 project folder.' }
$taskParent = Split-Path $taskSource -Parent
$taskDestination = Join-Path $taskParent 'prague'
if (Test-Path -LiteralPath $taskDestination) { throw 'prague already exists; nothing changed.' }
$taskPython = (Get-Command python -ErrorAction Stop).Source
$taskBlenderCommand = Get-Command blender -ErrorAction SilentlyContinue
if ($taskBlenderCommand) { $taskBlender = $taskBlenderCommand.Source }
else {
    $taskBlender = Get-ChildItem -Path 'C:\Program Files\Blender Foundation\Blender *\blender.exe' | Sort-Object FullName | Select-Object -Last 1 -ExpandProperty FullName
}
if (-not $taskBlender) { throw 'Blender was not found; nothing changed.' }
function Invoke-ProjectTool {
    param([string]$Executable, [string[]]$ToolArguments, [string]$LogPath = '')
    # Windows PowerShell 5.1 reports native stderr as ErrorRecord objects, even
    # for successful tools such as unittest. Decide success using the exit code.
    $ErrorActionPreference = 'Continue'
    if ($LogPath) { & $Executable @ToolArguments *> $LogPath }
    else { & $Executable @ToolArguments 2>&1 | ForEach-Object { "$_" } }
    $taskToolExit = $LASTEXITCODE
    if ($taskToolExit -ne 0) { throw "Tool exited with code ${taskToolExit}: $Executable $LogPath" }
}
# Release this PowerShell process's own current-directory handle first.
Set-Location -LiteralPath $taskParent
Rename-Item -LiteralPath $taskSource -NewName 'prague'
$taskRun = Join-Path $taskDestination ('scratch\rename_project\runs\' + [guid]::NewGuid().ToString('N'))
$taskScript = Join-Path $taskDestination 'scripts\rename_project.py'
$taskArguments = @('--old-root', $taskSource, '--new-root', $taskDestination, '--run-dir', $taskRun)
try {
    New-Item -ItemType Directory -Path $taskRun -Force | Out-Null
    Set-Location -LiteralPath $taskDestination
    Invoke-ProjectTool -Executable $taskBlender -ToolArguments (@('--background','--factory-startup','--python-exit-code','1','--python',$taskScript,'--','blender') + $taskArguments) -LogPath (Join-Path $taskRun 'blender.log')
    Invoke-ProjectTool -Executable $taskPython -ToolArguments (@('-X','utf8',$taskScript,'text') + $taskArguments)
    Invoke-ProjectTool -Executable $taskPython -ToolArguments (@('-X','utf8',$taskScript,'verify') + $taskArguments)
    Invoke-ProjectTool -Executable $taskPython -ToolArguments @('-X','utf8','run.py','test')
} catch {
    $taskFailure = $_
    Invoke-ProjectTool -Executable $taskPython -ToolArguments (@('-X','utf8',$taskScript,'rollback') + $taskArguments)
    Set-Location -LiteralPath $taskParent
    Rename-Item -LiteralPath $taskDestination -NewName 'test1'
    throw "Migration stopped; current files and folder name were restored. $taskFailure"
}
Write-Host "Project renamed successfully: $taskDestination"
