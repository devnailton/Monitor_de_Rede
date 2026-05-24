@echo off
setlocal
cd /d "d:\AAAPortfolio_Github\Monitoramento de Rede"

if not exist ".venv\Scripts\python.exe" (
    echo [setup] Criando ambiente virtual...
    python -m venv .venv
    if errorlevel 1 (
        echo [erro] Falha ao criar a venv. Verifique se o Python esta instalado.
        pause
        exit /b 1
    )
)

call ".venv\Scripts\activate.bat"

set "NEED_INSTALL=0"
if not exist ".venv\.requirements.ok" (
    set "NEED_INSTALL=1"
) else (
    for %%R in ("requirements.txt") do set "REQ_TIME=%%~tR"
    for %%S in (".venv\.requirements.ok") do set "STAMP_TIME=%%~tS"
    if not "%REQ_TIME%"=="%STAMP_TIME%" set "NEED_INSTALL=1"
)

if "%NEED_INSTALL%"=="1" (
    echo [setup] Instalando dependencias...
    python -m pip install --quiet --disable-pip-version-check -r requirements.txt
    if errorlevel 1 (
        echo [erro] Falha ao instalar dependencias.
        pause
        exit /b 1
    )
    copy /y "requirements.txt" ".venv\.requirements.ok" >nul
)

echo [run] Iniciando Monitor de Rede...
python -m streamlit run app.py

pause
endlocal
