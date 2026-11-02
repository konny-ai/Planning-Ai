<#
Collaudo locale CLIENTE, dall'estrazione al piano verificato, in sola lettura su Factory_Cliente.

  estrazione  run_extract.py (CSV nesting/materiali) + export_factory_scenario.py (scenario Factory)
  regole      build_scenario_cliente.py prepara -> to_engine_v02.py -> build_scenario_cliente.py nesting
  materiali   build_materiali_simulati.py (SIMULATI, finche' manca la fonte AS400)
  motore      schedule + verify sullo scenario con nesting
  controllo   cliente_reconcile.py (programmi A_NES inclusi/esclusi e perche')
  canonico    export-canonical + build_clienti.py + build_materiale_spessore.py + binding-cliente.json -> build-scenario (ADR 004) -> schedule + verify

Tutto finisce in -Out (default data\private\cliente, ignorata da git: dati cliente).
La Control Room si avvia poi sul binding:
  python -m planning_factory secure-web --fixture <Out>\canonico\binding.json --credentials ... --store ... --audit ...
(credenziali con tools\init_local_instance.py, vedi docs\local-installation.md).
#>
param(
    [string]$Server = 'SERVER\ISTANZA',
    [string]$Database = 'Factory_Cliente',
    [string]$Start = '2026-05-04',          # il dump CLIENTE e' fermo al 02/05/2026
    [string]$Out,
    # Avanzamento TRD (15_avanzamento_trd.sql, logica di V_OrderState): residual_policy del dominio progress
    # nello scenario canonico. 'time' sottrae i tempi registrati sui ticket, 'quantity' i pezzi prodotti
    # (S_PAV) x tempo unitario, 'none' pianifica le fasi per intero.
    [ValidateSet('time', 'quantity', 'none')][string]$Progress = 'quantity',
    [switch]$SkipExtract                     # riusa out\ e scenario.json gia' estratti
)
$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$root = (Resolve-Path (Join-Path $here '..\..')).Path
if (-not $Out) { $Out = Join-Path $root 'data\private\cliente' }
$py = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { $py = 'python' }
$env:PYTHONIOENCODING = 'utf-8'
$tools = Join-Path $root 'integrations\factory-export\tools'
$regole = Join-Path $Out 'regole'
$engine = Join-Path $regole 'engine'
$canonico = Join-Path $Out 'canonico'
# Ora di Roma del giorno di -Start (+01:00 o +02:00): orizzonte del binding, data di estrazione e 'now'.
$startDay = [datetime]::ParseExact($Start, 'yyyy-MM-dd', [Globalization.CultureInfo]::InvariantCulture)
$rome = [TimeZoneInfo]::FindSystemTimeZoneById('W. Europe Standard Time')
$offset = $rome.GetUtcOffset($startDay.AddHours(12)).ToString('hh\:mm'); $offset = "+$offset"
$endDay = $startDay.AddDays(365).ToString('yyyy-MM-dd')
$endOffset = "+" + $rome.GetUtcOffset($startDay.AddDays(365).AddHours(12)).ToString('hh\:mm')

function Step([string]$name, [scriptblock]$body) {
    Write-Host "== $name" -ForegroundColor Cyan
    & $body
    if ($LASTEXITCODE) { throw "$name fallito (exit $LASTEXITCODE)" }
}

function Plan([string]$name, [string]$scenarioFile, [string]$planFile) {
    Write-Host "== $name" -ForegroundColor Cyan
    & $py -m planning_factory schedule $scenarioFile -o $planFile
    # exit 2 = piano con unita' non collocate (es. carenze materiale): e' un esito, lo giudica verify; 1 = errore
    if ($LASTEXITCODE -notin 0, 2) { throw "$name fallito (exit $LASTEXITCODE)" }
    $global:LASTEXITCODE = 0
}

if (-not $SkipExtract) {
    $env:CLIENTE_SERVER = $Server; $env:CLIENTE_DB = $Database
    Step 'estrazione CSV CLIENTE' { & $py (Join-Path $here 'run_extract.py') }
    Step 'estrazione scenario Factory' {
        & $py (Join-Path $tools 'export_factory_scenario.py') --server $Server --database $Database `
            --start $Start --horizon-days 365 --out $Out
    }
}
Step 'regole cliente' { & $py (Join-Path $here 'build_scenario_cliente.py') prepara (Join-Path $Out 'scenario.json') $regole }
Step 'conversione motore' {
    & $py (Join-Path $tools 'to_engine_v02.py') (Join-Path $regole 'scenario.json') --out $engine --subcontract-policy anchored `
        --progress-policy none   # i consuntivi entrano una sola volta, dal dominio progress canonico
}
Step 'nesting' {
    & $py (Join-Path $here 'build_scenario_cliente.py') nesting (Join-Path $engine 'engine-scenario.json') `
        (Join-Path $engine 'engine-scenario-nesting.json') (Join-Path $engine 'materiali_simulati.csv')
}
Step 'materiali simulati' {
    & $py (Join-Path $here 'build_materiali_simulati.py') (Join-Path $engine 'engine-scenario-nesting.json') (Join-Path $engine 'materiali')
}
$scenario = Join-Path $engine 'engine-scenario-nesting.json'
Plan 'piano' $scenario (Join-Path $engine 'plan.json')
Step 'verifica' { & $py -m planning_factory verify $scenario (Join-Path $engine 'plan.json') }
$reconcile = Join-Path $engine 'reconcile.json'
Remove-Item $reconcile -ErrorAction SilentlyContinue
Write-Host '== riconciliazione nesting' -ForegroundColor Cyan
& $py (Join-Path $root 'tools\cliente_reconcile.py') --scenario $scenario `
    --programs (Join-Path $here 'out\10_nesting_programmi\1.csv') --members (Join-Path $here 'out\11_nesting_membri\1.csv') `
    --out $reconcile | Out-Null
# exit 2 = ci sono programmi da rivedere: e' l'esito atteso, il dettaglio e' in reconcile.json
if ($LASTEXITCODE -notin 0, 2) { throw "riconciliazione fallita (exit $LASTEXITCODE)" }
$global:LASTEXITCODE = 0

New-Item -ItemType Directory -Force $canonico | Out-Null
Copy-Item (Join-Path $engine 'materiali\*.csv') $canonico
$binding = Get-Content (Join-Path $here 'binding-cliente.json') -Raw | ConvertFrom-Json
$binding.domains.progress.residual_policy = $Progress
# L'orizzonte canonico segue -Start (binding-cliente.json porta quello del collaudo 2026-05-04).
$binding.scenario.id = "cliente-regole-$Start"
$binding.scenario.start = "$($Start)T00:00:00$offset"
$binding.scenario.end = "$($endDay)T00:00:00$endOffset"
$binding | ConvertTo-Json -Depth 10 | Set-Content (Join-Path $canonico 'binding.json') -Encoding utf8
Step 'bundle canonico Factory' {
    & $py -m planning_factory export-canonical $scenario -o (Join-Path $canonico 'factory-bundle.json') `
        --extracted-at "$($Start)T00:00:00$offset" --profile-id cliente-regole
}
Step 'clienti commesse' {
    & $py (Join-Path $here 'build_clienti.py') (Join-Path $canonico 'factory-bundle.json') (Join-Path $regole 'scenario.json')
}
Step 'materiale e spessore' {
    & $py (Join-Path $here 'build_materiale_spessore.py') (Join-Path $canonico 'factory-bundle.json') `
        (Join-Path $Out 'scenario.json') (Join-Path $here 'out')
}
Step 'avanzamento TRD' {
    & $py (Join-Path $here 'build_avanzamento_trd.py') (Join-Path $canonico 'factory-bundle.json') `
        (Join-Path $regole 'scenario.json') (Join-Path $here 'out\15_avanzamento_trd\1.csv') $canonico $Progress
}
# build-scenario esce con 1 quando lo scenario non e' pubblicabile (es. carenze materiale): lo scenario
# viene comunque scritto e resta consultabile in sola lettura.
Write-Host '== scenario canonico (ADR 004)' -ForegroundColor Cyan
Remove-Item (Join-Path $canonico 'scenario.json') -ErrorAction SilentlyContinue
& $py -m planning_factory build-scenario (Join-Path $canonico 'binding.json') -o (Join-Path $canonico 'scenario.json') `
    --manifest (Join-Path $canonico 'manifest.json') --report (Join-Path $canonico 'quality.json') --now "$($Start)T06:00:00$offset"
if (-not (Test-Path (Join-Path $canonico 'scenario.json'))) { throw 'scenario canonico non costruito' }
$global:LASTEXITCODE = 0
Step 'descrizioni Control Room' {
    & $py (Join-Path $here 'build_descrizioni.py') (Join-Path $Out 'scenario.json') (Join-Path $canonico 'scenario.json') `
        (Join-Path $here 'out') (Join-Path $canonico 'descrizioni.json') --cliente "Cliente di esempio" --nota-materiali 'simulati'   # file senza BOM: niente accenti nel sorgente
}
Plan 'piano canonico' (Join-Path $canonico 'scenario.json') (Join-Path $canonico 'plan.json')
Step 'verifica canonica' { & $py -m planning_factory verify (Join-Path $canonico 'scenario.json') (Join-Path $canonico 'plan.json') }
Write-Host "Fatto. Risultati in $Out" -ForegroundColor Green
