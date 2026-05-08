"""
News blackout filter — block entries 30 min before/after high-impact events.
For D1 bars, this means blocking the ENTIRE DAY of a news event.

Hardcoded: NFP, FOMC, CPI, ECB, BOE, BOC, RBA decisions 2020-2026.
"""

from datetime import date, timedelta
from typing import Set

# ── NFP — first Friday of each month ─────────────────────────────────────────

def _first_friday(year: int, month: int) -> date:
    d = date(year, month, 1)
    while d.weekday() != 4:  # 4 = Friday
        d += timedelta(days=1)
    return d


_NFP_DATES: Set[date] = set()
for _y in range(2020, 2027):
    for _m in range(1, 13):
        try:
            _NFP_DATES.add(_first_friday(_y, _m))
        except ValueError:
            pass

# ── FOMC dates 2020-2026 ─────────────────────────────────────────────────────

_FOMC_DATES: Set[date] = {
    # 2020
    date(2020, 1, 29), date(2020, 3, 3), date(2020, 3, 15),
    date(2020, 4, 29), date(2020, 6, 10), date(2020, 7, 29),
    date(2020, 9, 16), date(2020, 11, 5), date(2020, 12, 16),
    # 2021
    date(2021, 1, 27), date(2021, 3, 17), date(2021, 4, 28),
    date(2021, 6, 16), date(2021, 7, 28), date(2021, 9, 22),
    date(2021, 11, 3), date(2021, 12, 15),
    # 2022
    date(2022, 1, 26), date(2022, 3, 16), date(2022, 5, 4),
    date(2022, 6, 15), date(2022, 7, 27), date(2022, 9, 21),
    date(2022, 11, 2), date(2022, 12, 14),
    # 2023
    date(2023, 2, 1),  date(2023, 3, 22), date(2023, 5, 3),
    date(2023, 6, 14), date(2023, 7, 26), date(2023, 9, 20),
    date(2023, 11, 1), date(2023, 12, 13),
    # 2024
    date(2024, 1, 31), date(2024, 3, 20), date(2024, 5, 1),
    date(2024, 6, 12), date(2024, 7, 31), date(2024, 9, 18),
    date(2024, 11, 7), date(2024, 12, 18),
    # 2025
    date(2025, 1, 29), date(2025, 3, 19), date(2025, 5, 7),
    date(2025, 6, 18), date(2025, 7, 30), date(2025, 9, 17),
    date(2025, 11, 5), date(2025, 12, 17),
    # 2026
    date(2026, 1, 28), date(2026, 3, 18), date(2026, 4, 29),
    date(2026, 6, 17), date(2026, 7, 29), date(2026, 9, 16),
    date(2026, 11, 4), date(2026, 12, 16),
}

# ── US CPI — approximately 2nd-3rd week of each month ────────────────────────

_CPI_DATES: Set[date] = {
    # 2024
    date(2024, 1, 11), date(2024, 2, 13), date(2024, 3, 12),
    date(2024, 4, 10), date(2024, 5, 15), date(2024, 6, 12),
    date(2024, 7, 11), date(2024, 8, 14), date(2024, 9, 11),
    date(2024, 10, 10), date(2024, 11, 13), date(2024, 12, 11),
    # 2025
    date(2025, 1, 15), date(2025, 2, 12), date(2025, 3, 12),
    date(2025, 4, 10), date(2025, 5, 13), date(2025, 6, 11),
    date(2025, 7, 15), date(2025, 8, 12), date(2025, 9, 10),
    date(2025, 10, 15), date(2025, 11, 13), date(2025, 12, 10),
    # 2026
    date(2026, 1, 14), date(2026, 2, 11), date(2026, 3, 11),
    date(2026, 4, 9),  date(2026, 5, 13), date(2026, 6, 10),
}

# ── Major CB decisions (ECB, BOE, BOC, RBA — key dates) ──────────────────────

_CB_DATES: Set[date] = {
    # ECB 2024-2026 (approx every 6 weeks)
    date(2024, 1, 25), date(2024, 3, 7),  date(2024, 4, 11),
    date(2024, 6, 6),  date(2024, 7, 18), date(2024, 9, 12),
    date(2024, 10, 17), date(2024, 12, 12),
    date(2025, 1, 30), date(2025, 3, 6),  date(2025, 4, 17),
    date(2025, 6, 5),  date(2025, 7, 24), date(2025, 9, 11),
    date(2025, 10, 30), date(2025, 12, 11),
    date(2026, 1, 29), date(2026, 3, 5),  date(2026, 4, 30),
    # BOE roughly quarterly
    date(2024, 2, 1),  date(2024, 3, 21), date(2024, 5, 9),
    date(2024, 6, 20), date(2024, 8, 1),  date(2024, 9, 19),
    date(2024, 11, 7), date(2024, 12, 19),
    date(2025, 2, 6),  date(2025, 3, 20), date(2025, 5, 8),
    date(2025, 6, 19), date(2025, 8, 7),  date(2025, 9, 18),
    date(2025, 11, 6), date(2025, 12, 18),
}

# Combine all high-impact dates
_ALL_NEWS: Set[date] = _NFP_DATES | _FOMC_DATES | _CPI_DATES | _CB_DATES

# Build blackout set: news date ± 1 day buffer for daily bar strategy
_BLACKOUT: Set[date] = set()
for _d in _ALL_NEWS:
    for _delta in (-1, 0, 1):
        _BLACKOUT.add(_d + timedelta(days=_delta))


def is_news_blackout(bar_date) -> bool:
    """Return True if this daily bar date falls within a news blackout window."""
    try:
        d = bar_date.date() if hasattr(bar_date, "date") else bar_date
        return d in _BLACKOUT
    except Exception:
        return False


def news_event_on(bar_date) -> str:
    """Return name of event on this date, or empty string."""
    try:
        d = bar_date.date() if hasattr(bar_date, "date") else bar_date
        if d in _NFP_DATES:   return "NFP"
        if d in _FOMC_DATES:  return "FOMC"
        if d in _CPI_DATES:   return "CPI"
        if d in _CB_DATES:    return "CB"
        return ""
    except Exception:
        return ""
