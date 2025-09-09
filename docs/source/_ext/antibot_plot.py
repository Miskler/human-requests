from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict

from docutils import nodes
from docutils.parsers.rst import Directive, directives

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def _read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)

def _extract_timings(data: Dict[str, Any]) -> Dict[str, Dict[str, float]]:
    out: Dict[str, Dict[str, float]] = {}
    for br, brdata in data.items():
        t = brdata.get("timings", {}) or {}
        out[br] = {
            "avg": float(t.get("average_time_response", 0.0)),
            "min": float(t.get("minimum_time_response", 0.0)),
            "max": float(t.get("maximum_time_response", 0.0)),
        }
    return out

def _plot_universal(path_png: Path, timings: Dict[str, Dict[str, float]]) -> None:
    """
    Современный барчарт:
    - сортировка по avg (медленные справа)
    - читаемая палитра (Tableau/Okabe-Ito-friendly)
    - минималистские оси, тонкая сетка
    - подписи значений на барах
    - одна картинка (хорошо видна в любой теме)
    """
    import math
    import matplotlib.pyplot as plt

    # ---------- стиль / типографика ----------
    plt.rcdefaults()
    plt.rcParams.update({
        "figure.facecolor": "white",
        "axes.facecolor": "white",
        "axes.edgecolor": "#222",
        "axes.labelcolor": "#222",
        "text.color": "#222",
        "xtick.color": "#222",
        "ytick.color": "#222",
        "grid.color":  "#858585",
        "axes.spines.top":    False,
        "axes.spines.right":  False,
        "axes.spines.left":   True,
        "axes.spines.bottom": True,
        "font.size": 15,
    })

    # ---------- подготовка данных ----------
    items = [
        (br, float(v["avg"]), float(v["min"]), float(v["max"]))
        for br, v in timings.items()
    ]
    # сортируем по avg по возрастанию (самые медленные справа)
    items.sort(key=lambda x: x[1])

    browsers = [x[0] for x in items]
    avgs     = [x[1] for x in items]
    mins     = [x[2] for x in items]
    maxs     = [x[3] for x in items]

    # полуинтервалы для «усов»
    yerr = [
        [max(a - m, 0.0) for a, m in zip(avgs, mins)],
        [max(M - a, 0.0) for a, M in zip(avgs, maxs)],
    ]

    # палитра (стабильная, контрастная)
    palette = ["#4E79A7", "#59A14F", "#F28E2B", "#E15759", "#76B7B2", "#EDC948", "#B07AA1", "#FF9DA7"]
    colors  = [palette[i % len(palette)] for i in range(len(browsers))]

    # ---------- построение ----------
    fig, ax = plt.subplots(figsize=(8.8, 4.6), dpi=150)

    bars = ax.bar(
        browsers, avgs, yerr=yerr, capsize=7,
        color=colors, edgecolor="#1a1a1a", linewidth=0.6,
        error_kw=dict(ecolor="#1a1a1a", elinewidth=0.9, capsize=7),
    )

    ax.set_title("Browser timing (avg ± min/max)", pad=10, fontsize=13, weight="600")
    ax.set_ylabel("seconds")
    ax.grid(True, axis="y", linestyle="--", linewidth=0.6, alpha=0.55)
    ax.set_axisbelow(True)

    # небольшой зазор сверху, чтобы лейблы не упирались
    ymax = max(maxs) if maxs else 1.0
    ax.set_ylim(0, ymax * 1.1)

    # подписи значений на барах
    for rect, val in zip(bars, avgs):
        ax.annotate(
            f"{val:.2f}s",
            xy=(rect.get_x() + rect.get_width() / 2, rect.get_height()),
            xytext=(0, 8),
            textcoords="offset points",
            ha="center", va="bottom",
            fontsize=17, weight="600", color="#222",
        )

    # аккуратные подписи оси X (на случай длинных названий)
    ax.set_xticklabels(browsers, rotation=0, ha="center")

    fig.tight_layout()
    fig.savefig(path_png, dpi=150)
    plt.close(fig)



class AntibotSpeedPlot(Directive):
    """
    .. antibot-speed-plot:: ../tests/sannysoft/browser_antibot_sannysoft.json
       :title: Скорость браузеров (avg ± min/max)
       :outfile: _static/generated/antibot_speed.png
    """
    required_arguments = 1
    option_spec = {"title": directives.unchanged, "outfile": directives.unchanged}

    def run(self):
        env = self.state.document.settings.env
        srcdir = Path(env.app.srcdir)
        json_path = (srcdir / self.arguments[0]).resolve()
        data = _read_json(json_path)
        timings = _extract_timings(data)

        title = self.options.get("title") or "Browser timing"
        out_rel = self.options.get("outfile", "_static/generated/antibot_speed.png")
        out_png = (srcdir / out_rel)
        out_png.parent.mkdir(parents=True, exist_ok=True)

        _plot_universal(out_png, timings)

        img_rel = out_png.relative_to(srcdir).as_posix()+".png"
        html = f'<h2 class="ab-title">{title}</h2><img class="ab-theme-img" src="{img_rel}" alt="{title}">'
        return [nodes.raw("", html, format="html")]

def setup(app):
    app.add_directive("antibot-speed-plot", AntibotSpeedPlot)
    return {"version": "1.0", "parallel_read_safe": True, "parallel_write_safe": True}
