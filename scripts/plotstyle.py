"""Shared matplotlib styling so every PNG in reports/ looks like one system.

Colours are the validated categorical order from the dataviz palette (fixed
order, never cycled) plus reserved status colours for verdicts.
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")  # headless: no display in Codespaces / CI
import matplotlib.pyplot as plt  # noqa: E402

SURFACE = "#fcfcfb"
TEXT = "#0b0b0b"
TEXT_SECONDARY = "#52514e"
GRID = "#e4e3df"

# Categorical slots 1..8 (identity). Assign in this order.
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]

# Status colours (state, not identity). Always paired with a text label.
GOOD = "#0ca30c"
WARNING = "#fab219"
CRITICAL = "#d03b3b"


def apply() -> None:
    """Set rcParams once per script: thin marks, recessive grid and axes."""
    plt.rcParams.update({
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "axes.edgecolor": GRID,
        "axes.labelcolor": TEXT_SECONDARY,
        "axes.titlecolor": TEXT,
        "axes.titleweight": "bold",
        "axes.grid": True,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "grid.color": GRID,
        "grid.linewidth": 0.8,
        "xtick.color": TEXT_SECONDARY,
        "ytick.color": TEXT_SECONDARY,
        "lines.linewidth": 2.0,
        "font.size": 10,
        "legend.frameon": False,
        "savefig.dpi": 130,
        "savefig.bbox": "tight",
    })
