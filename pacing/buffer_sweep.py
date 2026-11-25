import matplotlib.pyplot as plt
import pandas as pd
from pacing.pacing_sim import run_experiment, summarize

BUFFERS = [0.1, 0.2, 0.3]
PATTERNS = ["steady", "bursty", "ramp_up"]


# tests different buffer sizes instead of just 0.1
def sweep(n_seeds: int = 500) -> pd.DataFrame:
    frames = []
    for buffer_frac in BUFFERS:
        summary = summarize(run_experiment(n_seeds=n_seeds, buffer_frac=buffer_frac))
        summary = summary.reset_index()
        summary["buffer"] = buffer_frac
        frames.append(summary)

    return pd.concat(frames, ignore_index=True)


def plot_tradeoff(results: pd.DataFrame, pattern: str = "bursty") -> None:
    data = results[results["pattern"] == pattern]

    fig, ax = plt.subplots(figsize=(7, 5))
    for pacing, group in data.groupby("pacing"):
        ax.plot(group['overrun_rate'], group['router_used'], marker='o', label=pacing)
        for _, row in group.iterrows():
            ax.annotate(f"{int(row['buffer'] * 100)}%",
                        (row["overrun_rate"], row["router_used"]),
                        textcoords="offset points", xytext=(5, 5), fontsize=8)

    ax.set_xlabel("Overrun rate (share of cycles over the donor's max)")
    ax.set_ylabel("Average $ the router used per cycle")
    ax.set_title(f"Pacing vs safety buffer ({pattern} donor)")
    ax.legend(title="Pacing")

    plt.tight_layout()
    plt.savefig(f"tradeoff_{pattern}.png", dpi=150)
    plt.show()



def plot_tradeoff_grid(results: pd.DataFrame) -> None:
    fig, axes = plt.subplots(1, len(PATTERNS), figsize=(15, 4.5), sharex=True, sharey=True)

    for ax, pattern in zip(axes, PATTERNS):
        data = results[results["pattern"] == pattern]
        for pacing, group in data.groupby("pacing"):
            ax.plot(group["overrun_rate"], group["router_used"], marker="o", label=pacing)
            for _, row in group.iterrows():
                ax.annotate(f"{int(row['buffer'] * 100)}%",
                            (row["overrun_rate"], row["router_used"]),
                            textcoords="offset points", xytext=(5, 5), fontsize=8)


        ax.set_title(f"{pattern} donor")
        ax.set_xlabel("Overrun rate")

    axes[0].set_ylabel("Average $ the router used per cycle")
    axes[0].legend(title="Pacing")
    fig.suptitle("Pacing vs safety buffer, by donor pattern")

    fig.tight_layout()
    fig.savefig("figures/tradeoff_all.png", dpi=150)
    plt.close(fig)



if __name__ == "__main__":
    results = sweep()
    print(results)
    plot_tradeoff(results, "bursty")
    plot_tradeoff_grid(results)