@echo off
title Beehus - Controle
cd /d "%~dp0"
echo Atualizando codigo...
git pull
echo.
echo Iniciando servidor...
start "" "http://localhost:5002"
python app.py
pause