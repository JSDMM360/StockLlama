"""
Charting for StockLlama.

Pure data + matplotlib code (no tkinter) so it can be tested headlessly.

How rows become chart data
--------------------------
Every row in the `inventory` table is one movement of one ingredient:
  * only date_used set      -> consumption  (negative)
  * anything else           -> purchase / stock-in (positive)
The movement date is date_bought, else date_used, else the day the row was
created.  Ingredient names are grouped case-insensitively.
"""
from __future__ import annotations

from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Iterable, Optional

from matplotlib.figure import Figure
import matplotlib.dates as mdates

# Label shown in the GUI -> internal key
CHART_TYPES: "OrderedDict[str, str]" = OrderedDict([
    ("Line  - stock over time", "line"),
    ("Area  - stock over time", "area"),
    ("Bar   - stock / daily change", "bar"),
    ("Pie   - share of stock", "pie"),
])

THEME = {
    "bg": "#0D1117", "surface": "#161B22", "grid": "#30363D",
    "text": "#E6EDF3", "dim": "#8B949E",
    "good": "#3FB950", "bad": "#F85149",
    "palette": ["#58A6FF", "#3FB950", "#F78166", "#D2A8FF", "#D29922",
                "#79C0FF", "#56D364", "#FF8B7A", "#E3B341", "#A371F7"],
}


# ── data model ────────────────────────────────────────────────────────────────
@dataclass
class Ingredient:
    key: str                      # casefolded name used for grouping
    name: str                     # display name
    unit: str = ""
    movements: list = field(default_factory=list)   # [(date, delta_float)]

    @property
    def label(self) -> str:
        return f"{self.name} ({self.unit})" if self.unit else self.name

    @property
    def bought(self) -> float:
        return sum(d for _, d in self.movements if d > 0)

    @property
    def used(self) -> float:
        return -sum(d for _, d in self.movements if d < 0)

    @property
    def net(self) -> float:
        return self.bought - self.used

    def timeline(self):
        """Sorted [(date, cumulative stock)] with same-day movements merged."""
        per_day: dict = defaultdict(float)
        for day, delta in self.movements:
            per_day[day] += delta
        total, out = 0.0, []
        for day in sorted(per_day):
            total += per_day[day]
            out.append((day, total))
        return out

    def daily_change(self):
        per_day: dict = defaultdict(float)
        for day, delta in self.movements:
            per_day[day] += delta
        return sorted(per_day.items())


def _to_date(value) -> Optional[date]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def build_ingredients(rows: Iterable[dict]) -> "OrderedDict[str, Ingredient]":
    """Group inventory rows into per-ingredient movement lists."""
    result: "OrderedDict[str, Ingredient]" = OrderedDict()
    for row in rows:
        name = str(row.get("item_name") or "").strip()
        if not name:
            continue
        try:
            qty = float(row.get("quantity") or 0)
        except (TypeError, ValueError):
            continue
        used, bought = _to_date(row.get("date_used")), _to_date(row.get("date_bought"))
        if used and not bought:
            sign, day = -1, used
        else:
            sign, day = 1, bought or _to_date(row.get("created_at")) or date.today()
        key = name.casefold()
        ing = result.setdefault(key, Ingredient(key=key, name=name))
        if not ing.unit and row.get("unit"):
            ing.unit = str(row["unit"]).strip()
        ing.movements.append((day, sign * qty))
    return OrderedDict(sorted(result.items(), key=lambda kv: kv[1].name.casefold()))


# ── figure building ───────────────────────────────────────────────────────────
def build_figures(ingredients: list, chart_type: str, combined: bool) -> list:
    """Return a list of matplotlib Figures.

    combined=True  -> exactly one figure containing every ingredient.
    combined=False -> one figure per ingredient.
    """
    if not ingredients:
        return []
    if combined:
        return [_figure(ingredients, chart_type,
                        "All selected ingredients"
                        if len(ingredients) > 1 else ingredients[0].name)]
    return [_figure([ing], chart_type, ing.name) for ing in ingredients]


def _new_axes(title: str, size=(6.4, 3.8)):
    fig = Figure(figsize=size, dpi=100, facecolor=THEME["bg"],
                 constrained_layout=True)
    ax = fig.add_subplot(111)
    ax.set_facecolor(THEME["surface"])
    ax.set_title(title, color=THEME["text"], fontsize=11, fontweight="bold",
                 loc="left")
    for spine in ax.spines.values():
        spine.set_color(THEME["grid"])
    ax.tick_params(colors=THEME["dim"], labelsize=8)
    ax.grid(True, color=THEME["grid"], alpha=0.5, linewidth=0.6)
    return fig, ax


def _legend(ax):
    leg = ax.legend(facecolor=THEME["surface"], edgecolor=THEME["grid"],
                    fontsize=8, labelcolor=THEME["text"])
    if leg:
        leg.get_frame().set_alpha(0.9)


def _mixed_units(ings) -> bool:
    return len({i.unit for i in ings if i.unit}) > 1


def _figure(ings: list, chart_type: str, title: str) -> Figure:
    if chart_type == "pie":
        return _pie(ings, title)
    fig, ax = _new_axes(title)
    if chart_type in ("line", "area"):
        _stock_over_time(ax, ings, fill=(chart_type == "area"))
    elif chart_type == "bar":
        if len(ings) > 1:
            _bar_net_stock(ax, ings)
        else:
            _bar_daily_change(ax, ings[0])
    else:
        raise ValueError(f"Unknown chart type: {chart_type}")
    return fig


def _stock_over_time(ax, ings, fill: bool):
    palette = THEME["palette"]
    last_day = max((d for i in ings for d, _ in i.timeline()), default=None)
    for idx, ing in enumerate(ings):
        pts = ing.timeline()
        if not pts:
            continue
        xs = [d for d, _ in pts]
        ys = [v for _, v in pts]
        if last_day and xs[-1] < last_day:       # extend the last level flat
            xs.append(last_day)
            ys.append(ys[-1])
        color = palette[idx % len(palette)]
        ax.step(xs, ys, where="post", color=color, linewidth=1.8,
                label=ing.label)
        ax.plot(xs[:len(pts)], ys[:len(pts)], "o", color=color, markersize=4)
        if fill:
            ax.fill_between(xs, ys, step="post", color=color, alpha=0.22)
    ax.axhline(0, color=THEME["grid"], linewidth=1)
    ax.set_ylabel("Quantity" + (" (mixed units)" if _mixed_units(ings)
                                else f" ({ings[0].unit})" if ings[0].unit else ""),
                  color=THEME["dim"], fontsize=9)
    loc = mdates.AutoDateLocator()
    ax.xaxis.set_major_locator(loc)
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(loc))
    if len(ings) > 1:
        _legend(ax)


def _bar_net_stock(ax, ings):
    palette = THEME["palette"]
    labels = [i.label for i in ings]
    values = [i.net for i in ings]
    colors = [palette[n % len(palette)] if v >= 0 else THEME["bad"]
              for n, v in enumerate(values)]
    bars = ax.bar(range(len(ings)), values, color=colors)
    ax.set_xticks(range(len(ings)))
    ax.set_xticklabels(labels, rotation=30 if len(ings) > 4 else 0,
                       ha="right" if len(ings) > 4 else "center",
                       color=THEME["dim"], fontsize=8)
    ax.bar_label(bars, fmt="%.1f", color=THEME["text"], fontsize=8, padding=2)
    ax.axhline(0, color=THEME["grid"], linewidth=1)
    ax.set_ylabel("Current stock" + (" (mixed units)" if _mixed_units(ings) else ""),
                  color=THEME["dim"], fontsize=9)
    ax.margins(y=0.15)


def _bar_daily_change(ax, ing):
    days = ing.daily_change()
    xs = [d for d, _ in days]
    ys = [v for _, v in days]
    colors = [THEME["good"] if v >= 0 else THEME["bad"] for v in ys]
    ax.bar(xs, ys, color=colors, width=0.6)
    ax.axhline(0, color=THEME["grid"], linewidth=1)
    ax.set_ylabel(f"Daily change ({ing.unit})" if ing.unit else "Daily change",
                  color=THEME["dim"], fontsize=9)
    loc = mdates.AutoDateLocator()
    ax.xaxis.set_major_locator(loc)
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(loc))
    ax.margins(y=0.15)


def _pie(ings, title) -> Figure:
    fig = Figure(figsize=(6.4, 3.8), dpi=100, facecolor=THEME["bg"],
                 constrained_layout=True)
    ax = fig.add_subplot(111)
    ax.set_facecolor(THEME["bg"])
    ax.set_title(title, color=THEME["text"], fontsize=11, fontweight="bold",
                 loc="left")
    if len(ings) == 1:
        ing = ings[0]
        parts = [(f"Bought ({ing.bought:g})", ing.bought, THEME["good"]),
                 (f"Used ({ing.used:g})", ing.used, THEME["bad"])]
    else:
        palette = THEME["palette"]
        parts = [(f"{i.label}", i.net, palette[n % len(palette)])
                 for n, i in enumerate(ings)]
    parts = [p for p in parts if p[1] > 0]
    if not parts:
        ax.text(0.5, 0.5, "Nothing in stock to show", ha="center", va="center",
                color=THEME["dim"], transform=ax.transAxes)
        ax.axis("off")
        return fig
    labels, values, colors = zip(*parts)
    wedges, _texts, autotexts = ax.pie(
        values, colors=colors, startangle=90, counterclock=False,
        autopct="%1.0f%%", pctdistance=0.78,
        wedgeprops={"edgecolor": THEME["bg"], "linewidth": 1.5},
        textprops={"color": THEME["bg"], "fontsize": 8, "fontweight": "bold"})
    ax.legend(wedges, labels, loc="center left", bbox_to_anchor=(1.0, 0.5),
              facecolor=THEME["surface"], edgecolor=THEME["grid"],
              fontsize=8, labelcolor=THEME["text"])
    if len(ings) > 1 and _mixed_units(ings):
        ax.set_xlabel("Quantities are in mixed units", color=THEME["dim"],
                      fontsize=8)
    return fig
