@echo off
chcp 65001 >nul
title Envoi des modifications vers GitHub
set PATH=%LOCALAPPDATA%\Programs\Git\cmd;%PATH%
cd /d "C:\Prog2\Iris\FileSens"

echo ========================================================
echo   Envoi de vos modifications vers GitHub...
echo   Dépôt : https://github.com/pse05200/filesens
echo ========================================================
echo.

git add .
git commit -m "Mise à jour du %date% à %time%"
git push -u origin main

echo.
if %errorlevel% equ 0 (
    echo ========================================================
    echo   SUCCÈS : Vos modifications sont bien en ligne !
    echo   https://github.com/pse05200/filesens
    echo ========================================================
) else (
    echo ========================================================
    echo   ATTENTION : Une erreur est survenue lors de l'envoi.
    echo ========================================================
)
echo.
pause
