# Libera la porta indicata chiudendo una copia di Planning Factory gia' in esecuzione.
#
# Riconosce le copie dalla riga di comando (planning-factory ... --port N), cosi'
# non tocca mai altri programmi. Se la porta risulta occupata da qualcosa che non
# e' Planning Factory, non chiude nulla e termina con codice 2.
param([int]$Port = 8770)

function Test-PortaAperta([int]$Port) {
    $c = New-Object Net.Sockets.TcpClient
    try {
        $r = $c.BeginConnect('127.0.0.1', $Port, $null, $null)
        if ($r.AsyncWaitHandle.WaitOne(700) -and $c.Connected) { return $true } else { return $false }
    } catch { return $false } finally { $c.Close() }
}

function Get-CopiePlanning([int]$Port) {
    # il lanciatore, il python del venv e il processo figlio condividono la stessa riga di comando
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.ProcessId -ne $PID -and $_.CommandLine -and
        $_.CommandLine -match 'planning[-_]factory' -and
        $_.CommandLine -match "--port[\s=]+$Port(\s|`"|$)"
    }
}

$copie = @(Get-CopiePlanning $Port)
if ($copie.Count -eq 0) {
    if (Test-PortaAperta $Port) {
        Write-Host "      la porta $Port e' usata da un altro programma (non Planning Factory)."
        Write-Host "      Usa un'altra porta, ad esempio: avvia.bat esempio 8800"
        exit 2
    }
    Write-Host '      libera.'
    exit 0
}

foreach ($p in $copie) {
    Write-Host "      chiudo la copia gia' aperta (PID $($p.ProcessId))"
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
}
for ($i = 0; $i -lt 10 -and (Test-PortaAperta $Port); $i++) { Start-Sleep -Milliseconds 500 }
if (Test-PortaAperta $Port) {
    Write-Host "      la porta $Port non si libera: chiudi a mano i processi python."
    exit 2
}
exit 0
