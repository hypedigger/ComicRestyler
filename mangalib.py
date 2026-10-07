# -*- coding: utf-8 -*-
"""Bibliothèque ComicRestyler : restylisation de pages manga/BD/comics via ComfyUI.

Pipeline : page -> resize SDXL -> img2img + ControlNet union (canny/lineart)
-> resize taille d'origine -> réincrustation des phylactères (bulles de texte)
détectés sur l'original.

Arborescence de sortie, à côté du dossier scanné :
    <parent>/<dossier>_stylized/<style>/variants/<page>/seed_XXXX_dYY.png
    <parent>/<dossier>_stylized/<style>/_selections.json   (image gardée par page)
    <parent>/<dossier>_stylized/<style>/export/<arborescence d'origine>
"""
import datetime
import hashlib
import io
import json
import os
import random
import re
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
import uuid

from PIL import Image, ImageFilter
from PIL.PngImagePlugin import PngInfo

Image.MAX_IMAGE_PIXELS = None  # pages de scan parfois très grandes

APP_NAME = "ComicRestyler"
APP_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(APP_DIR, "app_settings.json")
LAUNCH_FILE = os.path.join(APP_DIR, "launch_config.json")
CONTROLNET_NAME_HINT = "controlnet-union-sdxl-promax"
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
OUT_SUFFIX = "_stylized"
ORIGINAL = "__original__"

SAMPLERS = ["euler", "euler_ancestral", "dpmpp_2m", "dpmpp_2m_sde",
            "dpmpp_3m_sde", "dpmpp_sde", "ddim", "uni_pc"]
SCHEDULERS = ["normal", "karras", "sgm_uniform", "exponential", "beta", "simple"]


# ---------------------------------------------------------------- config

def load_styles():
    # styles.json est personnel (non versionne) ; a defaut, les styles
    # generiques livres avec l'app (styles.example.json) sont utilises.
    path = os.path.join(APP_DIR, "styles.json")
    if not os.path.exists(path):
        path = os.path.join(APP_DIR, "styles.example.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_styles(cfg):
    with open(os.path.join(APP_DIR, "styles.json"), "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def load_settings():
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(s):
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=1, ensure_ascii=False)


def comfy_url():
    return os.environ.get("COMFY_URL") \
        or load_settings().get("comfy_url") or "http://127.0.0.1:8188"


def load_launch_config():
    try:
        with open(LAUNCH_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_launch_config(cfg):
    with open(LAUNCH_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=1, ensure_ascii=False)


def start_comfyui():
    """Démarre ComfyUI headless d'après launch_config.json (fenêtre réduite)."""
    cfg = load_launch_config()
    python = os.path.expandvars(cfg.get("python", ""))
    main = os.path.expandvars(cfg.get("comfy_main", ""))
    if not (os.path.exists(python) and os.path.exists(main)):
        raise FileNotFoundError("launch_config.json : chemins python/comfy_main invalides")
    args = [python, main] + [os.path.expandvars(a) for a in cfg.get("comfy_args", [])]
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 7  # SW_SHOWMINNOACTIVE
    subprocess.Popen(args, startupinfo=si,
                     creationflags=subprocess.CREATE_NEW_CONSOLE)


# ---------------------------------------------------------------- scan

def _natural_key(s):
    return [int(t) if t.isdigit() else t.lower()
            for t in re.split(r"(\d+)", s)]


def scan_pages(root):
    """Toutes les images sous root (récursif), triées naturellement.

    Retourne [{path, rel, key, name}] ; rel = chemin relatif (sous-dossier =
    chapitre), key = rel sans extension (identifiant stable de la page).
    Les dossiers de sortie (*_stylized) sont ignorés.
    """
    pages = []
    root = os.path.abspath(root)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            (d for d in dirnames
             if not d.endswith(OUT_SUFFIX) and not d.startswith("_appdata")),
            key=_natural_key)
        for name in sorted(filenames, key=_natural_key):
            if name.lower().endswith(IMAGE_EXTS):
                path = os.path.join(dirpath, name)
                rel = os.path.relpath(path, root)
                pages.append({"path": path, "rel": rel,
                              "key": os.path.splitext(rel)[0].replace("\\", "/"),
                              "name": name})
    return pages


def work_root(scan_dir):
    """Répertoire de travail interne de l'app pour ce dossier scanné.

    Les variantes y vivent tant qu'elles ne sont pas exportées
    (%LOCALAPPDATA%/ComicRestyler/work/<nom>_<hash>)."""
    scan_dir = os.path.abspath(scan_dir)
    base = os.path.basename(scan_dir.rstrip("\\/"))
    h = hashlib.md5(scan_dir.lower().encode("utf-8")).hexdigest()[:8]
    return os.path.join(os.environ.get("LOCALAPPDATA", APP_DIR), APP_NAME,
                        "work", "%s_%s" % (base, h))


def output_root(scan_dir, style_key):
    """Zone de travail des variantes pour un style (interne à l'app)."""
    return os.path.join(work_root(scan_dir), style_key)


def export_root(scan_dir, style_key):
    """Destination d'export : répertoire adjacent au dossier scanné."""
    scan_dir = os.path.abspath(scan_dir)
    base = os.path.basename(scan_dir.rstrip("\\/"))
    return os.path.join(os.path.dirname(scan_dir), base + OUT_SUFFIX, style_key)


def variants_dir(scan_dir, style_key, page):
    return os.path.join(output_root(scan_dir, style_key), "variants",
                        page["key"].replace("/", os.sep))


def list_variants(scan_dir, style_key, page):
    """Fichiers de variantes triés par date de génération (plus ancien d'abord)."""
    vdir = variants_dir(scan_dir, style_key, page)
    if not os.path.isdir(vdir):
        return []
    files = [f for f in os.listdir(vdir) if f.lower().endswith(".png")]
    files.sort(key=lambda f: os.path.getmtime(os.path.join(vdir, f)))
    return files


def variant_path(scan_dir, style_key, page, fname):
    return os.path.join(variants_dir(scan_dir, style_key, page), fname)


def list_recent_variants(scan_dir, limit=500):
    """Toutes les variantes générées pour ce dossier (tous styles),
    triées de la plus récente à la plus ancienne."""
    out = []
    wroot = work_root(scan_dir)
    if not os.path.isdir(wroot):
        return out
    for style_key in os.listdir(wroot):
        vroot = os.path.join(wroot, style_key, "variants")
        if not os.path.isdir(vroot):
            continue
        for dirpath, _dn, files in os.walk(vroot):
            for f in files:
                if f.lower().endswith(".png"):
                    p = os.path.join(dirpath, f)
                    try:
                        mt = os.path.getmtime(p)
                    except OSError:
                        continue
                    out.append({"path": p, "style": style_key, "fname": f,
                                "key": os.path.relpath(dirpath, vroot)
                                .replace("\\", "/"), "mtime": mt})
    out.sort(key=lambda d: -d["mtime"])
    return out[:limit]


def storage_report():
    """Espace occupé par chaque série de régénérations (par dossier scanné
    et par style). [{series, style, dir, files, bytes}]"""
    base = os.path.join(os.environ.get("LOCALAPPDATA", APP_DIR), APP_NAME,
                        "work")
    rows = []
    if not os.path.isdir(base):
        return rows
    for series in sorted(os.listdir(base)):
        sdir = os.path.join(base, series)
        if not os.path.isdir(sdir):
            continue
        name = series.rsplit("_", 1)[0]
        for style_key in sorted(os.listdir(sdir)):
            stdir = os.path.join(sdir, style_key)
            if not os.path.isdir(stdir):
                continue
            nb = size = 0
            for dp, _d, fs in os.walk(stdir):
                for f in fs:
                    try:
                        size += os.path.getsize(os.path.join(dp, f))
                    except OSError:
                        pass
                    if f.lower().endswith(".png"):
                        nb += 1
            rows.append({"series": name, "style": style_key, "dir": stdir,
                         "files": nb, "bytes": size})
    return rows


def trash(*paths):
    """Envoie fichiers/dossiers à la CORBEILLE Windows (récupérables),
    sans boîte de confirmation."""
    paths = [os.path.abspath(p) for p in paths if os.path.exists(p)]
    if not paths:
        return
    try:
        from win32com.shell import shell, shellcon
        flags = (shellcon.FOF_ALLOWUNDO | shellcon.FOF_NOCONFIRMATION
                 | shellcon.FOF_SILENT | shellcon.FOF_NOERRORUI)
        shell.SHFileOperation((0, shellcon.FO_DELETE,
                               "\0".join(paths) + "\0", None, flags,
                               None, None))
    except Exception:
        for p in paths:  # secours : suppression directe
            try:
                if os.path.isdir(p):
                    shutil.rmtree(p, ignore_errors=True)
                else:
                    os.remove(p)
            except OSError:
                pass


def delete_work(path):
    """Envoie une série de régénérations à la corbeille (jamais les
    originaux)."""
    base = os.path.join(os.environ.get("LOCALAPPDATA", APP_DIR), APP_NAME)
    path = os.path.abspath(path)
    if path.startswith(os.path.abspath(base)):  # garde-fou
        trash(path)


def human_size(n):
    for unit in ("o", "Ko", "Mo", "Go"):
        if n < 1024 or unit == "Go":
            return ("%.1f %s" if unit != "o" else "%d %s") % (n, unit)
        n /= 1024.0
    return "%d o" % n


def load_variant_meta(scan_dir, style_key, page):
    """Métadonnées de génération par variante : {fname: {seed, denoise,
    cnet, cnet_end, checkpoint, lora_strength, colorize}}."""
    p = os.path.join(variants_dir(scan_dir, style_key, page), "_meta.json")
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def variant_preview(path, long_side=1600):
    """Aperçu JPEG rapide d'une variante PNG (créé au besoin, mis en cache
    à côté du PNG). Les PNG pleine page sont lents à décoder ; le JPEG
    s'affiche instantanément."""
    prev = path + ".jpg"
    try:
        if os.path.exists(prev) \
                and os.path.getmtime(prev) >= os.path.getmtime(path):
            return prev
        with Image.open(path) as im:
            im = im.convert("RGB")
            im.thumbnail((long_side, long_side), Image.LANCZOS)
            im.save(prev, quality=87)
        return prev
    except Exception:
        return path


def variant_label(fname):
    """'seed_0000001234_d0.70.png' -> 'seed 1234 · denoise 0.70'."""
    m = re.match(r"seed_(\d+)_d([\d.]+)\.png$", fname)
    if m:
        return "seed %d · denoise %s" % (int(m.group(1)), m.group(2))
    return fname


# ---------------------------------------------------------------- sélections

def _selections_file(scan_dir, style_key):
    return os.path.join(output_root(scan_dir, style_key), "_selections.json")


def load_selections(scan_dir, style_key):
    """{key: nom_de_variante | "__original__"} — l'image gardée par page."""
    try:
        with open(_selections_file(scan_dir, style_key), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_selections(scan_dir, style_key, sel):
    os.makedirs(output_root(scan_dir, style_key), exist_ok=True)
    with open(_selections_file(scan_dir, style_key), "w", encoding="utf-8") as f:
        json.dump(sel, f, indent=1, ensure_ascii=False)


def load_prompts(scan_dir, style_key):
    """Prompts +/− mémorisés PAR PAGE : {key: {"pos": ..., "neg": ...}}."""
    p = os.path.join(output_root(scan_dir, style_key), "_prompts.json")
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_prompts(scan_dir, style_key, prompts):
    os.makedirs(output_root(scan_dir, style_key), exist_ok=True)
    p = os.path.join(output_root(scan_dir, style_key), "_prompts.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(prompts, f, indent=1, ensure_ascii=False)


def effective_choice(scan_dir, style_key, page, selections=None, variants=None):
    """Image gardée pour une page : choix explicite valide, sinon la première
    variante générée, sinon l'original. Retourne ORIGINAL ou un nom de fichier."""
    if selections is None:
        selections = load_selections(scan_dir, style_key)
    if variants is None:
        variants = list_variants(scan_dir, style_key, page)
    explicit = selections.get(page["key"])
    if explicit == ORIGINAL:
        return ORIGINAL
    if explicit and explicit in variants:
        return explicit
    return variants[0] if variants else ORIGINAL


def select_bulk(scan_dir, style_key, pages, which="first"):
    """Sélectionne pour chaque page la première ou la dernière génération
    (l'original si la page n'a aucune variante)."""
    sel = load_selections(scan_dir, style_key)
    for p in pages:
        variants = list_variants(scan_dir, style_key, p)
        if variants:
            sel[p["key"]] = variants[0] if which == "first" else variants[-1]
        else:
            sel[p["key"]] = ORIGINAL
    save_selections(scan_dir, style_key, sel)
    return sel


# ---------------------------------------------------------------- export

DEFAULT_PATTERN = "{nom}_{style}_{date}_{heure}"


def _style_slug(label):
    slug = re.sub(r"[^0-9A-Za-z]+", "_", label).strip("_").lower()
    return slug or "style"


def export_name(page, style_label, when=None, pattern=None):
    """Nom de fichier d'export selon le formalisme configurable.

    Jetons : {nom} {style} {date} {heure} — ex. page_001_toriyama_20260723_211530.
    """
    s = load_settings()
    pattern = pattern or s.get("export_pattern") or DEFAULT_PATTERN
    when = when or datetime.datetime.now()
    stem = os.path.splitext(page["name"])[0]
    try:
        return pattern.format(nom=stem, style=_style_slug(style_label),
                              date=when.strftime("%Y%m%d"),
                              heure=when.strftime("%H%M%S"))
    except (KeyError, IndexError, ValueError):
        return DEFAULT_PATTERN.format(nom=stem, style=_style_slug(style_label),
                                      date=when.strftime("%Y%m%d"),
                                      heure=when.strftime("%H%M%S"))


def _save_variant_as(src, dst_base, fmt, quality):
    if fmt in ("jpg", "jpeg"):
        with Image.open(src) as im:
            im.convert("RGB").save(dst_base + ".jpg", quality=quality)
        return dst_base + ".jpg"
    shutil.copy2(src, dst_base + ".png")
    return dst_base + ".png"


def export_all(scan_dir, style_key, style_label, pages,
               file_mode=False, dest_dir=None):
    """Exporte l'image gardée de chaque page.

    - mode dossier : arborescence reproduite dans le répertoire adjacent
      <parent>/<dossier>_stylized/<style>/ (original copié tel quel si gardé) ;
    - mode images : nom selon le formalisme {nom}_{style}_{date}_{heure},
      dans dest_dir (ou le dossier de l'image s'il n'est pas fourni) ;
      les pages dont l'original est gardé sont ignorées.
    Retourne {exported, originals, out_dir}.
    """
    s = load_settings()
    fmt = (s.get("export_format") or "png").lower()
    quality = int(s.get("export_quality") or 95)
    sel = load_selections(scan_dir, style_key)
    when = datetime.datetime.now()
    exported = originals = 0
    if file_mode:
        out_dir = dest_dir or os.path.dirname(pages[0]["path"])
        os.makedirs(out_dir, exist_ok=True)
        for p in pages:
            choice = effective_choice(scan_dir, style_key, p, sel)
            if choice == ORIGINAL:
                originals += 1
                continue
            src = variant_path(scan_dir, style_key, p, choice)
            base = os.path.join(dest_dir or os.path.dirname(p["path"]),
                                export_name(p, style_label, when))
            _save_variant_as(src, base, fmt, quality)
            exported += 1
        return {"exported": exported, "originals": originals,
                "out_dir": out_dir}
    out_dir = export_root(scan_dir, style_key)
    for p in pages:
        choice = effective_choice(scan_dir, style_key, p, sel)
        dst_dir = os.path.join(out_dir, os.path.dirname(p["rel"]))
        os.makedirs(dst_dir, exist_ok=True)
        if choice == ORIGINAL:
            shutil.copy2(p["path"], os.path.join(out_dir, p["rel"]))
            originals += 1
        else:
            src = variant_path(scan_dir, style_key, p, choice)
            _save_variant_as(src, os.path.join(
                out_dir, os.path.splitext(p["rel"])[0]), fmt, quality)
            exported += 1
    return {"exported": exported, "originals": originals, "out_dir": out_dir}


# ---------------------------------------------------------------- ComfyUI API

def _get_json(url_path, timeout=30):
    with urllib.request.urlopen(comfy_url() + url_path, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def comfy_alive():
    try:
        _get_json("/system_stats", timeout=5)
        return True
    except Exception:
        return False


def comfy_interrupt():
    try:
        req = urllib.request.Request(comfy_url() + "/interrupt", data=b"")
        urllib.request.urlopen(req, timeout=10).read()
    except Exception:
        pass


_model_cache = {}


def list_checkpoints():
    """Checkpoints disponibles côté ComfyUI (liste vide si injoignable)."""
    try:
        return [os.path.basename(c) for c in _get_json("/models/checkpoints")]
    except Exception:
        return []


def resolve_model(folder, basename):
    """Retrouve le nom exact (avec sous-dossier) d'un modèle pour ComfyUI."""
    if folder not in _model_cache:
        _model_cache[folder] = _get_json("/models/" + folder)
    for full in _model_cache[folder]:
        if os.path.basename(full).lower() == basename.lower():
            return full
    for full in _model_cache[folder]:
        if basename.lower() in full.lower():
            return full
    raise FileNotFoundError("Introuvable dans ComfyUI %s : %s" % (folder, basename))


def upload_image(pil_image, name):
    """Upload une image RGB dans l'input de ComfyUI (multipart)."""
    buf = io.BytesIO()
    pil_image.save(buf, format="PNG")
    data = buf.getvalue()
    boundary = uuid.uuid4().hex
    body = io.BytesIO()
    body.write(("--%s\r\n" % boundary).encode())
    body.write(('Content-Disposition: form-data; name="image"; filename="%s"\r\n' % name).encode())
    body.write(b"Content-Type: image/png\r\n\r\n")
    body.write(data)
    body.write(("\r\n--%s\r\n" % boundary).encode())
    body.write(b'Content-Disposition: form-data; name="overwrite"\r\n\r\ntrue')
    body.write(("\r\n--%s--\r\n" % boundary).encode())
    req = urllib.request.Request(
        comfy_url() + "/upload/image", data=body.getvalue(),
        headers={"Content-Type": "multipart/form-data; boundary=%s" % boundary})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))["name"]


def build_workflow(cfgset, image_name, seed, control_name=None):
    """img2img + ControlNet union au format API. LoRA optionnel.

    control_name : image de contrôle déjà uploadée (lineart) ; sinon Canny
    est calculé côté ComfyUI.
    """
    model_ref, clip_ref = ["1", 0], ["1", 1]
    wf = {
        "1": {"class_type": "CheckpointLoaderSimple",
              "inputs": {"ckpt_name": cfgset["checkpoint"]}},
        "5": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "7": {"class_type": "Canny",
              "inputs": {"image": ["5", 0], "low_threshold": 0.25,
                         "high_threshold": 0.6}},
        "8": {"class_type": "ControlNetLoader",
              "inputs": {"control_net_name": cfgset["controlnet"]}},
        "9": {"class_type": "SetUnionControlNetType",
              "inputs": {"control_net": ["8", 0],
                         "type": "canny/lineart/anime_lineart/mlsd"}},
        "11": {"class_type": "VAEEncode",
               "inputs": {"pixels": ["5", 0], "vae": ["1", 2]}},
    }
    if cfgset.get("lora"):
        wf["2"] = {"class_type": "LoraLoader",
                   "inputs": {"model": ["1", 0], "clip": ["1", 1],
                              "lora_name": cfgset["lora"],
                              "strength_model": cfgset["lora_strength"],
                              "strength_clip": cfgset["lora_strength"]}}
        model_ref, clip_ref = ["2", 0], ["2", 1]
    wf["3"] = {"class_type": "CLIPTextEncode",
               "inputs": {"clip": clip_ref, "text": cfgset["positive"]}}
    wf["4"] = {"class_type": "CLIPTextEncode",
               "inputs": {"clip": clip_ref, "text": cfgset["negative"]}}
    if control_name:
        wf["7"] = {"class_type": "LoadImage", "inputs": {"image": control_name}}
    wf["10"] = {"class_type": "ControlNetApplyAdvanced",
                "inputs": {"positive": ["3", 0], "negative": ["4", 0],
                           "control_net": ["9", 0], "image": ["7", 0],
                           "strength": cfgset["cnet_strength"],
                           "start_percent": 0.0,
                           "end_percent": cfgset["cnet_end"],
                           "vae": ["1", 2]}}
    wf["12"] = {"class_type": "KSampler",
                "inputs": {"model": model_ref, "positive": ["10", 0],
                           "negative": ["10", 1], "latent_image": ["11", 0],
                           "seed": seed, "steps": cfgset["steps"],
                           "cfg": cfgset["cfg"],
                           "sampler_name": cfgset.get("sampler", "euler_ancestral"),
                           "scheduler": cfgset.get("scheduler", "normal"),
                           "denoise": cfgset["denoise"]}}
    wf["13"] = {"class_type": "VAEDecode",
                "inputs": {"samples": ["12", 0], "vae": ["1", 2]}}
    wf["14"] = {"class_type": "SaveImage",
                "inputs": {"images": ["13", 0], "filename_prefix": "pmds/raw"}}
    return wf


def queue_and_wait(workflow, timeout=900):
    payload = json.dumps({"prompt": workflow,
                          "client_id": "pmds"}).encode("utf-8")
    req = urllib.request.Request(comfy_url() + "/prompt", data=payload,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        resp = json.loads(r.read().decode("utf-8"))
    if "prompt_id" not in resp:
        raise RuntimeError("ComfyUI a refusé le workflow: %s" % resp)
    pid = resp["prompt_id"]
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(1.5)
        hist = _get_json("/history/" + pid)
        if pid in hist:
            entry = hist[pid]
            status = entry.get("status", {})
            if status.get("status_str") == "error":
                msgs = [m for m in status.get("messages", [])
                        if m[0] == "execution_error"]
                raise RuntimeError("Erreur ComfyUI: %s" % json.dumps(msgs)[:2000])
            outputs = entry.get("outputs", {})
            if "14" in outputs and outputs["14"].get("images"):
                return outputs["14"]["images"][0]
    comfy_interrupt()
    raise TimeoutError("Génération trop longue (%ss) — vérifie que le GPU "
                       "n'est pas occupé par un autre programme" % timeout)


def fetch_output(imginfo):
    qs = urllib.parse.urlencode({"filename": imginfo["filename"],
                                 "subfolder": imginfo.get("subfolder", ""),
                                 "type": imginfo.get("type", "output")})
    with urllib.request.urlopen(comfy_url() + "/view?" + qs, timeout=120) as r:
        return Image.open(io.BytesIO(r.read())).convert("RGB")


# ---------------------------------------------------------------- traitement image

def gen_dims(w, h, long_side):
    """Dimensions de génération : long côté = long_side, multiples de 8."""
    scale = float(long_side) / max(w, h)
    gw = max(8, int(round(w * scale / 8.0)) * 8)
    gh = max(8, int(round(h * scale / 8.0)) * 8)
    return gw, gh


def make_lineart(rgb_image):
    """Carte de traits (lignes blanches sur fond noir) calculée localement."""
    import numpy as np
    g = rgb_image.convert("L")
    blur = g.filter(ImageFilter.GaussianBlur(2))
    a = np.asarray(g, dtype=np.int16)
    b = np.asarray(blur, dtype=np.int16)
    diff = np.clip((b - a) * 4, 0, 255).astype(np.uint8)
    return Image.fromarray(diff, "L").convert("RGB")


def _detect_text_blocks(g, white_thresh):
    """Masque des blocs de TEXTE : grappes de petites lettres sur fond clair.

    Rattrape les bulles ouvertes ou fusionnées avec les gouttières (comics),
    que la détection de forme ne peut pas voir. Les onomatopées géantes et
    les hachures sont écartées (taille/forme des lettres), les visages aussi
    (pas de grappe de petites taches régulières)."""
    import cv2
    import numpy as np
    H, W = g.shape
    page = float(H * W)
    ink = (g < 110).astype(np.uint8)
    n, labels, stats, cents = cv2.connectedComponentsWithStats(ink, connectivity=8)
    hmin = max(4.0, H * 0.004)
    hmax = H * 0.025
    letters = []
    letter_mask = np.zeros((H, W), np.uint8)
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if not (hmin <= h <= hmax):
            continue
        if not (hmin * 0.3 <= w <= H * 0.06):
            continue
        if area < 6 or not (0.12 <= w / float(h) <= 6.0):
            continue
        if area / float(w * h) < 0.22:
            continue  # trop creux : trait diagonal (hachure), pas une lettre
        letters.append(i)
        letter_mask[y:y + h, x:x + w] |= (labels[y:y + h, x:x + w]
                                          == i).astype(np.uint8)
    if len(letters) < 5:
        return None
    other_ink = (ink == 1) & (letter_mask == 0)  # traits de dessin, aplats
    # relie les lettres proches en blocs (mots, lignes)
    k = max(3, int(H * 0.008))
    clusters = cv2.dilate(letter_mask, np.ones((k, 2 * k), np.uint8))
    n2, lab2, stats2, _ = cv2.connectedComponentsWithStats(clusters,
                                                           connectivity=8)
    counts, heights = {}, {}
    for i in letters:
        cx, cy = int(cents[i][0]), int(cents[i][1])
        cl = lab2[min(cy, H - 1), min(cx, W - 1)]
        counts[cl] = counts.get(cl, 0) + 1
        heights.setdefault(cl, []).append(stats[i][3])
    mask = np.zeros((H, W), np.uint8)
    found = False
    pad = k
    for cl in range(1, n2):
        if counts.get(cl, 0) < 4:
            continue  # trop peu de « lettres » : dessin, pas du texte
        x, y, w, h, _a = stats2[cl]
        if w * h > page * 0.12:
            continue
        # les lettres d'un texte ont une hauteur homogène (pas les traits)
        hs = sorted(heights[cl])
        med = hs[len(hs) // 2]
        uniform = sum(1 for v in hs if 0.45 * med <= v <= 2.0 * med)
        if uniform < 0.6 * len(hs):
            continue
        # zone interne (sans le contour du ballon) pour juger le contenu
        ix0, iy0 = max(0, x + 2), max(0, y + 2)
        ix1, iy1 = min(W, x + w - 2), min(H, y + h - 2)
        if ix1 <= ix0 or iy1 <= iy0:
            continue
        region = g[iy0:iy1, ix0:ix1]
        # le fond du bloc doit être clair (intérieur de bulle / cartouche)
        light = float((region >= white_thresh - 20).mean())
        if light < 0.45:
            continue
        # un bloc de texte ne contient presque QUE des lettres : s'il y a
        # beaucoup d'encre « étrangère » (traits, aplats), c'est du dessin
        oink = float(other_ink[iy0:iy1, ix0:ix1].sum())
        link = float(letter_mask[iy0:iy1, ix0:ix1].sum())
        if oink > 0.45 * (oink + link + 1):
            continue
        x0, y0 = max(0, x - pad), max(0, y - pad)
        x1, y1 = min(W, x + w + pad), min(H, y + h + pad)
        mask[y0:y1, x0:x1] = 1
        found = True
    return mask if found else None


def detect_bubble_mask(rgb_image, white_thresh=225):
    """Masque L des phylactères (255 = préserver l'original, bords adoucis).

    Heuristique : composantes connexes quasi-blanches, de taille raisonnable,
    de forme assez pleine, contenant un peu d'encre (le texte). Les fonds de
    case et gouttières sont écartés (trop grands, trop irréguliers, ou trop
    remplis de dessin). Retourne None si rien n'est détecté.
    """
    import cv2
    import numpy as np
    from scipy import ndimage
    g = np.asarray(rgb_image.convert("L"))
    H, W = g.shape
    page = float(H * W)
    white = (g >= white_thresh).astype(np.uint8)
    # érosion : casse les ponts fins (contour de bulle interrompu, trames)
    # pour que l'intérieur d'une bulle ne fusionne pas avec le fond de case
    white = cv2.erode(white, np.ones((3, 3), np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(white, connectivity=4)
    mask = np.zeros((H, W), np.uint8)
    found = False
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if area < page * 0.0006 or area > page * 0.08:
            continue  # trop petit (bruit) ou trop grand (fond de case)
        if w > W * 0.8 or h > H * 0.5:
            continue  # trop étendu pour une bulle (case, gouttière, marge)
        comp = (labels[y:y + h, x:x + w] == i).astype(np.uint8)
        filled = ndimage.binary_fill_holes(comp).astype(np.uint8)
        farea = float(filled.sum())
        if farea / (w * h) < 0.35:
            continue  # forme trop creuse/irrégulière pour une bulle
        ink = (farea - area) / farea  # proportion d'encre (texte) à l'intérieur
        if not (0.005 <= ink <= 0.55):
            continue
        # ---- anti faux-positifs (visages) : une bulle est convexe et lisse
        contours, _h = cv2.findContours(filled, cv2.RETR_EXTERNAL,
                                        cv2.CHAIN_APPROX_NONE)
        if not contours:
            continue
        cnt = max(contours, key=cv2.contourArea)
        c_area = cv2.contourArea(cnt)
        hull_area = cv2.contourArea(cv2.convexHull(cnt))
        if hull_area <= 0 or c_area / hull_area < 0.9:
            continue  # concave (visage mordu par les cheveux, forme libre)
        perim = cv2.arcLength(cnt, True)
        if c_area <= 0 or (perim * perim) / (4 * 3.14159 * c_area) > 1.9:
            continue  # contour trop tourmenté pour un phylactère
        # ---- le contenu doit ressembler à du texte : plusieurs petites
        # taches d'encre, de hauteur homogène et petite devant la bulle
        # mesuré sur pages réelles : bulles solidité >= 0.97 / circ ~1.3 ;
        # visages et fonds de case solidité <= 0.88 / circ >= 3.8 — la forme
        # seule discrimine, le contenu ne sert qu'à exiger un peu de texte
        ink_mask = (filled == 1) & (comp == 0)
        lab, n_ink = ndimage.label(ink_mask)
        if n_ink < 2:
            continue
        sl = ndimage.find_objects(lab)
        areas = ndimage.sum(ink_mask, lab, range(1, n_ink + 1))
        heights = [s[0].stop - s[0].start
                   for idx, s in enumerate(sl)
                   if s is not None and float(areas[idx]) >= 4]
        if len(heights) < 2:
            continue
        hmed = sorted(heights)[len(heights) // 2]
        if hmed > 0.4 * h:
            continue  # traits trop grands : dessin, pas des lettres
        mask[y:y + h, x:x + w] |= filled
        found = True
    # complément : blocs de texte (bulles ouvertes/fusionnées, cartouches)
    text = _detect_text_blocks(g, white_thresh)
    if text is not None:
        mask |= text
        found = True
    if not found:
        return None
    mask = cv2.dilate(mask, np.ones((7, 7), np.uint8)) * 255
    mask = cv2.GaussianBlur(mask, (0, 0), 3)
    return Image.fromarray(mask, "L")


# ------------------------------------------------------------- bulles (IA)

BUBBLE_MODEL_URL = ("https://github.com/zyddnys/manga-image-translator/"
                    "releases/download/beta-0.3/comictextdetector.pt.onnx")
_bubble_sess = None


def _load_bubble_detector():
    """Session onnx du détecteur comic-text-detector (téléchargé au
    premier usage dans .bubbles/, ~90 Mo, exécuté sur CPU)."""
    global _bubble_sess
    if _bubble_sess is None:
        import onnxruntime as ort
        d = os.path.join(APP_DIR, ".bubbles")
        model = os.path.join(d, "comictextdetector.pt.onnx")
        if not os.path.exists(model):
            os.makedirs(d, exist_ok=True)
            urllib.request.urlretrieve(BUBBLE_MODEL_URL, model + ".part")
            os.replace(model + ".part", model)
        _bubble_sess = ort.InferenceSession(
            model, providers=["CPUExecutionProvider"])
    return _bubble_sess


def detect_bubble_mask_ai(rgb_image, white_thresh=225):
    """Masque L des phylactères par le modèle comic-text-detector (~0,5 s CPU).

    Le modèle localise les blocs de DIALOGUE (boîtes YOLO) en ignorant
    onomatopées, texte de décor et dessin ; chaque boîte est ensuite étendue
    au ballon blanc englobant quand il existe (contour de bulle au pixel
    près), sinon rectangle légèrement élargi (bulles ouvertes, cartouches).
    Retourne None si aucun texte n'est détecté.
    """
    import cv2
    import numpy as np
    from scipy import ndimage
    sess = _load_bubble_detector()
    W, H = rgb_image.size
    S = 1024  # taille d'entrée fixe du modèle
    scale = S / float(max(W, H))
    nw, nh = max(1, int(round(W * scale))), max(1, int(round(H * scale)))
    im = rgb_image.resize((nw, nh), Image.BILINEAR)
    canvas = Image.new("RGB", (S, S), (114, 114, 114))
    canvas.paste(im, (0, 0))
    x = np.asarray(canvas, dtype=np.float32).transpose(2, 0, 1)[None] / 255.0
    blk = sess.run(["blk"], {sess.get_inputs()[0].name: x})[0][0]
    # décodage YOLO : x,y,w,h (px du canevas) + objectness + 2 scores de classe
    p = blk[blk[:, 4] >= 0.3]
    if not p.size:
        return None
    conf = p[:, 4] * p[:, 5:7].max(axis=1)
    p, conf = p[conf >= 0.4], conf[conf >= 0.4]
    if not p.size:
        return None
    xy, wh = p[:, :2] / scale, p[:, 2:4] / scale
    rects = np.concatenate([xy - wh / 2, wh], axis=1).tolist()
    idx = cv2.dnn.NMSBoxes(rects, conf.tolist(), 0.4, 0.35)
    if len(idx) == 0:
        return None
    g = np.asarray(rgb_image.convert("L"))
    page = float(H * W)
    mask = np.zeros((H, W), np.uint8)
    pad = max(3, int(H * 0.006))
    margin = max(24, int(0.05 * max(W, H)))  # zone de recherche du ballon
    for i in np.asarray(idx).flatten():
        bx, by, bw, bh = rects[i]
        x0, y0 = max(0, int(bx)), max(0, int(by))
        x1, y1 = min(W, int(bx + bw)), min(H, int(by + bh))
        if x1 <= x0 or y1 <= y0:
            continue
        # rectangle élargi : garantit le texte même sans ballon fermé
        mask[max(0, y0 - pad):min(H, y1 + pad),
             max(0, x0 - pad):min(W, x1 + pad)] = 1
        # expansion au ballon blanc englobant la boîte de texte
        cx0, cy0 = max(0, x0 - margin), max(0, y0 - margin)
        cx1, cy1 = min(W, x1 + margin), min(H, y1 + margin)
        crop = g[cy0:cy1, cx0:cx1]
        white = (crop >= white_thresh).astype(np.uint8)
        white = cv2.erode(white, np.ones((3, 3), np.uint8))
        n, labels, stats, _ = cv2.connectedComponentsWithStats(
            white, connectivity=4)
        if n < 2:
            continue
        # composante blanche dominante SOUS la boîte (le fond de la bulle)
        sub = labels[y0 - cy0:y1 - cy0, x0 - cx0:x1 - cx0]
        counts = np.bincount(sub[sub > 0], minlength=n)
        if not counts.size or counts.max() == 0:
            continue
        lab = int(counts.argmax())
        lx, ly, lw, lh, larea = stats[lab]
        if larea > page * 0.10:
            continue  # fond de case, pas un ballon
        if (lx <= 0 or ly <= 0 or lx + lw >= crop.shape[1]
                or ly + lh >= crop.shape[0]):
            continue  # déborde de la zone de recherche : fond, pas un ballon
        comp = ndimage.binary_fill_holes(labels == lab).astype(np.uint8)
        mask[cy0:cy1, cx0:cx1] |= comp
    if not mask.any():
        return None
    mask = cv2.dilate(mask, np.ones((7, 7), np.uint8)) * 255
    mask = cv2.GaussianBlur(mask, (0, 0), 3)
    return Image.fromarray(mask, "L")


def detect_bubbles(rgb_image, white_thresh=225, method="shape"):
    """Masque des phylactères selon la méthode choisie dans les Options.

    - "shape" : heuristique formes + blocs de texte (rapide, sans modèle)
    - "ai"    : modèle comic-text-detector (repli sur l'heuristique si le
                modèle est indisponible : pas de réseau, onnxruntime absent…)
    - "both"  : union des deux masques (filet de sécurité maximal)
    """
    if method in ("ai", "both"):
        try:
            m_ai = detect_bubble_mask_ai(rgb_image, white_thresh)
        except Exception:
            m_ai = None
            if method == "ai":  # modèle indisponible : repli heuristique
                return detect_bubble_mask(rgb_image, white_thresh)
        if method == "ai":
            return m_ai
        m_h = detect_bubble_mask(rgb_image, white_thresh)
        if m_ai is None:
            return m_h
        if m_h is None:
            return m_ai
        import numpy as np
        return Image.fromarray(
            np.maximum(np.asarray(m_ai), np.asarray(m_h)), "L")
    return detect_bubble_mask(rgb_image, white_thresh)


# ---------------------------------------------------------------- tagger (sexe)

TAGGER_URLS = (
    "https://huggingface.co/SmilingWolf/wd-vit-tagger-v3/resolve/main/model.onnx",
    "https://huggingface.co/SmilingWolf/wd-vit-tagger-v3/resolve/main/selected_tags.csv",
)
_tagger_cache = None


def _load_tagger():
    """(session onnx, [(nom, catégorie)]) — modèle local dans .tagger/,
    téléchargé au premier usage."""
    global _tagger_cache
    if _tagger_cache is not None:
        return _tagger_cache
    import csv
    import onnxruntime as ort
    for d in (os.path.join(APP_DIR, ".tagger"),):
        model = os.path.join(d, "model.onnx")
        tags_csv = os.path.join(d, "selected_tags.csv")
        if os.path.exists(model) and os.path.exists(tags_csv):
            break
    else:
        d = os.path.join(APP_DIR, ".tagger")
        os.makedirs(d, exist_ok=True)
        model = os.path.join(d, "model.onnx")
        tags_csv = os.path.join(d, "selected_tags.csv")
        for url, dest in zip(TAGGER_URLS, (model, tags_csv)):
            if not os.path.exists(dest):
                urllib.request.urlretrieve(url, dest + ".part")
                os.replace(dest + ".part", dest)
    sess = ort.InferenceSession(model, providers=["CPUExecutionProvider"])
    names = []
    with open(tags_csv, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            names.append(row["name"].replace("_", " "))
    _tagger_cache = (sess, names)
    return _tagger_cache


def detect_sex_tags(rgb_image):
    """(tags positifs, tags négatifs) si UN SEUL personnage est détecté
    avec un sexe net — ('', '') sinon (plusieurs persos ou doute)."""
    import numpy as np
    try:
        sess, names = _load_tagger()
    except Exception:
        return "", ""
    size = sess.get_inputs()[0].shape[1]
    im = rgb_image.resize((size, size), Image.BICUBIC)
    arr = np.asarray(im, dtype=np.float32)[:, :, ::-1][None, ...]  # RGB->BGR
    probs = sess.run(None, {sess.get_inputs()[0].name: arr})[0][0]
    p = {n: float(v) for n, v in zip(names, probs)}
    boy, girl = p.get("1boy", 0), p.get("1girl", 0)
    multi = max(p.get(t, 0) for t in ("multiple boys", "multiple girls",
                                      "2boys", "2girls", "3boys", "3girls",
                                      "6+boys", "6+girls"))
    if multi >= 0.35 or (boy >= 0.35 and girl >= 0.35):
        return "", ""  # plusieurs personnages : on ne force rien
    if boy >= 0.5 and girl < 0.35:
        return "1boy, male focus", "1girl"
    if girl >= 0.5 and boy < 0.35:
        return "1girl", "1boy, male focus"
    return "", ""


# ---------------------------------------------------------------- pipeline

def build_cfgset(styles_cfg, style_key, denoise=None):
    d = styles_cfg["defaults"]
    s = styles_cfg["styles"][style_key]
    base = d[s["base"]]
    app_s = load_settings()
    checkpoint = s.get("checkpoint") \
        or app_s.get("checkpoint_" + s["base"]) or base["checkpoint"]
    cfgset = {
        "checkpoint": resolve_model("checkpoints", checkpoint),
        "lora": resolve_model("loras", s["lora"]) if s.get("lora") else None,
        "lora_strength": s.get("lora_strength", 1.0),
        "positive": base["prefix"] + s["prompt"] + d.get("suffix", ""),
        "negative": base["negative"] + s.get("negative_extra", ""),
        "cfg": s.get("cfg", app_s.get("cfg_" + s["base"]) or base["cfg"]),
        "steps": int(s.get("steps", app_s.get("gen_steps") or d["steps"])),
        "denoise": s.get("denoise", app_s.get("gen_denoise") or d["denoise"]),
        "cnet_strength": s.get("cnet_strength",
                               app_s.get("gen_cnet_strength") or d["cnet_strength"]),
        "cnet_end": s.get("cnet_end", d["cnet_end"]),
        "long_side": int(app_s.get("long_side") or d["long_side"]),
        "sampler": app_s.get("gen_sampler") or "euler_ancestral",
        "scheduler": app_s.get("gen_scheduler") or "normal",
        "cnet_type": app_s.get("cnet_type") or "canny",
        "controlnet": resolve_model("controlnet", CONTROLNET_NAME_HINT),
    }
    if denoise is not None:
        cfgset["denoise"] = max(0.0, min(1.0, float(denoise)))
    return cfgset


# correspondances vers les noms A1111 / Forge
A1111_SAMPLERS = {"euler": "Euler", "euler_ancestral": "Euler a",
                  "dpmpp_2m": "DPM++ 2M", "dpmpp_2m_sde": "DPM++ 2M SDE",
                  "dpmpp_3m_sde": "DPM++ 3M SDE", "dpmpp_sde": "DPM++ SDE",
                  "ddim": "DDIM", "uni_pc": "UniPC"}
A1111_SCHEDULERS = {"normal": "Automatic", "karras": "Karras",
                    "sgm_uniform": "SGM Uniform",
                    "exponential": "Exponential", "beta": "Beta",
                    "simple": "Simple"}


def forge_parameters(cfgset, seed, gw, gh):
    """Chaîne « parameters » au format A1111 : lisible par Forge / SD WebUI
    (onglet PNG Info -> Send to img2img) et par Civitai."""
    lora_tag = ""
    if cfgset.get("lora"):
        stem = os.path.splitext(os.path.basename(cfgset["lora"]))[0]
        lora_tag = " <lora:%s:%g>" % (stem, cfgset["lora_strength"])
    model = os.path.splitext(os.path.basename(cfgset["checkpoint"]))[0]
    return (
        "%s%s\nNegative prompt: %s\n"
        "Steps: %d, Sampler: %s, Schedule type: %s, CFG scale: %g, "
        "Seed: %d, Size: %dx%d, Model: %s, Denoising strength: %g"
        % (cfgset["positive"], lora_tag, cfgset["negative"],
           cfgset["steps"],
           A1111_SAMPLERS.get(cfgset.get("sampler", ""), "Euler a"),
           A1111_SCHEDULERS.get(cfgset.get("scheduler", ""), "Automatic"),
           cfgset["cfg"], seed, gw, gh, model, cfgset["denoise"]))


def apply_boost(cfgset):
    """Réglages « style renforcé » : plus de liberté au LoRA, ControlNet allégé."""
    cfgset["denoise"] = max(cfgset["denoise"], 0.75)
    cfgset["cnet_strength"] = min(cfgset["cnet_strength"], 0.35)
    cfgset["cnet_end"] = min(cfgset["cnet_end"], 0.6)
    if cfgset.get("lora"):
        cfgset["lora_strength"] = min(1.2, cfgset["lora_strength"] + 0.1)
    return cfgset


def stylize_page(styles_cfg, style_key, scan_dir, page, seed=None,
                 denoise=None, preserve_bubbles=True, boost=False,
                 pos_extra=None, neg_extra=None, lora_strength=None,
                 cnet_strength=None, cnet_end=None, checkpoint=None,
                 lora_enabled=True, colorize=False):
    """Génère une nouvelle variante stylisée d'une page.

    Retourne (chemin de la variante, seed).
    """
    if seed is None:
        seed = random.randint(0, 2 ** 31 - 1)
    app_s = load_settings()
    cfgset = build_cfgset(styles_cfg, style_key, denoise=denoise)
    if checkpoint:
        cfgset["checkpoint"] = resolve_model("checkpoints", checkpoint)
    if not lora_enabled:
        cfgset["lora"] = None
    if lora_strength is not None and cfgset.get("lora"):
        cfgset["lora_strength"] = float(lora_strength)
    if cnet_strength is not None:
        cfgset["cnet_strength"] = float(cnet_strength)
    if cnet_end is not None:
        cfgset["cnet_end"] = float(cnet_end)
    if boost:
        apply_boost(cfgset)
    if colorize:
        cfgset["positive"] = "colored, anime coloring, vibrant colors, " \
            + cfgset["positive"]
        cfgset["negative"] += ", monochrome, greyscale"
    if pos_extra and pos_extra.strip():
        cfgset["positive"] = pos_extra.strip() + ", " + cfgset["positive"]
    if neg_extra and neg_extra.strip():
        cfgset["negative"] += ", " + neg_extra.strip()
    with Image.open(page["path"]) as im:
        orig = im.convert("RGB")
    W, H = orig.size
    gw, gh = gen_dims(W, H, cfgset["long_side"])
    small = orig.resize((gw, gh), Image.LANCZOS)
    if app_s.get("auto_sex", True):
        sex_pos, sex_neg = detect_sex_tags(small)
        if sex_pos:
            cfgset["positive"] = sex_pos + ", " + cfgset["positive"]
            cfgset["negative"] += ", " + sex_neg
    tag = uuid.uuid4().hex[:8]
    up_name = upload_image(small, "pmds_%s.png" % tag)
    control_name = None
    if cfgset["cnet_type"] == "lineart":
        control_name = upload_image(make_lineart(small), "pmds_ctrl_%s.png" % tag)
    wf = build_workflow(cfgset, up_name, seed, control_name)
    timeout = int(app_s.get("gen_timeout") or 900)
    out = fetch_output(queue_and_wait(wf, timeout=timeout))
    if out.size != (W, H):
        out = out.resize((W, H), Image.LANCZOS)
    if preserve_bubbles:
        m = detect_bubbles(orig, int(app_s.get("bubble_thresh") or 225),
                           app_s.get("bubble_method") or "shape")
        if m is not None:
            out = Image.composite(orig, out, m)
    vdir = variants_dir(scan_dir, style_key, page)
    os.makedirs(vdir, exist_ok=True)
    out_path = os.path.join(vdir, "seed_%010d_d%.2f.png" % (seed, cfgset["denoise"]))
    # métadonnées standard embarquées dans le PNG :
    #  - « parameters » : format A1111, relisible dans Forge (PNG Info)
    #  - « prompt » : graphe ComfyUI (JSON API) de cette génération
    pnginfo = PngInfo()
    pnginfo.add_text("parameters", forge_parameters(cfgset, seed, gw, gh))
    pnginfo.add_text("prompt", json.dumps(wf))
    out.save(out_path, pnginfo=pnginfo)
    # aperçu JPEG instantané pour l'interface
    prev = out.copy()
    prev.thumbnail((1600, 1600), Image.LANCZOS)
    prev.save(out_path + ".jpg", quality=87)
    # métadonnées de la variante (affichées à la consultation)
    meta_path = os.path.join(vdir, "_meta.json")
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
    except Exception:
        meta = {}
    meta[os.path.basename(out_path)] = {
        "seed": seed, "denoise": cfgset["denoise"],
        "cnet": cfgset["cnet_strength"], "cnet_end": cfgset["cnet_end"],
        "checkpoint": os.path.basename(cfgset["checkpoint"]),
        "lora_strength": cfgset["lora_strength"] if cfgset.get("lora") else None,
        "colorize": bool(colorize)}
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=1, ensure_ascii=False)
    return out_path, seed
