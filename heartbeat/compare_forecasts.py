# Compares ways of predicting donor spending (the unlock) on the same simulated donors.
# For each method, how much the router unlocked in total, and how many overruns the router caused:
# cycles where the donor would have stayed under its cap alone, but went over once the router's share was added.
# Broken down by billing type (invoice, credits, prepaid).
# python compare_forecasts.py company_1000

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import matplotlib.pyplot as plt
import pandas as pd

from heartbeat.heart_beat import load_donor_daily, overruns, replay_donor

# (label, forecast, cushion) for each method to try
METHODS = [
    ("run rate (old)",         "run_rate", 0.0),
    ("weekday",                "weekday",  0.0),
    ("weekday + 0.5 cushion",  "weekday",  0.5),
    ("weekday + 1.0 cushion",  "weekday",  1.0),
]

BILLING_COLORS = {"invoice": "#2a78d6", "credits": "#1baf7a", "prepaid": "#eb6834"}

# Panels on the chart. Prepaid is left out: the forecast method barely moves it (the runway does).
# It still shows in the printed table.
PLOT_BILLINGS = ["invoice", "credits"]


# One row per method per billing type: $ unlocked and overruns the router caused
def score(daily: dict, info: dict, forecast: str, cushion: float) -> pd.DataFrame:
    rows = []
    for alias, spend in daily.items():
        m = info[alias]
        r = replay_donor(alias, spend, m["cap"], m["billing"], m["reset_day"],
                         forecast=forecast, cushion=cushion)
        o = overruns(r, m["cap"])
        rows.append({"billing": m["billing"], "router": o["router_took"],
                     "caused": o["caused_by_router"], "cycles": o["cycles"]})
    df = pd.DataFrame(rows)
    return df.groupby("billing").agg(donors=("router", "size"), cycles=("cycles", "sum"),
                                     router_unlocked_usd=("router", "sum"),
                                     overruns_caused_by_router=("caused", "sum")).reset_index()


# one dot per method -> x = overruns the router caused, y = $ unlocked, one line per billing type
def plot_tradeoff(results: pd.DataFrame, run: str, n_donors: int) -> None:
    billings = [b for b in PLOT_BILLINGS if b in set(results["billing"])]
    fig, axes = plt.subplots(1, len(billings), figsize=(5 * len(billings), 4.5), squeeze=False)

    for ax, b in zip(axes[0], billings):
        r = results[results["billing"] == b]
        ax.plot(r["overruns_caused_by_router"], r["router_unlocked_usd"],
                color=BILLING_COLORS.get(b, "#2a78d6"), linewidth=2, marker="o", markersize=8)

        # label each dot with its method name
        # alternate label above/below so close dots stay readable
        for k, (_, row) in enumerate(r.iterrows()):
            ax.annotate(row["method"], (row["overruns_caused_by_router"], row["router_unlocked_usd"]),
                        textcoords="offset points", xytext=(6, 5 if k % 2 == 0 else -12), fontsize=8)

        ax.set_title(f"{b} ({int(r['donors'].iloc[0])} donors)", fontsize=11)
        ax.set_xlabel("Overruns caused by the router")
        ax.set_xlim(-0.5, r["overruns_caused_by_router"].max() * 1.4 + 2)   # room for labels
        ax.set_ylim(bottom=0)
        ax.grid(color="#e5e5e5", linewidth=0.8)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)

    axes[0][0].set_ylabel("Total \\$ the router unlocked for recipients")
    fig.suptitle(f"Money vs. safety by billing type ({n_donors} donors, {run})", fontsize=12)
    fig.tight_layout()
    out = ROOT / "figures" / f"forecast_tradeoff_{run}.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print("saved", out)


if __name__ == "__main__":
    run = sys.argv[1] if len(sys.argv) > 1 else "company_100"
    daily, info = load_donor_daily(run)

    results = pd.concat([score(daily, info, f, c).assign(method=label) for label, f, c in METHODS])
    results["router_unlocked_usd"] = results["router_unlocked_usd"].round(2)

    print(f"{run}: {len(daily)} donors")
    print(results[["method", "billing", "donors", "cycles", "router_unlocked_usd",
                   "overruns_caused_by_router"]].to_string(index=False))

    total = results.groupby("method", sort=False)[["router_unlocked_usd", "overruns_caused_by_router"]].sum()
    print("\nAll billing types together:")
    print(total.round(2).to_string())
    plot_tradeoff(results, run, len(daily))
