# Pacing vs safety buffer on the company_sim donors, one panel per billing type.
# invoice / credits: one line per pacing, one dot per buffer
# prepaid is left out: it has no pacing, and its runway lines all fall on one curve.
# To bring it back, add "prepaid" to BILLINGS (one line per runway length).
# x = share of billing cycles the router pushed over the donor's cap, y = average $ the router got per cycle
# Forecast is fixed at the heartbeat default (weekday + CUSHION).
#
# python -m heartbeat.buffer_sweep company_1000

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import matplotlib.pyplot as plt
import pandas as pd

from heartbeat.heart_beat import load_donor_daily, overruns, replay_donor

BUFFERS = [0.05, 0.10, 0.20, 0.30]
PACINGS = ["backloaded", "linear", "none"]
RUNWAYS = [30, 45, 60, 90]
BILLINGS = ["invoice", "credits"]

PACING_COLORS = {"backloaded": "#2a78d6", "linear": "#eb6834", "none": "#1baf7a"}
RUNWAY_COLORS = {30: "#d03b3b", 45: "#eda100", 60: "#2a78d6", 90: "#1baf7a"}


# One setting on every donor of one billing type -> overrun rate and $ per cycle
def score(daily: dict, info: dict, billing: str, buffer: float,
          pacing: str | None = None, runway: int | None = None) -> dict:
    cycles = caused = 0
    took = 0.0
    for alias, spend in daily.items():
        m = info[alias]
        if m["billing"] != billing:
            continue
        rows = replay_donor(alias, spend, m["cap"], m["billing"], m["reset_day"],
                            buffer=buffer, pacing=pacing, runway=runway)
        o = overruns(rows, m["cap"])
        cycles += o["cycles"]
        caused += o["caused_by_router"]
        took += o["router_took"]
    return {"cycles": cycles, "overrun_rate": caused / cycles if cycles else 0.0,
            "usd_per_cycle": took / cycles if cycles else 0.0, "router_total": took}


def sweep(daily: dict, info: dict) -> pd.DataFrame:
    rows = []
    for billing in BILLINGS:
        # prepaid ignores pacing, so its lines are runway lengths instead
        lines = [("runway", r) for r in RUNWAYS] if billing == "prepaid" else [("pacing", p) for p in PACINGS]
        for kind, value in lines:
            for buffer in BUFFERS:
                s = score(daily, info, billing, buffer,
                          pacing=value if kind == "pacing" else None,
                          runway=value if kind == "runway" else None)
                rows.append({"billing": billing, "line": kind, "value": value, "buffer": buffer, **s})
                print(f"{billing:8s} {kind} {str(value):10s} buffer {buffer:.0%}: "
                      f"{s['overrun_rate']:.1%} overruns, ${s['usd_per_cycle']:.2f}/cycle")
    return pd.DataFrame(rows)


def plot(results: pd.DataFrame, run: str) -> None:
    fig, axes = plt.subplots(1, len(BILLINGS), figsize=(5.5 * len(BILLINGS), 5), squeeze=False)
    axes = axes[0]

    for ax, billing in zip(axes, BILLINGS):
        r = results[results["billing"] == billing]
        for value, g in r.groupby("value", sort=False):
            is_runway = g["line"].iloc[0] == "runway"
            color = RUNWAY_COLORS[value] if is_runway else PACING_COLORS[value]
            label = f"{value}-day runway" if is_runway else value
            ax.plot(g["overrun_rate"], g["usd_per_cycle"], color=color, linewidth=2,
                    marker="o", markersize=6, label=label)

            # buffer % next to each dot
            for _, row in g.iterrows():
                ax.annotate(f"{row['buffer']:.0%}", (row["overrun_rate"], row["usd_per_cycle"]),
                            textcoords="offset points", xytext=(5, 4), fontsize=8)

        n = r["cycles"].iloc[0]
        ax.set_title(f"{billing} ({n} cycles)", fontsize=11)
        ax.set_xlabel("Overrun rate (share of cycles the router pushed over the cap)")
        ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x:.1%}"))
        ax.set_xlim(left=0)
        ax.set_ylim(bottom=0)
        ax.grid(color="#e5e5e5", linewidth=0.8)
        ax.legend(title="Runway" if billing == "prepaid" else "Pacing", frameon=False, fontsize=9)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)

    axes[0].set_ylabel("Average \\$ the router used per cycle")
    fig.suptitle(f"Pacing vs safety buffer by billing type ({run})", fontsize=12)
    fig.tight_layout()
    out = ROOT / "figures" / f"buffer_sweep_{run}.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print("saved", out)


if __name__ == "__main__":
    run = sys.argv[1] if len(sys.argv) > 1 else "company_100"
    daily, info = load_donor_daily(run)
    results = sweep(daily, info)
    results.to_csv(ROOT / "data" / f"buffer_sweep_{run}.csv", index=False)
    plot(results, run)
