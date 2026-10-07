# -*- coding: utf-8 -*-
"""Thèmes ComicRestyler — palettes inspirées d'applications populaires
et d'éditeurs de texte célèbres.

Chaque thème est une ThemeDefinition ttkbootstrap enregistrée au démarrage.
Règle de contraste : « secondary » doit rester lisible sur « bg » (boutons
et textes secondaires), jamais quasi identique au fond.
"""
from ttkbootstrap.style import ThemeDefinition

# nom -> (libellé affiché, mode, couleurs)
APP_THEMES = {
    # ---------------- macOS (design par défaut : accent unique, boutons
    # de barre neutres — « info » est volontairement un gris discret)
    "macos_dark": ("macOS Sombre", "dark", {
        "primary": "#0A84FF", "secondary": "#8E8E93", "success": "#30D158",
        "info": "#98989D", "warning": "#FF9F0A", "danger": "#FF453A",
        "light": "#AEAEB2", "dark": "#141416", "bg": "#212124",
        "fg": "#F5F5F7", "selectbg": "#0A84FF", "selectfg": "#FFFFFF",
        "border": "#3A3A3C", "inputfg": "#F5F5F7", "inputbg": "#323236",
        "active": "#2C2C2E"}),
    "macos_light": ("macOS Clair", "light", {
        "primary": "#007AFF", "secondary": "#8E8E93", "success": "#34C759",
        "info": "#6E6E73", "warning": "#FF9F0A", "danger": "#FF3B30",
        "light": "#E5E5EA", "dark": "#1D1D1F", "bg": "#F5F5F7",
        "fg": "#1D1D1F", "selectbg": "#007AFF", "selectfg": "#FFFFFF",
        "border": "#C6C6CB", "inputfg": "#1D1D1F", "inputbg": "#FFFFFF",
        "active": "#EBEBF0"}),
    # ---------------- apps populaires
    "discord": ("Discord", "dark", {
        "primary": "#5865F2", "secondary": "#6D7178", "success": "#23A55A",
        "info": "#00A8FC", "warning": "#F0B232", "danger": "#F23F43",
        "light": "#B5BAC1", "dark": "#1E1F22", "bg": "#313338",
        "fg": "#F2F3F5", "selectbg": "#404249", "selectfg": "#FFFFFF",
        "border": "#26272B", "inputfg": "#DBDEE1", "inputbg": "#383A40",
        "active": "#2B2D31"}),
    "spotify": ("Spotify", "dark", {
        "primary": "#1DB954", "secondary": "#6A6A6A", "success": "#1ED760",
        "info": "#509BF5", "warning": "#FFA42B", "danger": "#F15E6C",
        "light": "#B3B3B3", "dark": "#000000", "bg": "#121212",
        "fg": "#FFFFFF", "selectbg": "#333333", "selectfg": "#FFFFFF",
        "border": "#333333", "inputfg": "#FFFFFF", "inputbg": "#242424",
        "active": "#1A1A1A"}),
    "vscode": ("VS Code Dark+", "dark", {
        "primary": "#007ACC", "secondary": "#6E6E6E", "success": "#89D185",
        "info": "#75BEFF", "warning": "#CCA700", "danger": "#F48771",
        "light": "#C8C8C8", "dark": "#252526", "bg": "#1E1E1E",
        "fg": "#D4D4D4", "selectbg": "#264F78", "selectfg": "#FFFFFF",
        "border": "#3C3C3C", "inputfg": "#CCCCCC", "inputbg": "#3C3C3C",
        "active": "#2A2D2E"}),
    "netflix": ("Netflix", "dark", {
        "primary": "#E50914", "secondary": "#6D6D6D", "success": "#46D369",
        "info": "#54B9C5", "warning": "#E6B209", "danger": "#B9090B",
        "light": "#B3B3B3", "dark": "#000000", "bg": "#141414",
        "fg": "#FFFFFF", "selectbg": "#404040", "selectfg": "#FFFFFF",
        "border": "#404040", "inputfg": "#FFFFFF", "inputbg": "#333333",
        "active": "#1F1F1F"}),
    "whatsapp": ("WhatsApp", "dark", {
        "primary": "#00A884", "secondary": "#667781", "success": "#25D366",
        "info": "#53BDEB", "warning": "#FFB02E", "danger": "#F15C6D",
        "light": "#8696A0", "dark": "#0B141A", "bg": "#111B21",
        "fg": "#E9EDEF", "selectbg": "#2A3942", "selectfg": "#E9EDEF",
        "border": "#2F3B43", "inputfg": "#E9EDEF", "inputbg": "#202C33",
        "active": "#182229"}),
    "youtube": ("YouTube", "dark", {
        "primary": "#FF0000", "secondary": "#717171", "success": "#2BA640",
        "info": "#3EA6FF", "warning": "#FFC107", "danger": "#CC0000",
        "light": "#AAAAAA", "dark": "#000000", "bg": "#0F0F0F",
        "fg": "#F1F1F1", "selectbg": "#3F3F3F", "selectfg": "#FFFFFF",
        "border": "#3F3F3F", "inputfg": "#F1F1F1", "inputbg": "#222222",
        "active": "#272727"}),
    "steam": ("Steam", "dark", {
        "primary": "#66C0F4", "secondary": "#5B7C99", "success": "#A4D007",
        "info": "#66C0F4", "warning": "#C1B15F", "danger": "#D94126",
        "light": "#C7D5E0", "dark": "#0E141B", "bg": "#171A21",
        "fg": "#C7D5E0", "selectbg": "#2A475E", "selectfg": "#FFFFFF",
        "border": "#2A475E", "inputfg": "#E5E5E5", "inputbg": "#316282",
        "active": "#1B2838"}),
    "x_twitter": ("X (Twitter)", "dark", {
        "primary": "#1D9BF0", "secondary": "#536471", "success": "#00BA7C",
        "info": "#1D9BF0", "warning": "#FFD400", "danger": "#F4212E",
        "light": "#8B98A5", "dark": "#0A0A0A", "bg": "#000000",
        "fg": "#E7E9EA", "selectbg": "#333639", "selectfg": "#FFFFFF",
        "border": "#2F3336", "inputfg": "#E7E9EA", "inputbg": "#202327",
        "active": "#16181C"}),
    "slack": ("Slack", "light", {
        "primary": "#4A154B", "secondary": "#616061", "success": "#007A5A",
        "info": "#1264A3", "warning": "#B8860B", "danger": "#E01E5A",
        "light": "#E8E8E8", "dark": "#1D1C1D", "bg": "#FFFFFF",
        "fg": "#1D1C1D", "selectbg": "#1264A3", "selectfg": "#FFFFFF",
        "border": "#BBBBBB", "inputfg": "#1D1C1D", "inputbg": "#F8F8F8",
        "active": "#F0F0F0"}),
    "notion": ("Notion", "light", {
        "primary": "#2383E2", "secondary": "#787774", "success": "#0F7B6C",
        "info": "#2383E2", "warning": "#C4700D", "danger": "#E03E3E",
        "light": "#EBEAEA", "dark": "#37352F", "bg": "#FFFFFF",
        "fg": "#37352F", "selectbg": "#D3E5EF", "selectfg": "#37352F",
        "border": "#C9C9C7", "inputfg": "#37352F", "inputbg": "#F7F6F3",
        "active": "#EFEFEF"}),
    # ---------------- éditeurs de texte
    "monokai": ("Monokai (Sublime)", "dark", {
        "primary": "#66D9EF", "secondary": "#90917E", "success": "#A6E22E",
        "info": "#66D9EF", "warning": "#FD971F", "danger": "#F92672",
        "light": "#CFCFC2", "dark": "#1E1F1C", "bg": "#272822",
        "fg": "#F8F8F2", "selectbg": "#49483E", "selectfg": "#F8F8F2",
        "border": "#49483E", "inputfg": "#F8F8F2", "inputbg": "#3E3D32",
        "active": "#34352F"}),
    "dracula": ("Dracula", "dark", {
        "primary": "#BD93F9", "secondary": "#6272A4", "success": "#50FA7B",
        "info": "#8BE9FD", "warning": "#F1FA8C", "danger": "#FF5555",
        "light": "#F8F8F2", "dark": "#21222C", "bg": "#282A36",
        "fg": "#F8F8F2", "selectbg": "#44475A", "selectfg": "#F8F8F2",
        "border": "#44475A", "inputfg": "#F8F8F2", "inputbg": "#343746",
        "active": "#21222C"}),
    "one_dark": ("One Dark (Atom)", "dark", {
        "primary": "#61AFEF", "secondary": "#7F848E", "success": "#98C379",
        "info": "#56B6C2", "warning": "#E5C07B", "danger": "#E06C75",
        "light": "#ABB2BF", "dark": "#21252B", "bg": "#282C34",
        "fg": "#ABB2BF", "selectbg": "#3E4451", "selectfg": "#FFFFFF",
        "border": "#3E4451", "inputfg": "#ABB2BF", "inputbg": "#21252B",
        "active": "#2C313A"}),
    "nord": ("Nord", "dark", {
        "primary": "#88C0D0", "secondary": "#6C7893", "success": "#A3BE8C",
        "info": "#81A1C1", "warning": "#EBCB8B", "danger": "#BF616A",
        "light": "#D8DEE9", "dark": "#232831", "bg": "#2E3440",
        "fg": "#ECEFF4", "selectbg": "#434C5E", "selectfg": "#ECEFF4",
        "border": "#434C5E", "inputfg": "#ECEFF4", "inputbg": "#3B4252",
        "active": "#353C4A"}),
    "gruvbox": ("Gruvbox", "dark", {
        "primary": "#FE8019", "secondary": "#928374", "success": "#B8BB26",
        "info": "#83A598", "warning": "#FABD2F", "danger": "#FB4934",
        "light": "#D5C4A1", "dark": "#1D2021", "bg": "#282828",
        "fg": "#EBDBB2", "selectbg": "#3C3836", "selectfg": "#EBDBB2",
        "border": "#504945", "inputfg": "#EBDBB2", "inputbg": "#3C3836",
        "active": "#32302F"}),
    "tokyo_night": ("Tokyo Night", "dark", {
        "primary": "#7AA2F7", "secondary": "#787FA6", "success": "#9ECE6A",
        "info": "#7DCFFF", "warning": "#E0AF68", "danger": "#F7768E",
        "light": "#A9B1D6", "dark": "#16161E", "bg": "#1A1B26",
        "fg": "#C0CAF5", "selectbg": "#283457", "selectfg": "#C0CAF5",
        "border": "#3B4261", "inputfg": "#C0CAF5", "inputbg": "#1F2335",
        "active": "#1F2335"}),
    "solarized_dark": ("Solarized Dark", "dark", {
        "primary": "#268BD2", "secondary": "#7A929A", "success": "#859900",
        "info": "#2AA198", "warning": "#B58900", "danger": "#DC322F",
        "light": "#93A1A1", "dark": "#073642", "bg": "#002B36",
        "fg": "#B4C2C2", "selectbg": "#073642", "selectfg": "#EEE8D5",
        "border": "#2C5761", "inputfg": "#B4C2C2", "inputbg": "#073642",
        "active": "#08404F"}),
    "solarized_light": ("Solarized Light", "light", {
        "primary": "#268BD2", "secondary": "#7A8A8F", "success": "#859900",
        "info": "#2AA198", "warning": "#B58900", "danger": "#DC322F",
        "light": "#EEE8D5", "dark": "#073642", "bg": "#FDF6E3",
        "fg": "#586E75", "selectbg": "#EEE8D5", "selectfg": "#073642",
        "border": "#CDC5B0", "inputfg": "#586E75", "inputbg": "#EEE8D5",
        "active": "#F3ECD9"}),
    "github_light": ("GitHub Light", "light", {
        "primary": "#0969DA", "secondary": "#6E7781", "success": "#1A7F37",
        "info": "#218BFF", "warning": "#9A6700", "danger": "#CF222E",
        "light": "#F6F8FA", "dark": "#24292F", "bg": "#FFFFFF",
        "fg": "#24292F", "selectbg": "#DDF4FF", "selectfg": "#24292F",
        "border": "#B0B7BF", "inputfg": "#24292F", "inputbg": "#F6F8FA",
        "active": "#F3F4F6"}),
}


def register_all(style):
    """Enregistre tous les thèmes sur un ttkbootstrap.Style."""
    for name, (_label, mode, colors) in APP_THEMES.items():
        try:
            style.register_theme(ThemeDefinition(name, colors, mode))
        except Exception:
            pass  # déjà enregistré


def labels():
    """[(nom interne, libellé)] dans l'ordre d'affichage."""
    return [(name, spec[0]) for name, spec in APP_THEMES.items()]
