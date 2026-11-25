# Compares the unlock methods for ONE donor you pick.
# Line chart: how much each method would unlock for recipients each day.
# (For the donor's own heartbeat, use plot_heartbeat.py.)
# Also prints, per method, what the router took and whether the donor ended up over its cap.
#
# Run from heartbeat/:  python compare_donor.py donor-0058
#                       python compare_donor.py donor-0418 company_1000

import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import matplotlib.pyplot as plt
import pandas as pd

from heartbeat.compare_forecasts import METHODS
from heartbeat.heart_beat import load_donor_daily, overruns, replay_donor

MONTH_START = date(2026, 10, 1)
METHOD_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]   # one color per method, in METHODS order


# Day 0 in the data = Oct 1, used to turn day numbers into dates
def day_label(d: int) -> str:
    return (MONTH_START + timedelta(days=int(d))).strftime("%b %d")


# which day numbers are Fridays
def fridays(n_days: int) -> list[int]:
    return [d for d in range(n_days) if (MONTH_START + timedelta(days=d)).weekday() == 4]


# Runs every method on one donor: returns a summary table and each method's daily unlock
def compare_one(spend: list[float], meta: dict, alias: str) -> tuple[pd.DataFrame, dict]:
    summary, daily_unlock = [], {}
    for label, forecast, cushion in METHODS:
        rows = replay_donor(alias, spend, meta["cap"], meta["billing"], meta["reset_day"],
                            forecast=forecast, cushion=cushion)
        o = overruns(rows, meta["cap"])
        daily_unlock[label] = [r["allowed_usd"] for r in rows]
        summary.append({
            "method": label,
            "router_took_usd": round(o["router_took"], 2),
            "cycles": o["cycles"],
            "over_alone": o["over_alone"],
            "caused_by_router": o["caused_by_router"],
        })
    return pd.DataFrame(summary), daily_unlock


# One line chart: each method's daily unlock for this donor, on the same axes.
# Lines are drawn thick-to-thin so that when two methods unlock the same amount, both stay visible.
def plot_unlocks(daily_unlock: dict, summary: pd.DataFrame, alias: str, profile: str,
                 cap: float, run: str) -> None:
    n_days = len(next(iter(daily_unlock.values())))
    days = list(range(n_days))
    widths = [6, 4, 2.5, 1.2]                       # first method thickest, last thinnest

    fig, ax = plt.subplots(figsize=(10, 4.5))

    # one step line per method; the legend also shows what the router took in total
    for (label, unlock), color, width, (_, row) in zip(daily_unlock.items(), METHOD_COLORS,
                                                        widths, summary.iterrows()):
        flag = ", over cap" if row["caused_by_router"] > 0 else ""
        ax.step(days, unlock, where="mid", color=color, linewidth=width,
                label=f"{label} (took \\${row['router_took_usd']:.2f}{flag})")

    # dotted line on every Friday
    for d in fridays(n_days):
        ax.axvline(d, color="#bbbbbb", linestyle=":", linewidth=1)

    ax.set_title(f"{alias} ({profile.replace('_', ' ')}, cap \\${cap:.0f}): daily unlock by method", fontsize=12)
    ax.set_ylabel("$ unlocked for recipients")
    ax.set_ylim(bottom=0)
    ticks = list(range(0, n_days, 7))
    ax.set_xticks(ticks)
    ax.set_xticklabels([day_label(t) for t in ticks])
    ax.grid(axis="y", color="#e5e5e5", linewidth=0.8)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.legend(frameon=False, fontsize=9, loc="upper left")

    fig.tight_layout()
    out = ROOT / "figures" / f"forecast_{alias}_{run}.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print("saved", out)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python compare_donor.py donor-0058 [company_100]")
        sys.exit(1)
    alias = sys.argv[1]
    run = sys.argv[2] if len(sys.argv) > 2 else "company_100"

    daily, info = load_donor_daily(run)
    if alias not in daily:
        print(f"No donor called {alias} in {run}. Some examples: {', '.join(list(daily)[:5])}")
        sys.exit(1)

    spend, meta = daily[alias], info[alias]
    cap, profile = meta["cap"], f"{meta['profile']}, {meta['billing']}"
    summary, daily_unlock = compare_one(spend, meta, alias)

    print(f"{alias} ({profile}), cap ${cap:.2f}, spent ${sum(spend):.2f} on its own")
    print(summary.to_string(index=False))
    plot_unlocks(daily_unlock, summary, alias, profile, cap, run)
