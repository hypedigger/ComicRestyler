@echo off
rem Lance ComfyUI headless sur le port 8188 (requis pour la generation)
start "ComfyUI-pmds" /min "%USERPROFILE%\Documents\ComfyUI\.venv\Scripts\python.exe" "%LOCALAPPDATA%\Comfy-Desktop\ComfyUI-Installs\ComfyUI\ComfyUI\main.py" --port 8188 --listen 127.0.0.1 --base-directory "%USERPROFILE%\Documents\ComfyUI" --extra-model-paths-config "%USERPROFILE%\Documents\ComfyUI\extra_model_paths_xenogears.yaml" --disable-all-custom-nodes
echo ComfyUI demarre sur http://127.0.0.1:8188 (attendre ~20s)
