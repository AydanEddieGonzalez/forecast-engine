import sys
from pathlib import Path

# forecast-engine/ folder, so these scripts work from heartbeat/ or from forecast-engine/
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from datetime import date, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import text

from real_calls.log_real_calls import engine

# Two months (Oct + Nov) so every donor shows at least one reset or a real drawdown
START = date(2026, 10, 1)
DAYS = 61
SEED = 1

PROVIDERS = ["anthropic", "openai", "google", "xai", "deepseek", "qwen", "amazon"]

# Simulating different donor types
# dow = Day of Week, which is a multiplier for how much is used for every day of the week
# month_end multiplies the last 5 days of each calendar month (grinding work at month end)
# trend is the growth across each month, 0.5 being 50% busier at the end than the start
# active = chance the donor uses the key at all on a given day (individuals skip lots of days)
# cap_median = typical monthly cap / balance in $
# standard_deviation = how spread out caps are around the median (of the log of the cap, so ~2/3 of donors land within median times or divided by e^standard_deviation), cap_min / cap_max = hard limits
#   budgets and billing odds come from public spending data and provider billing research (Nov 2026)
# billing = chance of each billing type for this profile
# hackathon = chance of hackathon
# viral = chance something they created takes off
# runaway = chance that an AI agents keeps retrying something and racks a big bill
 
PROFILES = {

    "enterprise":   {"weight": 0.25, "dow": [1.1, 1.1, 1.1, 1.0, 0.9, 0.1, 0.1],
                     "friday_spike": 1.0, "month_end": 1.4, "trend": 0.0, "noise": 0.15,
                     "active": 1.0, "cap_median": 10000, "standard_deviation": 1.2, "cap_min": 1000, "cap_max": 250000,
                     "billing": {"invoice": 0.75, "credits": 0.15, "prepaid": 0.10},
                     "hackathon": 0.05, "viral": 0.02, "runaway": 0.0},

    "startup":      {"weight": 0.22, "dow": [1.0, 1.0, 1.0, 1.0, 1.0, 0.4, 0.4],
                     "friday_spike": 3.0, "month_end": 1.0, "trend": 0.5, "noise": 0.30,
                     "active": 1.0, "cap_median": 1000, "standard_deviation": 0.9, "cap_min": 100, "cap_max": 25000,
                     "billing": {"invoice": 0.25, "credits": 0.25, "prepaid": 0.50},
                     "hackathon": 0.3, "viral": 0.1, "runaway": 0.0},

    "research_lab": {"weight": 0.13, "dow": [0.8, 0.8, 0.8, 0.8, 1.0, 0.7, 0.7],
                     "friday_spike": 4.0, "month_end": 1.0, "trend": 0.0, "noise": 0.40,
                     "active": 1.0, "cap_median": 300, "standard_deviation": 0.7, "cap_min": 50, "cap_max": 2000,
                     "billing": {"invoice": 0.40, "credits": 0.20, "prepaid": 0.40},
                     "hackathon": 0.2, "viral": 0.02, "runaway": 0.0},

    "agency":       {"weight": 0.10, "dow": [1.2, 1.1, 1.0, 1.0, 0.8, 0.0, 0.0],
                     "friday_spike": 1.0, "month_end": 1.8, "trend": 0.0, "noise": 0.25,
                     "active": 1.0, "cap_median": 800, "standard_deviation": 0.8, "cap_min": 200, "cap_max": 10000,
                     "billing": {"invoice": 0.30, "credits": 0.30, "prepaid": 0.40},
                     "hackathon": 0.05, "viral": 0.1, "runaway": 0.0},

    "individual":   {"weight": 0.20, "dow": [0.6, 0.6, 0.6, 0.7, 0.8, 1.5, 1.4],
                     "friday_spike": 1.0, "month_end": 1.0, "trend": 0.0, "noise": 0.60,
                     "active": 0.55, "cap_median": 60, "standard_deviation": 0.8, "cap_min": 10, "cap_max": 500,
                     "billing": {"invoice": 0.05, "credits": 0.40, "prepaid": 0.55},
                     "hackathon": 0.4, "viral": 0.02, "runaway": 0.0},

    "agent":        {"weight": 0.10, "dow": [1.0] * 7,
                     "friday_spike": 1.0, "month_end": 1.0, "trend": 0.0, "noise": 0.10,
                     "active": 1.0, "cap_median": 1500, "standard_deviation": 1.0, "cap_min": 30, "cap_max": 15000,
                     "billing": {"invoice": 0.45, "credits": 0.05, "prepaid": 0.50},
                     "hackathon": 0.0, "viral": 0.0, "runaway": 0.3},

}

# How big each spike event is: (low, high) multiplier on that day's normal spend
HACKATHON_MULT = (3, 6)   # Fri, Sat, Sun of one weekend
VIRAL_MULT = (5, 15)      # one big day, then half as much extra each of the next 3 days
RUNAWAY_MULT = (8, 15)    # one day an agent gets stuck in a loop

# prepaid balance = cap x this many months of normal spend (random per donor)
PREPAID_MONTHS = (1.2, 3.0)


# Donor's own $ per day before spikes, expected total is about frac * cap per calendar month
def daily_usage(p: dict, cap: float, frac: float, rng: np.random.Generator) -> np.ndarray:
    days = [START + timedelta(days=int(i)) for i in range(DAYS)]
    dow = np.array([d.weekday() for d in days])  # 0 = Monday, 4 = Friday
    month = np.array([d.month for d in days])
    usage = np.zeros(DAYS)

    # each calendar month gets its own month-end crunch and trend
    for m in np.unique(month):
        idx = np.where(month == m)[0]
        n = len(idx)
        pos = np.arange(n)
        shape = np.array(p["dow"])[dow[idx]]
        shape = shape * np.where(dow[idx] == 4, p["friday_spike"], 1.0)
        shape = shape * np.where(pos >= n - 5, p["month_end"], 1.0)
        shape = shape * (1 + p["trend"] * (pos / (n - 1) - 0.5))

        # days the donor doesn't touch the key at all
        shape = shape * (rng.random(n) < p["active"])
        if shape.sum() == 0:
            continue

        # one month's worth (ratio of usage)
        shape = shape / shape.sum()
        usage[idx] = cap * frac * shape * rng.lognormal(0, p["noise"], n)

    return usage


# Random spike events on top of normal spend. Returns new usage and a label per day.
def add_events(usage: np.ndarray, p: dict, rng: np.random.Generator) -> tuple[np.ndarray, list[str]]:
    usage = usage.copy()
    events = [""] * DAYS
    months = DAYS / 30
    typical = usage[usage > 0].mean() if (usage > 0).any() else 0.0

    # hackathon: a whole Fri to Sun weekend at 3 to 6x
    fridays = [i for i in range(DAYS - 2) if (START + timedelta(days=i)).weekday() == 4]
    for _ in range(rng.poisson(p["hackathon"] * months)):
        f = int(rng.choice(fridays))
        mult = rng.uniform(*HACKATHON_MULT)
        for i in range(f, f + 3):
            # a quiet day still gets hackathon spend (based on the donor's typical day)
            usage[i] = max(usage[i], typical) * mult
            events[i] = "hackathon"

    # viral: one big day, then it fades over 3 days
    for _ in range(rng.poisson(p["viral"] * months)):
        d = int(rng.integers(0, DAYS))
        mult = rng.uniform(*VIRAL_MULT)
        for k in range(4):
            if d + k < DAYS:
                usage[d + k] = max(usage[d + k], typical) * (1 + (mult - 1) * 0.5 ** k)
                events[d + k] = "viral"

    # runaway agent loop: one day at 8 to 15x
    for _ in range(rng.poisson(p["runaway"] * months)):
        d = int(rng.integers(0, DAYS))
        usage[d] = max(usage[d], typical) * rng.uniform(*RUNAWAY_MULT)
        events[d] = "runaway"

    return usage, events


def make_donors(n: int, rng: np.random.Generator) -> pd.DataFrame:
    names = list(PROFILES)
    weights = np.array([PROFILES[k]["weight"] for k in names])
    rows = []

    # how many times to sim different donors
    for i in range(n):
        # random profile, provider and billing type
        profile = str(rng.choice(names, p=weights / weights.sum()))
        p = PROFILES[profile]
        provider = str(rng.choice(PROVIDERS))
        bill_names = list(p["billing"])
        billing = str(rng.choice(bill_names, p=list(p["billing"].values())))

        # cap: monthly limit / credits (median depends on the profile)
        # frac: how much they will use of that cap (1.05 means they go over by a little bit)
        cap = float(np.clip(rng.lognormal(np.log(p["cap_median"]), p["standard_deviation"]), p["cap_min"], p["cap_max"]))
        frac = float(rng.uniform(0.4, 1.05))

        # invoice and credits reset on the donor's billing day; prepaid never resets
        reset_day = int(rng.integers(1, 29)) if billing != "prepaid" else 0

        # monthly spend shape, then spikes
        usage = daily_usage(p, cap, frac, rng)
        usage, events = add_events(usage, p, rng)

        # prepaid: cap_usd stores the starting balance instead of a monthly limit
        cap_usd = cap * rng.uniform(*PREPAID_MONTHS) if billing == "prepaid" else cap

        # appends to rows
        for day in range(DAYS):
            rows.append({"key_alias": f"donor-{i:04d}", "profile": profile, "provider": provider,
                         "billing": billing, "reset_day": reset_day,
                         "cap_usd": round(cap_usd, 2), "day": START + timedelta(days=day),
                         "donor_usd": float(usage[day]), "event": events[day]})

    return pd.DataFrame(rows)

# add to database
def write(df: pd.DataFrame, run: str) -> None:
    df = df.assign(sim_run=run)
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS token_router.donor_daily (
                sim_run TEXT, key_alias TEXT, profile TEXT, provider TEXT,
                cap_usd NUMERIC(10, 2), day DATE, donor_usd NUMERIC(12, 6))"""))
        # new columns, added to the existing table if it was made before this version
        conn.execute(text("""
            ALTER TABLE token_router.donor_daily
                ADD COLUMN IF NOT EXISTS billing TEXT,
                ADD COLUMN IF NOT EXISTS reset_day INT,
                ADD COLUMN IF NOT EXISTS event TEXT"""))
        conn.execute(text("DELETE FROM token_router.donor_daily WHERE sim_run = :r"), {"r": run})
    df.to_sql("donor_daily", engine, schema="token_router", if_exists="append", index=False)

# run program
if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    df = make_donors(n, np.random.default_rng(SEED))
    write(df, f"company_{n}")

    totals = df.groupby(["key_alias", "profile", "billing"])["donor_usd"].sum().reset_index()
    print(totals.groupby(["profile", "billing"]).agg(donors=("key_alias", "count"),
                                                     avg_spend=("donor_usd", "mean")).round(2))
    print("spike days:", df.loc[df["event"] != "", "event"].value_counts().to_dict())
    print(f"Wrote {len(df)} rows as sim_run = company_{n}")
