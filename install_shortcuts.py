# -*- coding: utf-8 -*-
"""Crée les raccourcis ComicRestyler (Menu Démarrer + Bureau).

Chaque raccourci lance l'app sans console (pythonw), porte l'icône de l'app
ET le même AppUserModelID que la fenêtre : la barre des tâches groupe donc
l'app sous sa propre icône (épinglage compris).
"""
import os
import sys

import pythoncom
from win32com.propsys import propsys
from win32com.shell import shell, shellcon

APP_DIR = os.path.dirname(os.path.abspath(__file__))
APP_ID = "ComicRestyler.App"
PYTHONW = os.path.join(os.environ["USERPROFILE"],
                       r"Documents\ComfyUI\.venv\Scripts\pythonw.exe")
APP_PY = os.path.join(APP_DIR, "app.py")
ICON = os.path.join(APP_DIR, "app.ico")


def make_shortcut(lnk_path):
    link = pythoncom.CoCreateInstance(
        shell.CLSID_ShellLink, None, pythoncom.CLSCTX_INPROC_SERVER,
        shell.IID_IShellLink)
    link.SetPath(PYTHONW)
    link.SetArguments('"%s"' % APP_PY)
    link.SetWorkingDirectory(APP_DIR)
    link.SetIconLocation(ICON, 0)
    link.SetDescription("ComicRestyler — restylisation de pages "
                        "manga/BD via ComfyUI")
    # AppUserModelID : la barre des tâches groupe sous l'identité de l'app
    store = link.QueryInterface(propsys.IID_IPropertyStore)
    key = propsys.PSGetPropertyKeyFromName("System.AppUserModel.ID")
    store.SetValue(key, propsys.PROPVARIANTType(APP_ID))
    store.Commit()
    pf = link.QueryInterface(pythoncom.IID_IPersistFile)
    pf.Save(lnk_path, 0)
    print("Créé :", lnk_path)


def main():
    if not os.path.exists(PYTHONW):
        sys.exit("pythonw.exe introuvable : %s" % PYTHONW)
    start_menu = shell.SHGetFolderPath(0, shellcon.CSIDL_PROGRAMS, None, 0)
    desktop = shell.SHGetFolderPath(0, shellcon.CSIDL_DESKTOP, None, 0)
    for folder in (start_menu, desktop):
        make_shortcut(os.path.join(folder, "ComicRestyler.lnk"))
    print("Terminé. Si une ancienne épingle de la barre des tâches montre "
          "l'icône Python,\ndétache-la (clic droit > Détacher) puis épingle "
          "le nouveau raccourci.")


if __name__ == "__main__":
    main()
