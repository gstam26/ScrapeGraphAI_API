@echo off
rem Double-click launcher for the pipeline web UI (Windows).
rem Uses the project venv if present, otherwise the system python.
cd /d "%~dp0"
if exist .venv\Scripts\python.exe (
    .venv\Scripts\python.exe -m webapp
) else (
    python -m webapp
)
pause
