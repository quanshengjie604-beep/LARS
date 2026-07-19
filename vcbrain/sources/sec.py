"""SEC Form D quarterly data-set ingestion.

The adapter consumes SEC-published quarterly ZIP files once for the full cohort,
which is substantially faster and kinder to EDGAR than one search per company.
It keeps offering targets distinct from cumulative amount sold.
"""

from __future__ import annotations

import asyncio
import csv
import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from ..evidence import make_evidence
from ..models import Company, Evidence, FundingRound, SourceRun
from ..util import normalized_name, parse_date, scaled_number, stable_id


def _key(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", value.upper())


def _normalized_row(row: dict[str, Any]) -> dict[str, str]:
    return {_key(str(key)): str(value or "").strip() for key, value in row.items()}


def _pick(row: dict[str, str], *keys: str) -> str | None:
    for key in keys:
        value = row.get(_key(key))
        if value:
            return value
    return None


def _money(value: str | None) -> int | float | None:
    if not value or value.casefold() in {"indefinite", "decline to disclose", "none"}:
        return None
    cleaned = re.sub(r"[^0-9.,-]", "", value)
    return scaled_number(cleaned) if cleaned and cleaned not in {"-", ".", ","} else None


def _read_table(archive: zipfile.ZipFile, suffix: str) -> list[dict[str, str]]:
    member = next((name for name in archive.namelist() if name.upper().endswith(suffix.upper())), None)
    if member is None:
        return []
    with archive.open(member) as raw:
        text = io.TextIOWrapper(raw, encoding="utf-8-sig", errors="replace", newline="")
        return [_normalized_row(row) for row in csv.DictReader(text, delimiter="\t")]


@dataclass(slots=True)
class FormDRecord:
    accession: str
    issuer_name: str
    cik: str | None
    filing_date: str
    first_sale_date: str | None
    amount_sold: int | float | None
    offering_target: int | float | None
    security_type: str | None
    submission_type: str | None


def parse_form_d_zip(path: Path) -> list[FormDRecord]:
    with zipfile.ZipFile(path) as archive:
        submissions = _read_table(archive, "FORMDSUBMISSION.tsv")
        issuers = _read_table(archive, "ISSUERS.tsv")
        offerings = _read_table(archive, "OFFERING.tsv")

    submission_by_accession = {
        value: row
        for row in submissions
        if (value := _pick(row, "ACCESSIONNUMBER", "ACCESSIONNO", "ACCESSION"))
    }
    offering_by_accession = {
        value: row
        for row in offerings
        if (value := _pick(row, "ACCESSIONNUMBER", "ACCESSIONNO", "ACCESSION"))
    }
    records: list[FormDRecord] = []
    for issuer in issuers:
        accession = _pick(issuer, "ACCESSIONNUMBER", "ACCESSIONNO", "ACCESSION")
        issuer_name = _pick(issuer, "ENTITYNAME", "ISSUERNAME", "NAME")
        if not accession or not issuer_name:
            continue
        submission = submission_by_accession.get(accession, {})
        offering = offering_by_accession.get(accession, {})
        filing_raw = _pick(submission, "FILINGDATE", "FILEDAT", "FILED")
        filing_date = parse_date(filing_raw)
        if filing_date is None:
            continue
        first_sale = parse_date(_pick(offering, "DATEOFFIRSTSALE", "FIRSTSALEDATE"))
        security_flags = [
            name.removeprefix("IS").removesuffix("TYPE")
            for name, value in offering.items()
            if name.startswith("IS") and value.casefold() in {"true", "1", "yes", "y"}
        ]
        records.append(
            FormDRecord(
                accession=accession,
                issuer_name=issuer_name,
                cik=_pick(issuer, "CIK", "ISSUERCIK"),
                filing_date=filing_date.isoformat(),
                first_sale_date=first_sale.isoformat() if first_sale else None,
                amount_sold=_money(_pick(offering, "TOTALAMOUNTSOLD", "AMOUNTSOLD")),
                offering_target=_money(_pick(offering, "TOTALOFFERINGAMOUNT", "OFFERINGAMOUNT")),
                security_type=", ".join(sorted(security_flags)) or None,
                submission_type=_pick(submission, "SUBMISSIONTYPE", "FORMTYPE"),
            )
        )
    return sorted(records, key=lambda item: (item.issuer_name.casefold(), item.filing_date, item.accession))


async def collect_form_d(
    companies: Iterable[Company],
    zip_paths: Iterable[Path],
    *,
    collected_at: str,
) -> tuple[list[FundingRound], list[Evidence], SourceRun]:
    paths = sorted({Path(path) for path in zip_paths})
    started_at = collected_at
    if not paths:
        return [], [], SourceRun(
            source="sec_form_d",
            status="skipped",
            started_at=started_at,
            finished_at=collected_at,
            skipped_reason="no quarterly Form D ZIP paths configured",
        )
    errors: list[str] = []

    async def parse(path: Path) -> list[FormDRecord]:
        try:
            return await asyncio.to_thread(parse_form_d_zip, path)
        except Exception as exc:
            errors.append(f"{path}: {exc}")
            return []

    parsed = await asyncio.gather(*(parse(path) for path in paths))
    form_records = [record for group in parsed for record in group]
    company_by_name: dict[str, Company] = {}
    for company in companies:
        for alias in [company.name, *company.aliases]:
            key = normalized_name(alias)
            if key:
                company_by_name.setdefault(key, company)

    rounds: list[FundingRound] = []
    evidence_records: list[Evidence] = []
    for filing in form_records:
        company = company_by_name.get(normalized_name(filing.issuer_name))
        if company is None:
            continue
        accession_compact = re.sub(r"\D", "", filing.accession)
        cik_compact = re.sub(r"\D", "", filing.cik or "")
        uri = (
            f"https://www.sec.gov/Archives/edgar/data/{int(cik_compact)}/{accession_compact}/"
            if cik_compact and accession_compact
            else "https://www.sec.gov/edgar/search/"
        )
        excerpt = (
            f"Form {filing.submission_type or 'D'} filed {filing.filing_date} by {filing.issuer_name}; "
            f"date of first sale={filing.first_sale_date or 'not disclosed'}; "
            f"total amount sold={filing.amount_sold if filing.amount_sold is not None else 'not disclosed'}; "
            f"offering target={filing.offering_target if filing.offering_target is not None else 'not disclosed'}."
        )
        evidence = make_evidence(
            startup_id=company.startup_id,
            channel="sec",
            source_type="regulatory_filing",
            document_name=f"SEC Form D {filing.accession}",
            excerpt=excerpt,
            source_uri=uri,
            document_date=filing.filing_date,
            collected_at=collected_at,
            location=f"accession {filing.accession}",
            verification_status="independently_verified",
            confidence="high",
        )
        evidence_records.append(evidence)
        event_date = filing.first_sale_date or filing.filing_date
        rounds.append(
            FundingRound(
                startup_id=company.startup_id,
                company_name=company.name,
                date=event_date,
                date_basis="first_sale" if filing.first_sale_date else "filing",
                stage=None,
                raw_stage=None,
                amount_usd=filing.amount_sold,
                amount_original=filing.amount_sold,
                currency="USD" if filing.amount_sold is not None else None,
                instrument=filing.security_type,
                filed_date=filing.filing_date,
                source_ids=[evidence.source_id],
                source_names=["SEC Form D"],
                source_urls=[uri],
                headline=excerpt,
                verification_status="independently_verified",
                confidence="medium",  # filing is verified; issuer-entered amount is not audited
            )
        )
    status = "partial" if errors and rounds else "error" if errors else "ok"
    return rounds, evidence_records, SourceRun(
        source="sec_form_d",
        status=status,
        started_at=started_at,
        finished_at=collected_at,
        requests=len(paths),
        records=len(rounds),
        errors=sorted(errors),
    )

