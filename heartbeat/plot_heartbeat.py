# Heartbeat timeline for ONE donor: how much the router may take from it on each day of the month.
# Run from heartbeat/:        python plot_heartbeat.py donor-0018
# or from forecast-engine/:   python -m heartbeat.plot_heartbeat donor-0018
#                             python -m heartbeat.plot_heartbeat donor-0418 company_1000

import sys
from pathlib import Path

# forecast-engine/ folder, so these scripts work from heartbeat/ or from forecast-engine/
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))   # lets "from policy... import" find the other folders
from datetime import date, timedelta

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.patches import Patch

# Day 0 in the data = Oct 1, used to turn day numbers into dates
MONTH_START = date(2026, 10, 1)

# Red used to shade days when the donor is at capacity
AT_CAPACITY_COLOR = "#d03b3b"

# Line color for each donor type
PROFILE_COLORS = {"enterprise": "#2a78d6", "startup": "#eb6834", "research_lab": "#1baf7a",
                  "agency": "#eda100", "individual": "#8f5bd6", "agent": "#5c6b7a"}

# Gray shading for days before the donor's first reset (the router stays out)
NOT_STARTED_COLOR = "#9a9a9a"


# Turns a day number into a date label, e.g. 8 -> "Oct 09"
def day_label(d: int) -> str:
    return (MONTH_START + timedelta(days=int(d))).strftime("%b %d")


# Keeps only the day numbers that fall on a Friday (weekday() == 4 means Friday)
def fridays(days) -> list[int]:
    return [d for d in days if (MONTH_START + timedelta(days=int(d))).weekday() == 4]


# One chart for one donor: its daily allowance across the month
def plot_donor(hb: pd.DataFrame, alias: str, run: str) -> None:

    # That donor's daily rows, in day order
    d = hb[hb["key_alias"] == alias].sort_values("day")
    if d.empty:
        print(f"No donor called {alias}. Some examples: {', '.join(hb['key_alias'].unique()[:5])}")
        return

    # Info that's the same on every row
    profile = d["profile"].iloc[0]
    cap = d["cap"].iloc[0]
    billing = d["billing"].iloc[0]
    cap_word = "balance" if billing == "prepaid" else "cap"

    # Two stacked panels sharing the same dates: heartbeat on top, unlock below
    fig, (top, ax) = plt.subplots(2, 1, figsize=(10, 6.5), sharex=True,
                                  gridspec_kw={"height_ratios": [1, 1]})
    color = PROFILE_COLORS.get(profile, "#2a78d6")

    # TOP: the heartbeat, the company's own spend each day
    top.bar(d["day"], d["donor_today"], width=0.8, color=color)
    top.set_ylabel("Company's own $ that day")
    top.set_title(f"{alias} ({profile.replace('_', ' ')}, {billing}, {cap_word} \\${cap:.0f}): "
                  f"heartbeat and what the router unlocks", fontsize=12)
    top.grid(axis="y", color="#e5e5e5", linewidth=0.8)
    for side in ("top", "right"):
        top.spines[side].set_visible(False)

    # BOTTOM: what the router unlocks for recipients, based on the heartbeat so far
    for day in d.loc[d["status"] == "at_capacity", "day"]:
        ax.axvspan(day - 0.5, day + 0.5, color=AT_CAPACITY_COLOR, alpha=0.12, linewidth=0)
    for day in d.loc[d["status"] == "not_started", "day"]:
        ax.axvspan(day - 0.5, day + 0.5, color=NOT_STARTED_COLOR, alpha=0.12, linewidth=0)

    # dashed line where a new billing cycle starts, star over spike days
    resets = d.loc[(d["cycle"] >= 0) & (d["cycle"].diff() != 0), "day"] if billing != "prepaid" else []
    for day in resets:
        for p in (top, ax):
            p.axvline(day - 0.5, color="#333333", linestyle="--", linewidth=1)
    spikes = d[d["event"].fillna("") != ""]
    top.scatter(spikes["day"], spikes["donor_today"] * 1.05, marker="*", color="#333333", s=40, zorder=3)
    for p in (top, ax):                                   # Friday lines on both panels
        for day in fridays(d["day"]):
            p.axvline(day, color="#999999", linestyle=":", linewidth=1)
    ax.bar(d["day"], d["allowed_usd"], width=0.8, color=color)

    # Axes: $ starting at 0, dates once a week
    ax.set_ylabel("$ the router may take that day")
    ax.set_ylim(bottom=0)
    ticks = list(range(0, int(d["day"].max()) + 1, 7))
    ax.set_xticks(ticks)
    ax.set_xticklabels([day_label(t) for t in ticks])

    # Light gridlines, no top/right borders
    ax.grid(axis="y", color="#e5e5e5", linewidth=0.8)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)

    # Legend explaining the red shading and dotted lines
    ax.legend(handles=[Patch(color=AT_CAPACITY_COLOR, alpha=0.25, label="at capacity (the router takes nothing)"),
                       Patch(color=NOT_STARTED_COLOR, alpha=0.25, label="before first reset"),
                       plt.Line2D([], [], color="#333333", linestyle="--", label="billing reset"),
                       plt.Line2D([], [], color="#333333", marker="*", linestyle="", label="spike event"),
                       plt.Line2D([], [], color="#999999", linestyle=":", label="Friday")],
              loc="best", frameon=True, framealpha=0.9, fontsize=8)

    # Save and free the memory
    fig.tight_layout()
    out = ROOT / "figures" / f"heartbeat_{alias}_{run}.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print("saved", out)


if __name__ == "__main__":
    # Which donor to chart (required), and which simulation (optional, default company_100)
    if len(sys.argv) < 2:
        print("Usage: python plot_heartbeat.py donor-0018 [company_100]")
        sys.exit(1)
    alias = sys.argv[1]
    run = sys.argv[2] if len(sys.argv) > 2 else "company_100"

    hb = pd.read_csv(ROOT / "data" / f"heartbeat_{run}.csv")
    plot_donor(hb, alias, run)