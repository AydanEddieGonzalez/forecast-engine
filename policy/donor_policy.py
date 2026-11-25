from dataclasses import dataclass
from typing import Optional

# Fixed Cap = monthly, Excess Capcity = Leftover Tokens, Prepaid = balance that never resets
# Pacings = rate of allowence opening
KINDS = ("fixed_cap", "excess_capacity", "prepaid")
PACINGS = ("none", "linear", "backloaded")


@dataclass
class DonorPolicy:
    kind: str
    cycle_days: int = 30
    router_cap: Optional[float] = None
    donor_max: Optional[float] = None
    buffer_frac: float = 0.10
    pacing: str = "none"
    min_days_for_forecast: int = 3
    balance: Optional[float] = None   # prepaid only: starting balance
    runway_days: int = 30             # prepaid only: days of the donor's own spend to keep in reserve

    # checking DonorPolicy requirements
    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}")
        if self.pacing not in PACINGS:
            raise ValueError(f"pacing must be one of {PACINGS}")
        if self.cycle_days <= 0:
            raise ValueError("cycle_days must be positive")
        if self.kind == "fixed_cap" and (self.router_cap is None or self.router_cap < 0):
            raise ValueError("fixed_cap needs a non-negative router_cap")
        if self.kind == "excess_capacity" and (self.donor_max is None or self.donor_max <= 0):
            raise ValueError("excess_capacity needs a positive donor_max")
        if self.kind == "prepaid" and (self.balance is None or self.balance <= 0):
            raise ValueError("prepaid needs a positive balance")
        if self.runway_days <= 0:
            raise ValueError("runway_days must be positive")
        if not 0 <= self.buffer_frac < 1:
            raise ValueError("buffer_frac must be in [0, 1)")

# none = all tokens available at the start
# linear = tokens allowance unlocks over the cycle
# backloaded = unlocks with little tokens and progressively unlocks as time progresses
def pace_fraction(day_of_cycle: float, cycle_days: int, pacing: str) -> float:
    f = min(max(day_of_cycle / cycle_days, 0.0), 1.0)
    if pacing == "none":
        return 1.0
    if pacing == "linear":
        return f
    # backloaded
    return f ** 2

# Predicting donors future spending (spent so far / day) * (Rest of Cycle)
def forecast_donor_remaining(donor_used: float, day_of_cycle: float, cycle_days: int, donor_max: float, min_days: int, ) -> float:
    if day_of_cycle < min_days:
        return max(donor_max - donor_used, 0.0)
    
    rate = donor_used / day_of_cycle
    return rate * max(cycle_days - day_of_cycle, 0.0)

# Amount of money we have left for the billing cycle
# donor_forecast_remaining: optional override for the donor's expected spend over the rest of the cycle
def allowed_usage(policy: DonorPolicy, router_used: float, day_of_cycle: float, donor_used: float = 0.0, donor_forecast_remaining: Optional[float] = None,) -> float:

    if policy.kind == "prepaid":
        return prepaid_allowed(policy, router_used, day_of_cycle, donor_used, donor_forecast_remaining)

    pace = pace_fraction(day_of_cycle, policy.cycle_days, policy.pacing)

    if policy.kind == "fixed_cap":
        unlocked = policy.router_cap * pace
        return max(unlocked - router_used, 0.0)

    # excess_capacity
    if donor_forecast_remaining is None:
        donor_forecast_remaining = forecast_donor_remaining(
            donor_used, 
            day_of_cycle, 
            policy.cycle_days,
            policy.donor_max,
            policy.min_days_for_forecast

        )
    safe_limit = policy.donor_max * (1 - policy.buffer_frac)
    projected_donor_total = donor_used + donor_forecast_remaining
    projected_excess = max(safe_limit - projected_donor_total, 0.0)

    # hard stop, total spend must stay under the safe limit.
    hard_room = max(safe_limit - donor_used - router_used, 0.0)

    paced_room = max(projected_excess * pace - router_used, 0.0)
    return min(paced_room, hard_room)


# Prepaid: nothing resets, so keep a runway of the donor's own spend and only give what's above it
# donor_forecast_next: donor's expected spend over the next runway_days (from the heartbeat)
def prepaid_allowed(policy: DonorPolicy, router_used: float, day: float, donor_used: float,
                    donor_forecast_next: Optional[float] = None) -> float:
    if donor_forecast_next is None:
        # too early to know the donor's pace, give nothing
        if day < policy.min_days_for_forecast:
            return 0.0
        donor_forecast_next = donor_used / day * policy.runway_days

    left = policy.balance - donor_used - router_used
    reserve = donor_forecast_next + policy.buffer_frac * policy.balance
    return max(left - reserve, 0.0)


def donor_status(allowed: float, near_limit_threshold: float = 1.0) -> str:
    # We can update the limit threshold later
    if allowed <= 0:
        return "at_capacity"
    if allowed < near_limit_threshold:
        return "near_limit"
    return "available"

# To see whether a task's cost fits on 1 key
def can_route(policy, key_state, estimated_cost) -> bool:
    if estimated_cost < 0:
        raise ValueError("estimated cost can't be negative")
    return estimated_cost <= allowed_usage(policy, **key_state)
