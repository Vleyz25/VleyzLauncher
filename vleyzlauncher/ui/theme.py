# три палитры, переключаются в настройках

DARK = {
    "BG_TOP": "#0B0C11",
    "BG_BOTTOM": "#101219",
    "TITLEBAR": "#0C0E14",
    "SIDEBAR": "#0B0D13",
    "GRID": "#0C1119",

    "FIELD": "#0F1118",
    "TRACK": "#1E222C",
    "DIVIDER": "#21242E",

    "CARD": "#151823",
    "CARD_TOP": "#171A26",
    "CARD_BOTTOM": "#13151F",
    "CARD_SELECTED_TOP": "#1C2138",
    "CARD_SELECTED_BOTTOM": "#171B2E",
    "CARD_BORDER": "#212C3D",

    "SELECT_BG": "#1B2035",
    "PANEL": "#111319",
    "PANEL_BORDER": "#1F222C",

    "BORDER": "#242833",
    "BORDER_HOVER": "#3B4150",

    "TEXT": "#EBEDF3",
    "TEXT_SOFT": "#C3C8D3",
    "MUTED": "#969CA9",
    "DIM": "#646A76",

    "ACCENT": "#7C8CFA",
    "ACCENT_DARK": "#5A67E8",
    "ACCENT_DIM": "#232A45",
    "ON_ACCENT": "#0A0D18",
    "DANGER": "#F87171",

    "PRIMARY_OFF": "#22242F",
    "DANGER_BG": "#2A1B22",
    "DANGER_BORDER": "#45242E",
    "DANGER_HOVER": "#3A222A",
    "BUTTON_HOVER": "#1B1E29",
    "CLOSE_HOVER": "#7F1D1D",
    "HOVER": "#1B2432",
    "CUBE_OFF": "#3A3F4D",

    "WARNING_BG": "#241C16",
    "WARNING_BORDER": "#4A3624",
    "WARNING": "#E0A96D",
    "OVERLAY": "#05070C",
}

GRAY = {
    "BG_TOP": "#2C2F35",
    "BG_BOTTOM": "#35393F",
    "TITLEBAR": "#26292F",
    "SIDEBAR": "#25282E",
    "GRID": "#2F333A",

    "FIELD": "#353A41",
    "TRACK": "#444A52",
    "DIVIDER": "#3D424A",

    "CARD": "#33373E",
    "CARD_TOP": "#383C44",
    "CARD_BOTTOM": "#31353C",
    "CARD_SELECTED_TOP": "#414754",
    "CARD_SELECTED_BOTTOM": "#3A404C",
    "CARD_BORDER": "#474D57",

    "SELECT_BG": "#434956",
    "PANEL": "#2E3238",
    "PANEL_BORDER": "#434850",

    "BORDER": "#474D56",
    "BORDER_HOVER": "#606874",

    "TEXT": "#F3F5F8",
    "TEXT_SOFT": "#D5DAE1",
    "MUTED": "#AAB1BB",
    "DIM": "#7F8791",

    "ACCENT": "#93A0FF",
    "ACCENT_DARK": "#7583F2",
    "ACCENT_DIM": "#3B4159",
    "ON_ACCENT": "#14161B",
    "DANGER": "#FF8F8F",

    "PRIMARY_OFF": "#40464E",
    "DANGER_BG": "#3B2A2F",
    "DANGER_BORDER": "#5C3A42",
    "DANGER_HOVER": "#483137",
    "BUTTON_HOVER": "#3B414A",
    "CLOSE_HOVER": "#A33C3C",
    "HOVER": "#353B44",
    "CUBE_OFF": "#565D67",

    "WARNING_BG": "#3C3428",
    "WARNING_BORDER": "#5D4F39",
    "WARNING": "#F4C68C",
    "OVERLAY": "#1B1E24",
}

LIGHT = {
    "BG_TOP": "#F4F6FB",
    "BG_BOTTOM": "#E8ECF4",
    "TITLEBAR": "#FFFFFF",
    "SIDEBAR": "#F7F8FC",
    "GRID": "#E7ECF4",

    "FIELD": "#FFFFFF",
    "TRACK": "#DCE2EC",
    "DIVIDER": "#DCE2EC",

    "CARD": "#FFFFFF",
    "CARD_TOP": "#FFFFFF",
    "CARD_BOTTOM": "#F6F8FD",
    "CARD_SELECTED_TOP": "#EEF1FF",
    "CARD_SELECTED_BOTTOM": "#E7ECFF",
    "CARD_BORDER": "#D5DBE7",

    "SELECT_BG": "#E8EDFF",
    "PANEL": "#FFFFFF",
    "PANEL_BORDER": "#DBE1EC",

    "BORDER": "#DBE1EC",
    "BORDER_HOVER": "#B7C0D1",

    "TEXT": "#1A1D25",
    "TEXT_SOFT": "#3B4250",
    "MUTED": "#6C7483",
    "DIM": "#98A1B0",

    "ACCENT": "#4F5FE8",
    "ACCENT_DARK": "#3C4BD0",
    "ACCENT_DIM": "#DFE4FF",
    "ON_ACCENT": "#FFFFFF",
    "DANGER": "#D64545",

    "PRIMARY_OFF": "#C9D0DC",
    "DANGER_BG": "#FBEAEA",
    "DANGER_BORDER": "#F2C6C6",
    "DANGER_HOVER": "#F6DCDC",
    "BUTTON_HOVER": "#EDF1F9",
    "CLOSE_HOVER": "#E05A5A",
    "HOVER": "#E9EEF8",
    "CUBE_OFF": "#BAC3D1",

    "WARNING_BG": "#FFF4E3",
    "WARNING_BORDER": "#F2D7AF",
    "WARNING": "#946319",
    "OVERLAY": "#20242C",
}

PALETTES = {"dark": DARK, "gray": GRAY, "light": LIGHT}
TITLES = {"dark": "Тёмная", "gray": "Серая", "light": "Светлая"}

LOADER_ACCENTS = {
    "dark": {"vanilla": "#4ADE80", "fabric": "#E9D5A1", "forge": "#E07B39", "neoforge": "#C084FC"},
    "gray": {"vanilla": "#5FD48C", "fabric": "#E3D3A8", "forge": "#E58B54", "neoforge": "#BE93F5"},
    "light": {"vanilla": "#2E8B57", "fabric": "#9A7524", "forge": "#C05A1E", "neoforge": "#7546C9"},
}

FONT = "Segoe UI"
FONT_SEMI = "Segoe UI Semibold"

CURRENT = "dark"


def apply(name: str) -> str:
    """Переключает палитру: значения становятся атрибутами модуля."""
    global CURRENT
    palette = PALETTES.get(name) or PALETTES["dark"]
    CURRENT = name if name in PALETTES else "dark"
    for key, value in palette.items():
        globals()[key] = value
    return CURRENT


def palettes() -> list[tuple[str, str]]:
    return [(key, TITLES[key]) for key in ("dark", "gray", "light")]


def loader_accent(loader_id: str) -> str:
    palette = LOADER_ACCENTS.get(CURRENT, LOADER_ACCENTS["dark"])
    return palette.get(loader_id) or globals().get("ACCENT", "#7C8CFA")


apply("dark")
