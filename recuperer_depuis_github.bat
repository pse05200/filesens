@echo off
chcp 65001 >nul
title Récupération des mises à jour depuis GitHub
set PATH=%LOCALAPPDATA%\Programs\Git\cmd;%PATH%
cd /d "C:\Prog2\Iris\FileSens"

echo ========================================================
echo   Téléchargement des modifications depuis GitHub...
echo   Dépôt : https://github.com/pse05200/filesens
echo ========================================================
echo.

git pull origin main

echo.
if %errorlevel% equ 0 (
    echo ========================================================
    echo   SUCCÈS : Votre projet local est désormais à jour !
    echo ========================================================
) else (
    echo ========================================================
    echo   ATTENTION : Un problème est survenu lors de la
    echo   récupération (conflit de fichiers ou perte de connexion).
    echo ========================================================
)
echo.
pause
