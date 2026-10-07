# Cree les raccourcis PimpMyDrawingStyle (Menu Demarrer + Bureau)
# Lancement sans console via pythonw.exe du venv ComfyUI, avec l'icone de l'app.
$ErrorActionPreference = "Stop"

$appDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonw = Join-Path $env:USERPROFILE "Documents\ComfyUI\.venv\Scripts\pythonw.exe"
$appPy = Join-Path $appDir "app.py"
$icon = Join-Path $appDir "app.ico"

if (-not (Test-Path $pythonw)) { throw "pythonw.exe introuvable : $pythonw" }
if (-not (Test-Path $appPy)) { throw "app.py introuvable : $appPy" }

$shell = New-Object -ComObject WScript.Shell
$targets = @(
    (Join-Path ([Environment]::GetFolderPath("Programs")) "PimpMyDrawingStyle.lnk"),
    (Join-Path ([Environment]::GetFolderPath("Desktop")) "PimpMyDrawingStyle.lnk")
)
foreach ($lnkPath in $targets) {
    $lnk = $shell.CreateShortcut($lnkPath)
    $lnk.TargetPath = $pythonw
    $lnk.Arguments = '"' + $appPy + '"'
    $lnk.WorkingDirectory = $appDir
    $lnk.IconLocation = "$icon,0"
    $lnk.Description = "PimpMyDrawingStyle - restylisation de pages manga/BD via ComfyUI"
    $lnk.Save()
    Write-Host "Cree : $lnkPath"
}
Write-Host "Termine. L'app est disponible dans le menu Demarrer (taper 'Pimp')."
