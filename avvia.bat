@echo off
REM ============================================================================
REM  Planning Factory - avvio automatico con un doppio clic (Windows)
REM
REM  Cosa fa, in ordine:
REM    1. cerca Python 3.12 o successivo (se manca lo installa con winget)
REM    2. crea l'ambiente .venv se non esiste
REM    3. installa o aggiorna Planning Factory se serve
REM    4. chiude una copia gia' in esecuzione sulla stessa porta
REM    5. avvia la Control Room e apre il browser
REM
REM  Uso:
REM    avvia.bat                    Control Room completa con l'esempio incluso,
REM                                 porta 8770; salva revisioni, regole e audit
REM                                 nella cartella "istanza"
REM    avvia.bat esempio 8800       esempio incluso, con un'altra porta
REM    avvia.bat mio.xlsx           un tuo workbook Excel (o scenario .json)
REM    avvia.bat mio.xlsx 8800      con un'altra porta
REM    avvia.bat semplice           versione minima, senza salvataggio ne' accesso
REM                                 (revisioni, regole, audit, assistente AI)
REM    avvia.bat test               esegue la suite di test e termina
REM    avvia.bat ferma              chiude il server e termina
REM
REM  Variabili facoltative: AVVIA_NOBROWSER=1 (non apre il browser),
REM  AVVIA_NOPAUSE=1 (non aspetta un tasto in caso di errore).
REM ============================================================================
setlocal EnableExtensions
cd /d "%~dp0"

set "MODE=salva"
set "FIXTURE=examples\planning-workbookending.xlsx"
set "PORT=8770"

if /i "%~1"=="test"  set "MODE=test"
if /i "%~1"=="ferma" set "MODE=ferma"
if /i "%~1"=="semplice" set "MODE=web"
if not "%~1"=="" for %%K in (esempio salva semplice test ferma) do if /i "%~1"=="%%K" set "KW=1"
if not "%~1"=="" if not defined KW set "FIXTURE=%~1"
if not "%~2"=="" set "PORT=%~2"

echo.
echo === Planning Factory ===
echo Cartella: %CD%

REM ---------------------------------------------------------------- 1. Python
set "PY="
call :cerca_python
if not defined PY (
    echo.
    echo [1/5] Python 3.12 o successivo non trovato.
    where winget >nul 2>&1
    if errorlevel 1 (
        echo       winget non e' disponibile. Installa Python 3.13 da https://www.python.org/downloads/
        echo       spuntando "Add python.exe to PATH", poi rilancia questo file.
        goto :errore
    )
    echo       Installo Python 3.13 con winget ^(puo' chiedere conferma^)...
    winget install --id Python.Python.3.13 -e --silent --accept-package-agreements --accept-source-agreements
    call :cerca_python
    if not defined PY (
        echo.
        echo       Python e' stato installato ma questa finestra non lo vede ancora.
        echo       Chiudi la finestra e rilancia avvia.bat.
        goto :errore
    )
)
echo [1/5] Python: %PY%

REM ---------------------------------------------------------------- 2. venv
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import sys;sys.exit(0 if sys.version_info>=(3,12) else 1)" >nul 2>&1
    if errorlevel 1 (
        echo [2/5] L'ambiente .venv esistente usa un Python troppo vecchio: lo ricreo.
        rmdir /s /q ".venv"
        if exist ".venv" (
            echo       Non riesco a cancellare .venv: chiudi Planning Factory ^(avvia.bat ferma^) e riprova.
            goto :errore
        )
    )
)
if not exist ".venv\Scripts\python.exe" (
    echo [2/5] Creo l'ambiente .venv ...
    %PY% -m venv .venv
    if errorlevel 1 goto :errore
) else (
    echo [2/5] Ambiente .venv presente.
)
set "VPY=%CD%\.venv\Scripts\python.exe"
set "VPF=%CD%\.venv\Scripts\planning-factory.exe"

REM ---------------------------------------------------------- 3. installazione
set "STAMP=.venv\.pf_installed"
set "DAINSTALLARE=0"
if not exist "%STAMP%" set "DAINSTALLARE=1"
if not exist "%VPF%" set "DAINSTALLARE=1"
if "%DAINSTALLARE%"=="0" (
    powershell -NoProfile -Command "if ((Get-Item 'pyproject.toml').LastWriteTime -gt (Get-Item '%STAMP%').LastWriteTime) { exit 1 }" >nul 2>&1
    if errorlevel 1 set "DAINSTALLARE=1"
)
if "%DAINSTALLARE%"=="1" (
    echo [3/5] Installo Planning Factory ^(serve internet solo la prima volta^)...
    "%VPY%" -m pip install --disable-pip-version-check -q -e .
    if errorlevel 1 goto :errore
    echo ok> "%STAMP%"
) else (
    echo [3/5] Planning Factory gia' installato e aggiornato.
)

if /i "%MODE%"=="test" goto :test

REM ------------------------------------------------ 4. chiude copie in esecuzione
echo [4/5] Controllo se la porta %PORT% e' gia' in uso...
powershell -NoProfile -ExecutionPolicy Bypass -File "tools\libera_porta.ps1" -Port %PORT%
if errorlevel 2 goto :errore
if /i "%MODE%"=="ferma" (
    echo Fatto: nessun server in esecuzione sulla porta %PORT%.
    goto :fine
)

REM ------------------------------------------------------------------- 5. avvio
echo [5/5] Avvio la Control Room su http://127.0.0.1:%PORT%
echo       Per fermarla: chiudi questa finestra o premi CTRL+C.
echo.
if not defined AVVIA_NOBROWSER (
    start "" /b powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 4; Start-Process 'http://127.0.0.1:%PORT%'"
)

if /i not "%MODE%"=="test" if not exist "%FIXTURE%" (
    echo ERRORE: file non trovato: %FIXTURE%
    goto :errore
)
if /i "%MODE%"=="salva" (
    if not exist "istanza" mkdir "istanza"
    "%VPF%" secure-web --fixture "%FIXTURE%" --local-user --store "istanza\store.sqlite" --audit "istanza\audit.jsonl" --port %PORT%
) else (
    "%VPF%" web --fixture "%FIXTURE%" --port %PORT%
)
goto :fine

REM ------------------------------------------------------------------- test
:test
echo [4/5] Eseguo i test ^(circa 2 minuti^)...
"%VPY%" -m unittest discover -s tests -t .
if errorlevel 1 goto :errore
echo.
echo Test superati.
goto :fine

REM ------------------------------------------------------------- sottoprogrammi
:cerca_python
for %%V in (3.14 3.13 3.12) do (
    if not defined PY (
        py -%%V -c "import sys" >nul 2>&1
        if not errorlevel 1 set "PY=py -%%V"
    )
)
if not defined PY (
    python -c "import sys;sys.exit(0 if sys.version_info>=(3,12) else 1)" >nul 2>&1
    if not errorlevel 1 set "PY=python"
)
exit /b 0

:errore
echo.
echo *** Qualcosa non ha funzionato. Leggi i messaggi sopra, oppure docs\guida-installazione.md ***
if not defined AVVIA_NOPAUSE pause
exit /b 1

:fine
endlocal
exit /b 0
