import numpy as np
import pandas as pd

from policy.donor_policy import DonorPolicy, allowed_usage

# attributes
BILLING_CYCLE = 30
DONOR_MAX = 100.0
MEAN_DONOR_DAILY = 2.0
ROUTER_DEMAND = 2.0

def donor_usage(pattern: str, rng: np.random.Generator, days: int = BILLING_CYCLE) -> np.ndarray:
    # donor's own money spent per day
    m = MEAN_DONOR_DAILY
    if pattern == "steady":
        daily = rng.normal(m, 0.3 * m, days)

    elif pattern == "bursty":
        # spikes about 3 days every month
        daily = rng.normal(0.5 * m, 0.15 * m, days)
        spikes = rng.random(days) < 0.10
        daily += spikes * rng.normal(5 * m, m, days)

    elif pattern == "ramp_up":
        # quiet early and busy late
        trend = np.linspace(0.3, 1.7, days)
        daily = rng.normal(m * trend, 0.2 * m)

    else:
        raise ValueError(pattern)

    return np.clip(daily, 0, None)

def run_cycle(policy: DonorPolicy, donor_daily: np.ndarray) -> dict:
    router_used = 0.0
    donor_used = 0.0

    for day in range(len(donor_daily)):
        allowed = allowed_usage(policy, router_used=router_used, day_of_cycle=day, donor_used=donor_used)
        router_used += min(allowed, ROUTER_DEMAND)
        donor_used += donor_daily[day]

    total = router_used + donor_used

    return {
        "router_used": router_used,
        "donor_used": donor_used,
        "overrun": total > policy.donor_max,
        "unused": max(policy.donor_max - total, 0.0),
    }

def run_experiment(n_seeds: int = 500, buffer_frac: float = 0.10) -> pd.DataFrame:
    rows = []

    for pattern in ["steady", "bursty", "ramp_up"]:
        for seed in range(n_seeds):

            donor_daily = donor_usage(pattern, np.random.default_rng(seed))
            for pacing in ["none", "linear", "backloaded"]:
                policy = DonorPolicy(kind="excess_capacity", donor_max=DONOR_MAX, buffer_frac=buffer_frac, pacing=pacing)
                rows.append({"pattern": pattern, "pacing": pacing, "seed": seed, **run_cycle(policy, donor_daily)})

    return pd.DataFrame(rows)
 
 
def summarize(df: pd.DataFrame) -> pd.DataFrame:
    return (df.groupby(["pattern", "pacing"], sort=False).agg(router_used=("router_used", "mean"), overrun_rate=("overrun", "mean"), unused=("unused", "mean")).round({"router_used": 2, "unused": 2, "overrun_rate": 3}))
 
 
if __name__ == "__main__":
    print(summarize(run_experiment()))