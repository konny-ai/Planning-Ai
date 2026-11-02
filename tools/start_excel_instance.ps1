param(
    [Parameter(Mandatory=$true)][string]$Config,
    [switch]$NoBrowser
)
$ErrorActionPreference = 'Stop'
$settings = Get-Content -LiteralPath $Config -Raw | ConvertFrom-Json
$instanceFolder = Split-Path -Parent (Resolve-Path -LiteralPath $Config).Path
$port = if ($settings.port) { [int]$settings.port } else { 8766 }
if ($port -lt 1024 -or $port -gt 65535) { throw 'Porta non valida.' }
foreach ($name in @('python','runtime','input','store')) {
    if (-not (Test-Path -LiteralPath $settings.$name)) { throw "Percorso $name non trovato: $($settings.$name)" }
}
if ([IO.Path]::GetExtension($settings.input) -ne '.xlsx') { throw 'Questa istanza richiede un Excel .xlsx.' }
$oldPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = $settings.runtime
    Write-Host 'Controllo Excel di pianificazione...'
    & $settings.python -m planning_factory workbook-validate $settings.input --report (Join-Path $instanceFolder 'verifica-ingresso-avvio.json') | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Excel non pronto: avvio interrotto. Il server precedente resta attivo.' }
    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)
    foreach ($ownerId in @($listeners.OwningProcess | Select-Object -Unique)) {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$ownerId"
        if (-not $process -or $process.CommandLine -notmatch 'planning_factory\s+secure-web') {
            throw "Porta $port occupata da un altro servizio: processo non arrestato."
        }
        Stop-Process -Id $ownerId -Force
    }
    $deadline = (Get-Date).AddSeconds(15)
    while ((Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) -and (Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 200
    }
    if (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) { throw 'La porta non si e liberata.' }
    $arguments = @('-m','planning_factory','secure-web','--local-user','--fixture',('"'+$settings.input+'"'),
        '--store',('"'+$settings.store+'"'),'--audit',('"'+$settings.audit+'"'),'--port',"$port")
    $child = Start-Process -FilePath $settings.python -ArgumentList $arguments -WorkingDirectory $instanceFolder -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $instanceFolder 'server-excel.stdout.log') -RedirectStandardError (Join-Path $instanceFolder 'server-excel.stderr.log')
    $child.Id | Set-Content -LiteralPath (Join-Path $instanceFolder 'server-excel.pid')
    $url = "http://127.0.0.1:$port"
    $ready = $false
    $deadline = (Get-Date).AddMinutes(3)
    while ((Get-Date) -lt $deadline) {
        if ($child.HasExited) { throw 'Server arrestato: controllare server-excel.stderr.log.' }
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri "$url/api/health" -TimeoutSec 2
            if ($response.StatusCode -eq 200) { $ready = $true; break }
        } catch { }
        Start-Sleep -Milliseconds 300
    }
    if (-not $ready) { throw 'Il server non ha risposto: controllare i log nella cartella istanza.' }
    Write-Host "Planning CLIENTE avviato da Excel: $($settings.input)"
    Write-Host $url
    if (-not $NoBrowser) { Start-Process $url }
} finally {
    $env:PYTHONPATH = $oldPythonPath
}
