@echo off
rem Lance PimpMyDrawingStyle (fenetre native, sans console)
start "PimpMyDrawingStyle" "%USERPROFILE%\Documents\ComfyUI\.venv\Scripts\pythonw.exe" "%~dp0app.py"
