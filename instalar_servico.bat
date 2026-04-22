@echo off
chcp 65001 >nul
echo =============================================
echo   DriveBackup - Instalador
echo =============================================
echo.

:: Verifica se Python está instalado
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERRO] Python não encontrado!
    echo Instale o Python 3.8+ em: https://www.python.org/downloads/
    echo Marque "Add Python to PATH" durante a instalação.
    pause
    exit /b 1
)

echo [1/3] Instalando dependências...
cd /d "%~dp0"
pip install -r requirements.txt
if errorlevel 1 (
    echo [ERRO] Falha ao instalar dependências.
    pause
    exit /b 1
)

echo.
echo [2/3] Inicializando banco de dados...
python -c "import models; models.init_db(); print('Banco criado com sucesso!')"

echo.
echo [3/3] Criando atalho no Desktop...
set SCRIPT_DIR=%~dp0
set DESKTOP=%USERPROFILE%\Desktop

:: Cria script de inicialização
(
echo @echo off
echo cd /d "%SCRIPT_DIR%"
echo start "" "http://localhost:5555"
echo python app.py
) > "%SCRIPT_DIR%iniciar.bat"

:: Cria atalho no Desktop
(
echo Set oWS = WScript.CreateObject^("WScript.Shell"^)
echo sLinkFile = "%DESKTOP%\DriveBackup.lnk"
echo Set oLink = oWS.CreateShortcut^(sLinkFile^)
echo oLink.TargetPath = "%SCRIPT_DIR%iniciar.bat"
echo oLink.WorkingDirectory = "%SCRIPT_DIR%"
echo oLink.Description = "DriveBackup - Sistema de Backup"
echo oLink.Save
) > "%TEMP%\createLink.vbs"
cscript //nologo "%TEMP%\createLink.vbs"
del "%TEMP%\createLink.vbs"

echo.
echo =============================================
echo   Instalação concluída!
echo =============================================
echo.
echo   Para iniciar: clique em "DriveBackup" no Desktop
echo   Ou execute: python app.py
echo   Painel: http://localhost:5555
echo.
pause
