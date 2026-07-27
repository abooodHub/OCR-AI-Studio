"""
gui/styles.py — Dark Commercial Theme Palette & Styling Manager
"""

import tkinter as tk
from tkinter import ttk

# ── Commercial Dark Palette Tokens ──────────────────────────────────────────
C = {
    "bg":             "#08090e",
    "panel":          "#10121d",
    "card":           "#161827",
    "border":         "#272b47",
    "entry_bg":       "#0d0e17",
    "accent":         "#6366f1",    # Indigo Accent
    "accent_hover":   "#4f46e5",
    "accent_subtle":  "#1e1b4b",
    "accent2":        "#a5b4fc",
    "text":           "#f8fafc",
    "text_dim":       "#94a3b8",
    "green":          "#10b981",
    "green_bg":       "#043627",
    "yellow":         "#f59e0b",
    "red":            "#ef4444",
    "log_bg":         "#050508",
    "btn_bg":         "#20243b",
}


def apply_styles(root: tk.Tk) -> None:
    """Configure modern dark commercial theme styles for TTK widgets."""
    style = ttk.Style(root)
    style.theme_use("clam")

    style.configure(".",
        background=C["bg"], foreground=C["text"],
        font=("Segoe UI", 10))

    style.configure("TFrame", background=C["bg"])
    style.configure("Panel.TFrame", background=C["panel"])
    style.configure("Card.TFrame", background=C["card"],
                     relief="flat", borderwidth=1)

    style.configure("TLabel",
        background=C["bg"], foreground=C["text"], font=("Segoe UI", 10))
    style.configure("Card.TLabel",
        background=C["card"], foreground=C["text"], font=("Segoe UI", 10))
    style.configure("Dim.TLabel",
        background=C["card"], foreground=C["text_dim"], font=("Segoe UI", 9))
    style.configure("HeaderTitle.TLabel",
        background=C["panel"], foreground=C["accent2"],
        font=("Segoe UI", 20, "bold"))
    style.configure("HeaderSub.TLabel",
        background=C["panel"], foreground=C["text_dim"],
        font=("Segoe UI", 10))

    style.configure("TLabelframe",
        background=C["card"], foreground=C["accent2"],
        bordercolor=C["border"], relief="solid", borderwidth=1)
    style.configure("TLabelframe.Label",
        background=C["card"], foreground=C["accent2"],
        font=("Segoe UI", 10, "bold"))

    style.configure("TEntry",
        fieldbackground=C["entry_bg"], foreground=C["text"],
        insertcolor=C["accent"], bordercolor=C["border"],
        relief="flat", font=("Segoe UI", 10))

    style.configure("TCombobox",
        fieldbackground=C["entry_bg"], background=C["btn_bg"],
        foreground=C["text"], darkcolor=C["border"],
        lightcolor=C["border"], bordercolor=C["border"],
        arrowcolor=C["accent2"], font=("Segoe UI", 10))

    style.map("TCombobox",
        fieldbackground=[("readonly", C["entry_bg"])],
        foreground=[("readonly", C["text"])])

    style.configure("TCheckbutton",
        background=C["card"], foreground=C["text_dim"],
        font=("Segoe UI", 9))

    style.configure("TButton",
        background=C["btn_bg"], foreground=C["text"],
        bordercolor=C["border"], focuscolor="none",
        font=("Segoe UI", 10), padding=6)
    style.map("TButton",
        background=[("active", C["accent"]), ("pressed", "#4338ca")])

    style.configure("Accent.TButton",
        background=C["accent"], foreground="white",
        bordercolor=C["accent"], focuscolor="none",
        font=("Segoe UI", 11, "bold"), padding=10)
    style.map("Accent.TButton",
        background=[("active", C["accent_hover"]), ("pressed", "#3730a3")])

    style.configure("Pause.TButton",
        background="#a2610b", foreground="white",
        bordercolor="#b46d0e", focuscolor="none",
        font=("Segoe UI", 11, "bold"), padding=10)
    style.map("Pause.TButton",
        background=[("active", "#b8700d"), ("pressed", "#875007")])

    style.configure("Stop.TButton",
        background="#3f1717", foreground=C["red"],
        bordercolor="#5c2020", focuscolor="none",
        font=("Segoe UI", 10, "bold"), padding=8)
    style.map("Stop.TButton",
        background=[("active", "#572020"), ("pressed", "#3f1717")])

    style.configure("TProgressbar",
        background=C["accent"], troughcolor=C["entry_bg"],
        bordercolor=C["border"], lightcolor=C["accent"],
        darkcolor=C["accent"])

    style.configure("Success.TProgressbar",
        background=C["green"], troughcolor=C["entry_bg"],
        bordercolor=C["border"], lightcolor=C["green"],
        darkcolor=C["green"])

    style.configure("TScrollbar",
        background=C["border"], troughcolor=C["entry_bg"],
        arrowcolor=C["text_dim"])
