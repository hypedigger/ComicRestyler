# -*- coding: utf-8 -*-
"""ComicRestyler — restylise des pages de manga/BD/comics via ComfyUI.

Interface ttkbootstrap à FENÊTRE UNIQUE : les vues (accueil, grille, page
avant/après, zoom 100 %, options, réglages Custom) se remplacent dans la même
fenêtre ; seuls les sélecteurs de fichiers/dossiers ouvrent un dialogue.
19 thèmes (apps populaires + éditeurs de texte), variantes multiples par page,
export de l'image gardée.
"""
import os
import queue
import re
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, messagebox

import ttkbootstrap as tb
from PIL import Image, ImageTk

import mangalib as ml
import themes

DEFAULT_COLS, DEFAULT_ROWS = 5, 3
THUMB_MIN, THUMB_MAX = 120, 280
SMOKE = bool(os.environ.get("PMDS_SMOKE"))

EDIT_CLASSES = ("TEntry", "TSpinbox", "TCombobox", "Entry", "Spinbox")
TRANSIENT_VIEWS = ("zoom", "recent", "storage")
FILETYPES = [("Images", "*.png *.jpg *.jpeg *.webp *.bmp"), ("Tous", "*.*")]


def work_area():
    """(left, top, right, bottom) de la zone de travail (hors barre des tâches)."""
    try:
        import ctypes

        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
        r = RECT()
        ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0)
        return r.left, r.top, r.right, r.bottom
    except Exception:
        return 0, 0, 1920, 1080


def _in_editor(event):
    """Vrai si l'événement clavier vient d'un champ de saisie."""
    try:
        return event.widget.winfo_class() in EDIT_CLASSES
    except Exception:
        return False


class Tooltip:
    """Infobulle au survol : fenêtre légère sans bordure ni entrée dans la
    barre des tâches (overrideredirect), stylée selon le thème."""

    _tip = None

    def __init__(self, widget, text, delay=450):
        self.widget = widget
        self.text = text
        self.delay = delay
        self._after = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _e=None):
        self._cancel()
        self._after = self.widget.after(self.delay, self._show)

    def _cancel(self):
        if self._after:
            try:
                self.widget.after_cancel(self._after)
            except Exception:
                pass
            self._after = None

    def _show(self):
        self._hide_tip()
        w = self.widget
        try:
            colors = w.winfo_toplevel().style.colors
            bg = _blend(colors.bg, colors.fg, 0.06)
            fg, border = colors.fg, colors.primary
        except Exception:
            bg, fg, border = "#222222", "#eeeeee", "#888888"
        try:
            tip = tk.Toplevel(w)
            tip.wm_overrideredirect(True)  # pas de fenêtre dans la barre
            tip.attributes("-topmost", True)
            frame = tk.Frame(tip, bg=border)
            frame.pack()
            tk.Label(frame, text=self.text, justify="left", wraplength=400,
                     bg=bg, fg=fg, padx=10, pady=7,
                     font=("Segoe UI", 9)).pack(padx=1, pady=1)
            x = w.winfo_rootx() + 12
            y = w.winfo_rooty() + w.winfo_height() + 6
            tip.update_idletasks()
            sw = tip.winfo_screenwidth()
            if x + tip.winfo_width() > sw - 10:
                x = sw - tip.winfo_width() - 10
            tip.wm_geometry("+%d+%d" % (x, y))
            Tooltip._tip = tip
        except Exception:
            pass

    def _hide(self, _e=None):
        self._cancel()
        self._hide_tip()

    @staticmethod
    def _hide_tip():
        if Tooltip._tip is not None:
            try:
                Tooltip._tip.destroy()
            except Exception:
                pass
            Tooltip._tip = None


def tip(widget, text):
    """Attache une infobulle et retourne le widget (chaînable)."""
    Tooltip(widget, text)
    return widget


NAV_KEYS = ("Up", "Down", "Left", "Right", "Return", "Escape", "Tab",
            "Home", "End", "Shift_L", "Shift_R", "Control_L", "Control_R")


def _ensure_chime():
    """Génère (une fois) un petit carillon doux — arpège sol-do-mi en sinus
    avec décroissance, volume modéré. Retourne le chemin du wav."""
    path = os.path.join(ml.APP_DIR, "done.wav")
    if os.path.exists(path):
        return path
    import wave

    import numpy as np
    sr = 44100
    dur = 1.15
    t = np.arange(int(sr * dur)) / float(sr)
    sig = np.zeros_like(t)
    for f, start in ((392.0, 0.0), (523.25, 0.16), (659.25, 0.32)):
        tt = t - start
        env = np.where(tt > 0, np.exp(-np.maximum(tt, 0) / 0.30), 0.0)
        sig += env * (np.sin(2 * np.pi * f * tt)
                      + 0.22 * np.sin(4 * np.pi * f * tt))
    sig *= 0.26 / (np.max(np.abs(sig)) + 1e-9)   # volume doux
    sig *= np.minimum(1.0, (dur - t) / 0.18)     # fondu de fin
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((sig * 32767).astype(np.int16).tobytes())
    return path


def play_chime():
    """Joue le carillon de fin (asynchrone, silencieux en cas d'échec)."""
    try:
        import winsound
        winsound.PlaySound(_ensure_chime(),
                           winsound.SND_FILENAME | winsound.SND_ASYNC
                           | winsound.SND_NODEFAULT)
    except Exception:
        pass


def make_filterable(combo, all_values):
    """Combobox filtrable au clavier : taper une partie du nom réduit la
    liste déroulante aux éléments qui la contiennent."""
    values = list(all_values)
    combo["values"] = values

    def on_key(e=None):
        if e is not None and getattr(e, "keysym", "") in NAV_KEYS:
            return
        txt = combo.get().strip().lower()
        matches = [v for v in values if txt in v.lower()] if txt else values
        combo["values"] = matches or values

    combo.bind("<KeyRelease>", on_key, add="+")
    combo.apply_filter = on_key  # testable / réutilisable
    return combo


def _blend(hex1, hex2, t):
    """Mélange deux couleurs hex (t=0 -> hex1, t=1 -> hex2)."""
    a = [int(hex1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(hex2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(int(x + (y - x) * t) for x, y in zip(a, b))


class ModernProgress(tk.Canvas):
    """Barre de progression en pilule : remplissage adouci (easing),
    reflet animé qui balaye la partie remplie, pourcentage centré."""

    W, H = 250, 18

    def __init__(self, master, app):
        super().__init__(master, width=self.W, height=self.H,
                         highlightthickness=0, bd=0)
        self.app = app
        self.value = 0.0    # progression affichée (animée)
        self.target = 0.0
        self.done = 0
        self.total = 0
        self._anim = None
        self._shimmer = 0.0
        self.restyle()

    def restyle(self):
        try:
            self.configure(bg=self.app.style.colors.bg)
        except Exception:
            pass
        self.redraw()

    def set_progress(self, done, total):
        self.done, self.total = done, total
        self.target = 0.0 if total <= 0 else min(1.0, done / float(total))
        if self._anim is None:
            self._tick()

    def _pill(self, x0, y0, x1, y1, color):
        r = (y1 - y0) / 2.0
        if x1 - x0 < 2 * r:
            x1 = x0 + 2 * r
        self.create_oval(x0, y0, x0 + 2 * r, y1, fill=color, outline="")
        self.create_oval(x1 - 2 * r, y0, x1, y1, fill=color, outline="")
        self.create_rectangle(x0 + r, y0, x1 - r, y1, fill=color, outline="")

    def _tick(self):
        self.value += (self.target - self.value) * 0.18
        if abs(self.target - self.value) < 0.002:
            self.value = self.target
        self._shimmer = (self._shimmer + 0.022) % 1.35
        self.redraw()
        busy = self.total and self.done < self.total
        settling = abs(self.target - self.value) > 0.002
        if busy or settling:
            self._anim = self.after(33, self._tick)
        else:
            self._anim = None

    def redraw(self):
        try:
            colors = self.app.style.colors
        except Exception:
            return
        self.delete("all")
        w, h = self.W, self.H
        pad = 1
        track = _blend(colors.bg, colors.fg, 0.13)
        self._pill(pad, pad, w - pad, h - pad, track)
        fw = int((w - 2 * pad) * self.value)
        if fw > h:
            base = colors.primary
            self._pill(pad, pad + 1, pad + fw, h - pad - 1, base)
            # léger dégradé : moitié haute plus claire
            self.create_rectangle(pad + h // 2, pad + 2, pad + fw - h // 2,
                                  h // 2, fill=_blend(base, "#ffffff", 0.18),
                                  outline="")
            # reflet qui balaye la zone remplie
            if self.total and self.done < self.total:
                sx = pad + int(self._shimmer * (w - 2 * pad)) - 30
                x0 = max(pad + 2, sx)
                x1 = min(pad + fw - 2, sx + 34)
                if x1 > x0:
                    self.create_rectangle(x0, pad + 2, x1, h - pad - 2,
                                          fill=_blend(base, "#ffffff", 0.35),
                                          outline="")
        if self.total:
            pct = int(round(100 * self.value))
            txt = "%d %%  ·  %d/%d" % (pct, self.done, self.total)
            self.create_text(w // 2 + 1, h // 2 + 1, text=txt,
                             font=("Segoe UI Semibold", 8),
                             fill=_blend(colors.bg, "#000000", 0.4))
            self.create_text(w // 2, h // 2, text=txt,
                             font=("Segoe UI Semibold", 8), fill=colors.fg)


class App(tb.Window):
    def __init__(self):
        self.settings = ml.load_settings()
        if not self.settings.get("macos_default_applied"):
            # nouveau design par défaut : macOS sombre (changeable en options)
            self.settings["theme"] = "macos_dark"
            self.settings["macos_default_applied"] = True
            ml.save_settings(self.settings)
        theme = self.settings.get("theme")
        if theme not in themes.APP_THEMES:
            theme = "macos_dark"
        super().__init__(title=ml.APP_NAME, themename="darkly",
                         iconphoto=None)  # pas d'icône ttkbootstrap par défaut
        themes.register_all(self.style)
        self.style.theme_use(theme)
        self.configure(bg=self.style.colors.bg)
        try:  # icône fenêtre
            ico = os.path.join(ml.APP_DIR, "app.ico")
            if os.path.exists(ico):
                self.iconbitmap(default=ico)
        except Exception:
            pass
        self.after(250, self._apply_taskbar_icon)

        self.styles_cfg = ml.load_styles()
        self.style_labels = {k: v.get("label", k)
                             for k, v in self.styles_cfg["styles"].items()}
        self.label_to_key = {v: k for k, v in self.style_labels.items()}

        self.scan_dir = None
        self.pages = []          # pages référencées (scan ou fichiers choisis)
        self.file_mode = False   # vrai si sélection manuelle de fichiers
        self.view = []           # pages affichées (tri + filtre)
        self.grid_start = 0
        self.checked = set()
        self._thumb_cache = {}
        self._thumb_after = None
        self._opts_win = None
        self._custom_panel = None

        self.job_q = queue.Queue()
        self.msg_q = queue.Queue()
        self.thumb_q = queue.Queue()   # chargement asynchrone des vignettes
        self._thumb_gen = 0
        self._thumb_pending = {}
        self._cell_expect = {}
        self.jobs_total = 0
        self.jobs_done = 0
        self._chimed = True
        self.stop_flag = threading.Event()
        self.comfy_ok = False

        self.thumb_w = max(THUMB_MIN, min(THUMB_MAX,
                                          int(self.settings.get("thumb_w", 180))))
        self.strip_w = max(80, min(260, int(self.settings.get("strip_w", 130))))
        self.grid_cols, self.grid_rows = DEFAULT_COLS, DEFAULT_ROWS
        self._grid_size_after = None
        self._strip_after = None
        self.views = {}
        self.view_stack = ["welcome"]
        self._build_ui()
        wl, wt, _wr, _wb = work_area()
        self.geometry("+%d+%d" % (wl + 20, wt + 10))
        self.state("zoomed")  # plein écran de travail : la grille s'y adapte
        threading.Thread(target=self._worker, daemon=True).start()
        threading.Thread(target=self._comfy_watch, daemon=True).start()
        threading.Thread(target=self._thumb_worker, daemon=True).start()
        self.after(120, self._poll_msgs)

        start = self.settings.get("last_dir") or ""
        if SMOKE and start and os.path.isdir(start):
            self.after(100, lambda: self.set_dir(start))

    @property
    def thumb_h(self):
        return int(self.thumb_w * 1.4)

    @property
    def page_size(self):
        return self.grid_cols * self.grid_rows

    def _apply_taskbar_icon(self):
        """Force l'icône de la barre des tâches via WM_SETICON (l'iconbitmap
        de Tk ne définit pas toujours la grande icône sous Windows)."""
        try:
            import ctypes
            import ctypes.wintypes as wt
            ico = os.path.join(ml.APP_DIR, "app.ico")
            if not os.path.exists(ico):
                return
            u32 = ctypes.windll.user32
            u32.LoadImageW.restype = wt.HANDLE
            u32.LoadImageW.argtypes = [wt.HINSTANCE, wt.LPCWSTR,
                                       ctypes.c_uint, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_uint]
            u32.SendMessageW.restype = ctypes.c_ssize_t
            u32.SendMessageW.argtypes = [wt.HWND, ctypes.c_uint,
                                         ctypes.c_size_t, ctypes.c_ssize_t]
            hwnd = int(self.wm_frame(), 16)
            IMAGE_ICON, LR_LOADFROMFILE = 1, 0x10
            WM_SETICON = 0x80
            for which, size in ((1, 256), (0, 32)):  # ICON_BIG, ICON_SMALL
                hicon = u32.LoadImageW(None, ico, IMAGE_ICON, size, size,
                                       LR_LOADFROMFILE)
                if hicon:
                    u32.SendMessageW(hwnd, WM_SETICON, which, hicon)
        except Exception:
            pass

    # ------------------------------------------------------------------ UI

    def _build_ui(self):
        # ---- en-tête
        header = tb.Frame(self, padding=(12, 8, 12, 0))
        header.pack(fill="x")
        tb.Label(header, text="🎨 " + ml.APP_NAME,
                 font=("Segoe UI Semibold", 15),
                 bootstyle="primary").pack(side="left")
        tip(tb.Button(header, text="📤  Exporter", bootstyle="primary",
                      command=self.export),
            "Enregistre l'image gardée (★) de chaque page : en mode dossier "
            "dans « <dossier>_stylized » à côté des originaux ; en mode "
            "images avec un nom horodaté. Les originaux ne sont jamais "
            "modifiés.").pack(side="right", padx=(6, 0))
        tip(tb.Button(header, text="⚙  Options", bootstyle="info-outline",
                      command=self.open_options),
            "Réglages de l'application : thème, génération, bulles de texte, "
            "ComfyUI, export.").pack(side="right", padx=(6, 0))
        tip(tb.Button(header, text="🗄  Stockage", bootstyle="info-outline",
                      command=self.open_storage),
            "Espace disque occupé par les variantes générées, avec nettoyage "
            "par série. Ne touche jamais aux originaux.").pack(
            side="right", padx=(6, 0))
        tip(tb.Button(header, text="🕘  Récentes", bootstyle="info-outline",
                      command=self.open_recent),
            "Toutes les dernières images générées, de la plus récente à la "
            "plus ancienne. Clic = ouvrir la page.").pack(side="right")

        bar1 = tb.Frame(self, padding=(12, 8, 12, 4))
        bar1.pack(fill="x")
        tip(tb.Button(bar1, text="📁 Dossier…", bootstyle="info-outline",
                      command=self.choose_dir),
            "Scanner un dossier complet (sous-dossiers = "
            "chapitres).").pack(side="left")
        tip(tb.Button(bar1, text="🖼 Images…", bootstyle="info-outline",
                      command=self.choose_files),
            "Travailler sur une ou quelques images seulement, sans scanner "
            "tout le dossier.").pack(side="left", padx=(4, 0))
        self.dir_label = tb.Label(bar1, text="(aucun dossier)")
        self.dir_label.pack(side="left", padx=(8, 18))
        tb.Label(bar1, text="Style").pack(side="left")
        self.style_var = tk.StringVar()
        self.style_box = tb.Combobox(
            bar1, textvariable=self.style_var, state="readonly", width=30,
            values=sorted(self.style_labels.values(), key=str.lower))
        last_style = self.settings.get("style")
        if last_style in self.style_labels:
            self.style_var.set(self.style_labels[last_style])
        else:
            self.style_box.current(0)
        self.style_box.bind("<<ComboboxSelected>>", lambda e: self._style_changed())
        self.style_box.pack(side="left", padx=(6, 16))
        tip(self.style_box, "Le style d'auteur appliqué aux pages. Chaque "
                            "style a ses propres variantes, sélections et "
                            "réglages (modifiables via ⚙ Custom).")
        tb.Label(bar1, text="Tri").pack(side="left")
        self.sort_var = tk.StringVar(
            value="Date" if self.settings.get("sort_pages") == "date" else "Nom")
        sort_box = tb.Combobox(bar1, textvariable=self.sort_var, state="readonly",
                               values=["Nom", "Date"], width=6)
        sort_box.bind("<<ComboboxSelected>>", lambda e: self._sort_changed())
        sort_box.pack(side="left", padx=(6, 16))
        tb.Label(bar1, text="Afficher").pack(side="left")
        self.filter_var = tk.StringVar(value="Toutes")
        fbox = tb.Combobox(bar1, textvariable=self.filter_var,
                           state="readonly", width=10,
                           values=["Toutes", "À traiter", "Générées"])
        fbox.bind("<<ComboboxSelected>>", lambda e: self.refresh_grid())
        fbox.pack(side="left", padx=(6, 0))
        tip(fbox, "Filtre de la grille : « À traiter » = pages sans aucune "
                  "variante pour ce style · « Générées » = seulement les "
                  "pages qui ont déjà des variantes.")

        # ---- barre génération / sélection (visible en vue grille seulement)
        bar2 = self.bar2 = tb.Frame(self, padding=(12, 2, 12, 6))
        bar2.pack(fill="x")
        tip(tb.Button(bar2, text="▶  Générer tout", bootstyle="primary",
                      command=self.generate_all),
            "Génère toutes les pages affichées qui n'ont pas encore de "
            "variante (~18 s par page).").pack(side="left")
        tip(tb.Button(bar2, text="Générer les", bootstyle="primary-outline",
                      command=self.generate_first_n),
            "Génère seulement les N premières pages — pratique pour tester "
            "un style avant de lancer tout le chapitre.").pack(
            side="left", padx=(8, 2))
        self.first_n_var = tk.IntVar(value=int(self.settings.get("first_n", 15)))
        tb.Spinbox(bar2, from_=1, to=9999, width=5,
                   textvariable=self.first_n_var).pack(side="left")
        tb.Label(bar2, text="premières").pack(side="left", padx=(2, 8))
        self.gen_sel_btn = tb.Button(bar2, text="Générer la sélection (0)",
                                     bootstyle="primary-outline",
                                     command=self.generate_selection)
        self.gen_sel_btn.pack(side="left", padx=(0, 10))
        tip(self.gen_sel_btn, "Génère de nouvelles variantes pour les pages "
                              "cochées (case en haut à gauche des vignettes).")
        tb.Label(bar2, text="×").pack(side="left")
        self.qty_var = tk.IntVar(value=int(self.settings.get("gen_variants", 1)))
        tip(tb.Spinbox(bar2, from_=1, to=10, width=3,
                       textvariable=self.qty_var),
            "Nombre de variantes générées par page (seeds différentes) — tu "
            "garderas la meilleure ensuite.").pack(side="left", padx=(2, 2))
        tb.Label(bar2, text="variante(s)").pack(side="left", padx=(0, 10))
        self.force_var = tk.BooleanVar(value=False)
        tip(tb.Checkbutton(bar2, text="refaire les déjà générées",
                           variable=self.force_var,
                           bootstyle="round-toggle"),
            "Coché : les pages qui ont déjà des variantes en reçoivent de "
            "nouvelles au lieu d'être sautées.").pack(side="left")
        self.colorize_var = tk.BooleanVar(
            value=bool(self.settings.get("colorize", False)))
        tip(tb.Checkbutton(bar2, text="🎨 coloriser",
                           variable=self.colorize_var,
                           command=self._save_colorize,
                           bootstyle="success-round-toggle"),
            "Met les pages noir & blanc en couleurs, en plus du style "
            "choisi.").pack(side="left", padx=(12, 0))

        tip(tb.Button(bar2, text="Dernière génér.", bootstyle="info-outline",
                      command=lambda: self.bulk_select("last")),
            "Pour chaque page : garde la variante la plus récente "
            "(l'original si aucune).").pack(side="right")
        tip(tb.Button(bar2, text="1ʳᵉ génér.", bootstyle="info-outline",
                      command=lambda: self.bulk_select("first")),
            "Pour chaque page : garde la première variante générée "
            "(l'original si aucune).").pack(side="right", padx=6)
        tb.Label(bar2, text="Garder partout :").pack(side="right", padx=(0, 4))
        tip(tb.Button(bar2, text="✗", width=3, bootstyle="info-outline",
                      command=self.check_none),
            "Décoche toutes les pages.").pack(side="right", padx=(2, 16))
        tip(tb.Button(bar2, text="✓ tout", bootstyle="info-outline",
                      command=self.check_all),
            "Coche toutes les pages affichées.").pack(side="right")
        tb.Label(bar2, text="Cocher :").pack(side="right", padx=(0, 4))

        self.info_label = tb.Label(self, padding=(14, 2), font=("Segoe UI", 10, "bold"))
        self.info_label.pack(fill="x")

        # ---- barre d'état (packée avant le conteneur : garde sa place)
        bottom = tb.Frame(self, padding=(12, 6))
        bottom.pack(fill="x", side="bottom")
        self.comfy_dot = tb.Label(bottom, text="●", bootstyle="danger",
                                  font=("Segoe UI", 13))
        self.comfy_dot.pack(side="left")
        self.comfy_label = tb.Label(bottom, text="ComfyUI arrêté")
        self.comfy_label.pack(side="left", padx=(3, 8))
        self.comfy_btn = tb.Button(bottom, text="Lancer ComfyUI",
                                   bootstyle="warning-outline",
                                   command=self.launch_comfy)
        self.comfy_btn.pack(side="left", padx=(0, 16))
        # deux sliders indépendants : grille / vignettes de variantes,
        # affichés selon la vue active
        self.slider_zone = tb.Frame(bottom)
        self.slider_zone.pack(side="right", padx=(8, 0))
        tb.Label(self.slider_zone, text="🔍").pack(side="left")
        self.thumb_var = tk.IntVar(value=self.thumb_w)
        self.grid_scale = tb.Scale(self.slider_zone, from_=THUMB_MIN,
                                   to=THUMB_MAX, variable=self.thumb_var,
                                   length=140, command=self._thumb_slider)
        self.strip_var = tk.IntVar(value=self.strip_w)
        self.strip_scale = tb.Scale(self.slider_zone, from_=80, to=260,
                                    variable=self.strip_var, length=140,
                                    command=self._strip_slider)
        self.grid_scale.pack(side="left", padx=(4, 0))
        self.stop_btn = tb.Button(bottom, text="■  Stop", bootstyle="danger-outline",
                                  command=self.stop_jobs, state="disabled")
        self.stop_btn.pack(side="right", padx=(0, 14))
        self.progress = ModernProgress(bottom, self)
        self.progress.pack(side="right", padx=10)
        self.status_label = tb.Label(bottom, text="Prêt.")
        self.status_label.pack(side="left", fill="x", expand=True)

        # ---- conteneur des vues
        self.container = tb.Frame(self)
        self.container.pack(fill="both", expand=True)
        self._build_welcome()
        self._build_grid_view()
        self.views["welcome"] = self.welcome
        self.views["grid"] = self.grid_view
        self.welcome.pack(fill="both", expand=True)
        self.after(50, self._update_sliders)

        # ---- raccourcis globaux (routés selon la vue active)
        self.bind("<Left>", self._key_left)
        self.bind("<Right>", self._key_right)
        self.bind("<Up>", self._key_up)
        self.bind("<Escape>", self._key_escape)
        self.bind("<Delete>", self._key_delete)
        self.bind("<Key>", self._key_digit)
        self.bind("<MouseWheel>", self._on_wheel)

    def _tile(self, parent, emoji, title, desc, command):
        """Grande tuile cliquable avec effet de survol (style macOS)."""
        colors = self.style.colors
        base = _blend(colors.bg, colors.fg, 0.055)
        hover = _blend(colors.bg, colors.fg, 0.11)
        t = tk.Frame(parent, bg=base, highlightthickness=1,
                     highlightbackground=_blend(colors.bg, colors.fg, 0.16),
                     cursor="hand2")
        tk.Label(t, text=emoji, font=("Segoe UI Emoji", 30), bg=base,
                 fg=colors.fg).pack(pady=(22, 6), padx=60)
        tk.Label(t, text=title, font=("Segoe UI Semibold", 13), bg=base,
                 fg=colors.fg).pack()
        tk.Label(t, text=desc, font=("Segoe UI", 9), bg=base,
                 fg=_blend(colors.bg, colors.fg, 0.62),
                 justify="center").pack(pady=(4, 22), padx=24)
        widgets = [t] + list(t.winfo_children())
        for w in widgets:
            w.bind("<Button-1>", lambda e: command())
            w.bind("<Enter>", lambda e: [x.configure(bg=hover)
                                         for x in widgets])
            w.bind("<Leave>", lambda e: [x.configure(bg=base)
                                         for x in widgets])
        return t

    def _build_welcome(self):
        colors = self.style.colors
        self.welcome = tb.Frame(self.container)
        inner = tb.Frame(self.welcome)
        inner.pack(expand=True)

        # icône + titre + slogan
        try:
            with Image.open(os.path.join(ml.APP_DIR,
                                         "app_icon_preview.png")) as im:
                im = im.resize((104, 104), Image.LANCZOS)
                self._welcome_icon = ImageTk.PhotoImage(im)
            tb.Label(inner, image=self._welcome_icon).pack(pady=(0, 14))
        except Exception:
            tb.Label(inner, text="🎨", font=("Segoe UI Emoji", 42)).pack(
                pady=(0, 10))
        tb.Label(inner, text=ml.APP_NAME,
                 font=("Segoe UI Semibold", 24)).pack()
        tk.Label(inner, text="Restyle tes mangas, BD et comics dans le style "
                             "d'un autre auteur — bulles de texte préservées.",
                 font=("Segoe UI", 11), bg=colors.bg,
                 fg=_blend(colors.bg, colors.fg, 0.62)).pack(pady=(6, 30))

        # deux grandes tuiles d'action
        tiles = tb.Frame(inner)
        tiles.pack()
        self._tile(tiles, "📁", "Ouvrir un dossier",
                   "Un chapitre ou une série complète\n(sous-dossiers "
                   "inclus)", self.choose_dir).pack(side="left", padx=10)
        self._tile(tiles, "🖼", "Choisir des images",
                   "Une ou quelques pages seulement,\npour essayer un style",
                   self.choose_files).pack(side="left", padx=10)

        # accès rapides
        quick = tb.Frame(inner)
        quick.pack(pady=(30, 0))
        tk.Label(quick, text="Accès rapide", font=("Segoe UI", 9), bg=colors.bg,
                 fg=_blend(colors.bg, colors.fg, 0.5)).pack(side="left",
                                                            padx=(0, 10))
        lib = self.settings.get("library_dir") or ""
        if lib and os.path.isdir(lib):
            tip(tb.Button(quick, text="📖  " + lib, bootstyle="info-outline",
                          command=lambda: self.choose_dir(initial=lib)),
                "Ouvre le sélecteur de dossier directement dans ta "
                "bibliothèque de lecture.").pack(side="left", padx=4)
        last = self.settings.get("last_dir") or ""
        if last and os.path.isdir(last):
            short = last if len(last) <= 45 else "…" + last[-43:]
            tip(tb.Button(quick, text="↩  " + short, bootstyle="info-outline",
                          command=lambda: self.set_dir(last)),
                "Recharge directement le dernier dossier utilisé "
                "(%s)." % last).pack(side="left", padx=4)

        tk.Label(inner, text="Les originaux ne sont jamais modifiés — les "
                             "variantes vivent à part et tu exportes quand "
                             "tu veux.",
                 font=("Segoe UI", 9), bg=colors.bg,
                 fg=_blend(colors.bg, colors.fg, 0.45)).pack(pady=(34, 0))

    def _build_grid_view(self):
        self.grid_view = tb.Frame(self.container)
        # pagination collée en bas de l'app
        nav = tb.Frame(self.grid_view, padding=(8, 2, 8, 2))
        nav.pack(side="bottom", fill="x")
        inner = tb.Frame(nav)
        inner.pack()
        tb.Button(inner, text="◀   Précédentes", bootstyle="secondary",
                  command=lambda: self.turn(-1)).pack(side="left", padx=6)
        self.nav_label = tb.Label(inner, text="", width=24, anchor="center",
                                  font=("Segoe UI", 10))
        self.nav_label.pack(side="left", padx=10)
        tb.Button(inner, text="Suivantes   ▶", bootstyle="secondary",
                  command=lambda: self.turn(1)).pack(side="left", padx=6)
        # la grille occupe tout le reste et s'adapte à la taille
        self.grid_frame = tb.Frame(self.grid_view, padding=6)
        self.grid_frame.pack(expand=True)
        self.cells = []
        self._build_grid()
        self.grid_view.bind("<Configure>", self._on_grid_resize)

    # ------------------------------------------------------------------ vues

    def current_view(self):
        return self.view_stack[-1]

    def show(self, name, frame=None):
        """Affiche une vue dans la fenêtre unique (empile pour le retour)."""
        cur = self.view_stack[-1]
        self.views[cur].pack_forget()
        if frame is not None:
            old = self.views.pop(name, None)
            if old is not None and old.winfo_exists():
                old.destroy()
            self.views[name] = frame
        self.views[name].pack(fill="both", expand=True)
        self.view_stack.append(name)
        self._update_sliders()

    def back(self):
        if len(self.view_stack) < 2:
            return
        cur = self.view_stack.pop()
        f = self.views[cur]
        f.pack_forget()
        if cur in TRANSIENT_VIEWS:
            f.destroy()
            self.views.pop(cur, None)
        prev = self.view_stack[-1]
        self.views[prev].pack(fill="both", expand=True)
        self._update_sliders()
        if prev == "grid":
            self.refresh_grid()
        elif prev == "page":
            self.views["page"].refresh()

    def _reset_to_grid(self):
        cur = self.view_stack[-1]
        self.views[cur].pack_forget()
        for name in list(self.views):
            if name in TRANSIENT_VIEWS:
                self.views[name].destroy()
                del self.views[name]
        self.view_stack = ["grid"]
        self.views["grid"].pack(fill="both", expand=True)
        self._update_sliders()

    def open_page(self, page):
        if "page" not in self.views:
            self.views["page"] = PageView(self.container, self)
        self.views["page"].load(page)
        if self.current_view() != "page":
            self.show("page")
        else:
            self.views["page"].refresh()

    def open_zoom(self, path):
        self.show("zoom", ZoomView(self.container, self, path))

    def open_options(self):
        if self._opts_win is not None and self._opts_win.winfo_exists():
            self._opts_win.lift()
            return
        self._opts_win = OptionsView(self)

    def open_custom(self, page, anchor=None, preset=None):
        if self._custom_panel is not None and self._custom_panel.winfo_exists():
            self._custom_panel.destroy()
        self._custom_panel = CustomView(self, page, anchor, preset)

    def open_recent(self):
        if not self.scan_dir:
            messagebox.showinfo(ml.APP_NAME, "Choisis d'abord un dossier.")
            return
        if self.current_view() == "recent":
            return
        self.show("recent", RecentView(self.container, self))

    def open_storage(self):
        if self.current_view() == "storage":
            return
        self.show("storage", StorageView(self.container, self))

    # ---- raccourcis routés

    def _key_arrow(self, e, direction):
        if _in_editor(e):
            return
        v = self.current_view()
        if v == "grid":
            self.turn(direction)
        elif v == "page":
            if e.state & 0x4:  # Ctrl : page précédente / suivante
                self.views["page"].nav(direction)
            else:              # sans Ctrl : parcourir original + variantes
                self.views["page"].cycle(direction)

    def _key_left(self, e):
        self._key_arrow(e, -1)

    def _key_right(self, e):
        self._key_arrow(e, 1)

    def _key_up(self, e):
        if not _in_editor(e) and self.current_view() == "page":
            self.views["page"].keep_current()

    def _key_escape(self, e):
        if self.current_view() in ("page",) + TRANSIENT_VIEWS:
            self.back()

    def _key_delete(self, e):
        if not _in_editor(e) and self.current_view() == "page":
            self.views["page"].delete()

    def _key_digit(self, e):
        if _in_editor(e) or not e.char.isdigit():
            return
        if self.current_view() == "page":
            self.views["page"].keep_nth(int(e.char))

    def _on_wheel(self, e):
        """Molette : change de page dans la grille (et dans « Récentes »)."""
        if _in_editor(e):
            return
        step = -1 if e.delta > 0 else 1
        v = self.current_view()
        if v == "grid":
            self.turn(step)
        elif v == "recent":
            self.views["recent"].turn(step)
        elif v == "page":
            self.views["page"].scroll_strip(step)

    # ------------------------------------------------------------------ grille (widgets)

    def _grid_capacity(self):
        """(colonnes, lignes) qui tiennent dans l'espace disponible."""
        gw = self.grid_view.winfo_width()
        gh = self.grid_view.winfo_height()
        if gw < 80 or gh < 80:  # pas encore affiché
            return DEFAULT_COLS, DEFAULT_ROWS
        cols = max(1, (gw - 24) // (self.thumb_w + 26))
        rows = max(1, (gh - 60) // (self.thumb_h + 68))
        return int(cols), int(rows)

    def _on_grid_resize(self, _event):
        if self._grid_size_after:
            self.after_cancel(self._grid_size_after)
        self._grid_size_after = self.after(250, self._maybe_rebuild_grid)

    def _maybe_rebuild_grid(self):
        self._grid_size_after = None
        if self._grid_capacity() != (self.grid_cols, self.grid_rows):
            self._build_grid()
            self.refresh_grid()

    def _build_grid(self):
        self.grid_cols, self.grid_rows = self._grid_capacity()
        for child in self.grid_frame.winfo_children():
            child.destroy()
        self.cells = []
        tw, th = self.thumb_w, self.thumb_h
        for r in range(self.grid_rows):
            for c in range(self.grid_cols):
                cell = tk.Frame(self.grid_frame, width=tw + 16, height=th + 58,
                                highlightthickness=2)
                cell.grid(row=r, column=c, padx=5, pady=5)
                cell.pack_propagate(False)
                img = tb.Label(cell, cursor="hand2")
                img.pack(pady=(4, 0))
                check_var = tk.BooleanVar(value=False)
                check = tb.Checkbutton(cell, variable=check_var,
                                       bootstyle="primary")
                check.place(x=6, y=6)
                badge = tb.Label(cell, text="", bootstyle="inverse-primary",
                                 font=("Segoe UI", 9, "bold"), padding=(6, 1))
                cap = tb.Label(cell, font=("Segoe UI", 8),
                               wraplength=tw + 8, anchor="center",
                               justify="center")
                cap.pack(side="bottom", pady=(0, 3))
                self.cells.append({"cell": cell, "img": img, "cap": cap,
                                   "check": check, "check_var": check_var,
                                   "badge": badge})

    def _thumb_slider(self, _value):
        if self._thumb_after:
            self.after_cancel(self._thumb_after)
        self._thumb_after = self.after(250, self._apply_thumb_size)

    def _apply_thumb_size(self):
        self._thumb_after = None
        w = int(self.thumb_var.get())
        if w == self.thumb_w:
            return
        self.thumb_w = w
        self.settings["thumb_w"] = w
        ml.save_settings(self.settings)
        self._thumb_cache.clear()
        self._build_grid()
        self.refresh_grid()
        if self.current_view() == "recent":
            self.views["recent"].render()

    def _strip_slider(self, _value):
        if self._strip_after:
            self.after_cancel(self._strip_after)
        self._strip_after = self.after(250, self._apply_strip_size)

    def _apply_strip_size(self):
        self._strip_after = None
        w = int(self.strip_var.get())
        if w == self.strip_w:
            return
        self.strip_w = w
        self.settings["strip_w"] = w
        ml.save_settings(self.settings)
        if self.current_view() == "page":
            self.views["page"].refresh()

    def _update_sliders(self):
        """Adapte la barre d'outils et les sliders à la vue active."""
        v = self.current_view()
        # génération en lot : seulement sur la grille (en vue page, les
        # boutons ⟳/⚙ n'agissent que sur la page affichée)
        if v == "grid":
            if not self.bar2.winfo_ismapped():
                self.bar2.pack(fill="x", before=self.info_label)
        else:
            self.bar2.pack_forget()
        self.grid_scale.pack_forget()
        self.strip_scale.pack_forget()
        if v in ("grid", "recent"):
            self.grid_scale.pack(side="left", padx=(4, 0))
        elif v == "page":
            self.strip_scale.pack(side="left", padx=(4, 0))

    # ------------------------------------------------------------------ dossier / fichiers

    def choose_dir(self, initial=""):
        d = filedialog.askdirectory(
            title="Répertoire des chapitres à scanner",
            initialdir=initial or self.settings.get("last_dir") or "/")
        if d:
            self.set_dir(d)

    def choose_files(self):
        paths = filedialog.askopenfilenames(
            title="Choisir une ou plusieurs images",
            initialdir=self.settings.get("last_dir") or "/",
            filetypes=FILETYPES)
        if paths:
            self.set_files(paths)

    def set_dir(self, d):
        self.scan_dir = os.path.normpath(d)
        self.file_mode = False
        self.settings["last_dir"] = self.scan_dir
        ml.save_settings(self.settings)
        self._show_dir_label()
        self.busy("📁 Scan du dossier…")
        self.pages = ml.scan_pages(self.scan_dir)
        self.grid_start = 0
        self.checked.clear()
        self._reset_to_grid()
        self.refresh_grid()

    def set_files(self, paths):
        """Travaille sur une sélection d'images plutôt qu'un dossier complet."""
        paths = [os.path.normpath(p) for p in paths
                 if p.lower().endswith(ml.IMAGE_EXTS)]
        if not paths:
            messagebox.showwarning(ml.APP_NAME, "Aucune image reconnue "
                                   "dans la sélection.")
            return
        try:
            root = os.path.commonpath([os.path.dirname(p) for p in paths])
        except ValueError:
            root = os.path.dirname(paths[0])
        self.scan_dir = os.path.normpath(root)
        self.file_mode = True
        self.settings["last_dir"] = self.scan_dir
        ml.save_settings(self.settings)
        pages = []
        for p in paths:
            rel = os.path.relpath(p, self.scan_dir)
            pages.append({"path": p, "rel": rel,
                          "key": os.path.splitext(rel)[0].replace("\\", "/"),
                          "name": os.path.basename(p)})
        pages.sort(key=lambda pg: ml._natural_key(pg["rel"]))
        self.pages = pages
        self.grid_start = 0
        self.checked.clear()
        self._show_dir_label()
        self._reset_to_grid()
        self.refresh_grid()

    def _show_dir_label(self):
        short = self.scan_dir or ""
        if len(short) > 52:
            short = "…" + short[-50:]
        if self.file_mode:
            short += "  (%d image(s) choisies)" % len(self.pages or [])
        self.dir_label.config(text=short)

    def current_style(self):
        return self.label_to_key.get(self.style_var.get())

    def _style_changed(self):
        self.settings["style"] = self.current_style()
        ml.save_settings(self.settings)
        self.refresh_grid()
        if self.current_view() == "page":
            self.views["page"].refresh()

    def _save_colorize(self):
        self.settings["colorize"] = bool(self.colorize_var.get())
        ml.save_settings(self.settings)

    def _sort_changed(self):
        self.settings["sort_pages"] = \
            "date" if self.sort_var.get() == "Date" else "name"
        ml.save_settings(self.settings)
        self.grid_start = 0
        self.refresh_grid()

    def _compute_view(self):
        pages = list(self.pages)
        if self.settings.get("sort_pages") == "date":
            pages.sort(key=lambda p: os.path.getmtime(p["path"]))
        mode = self.filter_var.get()
        if mode != "Toutes" and self.scan_dir:
            style = self.current_style()
            want = (mode == "Générées")
            pages = [p for p in pages
                     if bool(ml.list_variants(self.scan_dir, style, p)) == want]
        return pages

    # ------------------------------------------------------------------ grille (contenu)

    # ------------------------------------------------------------------ occupé

    def busy(self, text):
        """Indique visuellement qu'un travail est en cours."""
        self.status_label.config(text=text)
        self.configure(cursor="watch")
        self.update_idletasks()

    def unbusy(self, text="Prêt."):
        if not self.jobs_total or self.jobs_done >= self.jobs_total:
            self.status_label.config(text=text)
        self.configure(cursor="")

    def _thumb_worker(self):
        """Charge les vignettes en arrière-plan (la grille reste fluide)."""
        while True:
            gen, idx, path, box = self.thumb_q.get()
            if gen != self._thumb_gen:
                self.msg_q.put(("thumb_ready", gen, idx, path, box, None))
                continue
            pil = None
            try:
                with Image.open(path) as im:
                    im.draft("RGB", (box[0] * 2, box[1] * 2))
                    im = im.convert("RGB")
                    im.thumbnail(box, Image.LANCZOS)
                    pil = im.copy()
            except Exception:
                pass
            self.msg_q.put(("thumb_ready", gen, idx, path, box, pil))

    @staticmethod
    def display_path(path):
        """Chemin rapide à afficher : les PNG de variantes passent par leur
        aperçu JPEG (décodage instantané), le PNG ne sert qu'au zoom 100 %."""
        if path.endswith(".png") and (os.sep + "variants" + os.sep) in path:
            return ml.variant_preview(path)
        return path

    def _thumb(self, path, box):
        path = self.display_path(path)
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return None
        key = (path, mtime, box)
        if key not in self._thumb_cache:
            try:
                with Image.open(path) as im:
                    im.draft("RGB", (box[0] * 2, box[1] * 2))
                    im = im.convert("RGB")
                    im.thumbnail(box, Image.LANCZOS)
                    self._thumb_cache[key] = ImageTk.PhotoImage(im)
            except Exception:
                return None
            if len(self._thumb_cache) > 500:  # borne mémoire
                for k in list(self._thumb_cache)[:150]:
                    del self._thumb_cache[k]
        return self._thumb_cache.get(key)

    def turn(self, direction):
        new = self.grid_start + direction * self.page_size
        if 0 <= new < max(1, len(self.view)):
            self.grid_start = new
            self.refresh_grid()

    def refresh_grid(self):
        colors = self.style.colors
        style = self.current_style()
        self.view = self._compute_view()
        total = len(self.pages)
        if self.grid_start >= len(self.view):
            self.grid_start = max(0, (max(0, len(self.view) - 1)
                                      // self.page_size) * self.page_size)
        done = 0
        if self.scan_dir:
            for p in self.pages:
                if ml.list_variants(self.scan_dir, style, p):
                    done += 1
        shown = ("%d affichée(s) / " % len(self.view)) \
            if len(self.view) != total else ""
        self.info_label.config(
            text="%s%d page(s) — %d avec génération(s) pour « %s » — %d cochée(s)"
            % (shown, total, done, self.style_var.get(), len(self.checked)))
        visible = self.view[self.grid_start:self.grid_start + self.page_size]
        box = (self.thumb_w, self.thumb_h)
        sel = ml.load_selections(self.scan_dir, style) if self.scan_dir else {}
        # nouvelle génération de vignettes : invalide les chargements en cours
        self._thumb_gen += 1
        gen = self._thumb_gen
        self._thumb_pending[gen] = 0
        self._cell_expect = {}
        for idx, cell in enumerate(self.cells):
            c = cell["cell"]
            c.config(bg=colors.bg)
            if idx < len(visible):
                if not c.winfo_ismapped():
                    c.grid()  # ré-affiche une cellule masquée
                page = visible[idx]
                variants = ml.list_variants(self.scan_dir, style, page)
                # miniature = l'image gardée (variante) si elle existe
                choice = ml.effective_choice(self.scan_dir, style, page,
                                             selections=sel,
                                             variants=variants) \
                    if variants else ml.ORIGINAL
                tpath = page["path"] if choice == ml.ORIGINAL \
                    else ml.variant_path(self.scan_dir, style, page, choice)
                dpath = self.display_path(tpath)
                try:
                    key = (dpath, os.path.getmtime(dpath), box)
                except OSError:
                    key = None
                ph = self._thumb_cache.get(key) if key else None
                if ph is not None:
                    cell["img"].config(image=ph, text="")
                    cell["img"].image = ph
                else:
                    # placeholder immédiat, la vignette arrive en fond
                    cell["img"].config(image="", text="⏳")
                    cell["img"].image = None
                    if key:
                        self._cell_expect[idx] = key
                        self._thumb_pending[gen] += 1
                        self.thumb_q.put((gen, idx, dpath, box))
                cell["cap"].config(text=page["rel"])
                if variants:
                    cell["badge"].config(text=str(len(variants)))
                    cell["badge"].place(relx=1.0, x=-8, y=6, anchor="ne")
                else:
                    cell["badge"].place_forget()
                cell["check_var"].set(page["key"] in self.checked)
                cell["check"].config(
                    command=lambda p=page, v=cell["check_var"]: self._toggle_check(p, v))
                checked = page["key"] in self.checked
                c.config(highlightthickness=2,
                         highlightbackground=(colors.primary if checked
                                              else (colors.success if variants
                                                    else colors.border)))
                cell["img"].bind("<Button-1>",
                                 lambda e, p=page: self.open_page(p))
                cell["img"].bind("<Button-3>",
                                 lambda e, tp=tpath: self.open_zoom(tp))
                cell["check"].place(x=6, y=6)
            else:
                # cellule vide : retirée de la grille pour que les vignettes
                # affichées restent centrées
                cell["img"].config(image="", text="")
                cell["img"].image = None
                cell["img"].unbind("<Button-1>")
                cell["img"].unbind("<Button-3>")
                cell["cap"].config(text="")
                cell["badge"].place_forget()
                cell["check"].place_forget()
                c.grid_remove()
                continue
        if self.view:
            first = self.grid_start + 1
            last = min(self.grid_start + self.page_size, len(self.view))
            self.nav_label.config(text="pages %d – %d  /  %d"
                                  % (first, last, len(self.view)))
        else:
            self.nav_label.config(text="aucune image")
        self.gen_sel_btn.config(text="Générer la sélection (%d)" % len(self.checked))
        n_pending = self._thumb_pending.get(gen, 0)
        if n_pending:
            self.busy("⏳ Chargement des vignettes… (%d)" % n_pending)
        elif str(self.status_label.cget("text")).startswith(("⏳", "📁", "🔍")):
            self.unbusy()

    def _toggle_check(self, page, var):
        if var.get():
            self.checked.add(page["key"])
        else:
            self.checked.discard(page["key"])
        self.refresh_grid()

    def check_all(self):
        self.checked = {p["key"] for p in self.view}
        self.refresh_grid()

    def check_none(self):
        self.checked.clear()
        self.refresh_grid()

    # ------------------------------------------------------------------ génération

    def _qty(self):
        try:
            q = max(1, min(10, int(self.qty_var.get())))
        except Exception:
            q = 1
        self.settings["gen_variants"] = q
        ml.save_settings(self.settings)
        return q

    def generate_all(self):
        self._generate_pages(self.view, "toutes les pages affichées")

    def generate_first_n(self):
        try:
            n = max(1, int(self.first_n_var.get()))
        except Exception:
            n = 15
        self.settings["first_n"] = n
        ml.save_settings(self.settings)
        self._generate_pages(self.view[:n], "les %d premières pages" % n)

    def generate_selection(self):
        pages = [p for p in self.pages if p["key"] in self.checked]
        if not pages:
            messagebox.showinfo(ml.APP_NAME, "Aucune page cochée : coche des "
                                "vignettes (case en haut à gauche) d'abord.")
            return
        self._generate_pages(pages, "%d page(s) cochée(s)" % len(pages),
                             always_new=True)

    def _generate_pages(self, pages, what, always_new=False):
        if not self._check_ready():
            return
        style = self.current_style()
        force = always_new or bool(self.force_var.get())
        qty = self._qty()
        todo = [p for p in pages
                if force or not ml.list_variants(self.scan_dir, style, p)]
        if not todo:
            messagebox.showinfo(ml.APP_NAME,
                                "Rien à faire : ces pages ont déjà des générations "
                                "(coche « refaire » pour de nouvelles variantes).")
            return
        if not SMOKE and not messagebox.askyesno(
                ml.APP_NAME, "Générer %s : %d génération(s) (%d page(s) × %d) "
                "avec le style « %s » ?"
                % (what, len(todo) * qty, len(todo), qty, self.style_var.get())):
            return
        jobs = [p for p in todo for _ in range(qty)]
        self._enqueue(jobs, style, {"boost": bool(self.settings.get("boost")),
                                    "colorize": bool(self.colorize_var.get())})

    def regen_page(self, page, count=1, **params):
        """Nouvelles variantes pour une page (vue page / vue Custom)."""
        if self._check_ready():
            self._enqueue([page] * max(1, count), self.current_style(), params)

    def _check_ready(self):
        if not self.pages:
            messagebox.showwarning(ml.APP_NAME,
                                   "Aucune image : choisis d'abord un dossier "
                                   "ou des images.")
            return False
        if not self.comfy_ok:
            messagebox.showwarning(
                ml.APP_NAME, "ComfyUI n'est pas démarré.\n"
                "Clique sur « Lancer ComfyUI » puis attends le point vert (~20 s).")
            return False
        return True

    def _enqueue(self, pages, style, params=None):
        if self.job_q.empty() and self.jobs_done >= self.jobs_total:
            self.jobs_total = self.jobs_done = 0
        self._chimed = False
        self.stop_flag.clear()
        base = {"style": style, "scan_dir": self.scan_dir,
                "preserve": bool(self.settings.get("preserve_bubbles", True))}
        base.update(params or {})
        for p in pages:
            job = dict(base)
            job["page"] = p
            self.job_q.put(job)
            self.jobs_total += 1
        self.stop_btn.config(state="normal")
        self._update_progress()

    def stop_jobs(self):
        self.stop_flag.set()
        try:
            while True:
                self.job_q.get_nowait()
                self.jobs_total -= 1
        except queue.Empty:
            pass
        ml.comfy_interrupt()
        self.msg_q.put(("status", "Arrêt demandé — fin de la page en cours…"))

    def _worker(self):
        while True:
            job = self.job_q.get()
            if self.stop_flag.is_set():
                self.msg_q.put(("job_done", None, "annulé"))
                continue
            page = job["page"]
            self.msg_q.put(("status", "Génération : %s" % page["rel"]))
            try:
                ml.stylize_page(
                    self.styles_cfg, job["style"], job["scan_dir"], page,
                    denoise=job.get("denoise"),
                    preserve_bubbles=job.get("preserve", True),
                    boost=job.get("boost", False),
                    pos_extra=job.get("pos_extra"),
                    neg_extra=job.get("neg_extra"),
                    lora_strength=job.get("lora_strength"),
                    cnet_strength=job.get("cnet_strength"),
                    cnet_end=job.get("cnet_end"),
                    checkpoint=job.get("checkpoint"),
                    lora_enabled=job.get("lora_enabled", True),
                    colorize=job.get("colorize", False))
                self.msg_q.put(("job_done", page["rel"], None))
            except Exception as e:
                self.msg_q.put(("job_done", page["rel"], str(e)))

    # ------------------------------------------------------------------ sélections / export

    def bulk_select(self, which):
        if not self.pages:
            return
        ml.select_bulk(self.scan_dir, self.current_style(), self.pages, which)
        label = "première" if which == "first" else "dernière"
        self.status_label.config(
            text="Image gardée = %s génération pour chaque page "
                 "(original si aucune)." % label)
        if self.current_view() == "page":
            self.views["page"].refresh()

    def export(self):
        if not self.pages:
            messagebox.showwarning(ml.APP_NAME, "Aucune image à exporter.")
            return
        style = self.current_style()
        style_label = self.style_var.get()
        dest_dir = None
        if self.file_mode and len(self.pages) > 1:
            dest_dir = filedialog.askdirectory(
                title="Où enregistrer les images exportées ?",
                initialdir=self.scan_dir)
            if not dest_dir:
                return
        self.status_label.config(text="Export en cours…")
        file_mode, pages, scan_dir = self.file_mode, self.pages, self.scan_dir

        def run():
            try:
                res = ml.export_all(scan_dir, style, style_label, pages,
                                    file_mode=file_mode, dest_dir=dest_dir)
                self.msg_q.put(("export_done", res))
            except Exception as e:
                self.msg_q.put(("status", "⚠ Export : %s" % e))
        threading.Thread(target=run, daemon=True).start()

    # ------------------------------------------------------------------ messages / état

    def _poll_msgs(self):
        try:
            while True:
                msg = self.msg_q.get_nowait()
                if msg[0] == "status":
                    self.status_label.config(text=msg[1])
                elif msg[0] == "comfy":
                    self._set_comfy(msg[1])
                elif msg[0] == "export_done":
                    res = msg[1]
                    self.status_label.config(
                        text="Export : %d page(s) (%d original(aux)) → %s"
                        % (res["exported"], res["originals"], res["out_dir"]))
                    if self.settings.get("open_after_export", True) and not SMOKE:
                        subprocess.Popen(["explorer", res["out_dir"]])
                elif msg[0] == "call":
                    try:
                        msg[1]()
                    except Exception:
                        pass
                elif msg[0] == "thumb_ready":
                    gen, idx, path, box, pil = msg[1], msg[2], msg[3], \
                        msg[4], msg[5]
                    if gen in self._thumb_pending:
                        self._thumb_pending[gen] -= 1
                        if self._thumb_pending[gen] <= 0:
                            del self._thumb_pending[gen]
                            if gen == self._thumb_gen:
                                self.unbusy()
                        elif gen == self._thumb_gen:
                            self.status_label.config(
                                text="⏳ Chargement des vignettes… (%d)"
                                % self._thumb_pending[gen])
                    if pil is not None and gen == self._thumb_gen:
                        try:
                            key = (path, os.path.getmtime(path), box)
                        except OSError:
                            key = None
                        if key:
                            ph = ImageTk.PhotoImage(pil)
                            self._thumb_cache[key] = ph
                            if self._cell_expect.get(idx) == key \
                                    and idx < len(self.cells):
                                cell = self.cells[idx]
                                cell["img"].config(image=ph, text="")
                                cell["img"].image = ph
                elif msg[0] == "job_done":
                    self.jobs_done += 1
                    rel, err = msg[1], msg[2]
                    if err and err != "annulé":
                        self.status_label.config(text="⚠ %s : %s" % (rel, err[:160]))
                    self._update_progress()
                    self.refresh_grid()
                    if self.current_view() == "page":
                        self.views["page"].refresh()
        except queue.Empty:
            pass
        self.after(150, self._poll_msgs)

    def _update_progress(self):
        self.progress.set_progress(self.jobs_done, self.jobs_total)
        if self.jobs_done >= self.jobs_total and self.jobs_total:
            self.status_label.config(text="Terminé : %d génération(s)."
                                     % self.jobs_total)
            self.stop_btn.config(state="disabled")
            if not self._chimed and not SMOKE \
                    and self.settings.get("done_sound", True):
                play_chime()
            self._chimed = True
        elif self.jobs_total:
            self.status_label.config(
                text="En cours… %d / %d" % (self.jobs_done, self.jobs_total))

    def _comfy_watch(self):
        while True:
            self.msg_q.put(("comfy", ml.comfy_alive()))
            threading.Event().wait(4)

    def _set_comfy(self, ok):
        self.comfy_ok = ok
        self.comfy_dot.config(bootstyle="success" if ok else "danger")
        self.comfy_label.config(text="ComfyUI prêt" if ok else "ComfyUI arrêté")
        self.comfy_btn.config(state="disabled" if ok else "normal")

    def launch_comfy(self):
        try:
            ml.start_comfyui()
            self.status_label.config(text="ComfyUI démarre… (~20 s)")
        except Exception as e:
            messagebox.showerror(ml.APP_NAME, "Impossible de lancer ComfyUI :\n%s" % e)

    def apply_theme(self, name):
        self.style.theme_use(name)
        self.settings["theme"] = name
        ml.save_settings(self.settings)
        self.configure(bg=self.style.colors.bg)
        for win in (self._opts_win, self._custom_panel):
            if win is not None and win.winfo_exists():
                try:
                    win.configure(bg=self.style.colors.bg)
                except Exception:
                    pass
        self.progress.restyle()
        # l'accueil utilise des couleurs figées au build : on le reconstruit
        was_welcome = self.current_view() == "welcome"
        old = self.views.get("welcome")
        if old is not None and old.winfo_exists():
            old.pack_forget()
            old.destroy()
        self._build_welcome()
        self.views["welcome"] = self.welcome
        if was_welcome:
            self.welcome.pack(fill="both", expand=True)
        self.refresh_grid()
        if self.current_view() == "page":
            self.views["page"].refresh()


class ZoomView(tb.Frame):
    """Vue zoom 100 % : image centrée ; cliquer-glisser, molette (Maj =
    horizontal) ou barres de défilement pour se déplacer ; Échap = retour."""

    def __init__(self, container, app, path):
        super().__init__(container)
        bar = tb.Frame(self, padding=(8, 6))
        bar.pack(fill="x")
        tb.Button(bar, text="←  Retour", bootstyle="secondary",
                  command=app.back).pack(side="left")
        tb.Label(bar, text="%s — 100 %%  (cliquer-glisser ou molette pour se "
                           "déplacer, Échap pour revenir)"
                 % os.path.basename(path)).pack(side="left", padx=12)
        try:
            with Image.open(path) as im:
                self._photo = ImageTk.PhotoImage(im.convert("RGB"))
                iw, ih = im.size
        except Exception:
            tb.Label(self, text="Image illisible").pack(pady=30)
            return
        wl, wt, wr, wb = work_area()
        vw, vh = (wr - wl) - 90, (wb - wt) - 330  # zone visible estimée
        canvas = tk.Canvas(self, highlightthickness=0,
                           bg=app.style.colors.bg)
        hbar = tb.Scrollbar(self, orient="horizontal", command=canvas.xview)
        vbar = tb.Scrollbar(self, orient="vertical", command=canvas.yview)
        canvas.configure(xscrollcommand=hbar.set, yscrollcommand=vbar.set)
        hbar.pack(side="bottom", fill="x")
        vbar.pack(side="right", fill="y")
        canvas.pack(fill="both", expand=True)
        # scrollregion au moins aussi grande que la vue : l'image est TOUJOURS
        # centrée, et le déplacement fonctionne dès qu'elle dépasse
        rw, rh = max(iw, vw), max(ih, vh)
        canvas.configure(scrollregion=(0, 0, rw, rh))
        canvas.create_image(rw // 2, rh // 2, image=self._photo,
                            anchor="center")
        # vue initiale centrée sur l'image
        if rw > vw:
            canvas.xview_moveto((rw - vw) / 2.0 / rw)
        if rh > vh:
            canvas.yview_moveto((rh - vh) / 2.0 / rh)
        canvas.bind("<ButtonPress-1>", lambda e: canvas.scan_mark(e.x, e.y))
        canvas.bind("<B1-Motion>",
                    lambda e: canvas.scan_dragto(e.x, e.y, gain=1))
        canvas.bind("<MouseWheel>",
                    lambda e: canvas.yview_scroll(-2 if e.delta > 0 else 2,
                                                  "units"))
        canvas.bind("<Shift-MouseWheel>",
                    lambda e: canvas.xview_scroll(-2 if e.delta > 0 else 2,
                                                  "units"))


class PageView(tb.Frame):
    """Vue d'une page façon « compare » : bande de variantes en haut,
    original et variante côte à côte en dessous, navigation sur les flancs.

    Raccourcis : ←/→ parcourir les variantes, ↑ garder l'affichée,
    Ctrl+←/→ page préc./suiv., 1-9/0 direct, Suppr, clic droit zoom."""

    def __init__(self, container, app):
        super().__init__(container)
        self.app = app
        self.page_ref = None
        self._photos = []

        bar = tb.Frame(self, padding=(8, 6, 8, 2))
        bar.pack(fill="x")
        tb.Button(bar, text="←  Retour", bootstyle="secondary",
                  command=app.back).pack(side="left", padx=(0, 12))
        rzone = tb.Frame(bar)
        rzone.pack(side="right")
        self.meta_label = tb.Label(rzone, text="")
        self.meta_label.pack(side="right")
        tb.Label(rzone, text="←/→ variantes · ↑ ou clic gauche = garder · "
                             "clic droit maintenu = zoom 100 % · Ctrl+←/→ "
                             "pages · Suppr · Échap ",
                 font=("Segoe UI", 8)).pack(side="right", padx=(0, 14))
        # actions centrées (seul « Retour » reste à gauche)
        czone = tb.Frame(bar)
        czone.pack(side="left", expand=True)
        actions = tb.Frame(czone)
        actions.pack(anchor="center")
        self.regen_btn = tb.Button(actions, text="⟳  Régénérer",
                                   bootstyle="primary", command=self.regen_quick)
        self.regen_btn.pack(side="left", padx=(0, 4))
        tip(self.regen_btn, "Génère de nouvelles variantes de CETTE page, "
                            "avec les réglages actuels du style.")
        self.custom_btn = tb.Button(
            actions, text="⚙  Custom…", bootstyle="primary-outline",
            command=lambda: app.open_custom(self.page_ref))
        self.custom_btn.pack(side="left")
        tip(self.custom_btn,
            "Tous les réglages avancés pour régénérer cette page : modèle, "
            "LoRA, denoise, ControlNet, prompts… (pop-in centrée, "
            "déplaçable)")
        self.iterate_btn = tb.Button(actions, text="⚙  Depuis cette image",
                                     bootstyle="primary-outline",
                                     command=self.custom_from_current)
        self.iterate_btn.pack(side="left", padx=(4, 0))
        tip(self.iterate_btn,
            "Ouvre Custom prérempli avec les réglages exacts qui ont produit "
            "l'image affichée (denoise, ControlNet, modèle, LoRA, "
            "colorisation) + les prompts de la page — pour itérer à partir "
            "d'une génération réussie.")
        self.del_btn = tb.Button(actions, text="🗑  Supprimer l'affichée",
                                 bootstyle="danger-outline", command=self.delete)
        self.del_btn.pack(side="left", padx=(16, 4))
        tip(self.del_btn, "Supprime la variante actuellement affichée à "
                          "droite (jamais l'original).")
        self.clean_btn = tb.Button(actions, text="🧹  Supprimer les autres",
                                   bootstyle="danger-outline",
                                   command=self.delete_others)
        self.clean_btn.pack(side="left")
        tip(self.clean_btn, "Fait le ménage : supprime toutes les variantes "
                            "de cette page SAUF la ★ gardée.")

        # bande des vignettes AU-DESSUS des images comparées (scrollable)
        strip_wrap = tb.Frame(self, padding=(8, 2))
        strip_wrap.pack(fill="x")
        self.strip_canvas = tk.Canvas(strip_wrap, highlightthickness=0,
                                      height=220)
        self.strip_sb = tb.Scrollbar(strip_wrap, orient="horizontal",
                                     bootstyle="secondary-round",
                                     command=self.strip_canvas.xview)
        self.strip_canvas.configure(xscrollcommand=self.strip_sb.set)
        self.strip_canvas.pack(fill="x")
        self.strip_sb.pack(fill="x")
        self.strip = tb.Frame(self.strip_canvas)
        self.strip_canvas.create_window((0, 0), window=self.strip,
                                        anchor="nw")
        self.strip.bind("<Configure>", lambda e: self.strip_canvas.configure(
            scrollregion=self.strip_canvas.bbox("all")))

        body = tb.Frame(self, padding=(4, 0, 4, 4))
        body.pack(fill="both", expand=True)
        tb.Button(body, text="◀", width=2, bootstyle="secondary",
                  command=lambda: self.nav(-1)).pack(side="left", fill="y",
                                                     padx=(0, 4), pady=8)
        tb.Button(body, text="▶", width=2, bootstyle="secondary",
                  command=lambda: self.nav(1)).pack(side="right", fill="y",
                                                    padx=(4, 0), pady=8)
        left = tb.Frame(body)
        left.pack(side="left", expand=True)
        tb.Label(left, text="Original", font=("Segoe UI Semibold", 10)).pack()
        self.left_img = tb.Label(left)
        self.left_img.pack()
        self.sep = tk.Frame(body, width=2)
        self.sep.pack(side="left", fill="y", pady=10)
        right = tb.Frame(body)
        right.pack(side="left", expand=True)
        self.right_title = tb.Label(right, text="Aperçu",
                                    font=("Segoe UI Semibold", 10))
        self.right_title.pack()
        self.right_img = tb.Label(right)
        self.right_img.pack()

        self.viewed = None  # ml.ORIGINAL ou nom de fichier de variante

    def scroll_strip(self, step):
        self.strip_canvas.xview_scroll(step * 3, "units")

    # ---- zoom 100 % temporaire (clic droit maintenu, des DEUX côtés) :
    # le crop remplit toute la moitié d'écran, pas seulement l'image

    def _side(self, side):
        if side == "left":
            return self.left_img, self._left_photo, self._left_path
        return self.right_img, self._right_photo, self._right_path

    def _zoom_start(self, e, side):
        try:
            lbl, photo, path = self._side(side)
            if not hasattr(self, "_zoom_cache"):
                self._zoom_cache = {}
            if self._zoom_cache.get("path") != path:
                self.app.busy("🔍 Chargement de la pleine résolution…")
                with Image.open(path) as im:  # pleine résolution (pas l'aperçu)
                    self._zoom_cache = {"path": path, "img": im.convert("RGB")}
                self.app.unbusy()
            self._zooming = side
            self._zoom_move(e, side)
        except Exception:
            self._zooming = None
            self.app.unbusy()

    def _zoom_move(self, e, side):
        if getattr(self, "_zooming", None) != side:
            return
        lbl, photo, _path = self._side(side)
        if photo is None:
            return
        img = self._zoom_cache["img"]
        iw, ih = img.size
        lw, lh = photo.width(), photo.height()
        ox = max(0, (lbl.winfo_width() - lw) // 2)
        oy = max(0, (lbl.winfo_height() - lh) // 2)
        fx = min(max((e.x - ox) / float(lw), 0.0), 1.0)
        fy = min(max((e.y - oy) / float(lh), 0.0), 1.0)
        # le crop occupe TOUTE la demi-zone (espaces vides compris)
        cw, ch = min(self._prev_w, iw), min(self._prev_h, ih)
        x0 = int(min(max(fx * iw - cw / 2, 0), iw - cw))
        y0 = int(min(max(fy * ih - ch / 2, 0), ih - ch))
        crop = img.crop((x0, y0, x0 + cw, y0 + ch))
        self._zoom_photo = ImageTk.PhotoImage(crop)
        lbl.config(image=self._zoom_photo)

    def _zoom_end(self, side):
        if getattr(self, "_zooming", None) != side:
            return
        self._zooming = None
        lbl, photo, _path = self._side(side)
        if photo is not None:
            lbl.config(image=photo)

    def custom_from_current(self):
        """Custom prérempli avec les réglages de l'image affichée."""
        app, page = self.app, self.page_ref
        preset = None
        if self.viewed != ml.ORIGINAL:
            preset = ml.load_variant_meta(app.scan_dir, app.current_style(),
                                          page).get(self.viewed)
            if not preset:  # ancienne variante : au moins le denoise du nom
                m = re.match(r"seed_(\d+)_d([\d.]+)\.png$", self.viewed)
                if m:
                    preset = {"denoise": float(m.group(2))}
        app.open_custom(page, anchor=self.iterate_btn, preset=preset)

    def load(self, page):
        self.page_ref = page
        self.viewed = None
        self.refresh()

    def keep_nth(self, n):
        variants = ml.list_variants(self.app.scan_dir, self.app.current_style(),
                                    self.page_ref)
        if n == 0:
            self.keep(ml.ORIGINAL)
        elif n <= len(variants):
            self.keep(variants[n - 1])

    def cycle(self, direction):
        """←/→ : fait défiler l'image affichée (original puis variantes)."""
        app, page = self.app, self.page_ref
        variants = ml.list_variants(app.scan_dir, app.current_style(), page)
        items = [ml.ORIGINAL] + variants
        try:
            i = items.index(self.viewed)
        except ValueError:
            i = 0
        self.viewed = items[(i + direction) % len(items)]
        self.refresh()

    def keep_current(self):
        """↑ : garde l'image actuellement affichée."""
        self.keep(self.viewed)

    def nav(self, direction):
        view = self.app.view
        try:
            i = view.index(self.page_ref)
        except ValueError:
            i = 0
        new = i + direction
        if 0 <= new < len(view):
            self.page_ref = view[new]
            self.viewed = None
            self.refresh()

    def _fit(self, path, box):
        try:
            with Image.open(App.display_path(path)) as im:
                im.draft("RGB", (box[0] * 2, box[1] * 2))
                im = im.convert("RGB")
                im.thumbnail(box, Image.LANCZOS)
                ph = ImageTk.PhotoImage(im)
                self._photos.append(ph)
                return ph
        except Exception:
            return None

    def refresh(self):
        app, page = self.app, self.page_ref
        if page is None:
            return
        style = app.current_style()
        colors = app.style.colors
        self._photos = []
        variants = ml.list_variants(app.scan_dir, style, page)
        kept = ml.effective_choice(app.scan_dir, style, page, variants=variants)
        if self.viewed is None or (self.viewed != ml.ORIGINAL
                                   and self.viewed not in variants):
            self.viewed = kept
        app.info_label.config(text="%s — %s" % (page["rel"], app.style_var.get()))
        self.sep.config(bg=colors.border)

        # dimensions dynamiques : la bande en haut prend strip_w, le reste
        # va aux deux grandes images
        sw = app.strip_w
        strip_box = (sw, int(sw * 1.42))
        self.strip_canvas.configure(height=strip_box[1] + 42,
                                    bg=colors.bg)
        cw = app.container.winfo_width() or 1600
        ch = app.container.winfo_height() or 900
        prev_w = max(300, (cw - 140) // 2)
        prev_h = max(300, ch - 54 - (strip_box[1] + 76) - 30)
        big = (prev_w, prev_h)

        self._prev_w, self._prev_h = prev_w, prev_h
        ph = self._fit(page["path"], big)
        self.left_img.config(image=ph or "")
        self._left_photo = ph
        self._left_path = page["path"]
        # zoom identique des deux côtés : clic droit MAINTENU = 100 %
        self.left_img.bind("<ButtonPress-3>",
                           lambda e: self._zoom_start(e, "left"))
        self.left_img.bind("<B3-Motion>",
                           lambda e: self._zoom_move(e, "left"))
        self.left_img.bind("<ButtonRelease-3>",
                           lambda e: self._zoom_end("left"))
        if self.viewed == ml.ORIGINAL:
            self.right_title.config(text="Gardée : original (inchangé)")
            self.right_img.config(image=ph or "", text="")
            self._right_photo = ph
            self._right_path = page["path"]
        else:
            vp = ml.variant_path(app.scan_dir, style, page, self.viewed)
            ph2 = self._fit(vp, big)
            mark = "★ gardée — " if self.viewed == kept else ""
            title = mark + ml.variant_label(self.viewed)
            vm = ml.load_variant_meta(app.scan_dir, style, page).get(
                self.viewed) or {}
            if vm:
                extra = " · ControlNet %.2f" % vm.get("cnet", 0)
                ckpt = os.path.splitext(vm.get("checkpoint", ""))[0]
                if ckpt:
                    extra += " · " + (ckpt[:26] + "…" if len(ckpt) > 27
                                      else ckpt)
                if vm.get("lora_strength") is not None:
                    extra += " · LoRA %.2f" % vm["lora_strength"]
                if vm.get("colorize"):
                    extra += " · 🎨"
                title += extra
            self.right_title.config(text=title)
            self.right_img.config(image=ph2 or "", text="")
            self._right_photo = ph2
            self._right_path = vp
        # image de droite : clic gauche = garder · clic droit MAINTENU =
        # zoom 100 % qui suit la souris · relâcher = retour
        self.right_img.bind("<Button-1>", lambda e: self.keep(self.viewed))
        self.right_img.bind("<ButtonPress-3>",
                            lambda e: self._zoom_start(e, "right"))
        self.right_img.bind("<B3-Motion>",
                            lambda e: self._zoom_move(e, "right"))
        self.right_img.bind("<ButtonRelease-3>",
                            lambda e: self._zoom_end("right"))
        self.meta_label.config(text="%d variante(s)" % len(variants))
        qty = int(ml.load_settings().get("gen_variants", 1))
        self.regen_btn.config(text="⟳  Régénérer ×%d" % qty)
        self.del_btn.config(state="normal" if (self.viewed != ml.ORIGINAL
                                               and variants) else "disabled")
        others = [v for v in variants if v != kept]
        self.clean_btn.config(state="normal" if others else "disabled",
                              text="🧹  Supprimer les autres (%d)" % len(others))

        for child in self.strip.winfo_children():
            child.destroy()
        items = [(ml.ORIGINAL, page["path"], "Original")]
        for v in variants:
            items.append((v, ml.variant_path(app.scan_dir, style, page, v),
                          ml.variant_label(v)))
        self._viewed_tile = None
        for ident, path, label in items:
            is_kept = (ident == kept)
            is_viewed = (ident == self.viewed)
            f = tk.Frame(self.strip, highlightthickness=3,
                         highlightbackground=(colors.success if is_kept else
                                              (colors.info if is_viewed
                                               else colors.border)),
                         bg=colors.bg)
            f.pack(side="left", padx=4)
            if is_viewed:
                self._viewed_tile = f
            ph = self._fit(path, strip_box)
            lab = tb.Label(f, image=ph or "", cursor="hand2")
            lab.pack()
            cap = tb.Label(f, text=("★ " if is_kept else "") + label,
                           font=("Segoe UI", 8))
            cap.pack()
            for w in (lab, cap):
                w.bind("<Button-1>", lambda e, i=ident: self.keep(i))
                w.bind("<Button-3>", lambda e, p=path: app.open_zoom(p))
            if ident != ml.ORIGINAL:  # ✖ suppression directe (PortraitForge)
                x = tk.Label(f, text="✖", font=("Segoe UI", 8, "bold"),
                             bg=colors.danger, fg="#ffffff", cursor="hand2",
                             padx=3)
                x.place(relx=1.0, x=-2, y=2, anchor="ne")
                x.bind("<Button-1>",
                       lambda e, i=ident: self.delete_one(i))
        # garde la vignette consultée visible dans la bande
        self.strip.update_idletasks()
        self._follow_strip()

    def _follow_strip(self):
        """Fait défiler la bande pour centrer la vignette consultée."""
        tile = getattr(self, "_viewed_tile", None)
        if tile is None or not tile.winfo_exists():
            return
        canvas = self.strip_canvas
        total = max(1, self.strip.winfo_reqwidth())
        cw = max(1, canvas.winfo_width())
        if total <= cw:
            canvas.xview_moveto(0)
            return
        frac = (tile.winfo_x() + tile.winfo_reqwidth() / 2.0
                - cw / 2.0) / total
        canvas.xview_moveto(max(0.0, min(frac, (total - cw) / float(total))))

    def keep(self, ident):
        """Définit l'image gardée pour cette page (original ou variante)."""
        app, page = self.app, self.page_ref
        sel = ml.load_selections(app.scan_dir, app.current_style())
        sel[page["key"]] = ident
        ml.save_selections(app.scan_dir, app.current_style(), sel)
        self.viewed = ident
        self.refresh()

    def _remove_variants(self, names, next_view=None):
        """Envoie les variantes (et leurs aperçus) à la corbeille."""
        app, page = self.app, self.page_ref
        style = app.current_style()
        files = []
        for name in names:
            vp = ml.variant_path(app.scan_dir, style, page, name)
            files += [vp, vp + ".jpg"]
        ml.trash(*files)
        sel = ml.load_selections(app.scan_dir, style)
        if sel.get(page["key"]) in names:
            del sel[page["key"]]
            ml.save_selections(app.scan_dir, style, sel)
        if self.viewed in names:
            self.viewed = next_view  # None -> retombe sur la gardée
        app.status_label.config(
            text="%d variante(s) envoyée(s) à la corbeille." % len(names))
        self.refresh()
        app.refresh_grid()

    def delete_one(self, ident):
        if ident == ml.ORIGINAL:
            return
        next_view = None
        if ident == self.viewed:
            # après suppression : variante suivante, sinon précédente,
            # sinon l'originale
            variants = ml.list_variants(self.app.scan_dir,
                                        self.app.current_style(),
                                        self.page_ref)
            try:
                i = variants.index(ident)
                if i + 1 < len(variants):
                    next_view = variants[i + 1]
                elif i > 0:
                    next_view = variants[i - 1]
                else:
                    next_view = ml.ORIGINAL
            except ValueError:
                pass
        self._remove_variants([ident], next_view=next_view)

    def delete(self):
        self.delete_one(self.viewed)

    def delete_others(self):
        """Envoie toutes les variantes sauf la gardée (★) à la corbeille."""
        app, page = self.app, self.page_ref
        style = app.current_style()
        variants = ml.list_variants(app.scan_dir, style, page)
        kept = ml.effective_choice(app.scan_dir, style, page, variants=variants)
        others = [v for v in variants if v != kept]
        if others:
            self._remove_variants(others)

    def regen_quick(self):
        """Régénération rapide : réglages du style + prompts de la page."""
        app = self.app
        s = ml.load_settings()
        pp = ml.load_prompts(app.scan_dir, app.current_style()).get(
            self.page_ref["key"], {})
        app.regen_page(
            self.page_ref, count=int(s.get("gen_variants", 1)),
            boost=bool(s.get("boost", False)),
            colorize=bool(app.colorize_var.get()),
            pos_extra=pp.get("pos"), neg_extra=pp.get("neg"))


class CustomView(tb.Toplevel):
    """Panneau « ⚙ Custom » ancré sous le bouton (comme PortraitForge) :
    modèle (checkpoint), LoRA on/off + force, presets, denoise, ControlNet,
    quantité, colorisation, prompts + / −, mémorisation pour le style.
    Fenêtre légère sans bordure ni entrée dans la barre des tâches."""

    def __init__(self, app, page, anchor=None, preset=None):
        super().__init__(master=app)
        self.overrideredirect(True)
        self.transient(app)
        self.app = app
        self.page = page
        self.style_key = app.current_style()
        scfg = app.styles_cfg["styles"][self.style_key]
        d = app.styles_cfg["defaults"]
        base = d[scfg["base"]]
        s = ml.load_settings()
        colors = app.style.colors

        outer = tk.Frame(self, bg=colors.primary)  # liseré accent
        outer.pack(fill="both", expand=True)
        frm = tb.Frame(outer, padding=14)
        frm.pack(padx=1, pady=1)
        frm.columnconfigure(1, weight=1)
        r = 0
        title = tb.Label(frm, text="⚙ %s" % scfg.get("label", self.style_key),
                         font=("Segoe UI Semibold", 12),
                         bootstyle="primary", cursor="fleur")
        title.grid(row=r, column=0, columnspan=2, sticky="w")
        hb = tb.Frame(frm)
        hb.grid(row=r, column=2, sticky="e")
        base_lbl = tb.Label(hb, text="base %s  " % scfg["base"],
                            cursor="fleur")
        base_lbl.pack(side="left")
        tb.Button(hb, text="✕", width=3, bootstyle="secondary",
                  command=self.destroy).pack(side="left")
        # déplaçable en glissant l'en-tête (ou le fond du panneau)
        for w in (title, base_lbl, frm):
            w.bind("<ButtonPress-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)
        r += 1
        tb.Label(frm, text="page : %s" % page["rel"]).grid(
            row=r, column=0, columnspan=3, sticky="w", pady=(4, 0))
        r += 1
        tb.Separator(frm).grid(row=r, column=0, columnspan=3,
                               sticky="ew", pady=8)
        r += 1

        self.base_ckpt = scfg.get("checkpoint") \
            or s.get("checkpoint_" + scfg["base"]) or base["checkpoint"]
        ckpts = ml.list_checkpoints() or [self.base_ckpt]
        tip(tb.Label(frm, text="Modèle (checkpoint)"),
            "Le « moteur de dessin » (modèle SDXL). Chaque modèle a son rendu "
            "général. En cas de doute, laisse celui proposé — le style vient "
            "surtout du LoRA.").grid(row=r, column=0, sticky="w", pady=4)
        self.ckpt_var = tk.StringVar(value=self.base_ckpt)
        tip(make_filterable(
            tb.Combobox(frm, textvariable=self.ckpt_var, width=42), ckpts),
            "Liste des modèles installés dans ComfyUI — tape une partie du "
            "nom pour filtrer la liste, puis ouvre-la. Changer de modèle "
            "change l'ambiance générale du dessin.").grid(
            row=r, column=1, columnspan=2, sticky="ew", padx=(10, 0))
        r += 1

        has_lora = bool(scfg.get("lora"))
        self.lora_on_var = tk.BooleanVar(value=has_lora)
        chk = tb.Checkbutton(frm, text="LoRA : %s"
                             % (scfg.get("lora") or "(aucun pour ce style)"),
                             variable=self.lora_on_var,
                             bootstyle="round-toggle")
        chk.grid(row=r, column=0, columnspan=2, sticky="w", pady=4)
        tip(chk, "Le LoRA est le « filtre » qui applique le style de "
                 "l'auteur. Désactivé = génération avec le modèle seul, sans "
                 "le style de l'auteur.")
        if not has_lora:
            chk.config(state="disabled")
        lf = tb.Frame(frm)
        lf.grid(row=r, column=2, sticky="e")
        tb.Label(lf, text="force").pack(side="left")
        self.lora_var = tk.DoubleVar(value=scfg.get("lora_strength", 1.0))
        sp = tb.Spinbox(lf, from_=0.0, to=1.5, increment=0.05, width=6,
                        textvariable=self.lora_var)
        sp.pack(side="left", padx=(4, 0))
        tip(sp, "Intensité du style de l'auteur : 0.8 = discret · 1.0 = "
                "normal · 1.2+ = très marqué (risque de déformer les "
                "personnages).")
        if not has_lora:
            sp.config(state="disabled")
        r += 1

        pf = tb.Frame(frm)
        pf.grid(row=r, column=0, columnspan=3, sticky="w", pady=(8, 4))
        tip(tb.Button(pf, text="🔥 Style renforcé", bootstyle="warning-outline",
                      command=self.preset_boost),
            "Préréglage « le style avant tout » : denoise monté à 0.75+, "
            "guide ControlNet allégé, LoRA +0.1. Le trait de l'auteur ressort "
            "beaucoup plus, la ressemblance à l'original diminue un "
            "peu.").pack(side="left")
        tip(tb.Button(pf, text="⚖️ Équilibré", bootstyle="info-outline",
                      command=self.preset_std),
            "Revient aux réglages enregistrés du style : bon équilibre entre "
            "style et fidélité à la page d'origine.").pack(side="left", padx=8)
        tip(tb.Button(pf, text="↺ Défauts", bootstyle="secondary",
                      command=self.reset_defaults),
            "Réinitialise tous les champs aux valeurs par défaut de "
            "l'application (ignore les réglages mémorisés du style et les "
            "prompts de la page). Avec « Mémoriser » coché, Lancer "
            "réenregistre ces défauts pour le style.").pack(side="left")
        r += 1

        eff_dn = scfg.get("denoise", s.get("gen_denoise") or d["denoise"])
        tip(tb.Label(frm, text="Denoise (liberté du style)"),
            "Liberté laissée à l'IA pour redessiner :\n· 0.3-0.5 = très "
            "proche de l'original, style léger\n· 0.7 = bon équilibre\n"
            "· 0.9-1.0 = réinvente presque tout (utile pour coloriser, mais "
            "les détails peuvent changer).").grid(row=r, column=0,
                                                  sticky="w", pady=4)
        self.denoise_var = tk.DoubleVar(value=float(eff_dn))
        tb.Scale(frm, from_=0.3, to=1.0, variable=self.denoise_var,
                 length=220, command=lambda v: self.dn_val.config(
                     text="%.2f" % float(v))).grid(row=r, column=1,
                                                   sticky="ew", padx=(10, 6))
        self.dn_val = tb.Label(frm, text="%.2f" % float(eff_dn), width=5)
        self.dn_val.grid(row=r, column=2, sticky="w")
        r += 1

        eff_cs = scfg.get("cnet_strength",
                          s.get("gen_cnet_strength") or d["cnet_strength"])
        eff_ce = scfg.get("cnet_end", d["cnet_end"])
        cf = tb.Frame(frm)
        cf.grid(row=r, column=0, columnspan=3, sticky="w", pady=4)
        tip(tb.Label(cf, text="ControlNet  force"),
            "Force du guide qui suit les traits de l'original :\n· 0.6+ = "
            "mise en page et poses très respectées\n· 0.35 = plus de liberté "
            "créative (recommandé avec « Style renforcé »).").pack(side="left")
        self.cnet_var = tk.DoubleVar(value=float(eff_cs))
        tip(tb.Spinbox(cf, from_=0.0, to=1.0, increment=0.05, width=6,
                       textvariable=self.cnet_var),
            "0 = aucun guide (composition libre) ; 1 = suit chaque trait à la "
            "lettre.").pack(side="left", padx=(4, 14))
        tip(tb.Label(cf, text="fin (0-1)"),
            "Moment où le guide lâche prise pendant la génération : 0.8 = "
            "guidé presque jusqu'au bout ; 0.5 = libre sur la seconde moitié "
            "(les détails prennent plus le style).").pack(side="left")
        self.cend_var = tk.DoubleVar(value=float(eff_ce))
        tb.Spinbox(cf, from_=0.1, to=1.0, increment=0.05, width=6,
                   textvariable=self.cend_var).pack(side="left", padx=(4, 0))
        r += 1

        qf = tb.Frame(frm)
        qf.grid(row=r, column=0, columnspan=3, sticky="w", pady=4)
        tb.Label(qf, text="Quantité").pack(side="left")
        self.qty_var = tk.IntVar(value=int(s.get("gen_variants", 1)))
        tip(tb.Spinbox(qf, from_=1, to=10, width=4,
                       textvariable=self.qty_var),
            "Nombre de variantes générées d'un coup (seeds différentes) — tu "
            "garderas ensuite la meilleure.").pack(side="left", padx=(6, 18))
        self.colorize_var = tk.BooleanVar(value=bool(app.colorize_var.get()))
        tip(tb.Checkbutton(qf, text="🎨 coloriser (N&B → couleur)",
                           variable=self.colorize_var,
                           bootstyle="success-round-toggle"),
            "Demande à l'IA de mettre la page en couleurs, en plus du style "
            "choisi. Inutile si la page est déjà en couleurs.").pack(side="left")
        r += 1

        saved_prompts = ml.load_prompts(app.scan_dir, self.style_key).get(
            page["key"], {})
        tip(tb.Label(frm, text="Prompt +"),
            "Mots-clés ajoutés à la consigne (en anglais). Ex. : « night, "
            "rain » pour une ambiance, « red hair » pour corriger une "
            "couleur. Mémorisés pour CETTE page.").grid(row=r, column=0,
                                                        sticky="w", pady=4)
        self.pos_var = tk.StringVar(value=saved_prompts.get("pos", ""))
        tb.Entry(frm, textvariable=self.pos_var).grid(
            row=r, column=1, columnspan=2, sticky="ew", padx=(10, 0))
        r += 1
        tip(tb.Label(frm, text="Prompt −"),
            "Ce que l'IA doit éviter (en anglais). Ex. : « blurry, "
            "watermark, extra fingers ». Mémorisé pour CETTE page.").grid(
            row=r, column=0, sticky="w", pady=4)
        self.neg_var = tk.StringVar(value=saved_prompts.get("neg", ""))
        tb.Entry(frm, textvariable=self.neg_var).grid(
            row=r, column=1, columnspan=2, sticky="ew", padx=(10, 0))
        r += 1

        self.memo_var = tk.BooleanVar(value=True)
        tip(tb.Checkbutton(frm, text="Mémoriser modèle / LoRA / denoise / "
                                     "ControlNet pour ce style",
                           variable=self.memo_var,
                           bootstyle="round-toggle"),
            "Coché : ces réglages deviennent les nouveaux réglages par "
            "défaut du style (pour toutes les prochaines générations, toutes "
            "pages).").grid(row=r, column=0, columnspan=3, sticky="w",
                            pady=(10, 0))
        r += 1

        btns = tb.Frame(frm)
        btns.grid(row=r, column=0, columnspan=3, sticky="e", pady=(14, 0))
        tb.Button(btns, text="Annuler", bootstyle="secondary",
                  command=self.destroy).pack(side="left", padx=(0, 8))
        tb.Button(btns, text="⟳  Lancer", bootstyle="primary",
                  command=self.launch).pack(side="left")

        # préremplissage depuis une variante existante (« Depuis cette image »)
        if preset:
            if preset.get("denoise") is not None:
                self.denoise_var.set(float(preset["denoise"]))
                self.dn_val.config(text="%.2f" % float(preset["denoise"]))
            if preset.get("cnet") is not None:
                self.cnet_var.set(float(preset["cnet"]))
            if preset.get("cnet_end") is not None:
                self.cend_var.set(float(preset["cnet_end"]))
            if preset.get("checkpoint"):
                self.ckpt_var.set(preset["checkpoint"])
            if preset.get("lora_strength") is not None:
                self.lora_var.set(float(preset["lora_strength"]))
                self.lora_on_var.set(True)
            self.colorize_var.set(bool(preset.get("colorize", False)))

        # position : centrée à l'ÉCRAN (zone de travail), déplaçable ensuite
        self.update_idletasks()
        pw, ph = self.winfo_reqwidth(), self.winfo_reqheight()
        wl, wt, wr, wb = work_area()
        x = wl + ((wr - wl) - pw) // 2
        y = wt + ((wb - wt) - ph) // 2
        self.geometry("+%d+%d" % (max(wl + 8, x), max(wt + 8, y)))
        # les fenêtres overrideredirect naissent parfois DERRIÈRE la fenêtre
        # maximisée : on force le passage au premier plan
        self.lift()
        self.attributes("-topmost", True)
        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<Return>", lambda e: self.launch())
        self.focus_force()

    def _drag_start(self, e):
        self._dx = e.x_root - self.winfo_x()
        self._dy = e.y_root - self.winfo_y()

    def _drag_move(self, e):
        self.geometry("+%d+%d" % (e.x_root - self._dx, e.y_root - self._dy))

    def preset_boost(self):
        """denoise ≥ 0.75, ControlNet allégé, LoRA +0.1 — style très marqué."""
        self.denoise_var.set(max(0.75, float(self.denoise_var.get())))
        self.lora_var.set(round(min(1.2, float(self.lora_var.get()) + 0.1), 2))
        self.cnet_var.set(0.35)
        self.cend_var.set(0.6)
        self.dn_val.config(text="%.2f" % float(self.denoise_var.get()))

    def preset_std(self):
        """Retour aux réglages du style (équilibre style / fidélité)."""
        scfg = self.app.styles_cfg["styles"][self.style_key]
        d = self.app.styles_cfg["defaults"]
        s = ml.load_settings()
        self.denoise_var.set(float(scfg.get("denoise",
                                            s.get("gen_denoise") or d["denoise"])))
        self.lora_var.set(float(scfg.get("lora_strength", 1.0)))
        self.cnet_var.set(float(scfg.get("cnet_strength",
                                         s.get("gen_cnet_strength")
                                         or d["cnet_strength"])))
        self.cend_var.set(float(scfg.get("cnet_end", d["cnet_end"])))
        self.ckpt_var.set(self.base_ckpt)
        self.dn_val.config(text="%.2f" % float(self.denoise_var.get()))

    def reset_defaults(self):
        """Réinitialise TOUT aux valeurs par défaut de l'application."""
        scfg = self.app.styles_cfg["styles"][self.style_key]
        d = self.app.styles_cfg["defaults"]
        base = d[scfg["base"]]
        s = ml.load_settings()
        self.denoise_var.set(float(s.get("gen_denoise") or d["denoise"]))
        self.cnet_var.set(float(s.get("gen_cnet_strength")
                                or d["cnet_strength"]))
        self.cend_var.set(float(d["cnet_end"]))
        self.lora_var.set(1.0)
        self.lora_on_var.set(bool(scfg.get("lora")))
        self.ckpt_var.set(s.get("checkpoint_" + scfg["base"])
                          or base["checkpoint"])
        self.qty_var.set(1)
        self.colorize_var.set(False)
        self.pos_var.set("")
        self.neg_var.set("")
        self.dn_val.config(text="%.2f" % float(self.denoise_var.get()))

    def launch(self):
        scfg = self.app.styles_cfg["styles"][self.style_key]
        try:
            count = max(1, min(10, int(self.qty_var.get())))
        except Exception:
            count = 1
        s = ml.load_settings()
        s["gen_variants"] = count
        ml.save_settings(s)
        self.app.settings["gen_variants"] = count
        denoise = round(float(self.denoise_var.get()), 2)
        lora_strength = round(float(self.lora_var.get()), 2)
        cnet_strength = round(float(self.cnet_var.get()), 2)
        cnet_end = round(float(self.cend_var.get()), 2)
        ckpt = self.ckpt_var.get().strip()
        checkpoint = ckpt if ckpt and os.path.basename(ckpt).lower() \
            != os.path.basename(self.base_ckpt).lower() else None
        # mémorise les prompts de CETTE page (rechargés au prochain Custom)
        prompts = ml.load_prompts(self.app.scan_dir, self.style_key)
        pos, neg = self.pos_var.get().strip(), self.neg_var.get().strip()
        if pos or neg:
            prompts[self.page["key"]] = {"pos": pos, "neg": neg}
        else:
            prompts.pop(self.page["key"], None)
        ml.save_prompts(self.app.scan_dir, self.style_key, prompts)
        if self.memo_var.get():
            scfg["denoise"] = denoise
            scfg["cnet_strength"] = cnet_strength
            scfg["cnet_end"] = cnet_end
            if scfg.get("lora"):
                scfg["lora_strength"] = lora_strength
            if checkpoint:
                scfg["checkpoint"] = os.path.basename(ckpt)
            ml.save_styles(self.app.styles_cfg)
        self.app.regen_page(
            self.page, count=count,
            denoise=denoise,
            lora_enabled=bool(self.lora_on_var.get()),
            lora_strength=lora_strength if scfg.get("lora") else None,
            cnet_strength=cnet_strength,
            cnet_end=cnet_end,
            checkpoint=checkpoint,
            colorize=bool(self.colorize_var.get()),
            pos_extra=self.pos_var.get(),
            neg_extra=self.neg_var.get())
        self.destroy()


class RecentView(tb.Frame):
    """Dernières images générées (tous styles), de la plus récente à la plus
    ancienne. Clic = ouvre la page (avec le bon style), molette = pages."""

    def __init__(self, container, app):
        super().__init__(container)
        self.app = app
        self.offset = 0
        self.items = ml.list_recent_variants(app.scan_dir)
        bar = tb.Frame(self, padding=(8, 6, 8, 2))
        bar.pack(fill="x")
        tb.Button(bar, text="←  Retour", bootstyle="secondary",
                  command=app.back).pack(side="left", padx=(0, 12))
        tb.Label(bar, text="🕘  Dernières générations (%d)" % len(self.items),
                 font=("Segoe UI Semibold", 12),
                 bootstyle="primary").pack(side="left")
        self.count_label = tb.Label(bar, text="")
        self.count_label.pack(side="right")
        nav = tb.Frame(self, padding=(8, 2))
        nav.pack(side="bottom", fill="x")
        inner = tb.Frame(nav)
        inner.pack()
        tb.Button(inner, text="◀   Précédentes", bootstyle="secondary",
                  command=lambda: self.turn(-1)).pack(side="left", padx=6)
        self.nav_label = tb.Label(inner, text="", width=24, anchor="center")
        self.nav_label.pack(side="left", padx=10)
        tb.Button(inner, text="Suivantes   ▶", bootstyle="secondary",
                  command=lambda: self.turn(1)).pack(side="left", padx=6)
        self.body = tb.Frame(self, padding=6)
        self.body.pack(expand=True)
        self.after(60, self.render)

    def _capacity(self):
        w = self.winfo_width() or self.app.container.winfo_width() or 1600
        h = (self.winfo_height() or 900) - 110
        tw, th = self.app.thumb_w, self.app.thumb_h
        return max(1, (w - 24) // (tw + 26)), max(1, (h - 40) // (th + 78))

    def turn(self, direction):
        cols, rows = self._capacity()
        per = cols * rows
        new = self.offset + direction * per
        if 0 <= new < max(1, len(self.items)):
            self.offset = new
            self.render()

    def render(self):
        app = self.app
        app.busy("⏳ Chargement des dernières générations…")
        for child in self.body.winfo_children():
            child.destroy()
        colors = app.style.colors
        cols, rows = self._capacity()
        per = cols * rows
        if self.offset >= len(self.items):
            self.offset = 0
        visible = self.items[self.offset:self.offset + per]
        tw, th = app.thumb_w, app.thumb_h
        import datetime
        for idx, it in enumerate(visible):
            r, c = divmod(idx, cols)
            cell = tk.Frame(self.body, width=tw + 16, height=th + 92,
                            highlightthickness=2,
                            highlightbackground=colors.border, bg=colors.bg)
            cell.grid(row=r, column=c, padx=5, pady=5)
            cell.pack_propagate(False)
            ph = app._thumb(it["path"], (tw, th))
            img = tb.Label(cell, image=ph or "", cursor="hand2")
            img.image = ph
            img.pack(pady=(4, 0))
            when = datetime.datetime.fromtimestamp(it["mtime"])
            style_label = app.style_labels.get(it["style"], it["style"])
            cap = tb.Label(cell, text="%s\n%s · %s" % (
                it["key"], style_label, when.strftime("%d/%m %H:%M")),
                font=("Segoe UI", 8), justify="center", wraplength=tw + 8)
            cap.pack(side="bottom", pady=(0, 3))
            for w in (img, cap):
                w.bind("<Button-1>", lambda e, i=it: self._open(i))
                w.bind("<Button-3>",
                       lambda e, i=it: app.open_zoom(i["path"]))
            # ✖ corbeille directe (sans confirmation, récupérable)
            x = tk.Label(cell, text="✖", font=("Segoe UI", 8, "bold"),
                         bg=colors.danger, fg="#ffffff", cursor="hand2",
                         padx=4)
            x.place(relx=1.0, x=-4, y=4, anchor="ne")
            x.bind("<Button-1>", lambda e, i=it: self._delete(i))
            tip(x, "Envoie cette variante à la corbeille (récupérable).")
            # ↗ ouvrir avec l'application associée
            o = tk.Label(cell, text="↗", font=("Segoe UI", 9, "bold"),
                         bg=_blend(colors.bg, colors.fg, 0.25),
                         fg=colors.fg, cursor="hand2", padx=4)
            o.place(x=4, y=4, anchor="nw")
            o.bind("<Button-1>",
                   lambda e, i=it: self._open_external(i))
            tip(o, "Ouvre l'image dans l'application associée aux PNG "
                   "(visionneuse, éditeur…).")
        if self.items:
            last = min(self.offset + per, len(self.items))
            self.nav_label.config(text="%d – %d  /  %d"
                                  % (self.offset + 1, last, len(self.items)))
        else:
            self.nav_label.config(text="aucune génération")
        app.unbusy()

    def _delete(self, it):
        """Corbeille directe depuis la vue Récentes."""
        app = self.app
        ml.trash(it["path"], it["path"] + ".jpg")
        sel = ml.load_selections(app.scan_dir, it["style"])
        if sel.get(it["key"]) == it["fname"]:
            del sel[it["key"]]
            ml.save_selections(app.scan_dir, it["style"], sel)
        self.items = [x for x in self.items if x is not it]
        app.status_label.config(text="Variante envoyée à la corbeille.")
        self.render()
        app.refresh_grid()

    def _open_external(self, it):
        try:
            os.startfile(it["path"])
        except OSError as e:
            self.app.status_label.config(text="⚠ Ouverture impossible : %s" % e)

    def _open(self, it):
        app = self.app
        label = app.style_labels.get(it["style"])
        page = next((p for p in app.pages if p["key"] == it["key"]), None)
        if not label or page is None:
            app.status_label.config(
                text="Cette page n'est pas dans le dossier affiché.")
            return
        if app.style_var.get() != label:
            app.style_var.set(label)
            app._style_changed()
        fname = it["fname"]
        app.back()  # ferme la vue Récentes
        app.open_page(page)
        app.views["page"].viewed = fname
        app.views["page"].refresh()


class StorageView(tb.Frame):
    """Espace occupé par chaque série de régénérations, avec suppression
    (les originaux ne sont jamais touchés)."""

    def __init__(self, container, app):
        super().__init__(container)
        self.app = app
        bar = tb.Frame(self, padding=(8, 6, 8, 2))
        bar.pack(fill="x")
        tb.Button(bar, text="←  Retour", bootstyle="secondary",
                  command=app.back).pack(side="left", padx=(0, 12))
        tb.Label(bar, text="🗄  Stockage des régénérations",
                 font=("Segoe UI Semibold", 12),
                 bootstyle="primary").pack(side="left")
        self.total_label = tb.Label(bar, text="")
        self.total_label.pack(side="right")
        outer = tb.Frame(self)
        outer.pack(expand=True, pady=8)
        self.table = tb.Frame(outer, padding=10)
        self.table.pack()
        tb.Label(self, text="Supprimer efface uniquement les variantes "
                            "générées et les sélections de la série — "
                            "jamais les images originales.",
                 padding=(12, 4)).pack(anchor="w", side="bottom")
        self._build()

    def _build(self):
        """Le calcul des tailles peut être long : il part en arrière-plan."""
        for child in self.table.winfo_children():
            child.destroy()
        tb.Label(self.table, text="⏳ Calcul de l'espace occupé…").grid(
            row=0, column=0, padx=10, pady=10)
        self.app.busy("⏳ Calcul de l'espace occupé…")

        def work():
            rows = ml.storage_report()
            self.app.msg_q.put(("call", lambda: self._fill(rows)))
        threading.Thread(target=work, daemon=True).start()

    def _fill(self, rows):
        if not self.winfo_exists():
            return
        self.app.unbusy()
        for child in self.table.winfo_children():
            child.destroy()
        total = sum(r["bytes"] for r in rows)
        self.total_label.config(text="total : %s" % ml.human_size(total))
        headers = ("Série", "Style", "Variantes", "Taille", "")
        for c, txt in enumerate(headers):
            tb.Label(self.table, text=txt,
                     font=("Segoe UI Semibold", 10)).grid(
                row=0, column=c, sticky="w", padx=10, pady=(0, 6))
        if not rows:
            tb.Label(self.table, text="Aucune régénération stockée.").grid(
                row=1, column=0, columnspan=5, sticky="w", padx=10)
            return
        for r, row in enumerate(rows, start=1):
            style_label = self.app.style_labels.get(row["style"], row["style"])
            tb.Label(self.table, text=row["series"]).grid(
                row=r, column=0, sticky="w", padx=10, pady=2)
            tb.Label(self.table, text=style_label).grid(
                row=r, column=1, sticky="w", padx=10)
            tb.Label(self.table, text=str(row["files"])).grid(
                row=r, column=2, sticky="e", padx=10)
            tb.Label(self.table, text=ml.human_size(row["bytes"])).grid(
                row=r, column=3, sticky="e", padx=10)
            tb.Button(self.table, text="🗑  Supprimer",
                      bootstyle="danger-outline",
                      command=lambda d=row: self._delete(d)).grid(
                row=r, column=4, padx=10, pady=2)

    def _delete(self, row):
        ml.delete_work(row["dir"])  # envoyé à la corbeille (récupérable)
        self.app.status_label.config(
            text="Série envoyée à la corbeille (%s récupérables)."
            % ml.human_size(row["bytes"]))
        self._build()
        self.app.refresh_grid()


class OptionsView(tb.Toplevel):
    """Pop-in d'options en onglets : apparence, génération, bulles,
    ComfyUI, export. Fenêtre liée à l'app (pas d'entrée barre des tâches)."""

    def __init__(self, app):
        super().__init__(title="Options — " + ml.APP_NAME, master=app)
        self.transient(app)
        self.app = app
        s = ml.load_settings()
        d = app.styles_cfg["defaults"]
        wl, wt, _wr, _wb = work_area()
        self.geometry("+%d+%d" % (wl + 340, wt + 70))
        self.bind("<Escape>", lambda e: self.destroy())
        nb = self.nb = tb.Notebook(self, padding=8)
        nb.pack(fill="both", expand=True, padx=8, pady=8)

        # ---- Apparence
        tab = tb.Frame(nb, padding=14)
        nb.add(tab, text="  Apparence  ")
        tb.Label(tab, text="Thème de l'application",
                 font=("Segoe UI Semibold", 10)).grid(row=0, column=0,
                                                      columnspan=4, sticky="w")
        self.theme_pairs = themes.labels()
        theme_names = [lbl for _n, lbl in self.theme_pairs]
        current = s.get("theme", "discord")
        self.theme_var = tk.StringVar(
            value=dict(self.theme_pairs).get(current, theme_names[0]))
        tb.Button(tab, text="◀", width=3, bootstyle="info-outline",
                  command=lambda: self._cycle_theme(-1)).grid(row=1, column=0,
                                                              pady=8)
        cb = tb.Combobox(tab, textvariable=self.theme_var, state="readonly",
                         values=theme_names, width=22)
        cb.grid(row=1, column=1, padx=6)
        cb.bind("<<ComboboxSelected>>", lambda e: self._apply_theme())
        tb.Button(tab, text="▶", width=3, bootstyle="info-outline",
                  command=lambda: self._cycle_theme(1)).grid(row=1, column=2)
        tb.Label(tab, text="(appliqué immédiatement)").grid(row=1, column=3, padx=8)
        tb.Label(tab, text="%d thèmes : apps populaires et éditeurs de texte. "
                           "La taille des vignettes se règle avec le curseur 🔍 "
                           "en bas à droite de la fenêtre." % len(theme_names),
                 wraplength=420, justify="left").grid(
            row=2, column=0, columnspan=4, sticky="w", pady=(14, 0))

        # ---- Génération (préremplie avec les valeurs réellement utilisées)
        tab = tb.Frame(nb, padding=14)
        nb.add(tab, text="  Génération  ")
        self.gen_vars = {}
        rows = [
            ("gen_steps", "Étapes (steps)", "spin", (10, 60, 1),
             s.get("gen_steps") or d["steps"],
             "Nombre de passes de dessin de l'IA. 20-30 = bon compromis ; "
             "plus = un peu plus fin mais plus lent ; moins = rapide mais "
             "brouillon."),
            ("gen_sampler", "Sampler", "combo", ml.SAMPLERS,
             s.get("gen_sampler") or "euler_ancestral",
             "Algorithme de dessin. « euler_ancestral » est un excellent "
             "défaut pour l'anime ; change seulement si tu sais pourquoi."),
            ("gen_scheduler", "Scheduler", "combo", ml.SCHEDULERS,
             s.get("gen_scheduler") or "normal",
             "Répartition des étapes. « normal » convient presque toujours ; "
             "« karras » lisse parfois mieux les dégradés."),
            ("long_side", "Long côté de génération (px)", "spin",
             (768, 2048, 64), s.get("long_side") or d["long_side"],
             "Résolution de travail de l'IA (le résultat est ensuite remis à "
             "la taille de la page). 1344 = bon équilibre qualité/vitesse ; "
             "plus haut = plus de détails mais plus lent et plus de VRAM."),
            ("cnet_type", "Type de contrôle", "combo", ["canny", "lineart"],
             s.get("cnet_type") or "canny",
             "Comment le guide « lit » l'original : canny = contours nets "
             "(bon partout) ; lineart = traits de dessin (parfois mieux pour "
             "le manga N&B)."),
            ("gen_denoise", "Denoise par défaut *", "spin", (0.3, 1.0, 0.05),
             s.get("gen_denoise") or d["denoise"],
             "Liberté de l'IA : 0.3-0.5 très fidèle, 0.7 équilibré, 0.9+ "
             "réinvente (pour coloriser). Les styles peuvent imposer leur "
             "propre valeur."),
            ("gen_cnet_strength", "Force ControlNet par défaut *", "spin",
             (0.1, 1.0, 0.05), s.get("gen_cnet_strength") or d["cnet_strength"],
             "Respect de la mise en page d'origine : 0.6+ très fidèle, 0.35 "
             "plus créatif."),
            ("gen_timeout", "Timeout par page (secondes)", "spin",
             (120, 3600, 60), s.get("gen_timeout") or 900,
             "Au-delà de ce délai, la génération d'une page est abandonnée. "
             "Augmente si ton GPU est occupé par autre chose."),
            ("cfg_pony", "CFG (styles Pony)", "spin", (1.0, 12.0, 0.5),
             s.get("cfg_pony") or d["pony"]["cfg"],
             "À quel point l'IA suit la consigne texte (styles basés Pony). "
             "7 = normal ; trop haut = images « brûlées », trop bas = "
             "consigne ignorée."),
            ("cfg_illustrious", "CFG (styles Illustrious)", "spin",
             (1.0, 12.0, 0.5), s.get("cfg_illustrious") or d["illustrious"]["cfg"],
             "Idem pour les styles basés Illustrious. 5-6 recommandé."),
            ("checkpoint_pony", "Checkpoint Pony", "ckpt", None,
             s.get("checkpoint_pony") or d["pony"]["checkpoint"],
             "Modèle SDXL utilisé par les styles de la famille Pony. Tape "
             "une partie du nom pour filtrer la liste."),
            ("checkpoint_illustrious", "Checkpoint Illustrious", "ckpt", None,
             s.get("checkpoint_illustrious") or d["illustrious"]["checkpoint"],
             "Modèle SDXL utilisé par les styles de la famille Illustrious. "
             "Tape une partie du nom pour filtrer la liste."),
        ]
        ckpts = ml.list_checkpoints()
        for i, (key, label, kind, spec, val, tt) in enumerate(rows):
            tip(tb.Label(tab, text=label), tt).grid(row=i, column=0,
                                                    sticky="w", pady=3)
            var = tk.StringVar(value="" if val in (None, "") else str(val))
            self.gen_vars[key] = var
            if kind == "spin":
                w = tb.Spinbox(tab, from_=spec[0], to=spec[1], increment=spec[2],
                               textvariable=var, width=10)
            elif kind == "combo":
                w = tb.Combobox(tab, textvariable=var, values=spec, width=18)
            else:  # "ckpt" : combobox filtrable au clavier
                w = make_filterable(
                    tb.Combobox(tab, textvariable=var, width=36),
                    ckpts or [str(val)])
            tip(w, tt)
            w.grid(row=i, column=1, sticky="w", padx=12)
        self.boost_def_var = tk.BooleanVar(value=bool(s.get("boost", False)))
        tip(tb.Checkbutton(tab, text="Boost style par défaut (denoise ↑, "
                                     "ControlNet ↓, LoRA ↑)",
                           variable=self.boost_def_var,
                           bootstyle="round-toggle"),
            "Applique automatiquement le préréglage « Style renforcé » à "
            "toutes les générations : style plus marqué, fidélité un peu "
            "réduite.").grid(
            row=len(rows), column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.sound_var = tk.BooleanVar(value=bool(s.get("done_sound", True)))
        tip(tb.Checkbutton(tab, text="🔔 Son discret quand toute la "
                                     "génération est terminée",
                           variable=self.sound_var,
                           bootstyle="round-toggle"),
            "Joue un petit carillon doux (1 s) quand la file de génération "
            "est finie — pratique quand tu fais autre chose pendant qu'un "
            "chapitre tourne.").grid(
            row=len(rows) + 2, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self.auto_sex_var = tk.BooleanVar(value=bool(s.get("auto_sex", True)))
        tip(tb.Checkbutton(tab, text="Détecter le sexe du personnage (WD "
                                     "tagger, seulement si un seul perso net)",
                           variable=self.auto_sex_var,
                           bootstyle="round-toggle"),
            "Avant chaque génération, une IA locale (~1 s) regarde l'image : "
            "si UN seul personnage est identifié avec certitude, "
            "« 1boy »/« 1girl » est ajouté à la consigne pour éviter les "
            "changements de sexe. Plusieurs personnages ou doute = ne fait "
            "rien.").grid(
            row=len(rows) + 1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        tb.Label(tab, text="* utilisés pour les styles qui n'imposent pas "
                           "leur propre valeur (ex. Colorisation force 0.9).",
                 wraplength=430, justify="left").grid(
            row=len(rows) + 3, column=0, columnspan=2, sticky="w", pady=(8, 0))

        # ---- Bulles
        tab = tb.Frame(nb, padding=14)
        nb.add(tab, text="  Phylactères  ")
        self.bubbles_var = tk.BooleanVar(value=bool(s.get("preserve_bubbles", True)))
        tip(tb.Checkbutton(tab, text="Préserver les bulles de texte "
                                     "(réincruster l'original)",
                           variable=self.bubbles_var,
                           bootstyle="round-toggle"),
            "Détecte les bulles et cartouches de texte sur la page d'origine "
            "et les recopie telles quelles sur l'image stylisée : le texte "
            "reste parfaitement lisible.").grid(row=0, column=0,
                                                columnspan=3, sticky="w")
        tip(tb.Label(tab, text="Méthode de détection"),
            "« Formes + blocs de texte » : heuristique locale, instantanée, "
            "sans téléchargement.\n« IA comic-text-detector » : modèle "
            "spécialisé (utilisé par les outils de scanlation) — plus précis "
            "sur les bulles ouvertes et les mises en page inhabituelles, "
            "ignore les onomatopées ; télécharge un modèle de ~90 Mo au "
            "premier usage, +0,5 s par page (CPU).\n« Combinée » : union des "
            "deux — préserve un maximum de texte, au risque de quelques "
            "zones en trop.").grid(row=1, column=0, sticky="w", pady=(14, 0))
        self._bubble_methods = [("shape", "Formes + blocs de texte (rapide)"),
                                ("ai", "IA comic-text-detector (précise)"),
                                ("both", "Combinée (union des deux)")]
        cur = s.get("bubble_method", "shape")
        self.bubble_method_var = tk.StringVar(
            value=dict(self._bubble_methods).get(cur,
                                                 self._bubble_methods[0][1]))
        tb.Combobox(tab, textvariable=self.bubble_method_var,
                    values=[lbl for _k, lbl in self._bubble_methods],
                    state="readonly", width=32).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=6)
        tip(tb.Label(tab, text="Seuil de blanc (plus bas = plus de zones "
                               "considérées comme bulles)"),
            "Luminosité minimale pour qu'une zone soit considérée comme le "
            "fond blanc d'une bulle. Baisse-le (215-220) si des bulles "
            "légèrement grises sont ratées ; monte-le (235+) si des zones de "
            "dessin claires sont prises pour des bulles.").grid(
            row=3, column=0, sticky="w", pady=(14, 0))
        self.bubble_thresh_var = tk.IntVar(value=int(s.get("bubble_thresh", 225)))
        tb.Scale(tab, from_=200, to=250, variable=self.bubble_thresh_var,
                 length=260, command=lambda v: self.bubble_val.config(
                     text=str(int(float(v))))).grid(row=4, column=0,
                                                    sticky="w", pady=6)
        self.bubble_val = tb.Label(tab, text=str(self.bubble_thresh_var.get()),
                                   width=4)
        self.bubble_val.grid(row=4, column=1, padx=8)
        tb.Label(tab, text="Formes : zones quasi blanches, fermées, contenant "
                           "du texte — cases et gouttières écartées.\n"
                           "IA : blocs de dialogue localisés par le modèle, "
                           "étendus au ballon blanc englobant.").grid(
            row=5, column=0, columnspan=3, sticky="w", pady=(12, 0))

        # ---- ComfyUI
        tab = tb.Frame(nb, padding=14)
        nb.add(tab, text="  ComfyUI  ")
        lc = ml.load_launch_config()
        tb.Label(tab, text="URL du serveur").grid(row=0, column=0, sticky="w", pady=4)
        self.url_var = tk.StringVar(value=s.get("comfy_url",
                                                "http://127.0.0.1:8188"))
        tb.Entry(tab, textvariable=self.url_var, width=34).grid(
            row=0, column=1, sticky="w", padx=10)
        tb.Button(tab, text="Tester", bootstyle="info-outline",
                  command=self._test_comfy).grid(row=0, column=2)
        tb.Label(tab, text="Python (venv ComfyUI)").grid(row=1, column=0,
                                                         sticky="w", pady=4)
        self.py_var = tk.StringVar(value=lc.get("python", ""))
        tb.Entry(tab, textvariable=self.py_var, width=48).grid(
            row=1, column=1, columnspan=2, sticky="w", padx=10)
        tb.Label(tab, text="Script main.py de ComfyUI").grid(row=2, column=0,
                                                             sticky="w", pady=4)
        self.main_var = tk.StringVar(value=lc.get("comfy_main", ""))
        tb.Entry(tab, textvariable=self.main_var, width=48).grid(
            row=2, column=1, columnspan=2, sticky="w", padx=10)
        self.comfy_test_label = tb.Label(tab, text="")
        self.comfy_test_label.grid(row=3, column=0, columnspan=3, sticky="w",
                                   pady=(10, 0))
        tb.Label(tab, text="Ces chemins servent au bouton « Lancer ComfyUI ».").grid(
            row=4, column=0, columnspan=3, sticky="w", pady=(6, 0))

        # ---- Export
        tab = tb.Frame(nb, padding=14)
        nb.add(tab, text="  Export  ")
        tb.Label(tab, text="Format des pages stylisées").grid(row=0, column=0,
                                                              sticky="w", pady=4)
        self.fmt_var = tk.StringVar(value=(s.get("export_format") or "png").upper())
        tb.Combobox(tab, textvariable=self.fmt_var, values=["PNG", "JPEG"],
                    state="readonly", width=10).grid(row=0, column=1,
                                                     sticky="w", padx=10)
        tb.Label(tab, text="Qualité JPEG").grid(row=1, column=0, sticky="w", pady=4)
        self.quality_var = tk.IntVar(value=int(s.get("export_quality", 95)))
        tb.Scale(tab, from_=60, to=100, variable=self.quality_var, length=200,
                 command=lambda v: self.quality_label.config(
                     text=str(int(float(v))))).grid(row=1, column=1,
                                                    sticky="w", padx=10)
        self.quality_label = tb.Label(tab, text=str(self.quality_var.get()), width=4)
        self.quality_label.grid(row=1, column=2)
        self.open_after_var = tk.BooleanVar(value=bool(s.get("open_after_export",
                                                             True)))
        tb.Checkbutton(tab, text="Ouvrir le dossier après l'export",
                       variable=self.open_after_var,
                       bootstyle="round-toggle").grid(row=2, column=0,
                                                      columnspan=2, sticky="w",
                                                      pady=(12, 0))
        tip(tb.Label(tab, text="Nommage (mode « quelques images »)"),
            "Modèle du nom de fichier exporté quand tu travailles sur "
            "quelques images. {nom} = nom d'origine, {style} = style "
            "appliqué, {date} = AAAAMMJJ, {heure} = HHMMSS.").grid(
            row=3, column=0, sticky="w", pady=(12, 0))
        self.pattern_var = tk.StringVar(
            value=s.get("export_pattern") or ml.DEFAULT_PATTERN)
        tip(tb.Entry(tab, textvariable=self.pattern_var, width=34),
            "Ex. « {nom}_{style}_{date}_{heure} » donne "
            "page_001_toriyama_20260723_213045.png").grid(
            row=3, column=1, columnspan=2, sticky="w", padx=10, pady=(12, 0))
        tb.Label(tab, text="Jetons : {nom} {style} {date} {heure} — ex. "
                           "page_001_toriyama_20260723_213045.png\n"
                           "Mode dossier : arborescence et noms d'origine "
                           "dans <dossier>_stylized ; l'original est copié "
                           "tel quel quand il est l'image gardée.",
                 wraplength=460, justify="left").grid(
            row=4, column=0, columnspan=3, sticky="w", pady=(10, 0))

        # ---- boutons
        btns = tb.Frame(self, padding=(8, 0, 8, 10))
        btns.pack(fill="x")
        tb.Button(btns, text="Enregistrer", bootstyle="primary",
                  command=self.save).pack(side="right", padx=4)
        tb.Button(btns, text="Fermer", bootstyle="secondary",
                  command=self.destroy).pack(side="right")

    def _cycle_theme(self, direction):
        names = [lbl for _n, lbl in self.theme_pairs]
        try:
            i = names.index(self.theme_var.get())
        except ValueError:
            i = 0
        self.theme_var.set(names[(i + direction) % len(names)])
        self._apply_theme()

    def _apply_theme(self):
        label = self.theme_var.get()
        for name, lbl in self.theme_pairs:
            if lbl == label:
                self.app.apply_theme(name)
                break

    def _test_comfy(self):
        s = ml.load_settings()
        s["comfy_url"] = self.url_var.get().strip()
        ml.save_settings(s)
        ok = ml.comfy_alive()
        self.comfy_test_label.config(
            text="✔ ComfyUI répond" if ok else "✘ Pas de réponse à cette adresse",
            bootstyle="success" if ok else "danger")

    INT_KEYS = ("gen_steps", "long_side", "gen_timeout")
    FLOAT_KEYS = ("gen_denoise", "gen_cnet_strength", "cfg_pony",
                  "cfg_illustrious")

    def save(self):
        s = ml.load_settings()
        for key, var in self.gen_vars.items():
            val = var.get().strip()
            if not val:
                s.pop(key, None)
                continue
            try:
                if key in self.INT_KEYS:
                    s[key] = int(float(val.replace(",", ".")))
                elif key in self.FLOAT_KEYS:
                    s[key] = float(val.replace(",", "."))
                else:
                    s[key] = val
            except ValueError:
                s.pop(key, None)
        s["boost"] = bool(self.boost_def_var.get())
        s["done_sound"] = bool(self.sound_var.get())
        s["auto_sex"] = bool(self.auto_sex_var.get())
        s["preserve_bubbles"] = bool(self.bubbles_var.get())
        s["bubble_thresh"] = int(self.bubble_thresh_var.get())
        s["bubble_method"] = next(
            (k for k, lbl in self._bubble_methods
             if lbl == self.bubble_method_var.get()), "shape")
        s["comfy_url"] = self.url_var.get().strip()
        s["export_format"] = self.fmt_var.get().lower()
        s["export_quality"] = int(self.quality_var.get())
        s["open_after_export"] = bool(self.open_after_var.get())
        pattern = self.pattern_var.get().strip()
        if pattern and pattern != ml.DEFAULT_PATTERN:
            s["export_pattern"] = pattern
        else:
            s.pop("export_pattern", None)
        for key in ("theme", "thumb_w", "strip_w", "last_dir", "style",
                    "first_n", "gen_variants", "sort_pages", "colorize"):
            if key in self.app.settings:
                s[key] = self.app.settings[key]
        ml.save_settings(s)
        self.app.settings = s
        lc = ml.load_launch_config()
        lc["python"] = self.py_var.get().strip()
        lc["comfy_main"] = self.main_var.get().strip()
        ml.save_launch_config(lc)
        self.app.status_label.config(text="Options enregistrées.")
        self.destroy()


def main():
    try:
        import ctypes
        # rendu net sur écrans HiDPI
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
        # identité d'application propre (icône/groupement barre des tâches)
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "ComicRestyler.App")
    except Exception:
        pass
    App().mainloop()


if __name__ == "__main__":
    main()
