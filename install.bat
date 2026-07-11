@echo off
echo === FlowLocal install ===
where python >nul 2>nul || (echo Python not found. Install Python 3.10+ from python.org & pause & exit /b 1)

python -m venv venv
call venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt

echo.
echo === Done ===
echo Optional cleanup pass needs Ollama: https://ollama.com then run:
echo    ollama pull llama3.2:3b
echo.
echo Start the app with run.bat
pause
