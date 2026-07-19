"""Parallel orchestration: seed a list of startups -> enrich -> score -> write.

The core answer to "iterate this process for a list of startups": seed the whole
age-capped cohort from the accelerator directory, build corpus-level context
once, then fan out per-company enrichment across a thread pool (I/O-bound work,
near-linear speedup until per-host rate limits bite).
"""

from __future__ import annotations

import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Optional

from . import assemble, config
from .context import MarketContext
from .evidence import EvidenceRegistry
from .sources import yc
from .util import opportunity_id, snapshot_id


@dataclass
class RunConfig:
    mode: str = "both"                    # inference | training | both
    limit: Optional[int] = 25
    batches: Optional[list] = None
    sectors: Optional[list] = None
    geographies: Optional[list] = None
    include_status: Optional[list] = None
    top_only: bool = False
    workers: int = config.MAX_WORKERS
    use_github: bool = True
    use_llm: bool = True
    output_dir: str = config.OUTPUT_DIR


@dataclass
class CompanyResult:
    inference: Optional[dict] = None
    training_feature: Optional[dict] = None
    training_outcome: Optional[dict] = None
    errors: list = field(default_factory=list)


def _process(company, ev: EvidenceRegistry, ctx: MarketContext, rc: RunConfig) -> CompanyResult:
    res = CompanyResult()
    try:
        raw = assemble.fetch_raw(company, use_github=rc.use_github, use_llm=rc.use_llm)
        res.errors.extend(raw.errors)

        # Batch-era (point-in-time) snapshot: needed as training record and/or as
        # the prior axes that give the live snapshot a real trend.
        batch_date = company.founding_date
        batch_snap = None
        if batch_date:
            batch_snap = assemble.build_feature_snapshot(
                company, raw, ev, ctx,
                observation_date=batch_date, cutoff_date=batch_date, live=False, prior_axes=None)

        if rc.mode in ("training", "both") and batch_snap is not None:
            sid = company.startup_id
            stage = "seed"
            snap_id = snapshot_id(sid, batch_date, stage)
            oid = opportunity_id(sid, batch_date)
            res.training_feature = assemble.finalize_record(
                company, batch_snap, observation_date=batch_date, cutoff_date=batch_date, stage=stage)
            res.training_outcome = assemble.build_outcomes(
                company, ev, snap_id=snap_id, oid=oid, observation_date=batch_date)

        if rc.mode in ("inference", "both"):
            today = config.TODAY.isoformat()
            prior = batch_snap.axes if batch_snap else None
            live_snap = assemble.build_feature_snapshot(
                company, raw, ev, ctx,
                observation_date=today, cutoff_date=today, live=True, prior_axes=prior)
            stage = live_snap.features["financials"]["current_stage"] or "unknown"
            res.inference = assemble.finalize_record(
                company, live_snap, observation_date=today, cutoff_date=today, stage=stage)
    except Exception as e:  # never let one company kill the run
        res.errors.append(f"{company.name}: {e!r}")
    return res


def _write_jsonl(path: str, rows: list) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            row = {k: v for k, v in row.items() if not k.startswith("_")}
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def run(rc: RunConfig, *, progress=True) -> dict:
    os.makedirs(rc.output_dir, exist_ok=True)
    ev = EvidenceRegistry()

    # Seed the FULL age-capped cohort for context (competitor counts, centrality),
    # then process only the requested slice.
    cohort = yc.seed_companies(
        batches=rc.batches, sectors=rc.sectors, geographies=rc.geographies,
        include_status=rc.include_status, top_only=rc.top_only, limit=None)
    ctx = MarketContext(cohort)
    targets = cohort[: rc.limit] if rc.limit else cohort

    if progress:
        print(f"[vcbrain] cohort={len(cohort)} (age<= {config.MAX_AGE_YEARS}y, "
              f"founded>={config.MIN_FOUNDING_YEAR}) | processing {len(targets)} "
              f"| workers={rc.workers} | github={'on' if rc.use_github else 'off'} "
              f"| llm={'on' if (rc.use_llm and config.ANTHROPIC_API_KEY) else 'off'}", file=sys.stderr)

    results: list[CompanyResult] = []
    done = 0
    lock = threading.Lock()
    with ThreadPoolExecutor(max_workers=rc.workers) as pool:
        futures = {pool.submit(_process, c, ev, ctx, rc): c for c in targets}
        for fut in as_completed(futures):
            r = fut.result()
            results.append(r)
            with lock:
                done += 1
                if progress and (done % 5 == 0 or done == len(targets)):
                    print(f"[vcbrain] {done}/{len(targets)} companies enriched", file=sys.stderr)

    inference = [r.inference for r in results if r.inference]
    train_feat = [r.training_feature for r in results if r.training_feature]
    train_out = [r.training_outcome for r in results if r.training_outcome]
    errors = [e for r in results for e in r.errors]

    _write_jsonl(os.path.join(rc.output_dir, "inference_requests.jsonl"), inference)
    _write_jsonl(os.path.join(rc.output_dir, "training_features.jsonl"), train_feat)
    _write_jsonl(os.path.join(rc.output_dir, "training_outcomes.jsonl"), train_out)
    _write_jsonl(os.path.join(rc.output_dir, "evidence_registry.jsonl"), ev.records())
    _write_data_dictionary(os.path.join(rc.output_dir, "data_dictionary.md"), rc, len(cohort))

    summary = {
        "cohort": len(cohort),
        "processed": len(targets),
        "inference_requests": len(inference),
        "training_features": len(train_feat),
        "training_outcomes": len(train_out),
        "evidence_records": len(ev.records()),
        "errors": len(errors),
        "output_dir": rc.output_dir,
    }
    if progress:
        print(f"[vcbrain] DONE {json.dumps(summary)}", file=sys.stderr)
        if errors:
            print(f"[vcbrain] {len(errors)} non-fatal source errors (first 3): {errors[:3]}", file=sys.stderr)
    return summary


def _write_data_dictionary(path: str, rc: RunConfig, cohort_size: int) -> None:
    from .datadict import DATA_DICTIONARY
    with open(path, "w", encoding="utf-8") as f:
        f.write(DATA_DICTIONARY.format(
            cohort_size=cohort_size, max_age=config.MAX_AGE_YEARS,
            min_year=config.MIN_FOUNDING_YEAR, today=config.TODAY.isoformat(),
            mode=rc.mode))
