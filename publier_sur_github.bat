@echo off
title Envoi des modifications vers GitHub
set "PATH=%LOCALAPPDATA%\Programs\Git\cmd;%PATH%"
cd /d "C:\Prog2\Iris\FileSens"

echo ========================================================
echo   Envoi de vos modifications vers GitHub...
echo   Depot : https://github.com/pse05200/filesens
echo ========================================================
echo.

git add .
git commit -m "Mise a jour du %date% a %time%"
git push origin main

echo.
if %ERRORLEVEL% equ 0 (
    echo ========================================================
    echo   SUCCES : Vos modifications sont bien en ligne !
    echo   https://github.com/pse05200/filesens
    echo ========================================================
) else (
    echo ========================================================
    echo   ATTENTION : Une erreur est survenue lors de l'envoi.
    echo ========================================================
)
echo.
pause