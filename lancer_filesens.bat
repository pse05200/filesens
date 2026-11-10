@echo off
title FileSens - Interface Graphique
cd /d "%~dp0FileSens"
echo ========================================================
echo        Lancement de l'interface FileSens Web
echo ========================================================
echo.
echo Ouverture du navigateur sur http://localhost:7885 ...
start "" http://localhost:7885
echo Demarrage du serveur Flask...
python app.py
pause
