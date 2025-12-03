@echo off
chcp 65001 >nul
color 0A

echo ========================================
echo    Binance Auto Trading Bot Launcher
echo ========================================
echo.

:: Set Python path (based on Python 3.13 found in PATH environment variable)
set PYTHON_PATH=C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe

:: Check if Python is installed
%PYTHON_PATH% --version >nul 2>&1
if errorlevel 1 (
    echo Error: Python environment not detected!
    echo Please install Python 3.7 or higher first
    echo Download URL: https://www.python.org/downloads/
    pause
    exit /b 1
)

:: Check if pip is available
%PYTHON_PATH% -m pip --version >nul 2>&1
if errorlevel 1 (
    echo Error: pip package manager not detected!
    pause
    exit /b 1
)

:: Check and install dependencies
echo Checking dependencies...
%PYTHON_PATH% -c "import pandas" >nul 2>&1
if errorlevel 1 (
    echo Installing pandas...
    %PYTHON_PATH% -m pip install pandas
)

%PYTHON_PATH% -c "import binance" >nul 2>&1
if errorlevel 1 (
    echo Installing python-binance...
    %PYTHON_PATH% -m pip install python-binance
)

%PYTHON_PATH% -c "import requests" >nul 2>&1
if errorlevel 1 (
    echo Installing requests...
    %PYTHON_PATH% -m pip install requests
)

:: Create logs directory
if not exist "logs" mkdir logs

:: Run main program and take profit monitor
echo.
echo Starting Binance Trading System...
echo Start time: %date% %time%
echo Main program runs in current window, take profit monitor runs in new window
echo Press Ctrl+C to safely stop the program in current window
echo.

:: Start take profit monitor in new window
start "Binance Take Profit" cmd /k "%PYTHON_PATH% binance_take_profit.py"

:: Set console window title
title Binance Trading Bot

:: Run main program in current window
%PYTHON_PATH% binance_main.py

:: Post-processing after program ends
if errorlevel 1 (
    echo.
    echo Program error occurred, please check log files
    pause
)

echo.
echo Program exited normally
pause