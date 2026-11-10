@echo off
title Recuperation des modifications depuis GitHub
set "PATH=%LOCALAPPDATA%\Programs\Git\cmd;%PATH%"
cd /d "C:\Prog2\Iris\FileSens"

echo ========================================================
echo   Telechargement des modifications depuis GitHub...
echo   Depot : https://github.com/pse05200/filesens
echo ========================================================
echo.

git pull origin main

echo.
if %ERRORLEVEL% equ 0 (
    echo ========================================================
    echo   SUCCES : Votre projet local est desormais a jour !
    echo ========================================================
) else (
    echo ========================================================
    echo   ATTENTION : Une erreur est survenue lors de la
    echo   recuperation.
    echo ========================================================
)
echo.
pause