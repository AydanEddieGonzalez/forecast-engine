# Heartbeat: replays each donor key day by day and records what the router could use from it.
# One row per key per day. The router reads it: limit_left + reliability -> pulse score.

import sys
from pathlib import Path

# forecast-engine/ folder, so these scripts work from heartbeat/ or from forecast-engine/
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))   # lets "from policy... import" find the other folders
from datetime import date, timedelta

import numpy as np
import pandas as pd

from policy.donor_policy import DonorPolicy, allowed_usage, donor_status
from real_calls.log_real_calls import engine

START = date(2026, 10, 1)   # day 0 of the company_sim data

INVOICE_PACING = "backloaded"
CREDIT_PACING = "backloaded"

INVOICE_BUFFER = 0.10    # invoice / spend limit: going over costs the donor real money
CREDIT_BUFFER = 0.10     # expiring credits: same as invoice (5% caused 2.8% overruns in buffer_sweep)
PREPAID_BUFFER = 0.10    # prepaid: share of the starting balance never touched

RUNWAY_DAYS = 60         # prepaid: days of the donor's own spend kept in reserve (30 drained a third of balances, see notes)

ROUTER_DEMAND = 2.0 # pacing_sim
MIN_HISTORY = 8 # days it takes to set up

# How the unlock predicts the donor's spending for the rest of the cycle
# "run_rate" = average so far x days left
# "weekday"  = learns the donor's average for each weekday from its heartbeat
FORECAST = "weekday"

# Extra holdback for surprise spikes, 0 = none and 1 = hold back one full worst day
CUSHION = 0.5

# Reliability: size = given / (given + RELIABILITY_HALF), so $7.50 given -> 0.5
RELIABILITY_HALF = 7.5


# {donor: [daily $]} and {donor: {profile, provider, billing, reset_day, cap, events}} for one run
def load_donor_daily(run: str = "company_100") -> tuple[dict[str, list[float]], dict[str, dict]]:
    # gets every row for this run, sorted by donor, then by the day
    df = pd.read_sql(
        "SELECT key_alias, profile, provider, billing, reset_day, cap_usd, day, donor_usd, event "
        "FROM token_router.donor_daily WHERE sim_run = %(r)s ORDER BY key_alias, day",
        engine, params={"r": run},
    )

    if df.empty:
        raise ValueError(f"No rows for sim_run = {run}. Run company_sim.py first.")

    # runs made before billing existed: treat them as invoice that resets on the 1st
    df["billing"] = df["billing"].fillna("invoice")
    df["reset_day"] = df["reset_day"].fillna(1).astype(int)
    df["event"] = df["event"].fillna("")

    # goes thru one donor at a time, alias = "donor-0000", g = the donors daily rows
    daily, info = {}, {}
    for alias, g in df.groupby("key_alias"):
        daily[alias] = g["donor_usd"].astype(float).tolist()
        first = g.iloc[0]
        info[alias] = {"profile": first["profile"],
                       "provider": first["provider"],
                       "billing": first["billing"],
                       "reset_day": int(first["reset_day"]),
                       "cap": float(first["cap_usd"]),
                       "events": g["event"].tolist()}
    return daily, info


# Predicts how much the donor will spend from `day` up to (not including) `end_day`, using its heartbeat so far.
# Day numbers count from day 0 of the data, so weekdays line up across cycles.
def forecast_remaining(history: list[float], day: int, end_day: int,
                       forecast: str = FORECAST, cushion: float = CUSHION) -> float | None:
    if forecast == "run_rate" or day < 7:
        return None
    h = np.asarray(history, dtype=float)

    # average spend for each weekday so far
    by_weekday = [h[w::7].mean() for w in range(7)]

    # expected spend for each remaining day, based on which weekday it falls on
    expected = sum(by_weekday[d % 7] for d in range(day, end_day))

    # plus a cushion sized by the donor's worst day so far, for spikes the pattern can't predict
    return float(expected + cushion * h.max())


# Next date after `d` that falls on the donor's reset day
def next_reset(d: date, reset_day: int) -> date:
    y, m = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
    candidate = d.replace(day=reset_day)
    return candidate if candidate > d else date(y, m, reset_day)


# Billing cycles that START inside the data: [(first_day, cycle_length), ...]
# The cycle running when the data begins is skipped: the router doesn't know what the donor
# already spent in it, so it waits for the first reset.
def billing_cycles(n_days: int, reset_day: int) -> list[tuple[int, int]]:
    cycles = []
    for i in range(n_days):
        d = START + timedelta(days=i)
        if d.day == reset_day:
            cycles.append((i, (next_reset(d, reset_day) - d).days))
    return cycles


# records the heartbeat for each day with how much the router can use in the morning, and if that donor is available
# buffer / pacing / runway: optional overrides of the settings above (used by buffer_sweep.py)
def replay_donor(alias: str, daily: list[float], cap: float, billing: str = "invoice",
                 reset_day: int = 1, forecast: str = FORECAST, cushion: float = CUSHION,
                 buffer: float | None = None, pacing: str | None = None,
                 runway: int | None = None) -> list[dict]:
    n = len(daily)
    if buffer is None:
        buffer = {"credits": CREDIT_BUFFER, "prepaid": PREPAID_BUFFER}.get(billing, INVOICE_BUFFER)
    pacing = pacing or (CREDIT_PACING if billing == "credits" else INVOICE_PACING)   # prepaid ignores pacing
    runway = runway or RUNWAY_DAYS
    rows = []

    # which cycle each day belongs to: -1 = before the first reset (the router stays out)
    cycle_of = [-1] * n
    if billing == "prepaid":
        cycle_of = [0] * n
        starts = {0: n}
    else:
        starts = {}
        for c, (s, length) in enumerate(billing_cycles(n, reset_day)):
            starts[s] = length
            for i in range(s, min(s + length, n)):
                cycle_of[i] = c

    donor_used = router_used = 0.0
    policy = None
    cycle_start = 0

    for day, donor_today in enumerate(daily):

        # new billing cycle: both counters reset
        if day in starts:
            cycle_start = day
            donor_used = router_used = 0.0
            if billing == "prepaid":
                policy = DonorPolicy(kind="prepaid", balance=cap, buffer_frac=buffer,
                                     runway_days=runway)
            else:
                policy = DonorPolicy(kind="excess_capacity", cycle_days=starts[day], donor_max=cap,
                                     buffer_frac=buffer, pacing=pacing)

        if cycle_of[day] == -1:
            allowed, status = 0.0, "not_started"
        else:
            day_of_cycle = day - cycle_start
            if billing == "prepaid":
                # prepaid: forecast the donor's next RUNWAY_DAYS, not the rest of a cycle
                predicted = forecast_remaining(daily[:day], day, day + runway, forecast, cushion)
                pace_day = day_of_cycle
            else:
                # predict the rest of the cycle from the heartbeat so far (only days BEFORE today)
                predicted = forecast_remaining(daily[:day], day, cycle_start + policy.cycle_days,
                                               forecast, cushion)
                # credits expire at the end of the cycle, so the last day unlocks fully
                pace_day = day_of_cycle + 1 if billing == "credits" else day_of_cycle

            # start of the day: how much the router can unlock, given that prediction
            allowed = allowed_usage(policy, router_used=router_used, day_of_cycle=pace_day,
                                    donor_used=donor_used, donor_forecast_remaining=predicted)

            # "available", "near_limit", or "at_capacity"
            status = donor_status(allowed)

        # The router takes what it needs, up to the allowance
        given = min(allowed, ROUTER_DEMAND)

        rows.append({"key_alias": alias, "day": day, "billing": billing, "cycle": cycle_of[day],
                     "status": status, "allowed_usd": allowed, "given": given,
                     "donor_today": donor_today, "donor_used": donor_used, "router_used": router_used,
                     "limit_left": max(cap - donor_used - router_used, 0.0)})

        # End of the day: add today's spending before moving to tomorrow
        router_used += given
        donor_used += donor_today

    return rows


# Per cycle: did the donor go over on its own, or only once the router's share was added?
# Prepaid has one "cycle": over = the balance ran out.
def overruns(rows: list[dict], cap: float) -> dict:
    df = pd.DataFrame(rows)
    df = df[df["cycle"] >= 0]
    per = df.groupby("cycle").agg(donor=("donor_today", "sum"), router=("given", "sum"))
    caused = int(((per["donor"] <= cap) & (per["donor"] + per["router"] > cap)).sum())
    alone = int((per["donor"] > cap).sum())
    return {"cycles": len(per), "caused_by_router": caused, "over_alone": alone,
            "router_took": float(per["router"].sum())}


# Per-key reliability, using past days only:
# size = given / (given + 7.5) and consistent = share of weeks the key gave anything
def reliability(hb: pd.DataFrame) -> pd.DataFrame:
    hb = hb.sort_values(["key_alias", "day"]).copy()
    hb["rel"] = np.nan

    for day in range(MIN_HISTORY, hb["day"].max() + 1):
        past = hb[hb["day"] < day]
        totals = past.groupby("key_alias")["given"].sum()
        size = totals / (totals + RELIABILITY_HALF)

        # share of weeks so far where the donor gave anything
        weeks = past.assign(week=past["day"] // 7).groupby(["key_alias", "week"])["given"].sum()
        consistent = (weeks > 0).groupby("key_alias").mean()

        score = 0.4 * size + 0.6 * consistent
        today = hb["day"] == day
        hb.loc[today, "rel"] = hb.loc[today, "key_alias"].map(score)

    return hb


# Heartbeat for every donor in a company_sim run: one row per donor per day
def build_heartbeat_company(run: str = "company_100") -> pd.DataFrame:
    daily, info = load_donor_daily(run)
    rows = []

    for alias, spend in daily.items():
        meta = info[alias]
        for r, event in zip(replay_donor(alias, spend, meta["cap"], meta["billing"], meta["reset_day"]),
                            meta["events"]):
            r.update(profile=meta["profile"], provider=meta["provider"],
                     cap=meta["cap"], reset_day=meta["reset_day"], event=event)
            rows.append(r)
    return reliability(pd.DataFrame(rows))  # adds the "rel" column (reliability score)

if __name__ == "__main__":
    import os

    run = sys.argv[1] if len(sys.argv) > 1 else "company_100"
    hb = build_heartbeat_company(run)

    # convert to csv
    out = ROOT / "data" / f"heartbeat_{run}.csv"
    os.makedirs(out.parent, exist_ok=True)
    hb.to_csv(out, index=False)
    print(f"Saved {out} ({len(hb)} rows)")
