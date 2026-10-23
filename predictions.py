"""
Usage prediction for StockLlama (no tkinter in here).

For every ingredient we look at its *usage events* (rows that only have a
date_used, see charts.py) and answer:

  * When will it probably be used next?            -> next_use
  * How likely is it to be used in the next N days -> chance
  * When will the current stock run out?           -> runout (+ early/late range)
  * Should I reorder?                              -> advice

Method (deliberately simple and explainable - no training step):
  - Only the last RECENT usage days are used, so the forecast follows changing habits.
  - typical gap   = median number of days between usages
  - consumption   = quantity used / days elapsed over that window  (units per day)
  - next use      = last use + typical gap
  - chance        = empirical: of the past gaps that are still "open" (longer than
                    the days since the last use), the share that ended inside the
                    window, smoothed so it never claims 0% or 100%
  - run-out range = stock divided by the fast (75th percentile) and slow (25th
                    percentile) per-interval consumption rates
  - confidence    = from how many usage records exist and how regular the gaps are

It needs at least MIN_USES usage days per ingredient; fewer -> "Not enough data".
"""
from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

import matplotlib.dates as mdates
from matplotlib.figure import Figure

from charts import THEME, Ingredient, _new_axes

RECENT = 10          # usage days considered per ingredient
MIN_USES = 3         # usage days needed before predicting
MAX_DAYS = 3650      # beyond this we say "no run-out in sight"
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
            "Saturday", "Sunday"]


@dataclass
class Forecast:
    ingredient: Ingredient
    stock: float
    n_uses: int                               # usage days on record
    last_used: Optional[date] = None
    confidence: str = "none"                  # none | low | medium | high
    typical_gap: Optional[float] = None       # days between usages (median)
    typical_qty: Optional[float] = None       # quantity per usage (median)
    rate: Optional[float] = None              # units per day
    rate_fast: Optional[float] = None
    rate_slow: Optional[float] = None
    next_use: Optional[date] = None
    chance: Optional[float] = None            # probability of use within horizon
    horizon: int = 7
    overdue: bool = False
    inactive: bool = False
    runout: Optional[date] = None
    days_left: Optional[int] = None
    days_left_early: Optional[int] = None
    days_left_late: Optional[int] = None
    weekday: Optional[str] = None             # habitual weekday, if clear
    advice: str = "Not enough data"


def usage_events(ing: Ingredient) -> list:
    """[(date, quantity used)] one entry per day, oldest first."""
    per_day: dict = defaultdict(float)
    for day, delta in ing.movements:
        if delta < 0:
            per_day[day] += -delta
    return sorted(per_day.items())


def _quartiles(values: list):
    if len(values) < 2:
        return values[0], values[0], values[0]
    q = statistics.quantiles(values, n=4, method="inclusive")
    return q[0], q[1], q[2]


def forecast(ing: Ingredient, today: Optional[date] = None, horizon: int = 7,
             lead: int = 5) -> Forecast:
    today = today or date.today()
    all_uses = usage_events(ing)
    f = Forecast(ingredient=ing, stock=ing.net, n_uses=len(all_uses),
                 last_used=all_uses[-1][0] if all_uses else None, horizon=horizon)
    uses = all_uses[-RECENT:]

    if len(uses) < MIN_USES:
        f.advice = "Out of stock" if f.stock <= 0 else "Not enough data"
        return f

    gaps = [(uses[i][0] - uses[i - 1][0]).days for i in range(1, len(uses))]
    rates = [uses[i][1] / gaps[i - 1] for i in range(1, len(uses))]
    f.typical_gap = statistics.median(gaps)
    f.typical_qty = statistics.median(q for _, q in uses)
    f.rate = sum(uses[i][1] for i in range(1, len(uses))) / sum(gaps)
    p25, _p50, p75 = _quartiles(rates)
    f.rate_fast = max(p75, f.rate)
    f.rate_slow = min(p25, f.rate)

    # ── when / how likely is the next use? ──────────────────────────────────
    last = uses[-1][0]
    elapsed = (today - last).days
    f.inactive = elapsed > max(14, 3 * f.typical_gap)
    nxt = last + timedelta(days=round(f.typical_gap))
    f.overdue = nxt < today
    f.next_use = None if f.inactive else max(nxt, today)
    if f.inactive:
        f.chance = 0.1
    else:
        open_gaps = [g for g in gaps if g > elapsed]
        if open_gaps:
            hits = sum(1 for g in open_gaps if g <= elapsed + horizon)
            f.chance = (hits + 0.5) / (len(open_gaps) + 1)
        else:
            f.chance = 0.8          # longer than ever before: use is due

    # ── when does the stock run out? ────────────────────────────────────────
    def days(rate):
        if not rate or rate <= 0:
            return None
        d = int(f.stock / rate)
        return d if d <= MAX_DAYS else None

    if f.stock <= 0:
        f.days_left = f.days_left_early = f.days_left_late = 0
        f.runout = today
    else:
        f.days_left = days(f.rate)
        f.days_left_early = days(f.rate_fast)
        f.days_left_late = days(f.rate_slow)
        f.runout = today + timedelta(days=f.days_left) if f.days_left is not None else None

    # ── confidence ──────────────────────────────────────────────────────────
    n = len(all_uses)
    level = 0 if n < 6 else (1 if n < 10 else 2)
    mean_gap = statistics.mean(gaps)
    if len(gaps) > 1 and mean_gap > 0 and statistics.pstdev(gaps) / mean_gap > 1 and level > 0:
        level -= 1                  # irregular habits
    f.confidence = ["low", "medium", "high"][level]

    if n >= 8:
        weekday, count = Counter(d.weekday() for d, _ in all_uses).most_common(1)[0]
        if count / n >= 0.4:
            f.weekday = WEEKDAYS[weekday]

    # ── advice ──────────────────────────────────────────────────────────────
    if f.stock <= 0:
        f.advice = "Out of stock"
    elif f.inactive:
        f.advice = "Possibly inactive"
    else:
        soonest = f.days_left_early if f.days_left_early is not None else f.days_left
        if soonest is not None and soonest <= lead:
            f.advice = "Reorder now"
        elif f.days_left is not None and f.days_left <= 2 * lead:
            f.advice = "Reorder soon"
        else:
            f.advice = "OK"
    return f


def forecast_all(ings, today=None, horizon=7, lead=5, sort="Next use") -> list:
    today = today or date.today()
    results = [forecast(i, today, horizon, lead) for i in ings]
    far = date.max
    keys = {
        "Next use": lambda f: (f.next_use is None, f.next_use or far, -(f.chance or 0)),
        "Runs out first": lambda f: (f.runout is None, f.runout or far),
        "Most likely": lambda f: (-(f.chance or -1), f.next_use or far),
    }
    return sorted(results, key=keys.get(sort, keys["Next use"]))


# ── self-check: how good are the next-use predictions on your own history? ────
def backtest(ings) -> dict:
    """Walk through history: predict each usage date from the ones before it."""
    errors = []
    for ing in ings:
        uses = usage_events(ing)
        for k in range(MIN_USES, len(uses)):
            hist = uses[:k][-RECENT:]
            gaps = [(hist[i][0] - hist[i - 1][0]).days for i in range(1, len(hist))]
            predicted = hist[-1][0] + timedelta(days=round(statistics.median(gaps)))
            errors.append(abs((uses[k][0] - predicted).days))
    if not errors:
        return {"n": 0}
    return {"n": len(errors), "mae": statistics.mean(errors),
            "within1": sum(1 for e in errors if e <= 1) / len(errors)}


# ── text helpers ──────────────────────────────────────────────────────────────
def fmt_date(d: Optional[date], today: date) -> str:
    if d is None:
        return "-"
    text = d.strftime("%a %d %b")
    return text if d.year == today.year else text + f" {d.year}"


def relative(d: Optional[date], today: date) -> str:
    if d is None:
        return ""
    n = (d - today).days
    if n == 0:
        return "today"
    if n == 1:
        return "tomorrow"
    if n == -1:
        return "yesterday"
    return f"in {n} days" if n > 0 else f"{-n} days ago"


def describe(f: Forecast, today: date, lead: int) -> str:
    ing = f.ingredient
    unit = f" {ing.unit}" if ing.unit else ""
    out = [ing.label, ""]
    out.append(f"In stock now: {f.stock:g}{unit}  (bought {ing.bought:g}, used {ing.used:g})")
    if f.n_uses == 0:
        out += ["", "No usage recorded yet. Add entries like \"used 2 kg rice\" so the "
                    "pattern can be learned."]
        return "\n".join(out)
    out.append(f"Last used: {fmt_date(f.last_used, today)} ({relative(f.last_used, today)})")
    if f.rate is None:
        out += ["", f"Only {f.n_uses} usage day(s) on record; at least {MIN_USES} are "
                    f"needed before a pattern can be predicted."]
        return "\n".join(out)

    out += ["", f"Pattern  (last {min(f.n_uses, RECENT)} usage days)",
            f"  used about every {f.typical_gap:g} day(s), ~{f.typical_qty:.2g}{unit} each time",
            f"  average consumption {f.rate:.3g}{unit} per day"]
    if f.weekday:
        out.append(f"  most often used on {f.weekday}s")
    out += ["", "Forecast"]
    if f.inactive:
        out.append("  Not used for much longer than usual - possibly no longer in use.")
    else:
        note = "  (overdue, expected any time)" if f.overdue else ""
        out.append(f"  next use: {fmt_date(f.next_use, today)} ({relative(f.next_use, today)}){note}")
        out.append(f"  chance of use within {f.horizon} days: {f.chance:.0%}")
    if f.stock <= 0:
        out.append("  stock is at or below zero - check that purchases are recorded.")
    elif f.runout is None:
        out.append("  at this pace the stock will last longer than 10 years.")
    else:
        early = today + timedelta(days=f.days_left_early) if f.days_left_early is not None else None
        late = today + timedelta(days=f.days_left_late) if f.days_left_late is not None else None
        out.append(f"  stock runs out: {fmt_date(f.runout, today)} ({relative(f.runout, today)})")
        if early:
            if late == early:
                out.append("  likely range: same day (very regular usage)")
            else:
                rng = f"{fmt_date(early, today)} to {fmt_date(late, today) if late else 'much later'}"
                out.append(f"  likely range: {rng}")
            reorder = early - timedelta(days=lead)
            when = "now" if reorder <= today else fmt_date(reorder, today)
            out.append(f"  reorder by: {when}  ({lead}-day lead time)")
    reason = {"low": "few usage records", "medium": "a fair amount of history",
              "high": "plenty of history"}[f.confidence]
    out += ["", f"Confidence: {f.confidence} ({reason}). Based on current stock only; "
                f"future purchases are not included."]
    return "\n".join(out)


# ── chart ─────────────────────────────────────────────────────────────────────
def build_projection_figure(f: Forecast, today: date, lead: int) -> Figure:
    ing = f.ingredient
    fig, ax = _new_axes(f"{ing.name} - stock history and forecast", size=(6.4, 3.6))
    num = mdates.date2num
    t0 = num(today)

    pts = ing.timeline()
    if pts:
        xs = [num(d) for d, _ in pts] + [t0]
        ys = [v for _, v in pts] + [f.stock]
        ax.step(xs, ys, where="post", color=THEME["palette"][0], linewidth=1.8,
                label="Stock so far")
        ax.plot(xs[:-1], ys[:-1], "o", color=THEME["palette"][0], markersize=4)
    ax.axvline(t0, color=THEME["dim"], linestyle=":", linewidth=1)
    ax.axhline(0, color=THEME["grid"], linewidth=1)

    if f.rate and f.stock > 0:
        span = f.days_left_late or f.days_left or 14
        span = min(max(span * 1.1, 14), 120)
        grid = [t0 + span * i / 60 for i in range(61)]
        proj = lambda rate: [max(f.stock - rate * (x - t0), 0.0) for x in grid]
        ax.fill_between(grid, proj(f.rate_fast), proj(f.rate_slow),
                        color=THEME["palette"][4], alpha=0.22, label="Likely range")
        ax.plot(grid, proj(f.rate), "--", color=THEME["palette"][4], linewidth=1.8,
                label="Projected stock")
        if f.next_use and not f.inactive:
            xn = num(f.next_use)
            ax.plot([xn], [max(f.stock - f.rate * (xn - t0), 0.0)], "v",
                    color=THEME["palette"][3], markersize=8, label="Next use")
        if f.days_left_early is not None:
            reorder = t0 + f.days_left_early - lead
            if reorder > t0:
                ax.axvline(reorder, color=THEME["palette"][2], linestyle="--",
                           linewidth=1.2, label=f"Reorder by ({lead}d lead)")
    elif f.rate is None:
        ax.text(0.5, 0.5, "Not enough usage records to forecast",
                transform=ax.transAxes, ha="center", va="center",
                color=THEME["dim"], fontsize=10)

    ax.xaxis_date()
    loc = mdates.AutoDateLocator()
    ax.xaxis.set_major_locator(loc)
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(loc))
    ax.set_ylabel(f"Stock ({ing.unit})" if ing.unit else "Stock",
                  color=THEME["dim"], fontsize=9)
    ax.set_ylim(bottom=min(0, ax.get_ylim()[0]))
    leg = ax.legend(facecolor=THEME["surface"], edgecolor=THEME["grid"],
                    fontsize=8, labelcolor=THEME["text"])
    if leg:
        leg.get_frame().set_alpha(0.9)
    return fig
