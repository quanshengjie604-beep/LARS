"""Corpus-level derived quantities computed once over the whole seed cohort.

These are the §5 inputs that need cross-company context rather than a single
company's own data:
  * competitor_density   — company count in the same category
  * funding_climate_index — deal-volume-per-quarter proxy from batch cadence
  * network_centrality   — degree centrality in the sourcing graph (§6)

All are point-in-time aware: pass a cutoff to restrict to companies knowable then.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Optional

from .util import batch_to_date, clamp, sat

# Batch season -> ordinal quarter index within a year (for climate cadence).
_SEASON_Q = {"winter": 1, "spring": 2, "summer": 3, "fall": 4}


class MarketContext:
    def __init__(self, companies: list):
        self._companies = companies
        # category -> list of (founding_year, name)
        self._by_category: dict[str, list] = defaultdict(list)
        # (year, season) -> count  (deal-volume-per-quarter proxy)
        self._batch_counts: dict[tuple, int] = defaultdict(int)
        for c in companies:
            cat = self._category(c)
            self._by_category[cat].append((c.founding_year, c.name))
            q = self._quarter_key(c.batch)
            if q:
                self._batch_counts[q] += 1
        self._centrality = self._compute_centrality(companies)

    # --- categorisation ---
    @staticmethod
    def _category(c) -> str:
        return (c.subindustry or c.sector or "unknown").strip().lower()

    @staticmethod
    def _quarter_key(batch: Optional[str]):
        if not batch:
            return None
        parts = batch.strip().split()
        if len(parts) != 2 or not parts[1].isdigit():
            return None
        season = parts[0].lower()
        return (int(parts[1]), _SEASON_Q.get(season, 1))

    # --- §5.6 competitor_density = sat(n_competitors_in_category, 50) ---
    def competitor_density(self, c, cutoff_year: Optional[int] = None) -> tuple[Optional[float], int]:
        cat = self._category(c)
        peers = self._by_category.get(cat, [])
        if cutoff_year is not None:
            n = sum(1 for (fy, name) in peers if fy is not None and fy <= cutoff_year and name != c.name)
        else:
            n = sum(1 for (fy, name) in peers if name != c.name)
        if not peers:
            return None, 0
        return sat(n, 50), n

    # --- §5.6 funding_climate_index (per-quarter, trailing-8q normalised) ---
    def funding_climate_index(self, c) -> Optional[float]:
        key = self._quarter_key(c.batch)
        if not key:
            return None
        this_q = self._batch_counts.get(key, 0)
        # trailing 8 quarters strictly before this one
        year, q = key
        trailing = []
        yy, qq = year, q
        for _ in range(8):
            qq -= 1
            if qq == 0:
                qq = 4
                yy -= 1
            trailing.append(self._batch_counts.get((yy, qq), 0))
        peak = max(trailing + [this_q]) or 1
        return clamp(this_q / peak, 0.0, 1.0)

    # --- §6 sourcing graph: degree centrality ---
    def _compute_centrality(self, companies: list) -> dict[str, float]:
        """Nodes = companies; connected when they share a batch or a category.

        Degree = size of the union of a company's batch-mates and category-mates.
        Min-max normalised to [0, 1]. A cheap, deterministic first pass at the
        eigenvector-centrality intent of plan §6.
        """
        batch_members: dict[str, set] = defaultdict(set)
        cat_members: dict[str, set] = defaultdict(set)
        for c in companies:
            if c.batch:
                batch_members[c.batch].add(c.name)
            cat_members[self._category(c)].add(c.name)
        raw: dict[str, float] = {}
        for c in companies:
            neighbours: set = set()
            if c.batch:
                neighbours |= batch_members[c.batch]
            neighbours |= cat_members[self._category(c)]
            neighbours.discard(c.name)
            raw[c.name] = float(len(neighbours))
        if not raw:
            return {}
        lo, hi = min(raw.values()), max(raw.values())
        span = (hi - lo) or 1.0
        return {name: (v - lo) / span for name, v in raw.items()}

    def network_centrality(self, c) -> Optional[float]:
        return self._centrality.get(c.name)
