@echo off
REM Always run the API from the backend folder so "import crud" works.
cd /d "%~dp0"
echo Starting API from: %CD%
echo.
uvicorn main:app --reload --port 8000
pause
