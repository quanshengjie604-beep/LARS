"""Cohort-level market and sourcing-graph context."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
from typing import Iterable

from .models import Company, FundingRound
from .util import clamp, parse_date, sat


class CohortContext:
    def __init__(self, companies: Iterable[Company], rounds: Iterable[FundingRound]):
        self.companies = list(companies)
        self.rounds = list(rounds)
        self.sector_counts = Counter(
            sector.casefold() for company in self.companies for sector in company.sectors if sector
        )
        self.company_sectors = {
            company.startup_id: [sector.casefold() for sector in company.sectors] for company in self.companies
        }
        self.network = self._network_scores()

    def competitor_density(self, company: Company) -> float | None:
        counts = [self.sector_counts[sector.casefold()] for sector in company.sectors if sector]
        return sat(float(max(counts)), 50) if counts else None

    @staticmethod
    def _quarter(value: date) -> tuple[int, int]:
        return value.year, (value.month - 1) // 3 + 1

    def funding_climate(self, company: Company, cutoff: date) -> float | None:
        sectors = {sector.casefold() for sector in company.sectors if sector}
        if not sectors:
            return None
        counts: Counter[tuple[int, int]] = Counter()
        for round_ in self.rounds:
            event_date = parse_date(round_.date)
            if event_date is None or event_date > cutoff:
                continue
            if not sectors.intersection(self.company_sectors.get(round_.startup_id, [])):
                continue
            counts[self._quarter(event_date)] += 1
        current = self._quarter(cutoff)
        trailing: list[int] = []
        year, quarter = current
        for offset in range(8):
            absolute = year * 4 + quarter - 1 - offset
            trailing.append(counts[(absolute // 4, absolute % 4 + 1)])
        peak = max(trailing) if trailing else 0
        return clamp(trailing[0] / peak) if peak else None

    def _network_scores(self) -> dict[str, float | None]:
        """Low-confidence normalized degree over founder/company/accelerator edges."""
        graph: dict[str, set[str]] = defaultdict(set)
        founder_by_company: dict[str, list[str]] = {}
        for company in self.companies:
            company_node = "company:" + company.startup_id
            founders: list[str] = []
            for founder in company.founders:
                founder_node = "founder:" + founder.casefold().strip()
                graph[founder_node].add(company_node)
                graph[company_node].add(founder_node)
                founders.append(founder_node)
            founder_by_company[company.startup_id] = founders
            for accelerator in company.accelerators:
                accelerator_node = "accelerator:" + accelerator.casefold().strip()
                graph[company_node].add(accelerator_node)
                graph[accelerator_node].add(company_node)
        founder_degrees = {node: len(neighbors) for node, neighbors in graph.items() if node.startswith("founder:")}
        if not founder_degrees:
            return {company.startup_id: None for company in self.companies}
        low, high = min(founder_degrees.values()), max(founder_degrees.values())
        scores: dict[str, float | None] = {}
        for company in self.companies:
            values = [founder_degrees[node] for node in founder_by_company[company.startup_id]]
            if not values:
                scores[company.startup_id] = None
            elif high == low:
                scores[company.startup_id] = 0.5
            else:
                scores[company.startup_id] = sum((value - low) / (high - low) for value in values) / len(values)
        return scores
