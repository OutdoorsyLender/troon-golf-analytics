"""
Realistic Troon North (Scottsdale, AZ) tee time dataset generator for Power BI.

Assumptions baked in (more realistic):
- 18 holes only (no standard 9-hole option)
- Carts required (no walking)
- Premium desert pricing with strong seasonality (peak vs summer)
- Big AM premium + twilight discounts
- Higher utilization in peak season; more open inventory in summer
- Reasonable cancellations (summer heat + monsoon rain/wind)
- Two courses: Monument + Pinnacle with slightly different pricing

Output:
- TeeTimes.csv with columns ideal for Power BI:
  BookingID, Date, TeeTime, Course, Players,
  GreenFee, CartFee, FandB, TotalRevenue,
  BookingSource, CustomerType, Cancelled, Weather

Run:
  python generate_troon_north_teetimes.py
  python generate_troon_north_teetimes.py --rows 8000 --start 2025-01-01 --end 2025-12-31 --out TeeTimes.csv --seed 42
"""

from __future__ import annotations

import argparse
import csv
import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta, time
from typing import Dict, List, Tuple


# -----------------------------
# Config
# -----------------------------
@dataclass
class Config:
    rows: int
    start: date
    end: date
    out: str
    seed: int


COURSES = ["Monument", "Pinnacle"]

# Course pricing factors (Pinnacle often priced slightly higher)
COURSE_PRICE_FACTOR: Dict[str, float] = {
    "Monument": 1.00,
    "Pinnacle": 1.05,
}

# Cart required, usually included in "green fee" at many resort courses,
# but we keep it itemized for BI visuals (Cart Revenue vs Green Fee).
BASE_CART_PER_PLAYER = 34.00


# -----------------------------
# Helpers
# -----------------------------
def parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def weighted_choice(rng: random.Random, items: List[Tuple[str, float]]) -> str:
    total = sum(w for _, w in items)
    r = rng.random() * total
    upto = 0.0
    for v, w in items:
        upto += w
        if r <= upto:
            return v
    return items[-1][0]


def gaussian_int(rng: random.Random, mean: float, std: float, lo: int, hi: int) -> int:
    return int(clamp(round(rng.gauss(mean, std)), lo, hi))


def money(x: float) -> float:
    return round(x + 1e-9, 2)


def daterange(start: date, end: date) -> List[date]:
    days = (end - start).days
    return [start + timedelta(days=i) for i in range(days + 1)]


def is_weekend(d: date) -> bool:
    return d.weekday() >= 5  # Sat/Sun


def season_band(d: date) -> str:
    m = d.month
    if m in (11, 12, 1, 2, 3):
        return "Peak"
    if m in (4, 10):
        return "Shoulder"
    return "Summer"


def season_multiplier(d: date) -> float:
    band = season_band(d)
    if band == "Peak":
        return 1.35
    if band == "Shoulder":
        return 1.15
    return 0.75


def tee_time_blocks() -> List[time]:
    """
    Troon-ish tee time window. Adjust as desired.
    - Every 10 minutes
    - 6:30 to 15:50 (twilight still possible later, but keep it sane for portfolio)
    """
    times: List[time] = []
    start_dt = datetime(2000, 1, 1, 6, 30)
    end_dt = datetime(2000, 1, 1, 15, 50)
    step = timedelta(minutes=10)
    cur = start_dt
    while cur <= end_dt:
        times.append(cur.time())
        cur += step
    return times


def time_band(t: time) -> str:
    if t <= time(9, 0):
        return "AM Prime"
    if t <= time(12, 0):
        return "Midday"
    if t <= time(14, 0):
        return "PM"
    return "Twilight"


def pick_weather(rng: random.Random, d: date) -> str:
    m = d.month
    # Monsoon months: more wind/rain
    if m in (7, 8, 9):
        weights = [("Clear", 0.55), ("Cloudy", 0.20), ("Windy", 0.15), ("Rain", 0.10)]
    elif m in (12, 1, 2):
        weights = [("Clear", 0.70), ("Cloudy", 0.22), ("Windy", 0.07), ("Rain", 0.01)]
    else:
        weights = [("Clear", 0.75), ("Cloudy", 0.15), ("Windy", 0.09), ("Rain", 0.01)]
    return weighted_choice(rng, weights)


# -----------------------------
# Realistic demand + selection
# -----------------------------
def choose_date(rng: random.Random, days: List[date]) -> date:
    """
    Slightly biases selection toward peak season + weekends,
    because premium AZ golf is busier then.
    """
    d = rng.choice(days)
    # Add a gentle preference for Peak season
    if rng.random() < 0.55:
        peak_days = [x for x in days if season_band(x) == "Peak"]
        if peak_days:
            d = rng.choice(peak_days)
    # Add a gentle preference for weekends
    if rng.random() < 0.45:
        wknd = [x for x in days if is_weekend(x)]
        if wknd:
            d = rng.choice(wknd)
    return d


def choose_time(rng: random.Random, times: List[time], d: date) -> time:
    """
    More AM prime in Peak season; more midday in Summer.
    """
    band = season_band(d)

    if band == "Peak":
        weights = [("AM Prime", 0.55), ("Midday", 0.28), ("PM", 0.12), ("Twilight", 0.05)]
    elif band == "Shoulder":
        weights = [("AM Prime", 0.45), ("Midday", 0.30), ("PM", 0.18), ("Twilight", 0.07)]
    else:  # Summer
        weights = [("AM Prime", 0.35), ("Midday", 0.33), ("PM", 0.22), ("Twilight", 0.10)]

    chosen = weighted_choice(rng, weights)
    candidates = [t for t in times if time_band(t) == chosen]
    return rng.choice(candidates if candidates else times)


def choose_course(rng: random.Random) -> str:
    # Slightly more Pinnacle (just to diversify)
    return weighted_choice(rng, [("Monument", 0.48), ("Pinnacle", 0.52)])


def choose_customer_type(rng: random.Random, d: date) -> str:
    """
    CustomerType is a simplification for BI:
    - Public
    - Member (or similar)
    - ResortGuest
    - League/Group
    """
    if is_weekend(d):
        items = [("Public", 0.55), ("ResortGuest", 0.18), ("Member", 0.20), ("Group", 0.07)]
    else:
        items = [("Public", 0.48), ("ResortGuest", 0.16), ("Member", 0.26), ("Group", 0.10)]
    return weighted_choice(rng, items)


def choose_booking_source(rng: random.Random, customer_type: str, d: date) -> str:
    """
    More online booking for public; more phone for resort/group.
    """
    if customer_type == "Group":
        items = [("Sales/Events", 0.65), ("Phone", 0.25), ("Online", 0.10)]
    elif customer_type == "ResortGuest":
        items = [("Concierge", 0.45), ("Phone", 0.35), ("Online", 0.20)]
    elif customer_type == "Member":
        items = [("Online", 0.55), ("Phone", 0.25), ("Walk-up", 0.20)]
    else:  # Public
        if is_weekend(d):
            items = [("Online", 0.70), ("Phone", 0.18), ("Walk-up", 0.12)]
        else:
            items = [("Online", 0.62), ("Phone", 0.22), ("Walk-up", 0.16)]
    return weighted_choice(rng, items)


def cancellation_probability(d: date, t: time, weather: str) -> float:
    """
    Realistic-ish cancels:
    - Low baseline
    - Summer slightly higher
    - Rain/wind higher
    - Later tee times slightly higher
    """
    p = 0.035
    if season_band(d) == "Summer":
        p += 0.02
    if weather == "Rain":
        p += 0.10
    elif weather == "Windy":
        p += 0.05
    if t >= time(14, 0):
        p += 0.015
    return clamp(p, 0.01, 0.25)


def choose_players(rng: random.Random, customer_type: str, cancelled: bool) -> int:
    if cancelled:
        # keep a plausible count for analysis
        return gaussian_int(rng, mean=3.1, std=0.9, lo=1, hi=4)

    if customer_type == "Group":
        # groups tend to fill tee times
        return gaussian_int(rng, mean=3.8, std=0.5, lo=2, hi=4)
    if customer_type == "Member":
        return gaussian_int(rng, mean=3.2, std=0.7, lo=1, hi=4)
    if customer_type == "ResortGuest":
        return gaussian_int(rng, mean=3.4, std=0.7, lo=1, hi=4)
    return gaussian_int(rng, mean=3.0, std=0.8, lo=1, hi=4)


# -----------------------------
# Pricing (Premium, realistic-ish)
# -----------------------------
def base_green_fee_by_season(d: date) -> float:
    """
    Baseline 18-hole green fee before time band / course / randomness.
    Calibrated so that:
    - Peak AM Prime often lands ~250–320+
    - Summer can dip ~90–160, especially twilight
    """
    band = season_band(d)
    if band == "Peak":
        return 235.0
    if band == "Shoulder":
        return 185.0
    return 125.0


def time_price_multiplier(t: time) -> float:
    band = time_band(t)
    if band == "AM Prime":
        return 1.25
    if band == "Midday":
        return 1.08
    if band == "PM":
        return 0.92
    return 0.80  # Twilight


def customer_discount_multiplier(customer_type: str) -> float:
    # Members/groups/resort guests typically discounted vs public rack
    if customer_type == "Member":
        return 0.78
    if customer_type == "Group":
        return 0.82
    if customer_type == "ResortGuest":
        return 0.88
    return 1.00  # Public


def green_fee(rng: random.Random, d: date, t: time, course: str, customer_type: str) -> float:
    base = base_green_fee_by_season(d)
    base *= time_price_multiplier(t)
    base *= COURSE_PRICE_FACTOR.get(course, 1.00)
    base *= customer_discount_multiplier(customer_type)

    # small randomness to create realistic distribution
    base *= rng.uniform(0.93, 1.10)

    # clamp to realistic bounds
    # (public peak mornings can be high; summer twilight can be lower)
    return money(clamp(base, 85.0, 360.0))


def cart_fee(rng: random.Random, d: date, t: time, players: int, customer_type: str) -> float:
    # carts required, per-player cart component
    per_player = BASE_CART_PER_PLAYER

    # twilight tends to be discounted
    if time_band(t) == "Twilight":
        per_player *= 0.90

    # member/group sometimes has slightly reduced cart component
    if customer_type in ("Member", "Group"):
        per_player *= 0.92

    total = per_player * players * rng.uniform(0.96, 1.04)
    return money(clamp(total, 40.0, 200.0))


def fandb_spend(rng: random.Random, d: date, t: time, players: int, cancelled: bool, customer_type: str) -> float:
    if cancelled:
        return 0.0

    # attach rate: higher later in day, weekends, resort guests
    attach = 0.45
    if is_weekend(d):
        attach += 0.05
    if time_band(t) in ("PM", "Twilight"):
        attach += 0.07
    if customer_type == "ResortGuest":
        attach += 0.06
    if customer_type == "Member":
        attach += 0.02

    attach = clamp(attach, 0.10, 0.60)
    if rng.random() > attach:
        return 0.0

    per_player = rng.uniform(15.0, 60.0)
    total = per_player * players * rng.uniform(0.85, 1.20)
    return money(clamp(total, 0.0, 320.0))


# -----------------------------
# Generator
# -----------------------------
def generate(cfg: Config) -> None:
    rng = random.Random(cfg.seed)
    days = daterange(cfg.start, cfg.end)
    times = tee_time_blocks()

    fieldnames = [
        "BookingID",
        "Date",
        "TeeTime",
        "Course",
        "Players",
        "GreenFee",
        "CartFee",
        "FandB",
        "TotalRevenue",
        "BookingSource",
        "CustomerType",
        "Cancelled",
        "Weather",
    ]

    with open(cfg.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()

        for i in range(1, cfg.rows + 1):
            d = choose_date(rng, days)
            t = choose_time(rng, times, d)
            course = choose_course(rng)
            weather = pick_weather(rng, d)

            customer_type = choose_customer_type(rng, d)
            booking_source = choose_booking_source(rng, customer_type, d)

            cancel_p = cancellation_probability(d, t, weather)
            cancelled = rng.random() < cancel_p

            players = choose_players(rng, customer_type, cancelled)

            gf = green_fee(rng, d, t, course, customer_type)
            cf = cart_fee(rng, d, t, players, customer_type)
            fb = fandb_spend(rng, d, t, players, cancelled, customer_type)

            total = money(0.0 if cancelled else (gf * players + cf + fb))
            # Note: gf modeled as "per player green fee" (common in public golf pricing),
            # while cart fee is per player component aggregated into cf already.

            w.writerow(
                {
                    "BookingID": f"TN{i:06d}",
                    "Date": d.isoformat(),
                    "TeeTime": t.strftime("%H:%M:%S"),
                    "Course": course,
                    "Players": players,
                    "GreenFee": gf,         # per-player
                    "CartFee": cf,          # total for booking
                    "FandB": fb,            # total for booking
                    "TotalRevenue": total,  # total for booking
                    "BookingSource": booking_source,
                    "CustomerType": customer_type,
                    "Cancelled": "Yes" if cancelled else "No",
                    "Weather": weather,
                }
            )

    print(f"✅ Wrote {cfg.rows:,} rows to: {cfg.out}")
    print("Power BI tips:")
    print("- Set Date to Date type; TeeTime to Time type")
    print("- GreenFee is PER PLAYER; CartFee/FandB/TotalRevenue are PER BOOKING")
    print("- Build a Date table and relate Date[Date] -> TeeTimes[Date]")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=5000)
    ap.add_argument("--start", type=str, default="2025-01-01")
    ap.add_argument("--end", type=str, default="2025-12-31")
    ap.add_argument("--out", type=str, default="TeeTimes.csv")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    start = parse_date(args.start)
    end = parse_date(args.end)
    if end < start:
        raise SystemExit("Error: --end must be >= --start")

    cfg = Config(rows=args.rows, start=start, end=end, out=args.out, seed=args.seed)
    generate(cfg)


if __name__ == "__main__":
    main()
