@echo off
REM Pre-downloads the Whisper model with retries. Run this if app.py fails to download.
cd /d "%~dp0"
call venv\Scripts\activate.bat
set HF_HUB_DISABLE_XET=1
set HF_HUB_ETAG_TIMEOUT=30

for /L %%i in (1,1,5) do (
    echo === Download attempt %%i of 5 ===
    python -c "from huggingface_hub import snapshot_download; snapshot_download('Systran/faster-distil-whisper-large-v3')" && goto :done
    echo Attempt failed, retrying in 5 seconds...
    timeout /t 5 /nobreak >nul
)
echo All attempts failed. Check firewall/antivirus or try different network.
pause
exit /b 1

:done
echo.
echo === Model downloaded successfully. Run run.bat ===
pause
