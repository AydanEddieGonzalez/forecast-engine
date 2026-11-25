# Charts token_router.key_usage: cumulative spend per donor across the month vs that donor's cap.

import sys

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd

from real_calls.log_real_calls import engine
from october_sim.simulate_month import DONORS

COLORS = ["#2a78d6", "#eb6834", "#1baf7a"]  # fixed order: donor 1, 2, 3
CAPS = {d["alias"]: d["monthly_max"] for d in DONORS}


def load(source: str) -> pd.DataFrame:
    df = pd.read_sql(
        "SELECT ts, key_alias, cost_usd, status FROM token_router.key_usage WHERE source = %(s)s ORDER BY ts",
        engine, params={"s": source},
    )
    df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert("America/Los_Angeles")
    return df


def plot_cumulative(df: pd.DataFrame, source: str) -> None:
    aliases = sorted(df["key_alias"].unique())
    fig, axes = plt.subplots(1, len(aliases), figsize=(5 * len(aliases), 4), squeeze=False)

    for ax, alias, color in zip(axes[0], aliases, COLORS):
        d = df[df["key_alias"] == alias]
        daily = d.set_index("ts")["cost_usd"].astype(float).resample("D").sum()
        cum = daily.cumsum()

        ax.plot(cum.index, cum.values, color=color, linewidth=2)
        cap = CAPS.get(alias)
        if cap:
            ax.axhline(cap, color="#888888", linestyle="--", linewidth=1)
            ax.text(cum.index[0], cap, f" cap ${cap}", va="bottom", fontsize=8, color="#555555")
            ax.set_ylim(0, cap * 1.15)

        n429 = int((d["status"] == "429").sum())
        ax.set_title(f"{alias}  (${cum.iloc[-1]:.2f} spent, {n429} 429s)", fontsize=10)
        ax.set_xlabel("Date")
        ax.xaxis.set_major_locator(mdates.WeekdayLocator(byweekday=mdates.MO))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
        ax.tick_params(axis="x", labelsize=8)
        ax.grid(axis="y", color="#e5e5e5", linewidth=0.8)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)

    axes[0][0].set_ylabel("Cumulative $ spent")
    fig.suptitle(f"Donor spend over the month ({source} data)")
    fig.tight_layout()
    out = f"figures/usage_{source}.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print("saved", out)


if __name__ == "__main__":
    source = sys.argv[1] if len(sys.argv) > 1 else "sim"
    df = load(source)
    if df.empty:
        print(f"No rows with source = '{source}'")
    else:
        plot_cumulative(df, source)
