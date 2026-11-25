# Simulates a month of calls for different donor types and writes them to token_router.key_usage

from datetime import datetime, timedelta, timezone

import numpy as np
from sqlalchemy import text

from real_calls.log_real_calls import engine, get_prices
from pacing.pacing_sim import donor_usage

MONTH_START = datetime(2026, 10, 1, tzinfo=timezone(timedelta(hours=-7)))
DAYS = 31
SEED = 42

# Typical request size (coding-agent style)
AVG_IN = 8000
AVG_OUT = 800
SIGMA = 0.8
MU_IN = np.log(AVG_IN) - SIGMA**2 / 2
MU_OUT = np.log(AVG_OUT) - SIGMA**2 / 2
ERROR_RATE = 0.01

# More traffic in the daytime than overnight
HOUR_W = np.array([1] * 7 + [3] * 3 + [6] * 8 + [4] * 4 + [2] * 2, dtype=float)
HOUR_P = HOUR_W / HOUR_W.sum()

# monthly_max = donors own spending cap
DONORS = [
    {"alias": "sim-anthropic", "provider": "anthropic", "model": "claude-haiku-5-5",
     "price_model": "claude-haiku-5.5", "pattern": "steady", "monthly_max": 30},

    {"alias": "sim-openai", "provider": "openai", "model": "gpt-4o-mini",
     "price_model": "gpt-4o-mini", "pattern": "bursty", "monthly_max": 50},

    {"alias": "sim-google", "provider": "google", "model": "gemini-3.1-flash-lite",
     "price_model": "gemini-3.1-flash-lite", "pattern": "ramp_up", "monthly_max": 100},
]


def make_row(d, ts, tin, tout, cost, latency, status):
    return {"ts": ts, "key_alias": d["alias"], "provider": d["provider"], "model": d["model"],
            "input_tokens": tin, "output_tokens": tout, "cost_usd": cost,
            "latency_ms": latency, "status": status}


def simulate_donor(d: dict, rng: np.random.Generator) -> list[dict]:
    in_p, out_p = get_prices(d["provider"], d["price_model"])
    avg_cost = (AVG_IN * in_p + AVG_OUT * out_p) / 1_000_000

    # daily dollar the donor spends, scaled from pacing_sim's $100 donor to this donor's max
    daily = donor_usage(d["pattern"], rng, days=DAYS) * d["monthly_max"] / 100.0

    spent, rows = 0.0, []
    for day in range(DAYS):
        n = rng.poisson(daily[day] / avg_cost)
        hours = rng.choice(24, size=n, p=HOUR_P)
        secs = np.sort(hours * 3600 + rng.integers(0, 3600, size=n))

        for s in secs:
            ts = MONTH_START + timedelta(days=day, seconds=int(s))
            latency = int(rng.lognormal(np.log(1000), 0.4))

            if spent >= d["monthly_max"]:
                rows.append(make_row(d, ts, 0, 0, 0.0, latency, "429"))
                continue
            if rng.random() < ERROR_RATE:
                rows.append(make_row(d, ts, 0, 0, 0.0, latency, "error"))
                continue

            tin = int(rng.lognormal(MU_IN, SIGMA))
            tout = int(rng.lognormal(MU_OUT, SIGMA))
            cost = (tin * in_p + tout * out_p) / 1_000_000
            spent += cost
            rows.append(make_row(d, ts, tin, tout, cost, latency, "ok"))

    return rows


def write_rows(rows: list[dict]) -> None:
    with engine.begin() as conn:
        # rerunning replaces the old simulation instead of stacking a second copy
        conn.execute(text("DELETE FROM token_router.key_usage WHERE source = 'sim' AND key_alias = ANY(:a)"),
                     {"a": [d["alias"] for d in DONORS]})
        conn.execute(text("""
            INSERT INTO token_router.key_usage
              (ts, key_alias, provider, model, input_tokens, output_tokens, cost_usd, latency_ms, status, source)
            VALUES
              (:ts, :key_alias, :provider, :model, :input_tokens, :output_tokens, :cost_usd, :latency_ms, :status, 'sim')
        """), rows)


if __name__ == "__main__":
    rng = np.random.default_rng(SEED)
    all_rows = []
    for d in DONORS:
        rows = simulate_donor(d, rng)
        spent = sum(r["cost_usd"] for r in rows)
        n429 = sum(r["status"] == "429" for r in rows)
        print(f"{d['alias']:24} {d['pattern']:8} calls={len(rows):6}  spent=${spent:7.2f} "
              f"of ${d['monthly_max']}  ({spent / d['monthly_max']:.0%})  429s={n429}")
        all_rows.extend(rows)

    write_rows(all_rows)
    print(f"Wrote {len(all_rows)} simulated rows for October")