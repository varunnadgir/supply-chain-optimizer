"""Small helpers shared by the three notebooks.

Nothing clever lives here on purpose. The interesting logic (the rate card,
the optimization model, the inventory math) is written out inside the
notebooks so you can read it top to bottom.
"""

import re
import sqlite3
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "olist.db"
SQL_PATH = ROOT / "sql" / "queries.sql"
FIGURES = ROOT / "figures"
RESULTS = ROOT / "results"

# ---------------------------------------------------------------- database


def connect() -> sqlite3.Connection:
    """Open the SQLite database built by src/build_database.py."""
    if not DB_PATH.exists():
        raise FileNotFoundError(
            "data/olist.db not found. Run `python src/build_database.py` first."
        )
    return sqlite3.connect(DB_PATH)


def load_queries() -> dict[str, str]:
    """Read sql/queries.sql and return {query_name: sql_text}.

    Each query in the file starts with a line like `-- name: demand_by_state`.
    """
    text = SQL_PATH.read_text()
    parts = re.split(r"^-- name: *(\w+) *$", text, flags=re.MULTILINE)
    # parts = [text before first name, name1, sql1, name2, sql2, ...]
    return {name: sql.strip() for name, sql in zip(parts[1::2], parts[2::2])}


def run_query(name: str, con: sqlite3.Connection, show_sql: bool = True, **kwargs) -> pd.DataFrame:
    """Run one named query from sql/queries.sql and return a DataFrame."""
    sql = load_queries()[name]
    if show_sql:
        print(sql)
        print()
    return pd.read_sql(sql, con, **kwargs)


# ---------------------------------------------------------------- geography

def pretty_city(city_key: str) -> str:
    """'sao caetano do sul, SP' -> 'Sao Caetano do Sul' (the data has no accents)."""
    small = {"do", "da", "de", "dos", "das", "e"}
    words = city_key.split(",")[0].split()
    return " ".join(w if (w in small and k) else w.capitalize() for k, w in enumerate(words))


EARTH_RADIUS_KM = 6371.0


def haversine_km(lat1, lng1, lat2, lng2):
    """Straight-line ("as the crow flies") distance in km between two points.

    Works on single numbers or on NumPy arrays. This is the standard haversine
    formula for distance on a sphere.
    """
    lat1, lng1, lat2, lng2 = map(np.radians, (lat1, lng1, lat2, lng2))
    a = (
        np.sin((lat2 - lat1) / 2) ** 2
        + np.cos(lat1) * np.cos(lat2) * np.sin((lng2 - lng1) / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(a))


# ---------------------------------------------------------------- charts

# One consistent look for every chart in the project.
COLORS = {
    "blue": "#2a78d6",
    "orange": "#eb6834",
    "aqua": "#1baf7a",
    "gray": "#b9b8b2",
    "text": "#0b0b0b",
    "muted": "#52514e",
    "grid": "#e6e5e0",
    "surface": "#fcfcfb",
}


def set_chart_style() -> None:
    """Apply the project's matplotlib style (call once at the top of a notebook)."""
    plt.rcParams.update(
        {
            "figure.facecolor": COLORS["surface"],
            "axes.facecolor": COLORS["surface"],
            "savefig.facecolor": COLORS["surface"],
            "figure.dpi": 110,
            "savefig.dpi": 200,
            "savefig.bbox": "tight",
            "font.size": 10,
            "axes.titlesize": 12,
            "axes.titleweight": "bold",
            "axes.titlelocation": "left",
            "axes.titlepad": 12,
            "axes.labelcolor": COLORS["muted"],
            "axes.edgecolor": COLORS["grid"],
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": True,
            "axes.axisbelow": True,
            "grid.color": COLORS["grid"],
            "grid.linewidth": 0.8,
            "xtick.color": COLORS["muted"],
            "ytick.color": COLORS["muted"],
            "xtick.major.size": 0,
            "ytick.major.size": 0,
            "text.color": COLORS["text"],
            "lines.linewidth": 2,
            "legend.frameon": False,
        }
    )


def save_figure(fig, name: str) -> None:
    """Save a chart into figures/ so the README can show it."""
    FIGURES.mkdir(exist_ok=True)
    fig.savefig(FIGURES / name)
