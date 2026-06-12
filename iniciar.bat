@echo off
title Beehus - Controle
cd /d "%~dp0"

echo Encerrando versao anterior...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :5002 ^| findstr LISTENING 2^>nul') do (
    taskkill /PID %%a /F >nul 2>&1
)

echo Atualizando codigo...
git pull

echo.
echo Iniciando servidor...
timeout /t 1 /nobreak >nul
start "" "http://localhost:5002"
python app.py
pause
