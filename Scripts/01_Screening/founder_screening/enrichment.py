from __future__ import annotations

import json
import re
import unicodedata
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree as ET

from .models import stable_id


QS_EDITION = 2027
QS_SOURCE_URL = "https://www.qs.com/insights/qs-world-university-rankings-2027-results-table-excel"
QS_DOWNLOAD_URL = (
    "https://insights.qs.com/hubfs/Rankings%20Excel%20Reports/"
    "2027%20QS%20World%20University%20Rankings%201.1%20(For%20qs.com).xlsx"
)

_NS = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_DEGREE_PATTERNS = {
    "phd": re.compile(r"\b(?:PhD|DPhil|doctor(?:al|ate))\b", re.IGNORECASE),
    "master": re.compile(
        r"\b(?:MEng|MSc|MS|MBA|MA|MPH|MPhil|master(?:'s)?|masters)\b",
        re.IGNORECASE,
    ),
    "bachelor": re.compile(
        r"\b(?:BEng|BSc|BS|BA|AB|BBA|bachelor(?:'s)?|undergraduate degree)\b",
        re.IGNORECASE,
    ),
}
_NORMALIZED_DEGREE_PATTERNS = {
    "phd": re.compile(r"\b(?:phd|dphil|doctor(?:al|ate))\b"),
    "master": re.compile(r"\b(?:meng|msc|ms|mba|ma|mph|mphil|master(?:s)?)\b"),
    "bachelor": re.compile(r"\b(?:beng|bsc|bs|ba|ab|bba|bachelor(?:s)?|undergraduate degree)\b"),
}
_DEGREE_ORDER = {"bachelor": 1, "master": 2, "phd": 3}
_EXIT_STATUS = {
    "inactive": "closed_or_inactive",
    "acquired": "acquisition",
    "public": "ipo_or_public_listing",
}

# These are aliases, not substitute ranking data. Every alias resolves to a name
# that must exist in the official QS workbook before a rank is emitted.
_INSTITUTION_ALIASES = {
    "mit": "Massachusetts Institute of Technology (MIT)",
    "stanford": "Stanford University",
    "harvard": "Harvard University",
    "yale": "Yale University",
    "princeton": "Princeton University",
    "cornell": "Cornell University",
    "columbia": "Columbia University",
    "caltech": "California Institute of Technology (Caltech)",
    "uc berkeley": "University of California, Berkeley (UCB)",
    "berkeley": "University of California, Berkeley (UCB)",
    "ucb": "University of California, Berkeley (UCB)",
    "upenn": "University of Pennsylvania",
    "penn": "University of Pennsylvania",
    "cmu": "Carnegie Mellon University",
    "carnegie mellon": "Carnegie Mellon University",
    "imperial": "Imperial College London",
    "oxford": "University of Oxford",
    "cambridge": "University of Cambridge",
    "eth": "ETH Zurich",
    "epfl": "EPFL – École polytechnique fédérale de Lausanne",
    "ucl": "UCL",
    "nus": "National University of Singapore (NUS)",
    "ntu": "Nanyang Technological University, Singapore (NTU)",
    "tsinghua": "Tsinghua University",
    "georgia tech": "Georgia Institute of Technology",
    "uiuc": "University of Illinois Urbana-Champaign",
    "ucsd": "University of California, San Diego (UCSD)",
    "ucla": "University of California, Los Angeles (UCLA)",
    "usc": "University of Southern California",
    "nyu": "New York University (NYU)",
    "uw": "University of Washington",
    "uwaterloo": "University of Waterloo",
    "ucsb": "University of California, Santa Barbara (UCSB)",
}

_SKILL_TERMS = {
    "artificial intelligence": (r"\bartificial intelligence\b", r"\bAI/ML\b"),
    "machine learning": (r"\bmachine learning\b", r"\bML\b"),
    "deep learning": (r"\bdeep learning\b",),
    "large language models": (r"\blarge language models?\b", r"\bLLMs?\b", r"\bLMMs?\b"),
    "natural language processing": (r"\bnatural language processing\b", r"\bNLP\b"),
    "computer vision": (r"\bcomputer vision\b",),
    "reinforcement learning": (r"\breinforcement learning\b",),
    "diffusion models": (r"\bdiffusion models?\b",),
    "robotics": (r"\brobotics?\b", r"\bhumanoid robots?\b"),
    "data science": (r"\bdata science\b",),
    "statistics": (r"\bstatistics\b",),
    "bioinformatics": (r"\bbioinformatics\b",),
    "computational biology": (r"\bcomputational biology\b",),
    "computational neuroscience": (r"\bcomputational neuroscience\b",),
    "genetics": (r"\bgenetics\b",),
    "epigenetics": (r"\bepigenetics\b",),
    "next-generation sequencing": (r"\bnext[- ]gen(?:eration)? sequencing\b",),
    "medical imaging": (r"\bmedical imaging\b",),
    "cryptography": (r"\bcryptograph(?:y|ic)\b",),
    "secure hardware": (r"\bsecure hardware\b",),
    "confidential computing": (r"\bconfidential computing\b",),
    "fully homomorphic encryption": (r"\bfully homomorphic encryption\b", r"\bFHE\b"),
    "multi-party computation": (r"\bmulti[- ]party computation\b", r"\bMPC\b"),
    "distributed systems": (r"\bdistributed systems?\b",),
    "systems engineering": (r"\bsystems engineering\b",),
    "aerospace engineering": (r"\baerospace engineering\b",),
    "bioengineering": (r"\bbioengineering\b",),
    "electrical engineering": (r"\belectrical engineering\b",),
    "mechanical engineering": (r"\bmechanical engineering\b",),
    "computer science": (r"\bcomputer science\b",),
    "software development": (r"\bsoftware development\b", r"\bsoftware engineering\b"),
    "Python": (r"\bPython\b",),
    "Rust": (r"\bRust\b",),
    "JavaScript": (r"\bJavaScript\b",),
    "TypeScript": (r"\bTypeScript\b",),
    "Java": (r"\bJava\b",),
    "C++": (r"(?<!\w)C\+\+(?!\w)",),
    "CUDA": (r"\bCUDA\b",),
    "SQL": (r"\bSQL\b",),
    "React": (r"\bReact(?:\.js)?\b",),
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _cell_value(cell: ET.Element, shared_strings: list[str]) -> str | None:
    cell_type = cell.attrib.get("t")
    value = cell.find("main:v", _NS)
    if cell_type == "inlineStr":
        node = cell.find("main:is/main:t", _NS)
        return node.text if node is not None else None
    if value is None or value.text is None:
        return None
    if cell_type == "s":
        return shared_strings[int(value.text)]
    return value.text


def _published_rank(value: str) -> int | str:
    value = value.strip().replace("–", "-")
    if not re.fullmatch(r"\d+(?:-\d+|\+)?", value):
        raise ValueError(f"not a published QS rank: {value}")
    return int(value) if value.isdigit() else value


def _rank_sort_key(value: int | str) -> int:
    if isinstance(value, int):
        return value
    match = re.match(r"\d+", value)
    return int(match.group()) if match else 10**9


def load_qs_rankings_xlsx(path: Path) -> dict[str, int | str]:
    """Read institution -> official published overall rank from the QS workbook."""
    with zipfile.ZipFile(path) as archive:
        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in root.findall("main:si", _NS):
                shared_strings.append("".join(node.text or "" for node in item.findall(".//main:t", _NS)))
        sheet_name = "xl/worksheets/sheet1.xml"
        root = ET.fromstring(archive.read(sheet_name))
        rankings: dict[str, int | str] = {}
        for row in root.findall(".//main:sheetData/main:row", _NS):
            values: dict[str, str | None] = {}
            for cell in row.findall("main:c", _NS):
                reference = cell.attrib.get("r", "")
                column = re.match(r"[A-Z]+", reference)
                if column:
                    values[column.group()] = _cell_value(cell, shared_strings)
            rank_text, institution = values.get("B"), values.get("D")
            if not rank_text or not institution:
                continue
            if institution.strip().casefold() in {"institution", "name"}:
                continue
            try:
                rank = _published_rank(rank_text)
            except (TypeError, ValueError):
                continue
            rankings[institution.strip()] = rank
    if len(rankings) < 1000:
        raise ValueError(f"QS workbook appears incomplete: only {len(rankings)} ranked institutions")
    return rankings


class InstitutionMatcher:
    def __init__(self, rankings: dict[str, int | str]):
        self.rankings = rankings
        aliases: dict[str, str] = {}
        for institution in rankings:
            variants = {institution, re.sub(r"\s*\([^)]*\)\s*", " ", institution).strip()}
            for acronym in re.findall(r"\(([A-Z][A-Z0-9]{1,10})\)", institution):
                variants.add(acronym)
            if institution.startswith("The "):
                variants.add(institution[4:])
            for variant in variants:
                if len(_normalize(variant)) >= 3:
                    aliases[_normalize(variant)] = institution
        for alias, canonical in _INSTITUTION_ALIASES.items():
            if canonical in rankings:
                aliases[_normalize(alias)] = canonical
        self.aliases = aliases
        self._ordered_aliases = sorted(aliases, key=lambda item: (len(item.split()), len(item)), reverse=True)

    def find(self, text: str) -> list[dict[str, Any]]:
        normalized = f" {_normalize(text)} "
        found: list[dict[str, Any]] = []
        occupied: list[tuple[int, int]] = []
        for alias in self._ordered_aliases:
            pattern = f" {alias} "
            start = normalized.find(pattern)
            if start < 0:
                continue
            span = (start, start + len(pattern))
            if any(not (span[1] <= left or span[0] >= right) for left, right in occupied):
                continue
            canonical = self.aliases[alias]
            found.append({"institution": canonical, "qs_world_rank": self.rankings[canonical], "alias": alias, "position": start + 1})
            occupied.append(span)
        return found


def _normalize_degree_abbreviations(text: str) -> str:
    replacements = {
        r"\bPh\.\s*D\.?": "PhD",
        r"\bD\.\s*Phil\.?": "DPhil",
        r"\bM\.\s*Eng\.?": "MEng",
        r"\bM\.\s*Sc\.?": "MSc",
        r"\bM\.\s*S\.?": "MS",
        r"\bM\.\s*B\.\s*A\.?": "MBA",
        r"\bB\.\s*Eng\.?": "BEng",
        r"\bB\.\s*Sc\.?": "BSc",
        r"\bB\.\s*S\.?": "BS",
        r"\bB\.\s*A\.?": "BA",
    }
    for pattern, replacement in replacements.items():
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text


def _education_status(clause: str) -> tuple[str, bool | None]:
    if re.search(r"\b(?:drop(?:ped)? out|did not complete|left (?:the )?program)\b", clause, re.IGNORECASE):
        return "not_completed", False
    if re.search(r"\b(?:candidate|pursuing|studying|student|current(?:ly)?)\b", clause, re.IGNORECASE):
        return "in_progress", False
    if re.search(r"\b(?:hold(?:s)?|earned|received|graduat(?:ed|e)|completed|alumn(?:us|a|i|ae))\b", clause, re.IGNORECASE):
        return "completed", True
    return "reported_degree", None


def extract_education(text: str | None, source_url: str | None, matcher: InstitutionMatcher) -> dict[str, Any]:
    normalized = _normalize_degree_abbreviations(text or "")
    degree_records: list[dict[str, Any]] = []
    seen: set[tuple[str, str | None, str]] = set()
    for clause in re.split(r"(?<=[!?;|])\s+|(?<!\b[A-Z])\.\s+", normalized):
        levels = [level for level, pattern in _DEGREE_PATTERNS.items() if pattern.search(clause)]
        if not levels:
            continue
        institutions = matcher.find(clause)
        clause_key = _normalize(clause)
        level_positions = {
            level: [match.start() for match in _NORMALIZED_DEGREE_PATTERNS[level].finditer(clause_key)]
            for level in levels
        }
        selected: dict[str, list[dict[str, Any]]] = {}
        if len(institutions) <= 1:
            for level in levels:
                selected[level] = institutions or [{"institution": None, "qs_world_rank": None, "alias": None}]
        else:
            institution_positions = sorted(item["position"] for item in institutions)
            all_degree_positions = sorted(position for positions in level_positions.values() for position in positions)
            institution_list_is_ambiguous = bool(
                all_degree_positions
                and institution_positions[-1] < all_degree_positions[0]
                and len(levels) > 1
                and " and " in clause_key[institution_positions[0]:all_degree_positions[0]]
            )
            for level in levels:
                choices = []
                for institution in institutions:
                    distance = min(
                        (
                            position - institution["position"]
                            if institution["position"] <= position
                            else 50 + institution["position"] - position
                            for position in level_positions[level]
                        ),
                        default=10**9,
                    )
                    choices.append((distance, institution))
                choices.sort(key=lambda item: item[0])
                unambiguous = (
                    not institution_list_is_ambiguous
                    and choices
                    and (len(choices) == 1 or choices[0][0] + 12 < choices[1][0])
                )
                selected[level] = [choices[0][1]] if unambiguous else [
                    {"institution": None, "qs_world_rank": None, "alias": None}
                ]
        status, completed = _education_status(clause)
        for level in levels:
            for institution in selected[level]:
                key = (level, institution["institution"], status)
                if key in seen:
                    continue
                seen.add(key)
                degree_records.append({
                    "level": level,
                    "institution": institution["institution"],
                    "status": status,
                    "completed": completed,
                    "qs_world_rank": institution["qs_world_rank"],
                    "qs_edition": QS_EDITION if institution["qs_world_rank"] is not None else None,
                    "education_source_url": source_url,
                    "qs_source_url": QS_SOURCE_URL if institution["qs_world_rank"] is not None else None,
                    "evidence_excerpt": clause.strip()[:500],
                })
    levels_present = {item["level"] for item in degree_records}
    ranked = [item for item in degree_records if item["qs_world_rank"] is not None]
    best = min(ranked, key=lambda item: _rank_sort_key(item["qs_world_rank"])) if ranked else None
    highest = max(levels_present, key=lambda item: _DEGREE_ORDER[item]) if levels_present else None
    return {
        "degrees": degree_records,
        "has_bachelor": True if "bachelor" in levels_present else None,
        "has_master": True if "master" in levels_present else None,
        "has_phd": True if "phd" in levels_present else None,
        "highest_documented_level": highest,
        "best_qs_world_rank": best["qs_world_rank"] if best else None,
        "highest_ranked_institution": best["institution"] if best else None,
        "qs_edition": QS_EDITION if best else None,
        "interpretation": "null means not established by inspected public evidence, not false",
    }

def extract_exits(candidate: dict[str, Any]) -> dict[str, Any]:
    exits: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for company in candidate.get("founded_companies") or []:
        status = str(company.get("status") or "").casefold()
        outcome = _EXIT_STATUS.get(status)
        if not outcome:
            continue
        name = company.get("company_name") or candidate.get("company_name")
        key = (_normalize(str(name or "")), outcome)
        if key in seen:
            continue
        seen.add(key)
        exits.append({
            "company_name": name,
            "outcome": outcome,
            "source_status": company.get("status"),
            "evidence_url": company.get("relationship_evidence_url") or candidate.get("founder_relationship_evidence_url"),
            "evidence_excerpt": f"Public company profile status: {company.get('status')}",
        })
    bio = candidate.get("headline") or ""
    pattern = re.compile(
        r"\b(?:founded|co-founded)\s+([A-Z][A-Za-z0-9&.'’ -]{1,60}?)"
        r"(?:,|\s+which\s+|\s+that\s+|\s+and\s+)?(?:was\s+)?"
        r"(acquired by|sold to|went public|IPO(?:ed)?|shut down|closed)\b",
        re.IGNORECASE,
    )
    for match in pattern.finditer(bio):
        company_name = re.sub(r"\s+", " ", match.group(1)).strip(" ,")
        if _normalize(company_name).split(" ", 1)[0] in {"a", "an", "two", "three", "multiple", "several", "companies", "startups"}:
            continue
        phrase = match.group(2).casefold()
        if "acquired" in phrase or "sold" in phrase:
            outcome = "acquisition"
        elif "public" in phrase or "ipo" in phrase:
            outcome = "ipo_or_public_listing"
        else:
            outcome = "closed_or_inactive"
        key = (_normalize(company_name), outcome)
        if key in seen:
            continue
        seen.add(key)
        exits.append({
            "company_name": company_name,
            "outcome": outcome,
            "source_status": None,
            "evidence_url": candidate.get("founder_relationship_evidence_url"),
            "evidence_excerpt": match.group(0)[:500],
        })
    return {
        "verified_prior_exit_count": len(exits) if exits else None,
        "exits": exits,
        "count_interpretation": "verified minimum; null means no verified exit was found, not zero exits",
        "closed_or_inactive_counts_as_exit": True,
    }


def extract_skills(candidate: dict[str, Any]) -> dict[str, Any]:
    text = candidate.get("headline") or ""
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for skill, patterns in _SKILL_TERMS.items():
        match = next((re.search(pattern, text, re.IGNORECASE) for pattern in patterns if re.search(pattern, text, re.IGNORECASE)), None)
        if not match:
            continue
        key = skill.casefold()
        seen.add(key)
        items.append({
            "name": skill,
            "evidence_type": "public_bio_term",
            "evidence_url": candidate.get("founder_relationship_evidence_url"),
            "evidence_excerpt": text[max(0, match.start() - 80):match.end() + 80].strip(),
        })
    for repository in (candidate.get("open_source") or {}).get("owned_repositories", []):
        if not isinstance(repository, dict):
            continue
        languages = repository.get("languages") or []
        if isinstance(languages, dict):
            languages = languages.keys()
        for language in languages:
            key = str(language).casefold()
            if key in seen:
                continue
            seen.add(key)
            items.append({
                "name": str(language),
                "evidence_type": "github_language",
                "evidence_url": repository.get("html_url") or repository.get("url"),
                "evidence_excerpt": f"Language listed for public repository {repository.get('name') or ''}".strip(),
            })
    for field in (candidate.get("research") or {}).get("categories", []):
        key = str(field).casefold()
        if key in seen:
            continue
        seen.add(key)
        items.append({
            "name": str(field),
            "evidence_type": "paper_field",
            "evidence_url": None,
            "evidence_excerpt": "Field attached to a matched public paper record",
        })
    return {
        "items": items,
        "documented_skill_count": len(items),
        "count_interpretation": "unique explicitly documented terms; no skill is inferred from title or company sector",
    }


def _evidence_record(candidate: dict[str, Any], category: str, excerpt: str, source_url: str, metadata: dict[str, Any]) -> dict[str, Any]:
    source_id = stable_id("source", candidate["candidate_id"], category, source_url, excerpt[:80])
    return {
        "source_id": source_id,
        "candidate_id": candidate["candidate_id"],
        "source_type": "public_web" if category != "qs_rank" else "public_dataset",
        "document_name": "Founder public biography enrichment" if category != "qs_rank" else f"QS World University Rankings {QS_EDITION}",
        "source_uri": source_url,
        "document_date": "2026-06-18" if category == "qs_rank" else None,
        "collected_at": _utc_now(),
        "location": category,
        "evidence_excerpt": excerpt[:1000],
        "verification_status": "document_verified",
        "confidence": "high" if category in {"qs_rank", "company_status_exit"} else "medium",
        "raw_metadata": metadata,
    }


def enrich_candidate(candidate: dict[str, Any], matcher: InstitutionMatcher) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    source_url = candidate.get("founder_relationship_evidence_url")
    candidate["education"] = extract_education(candidate.get("headline"), source_url, matcher)
    candidate["career_history"] = extract_exits(candidate)
    candidate["skills"] = extract_skills(candidate)
    evidence: list[dict[str, Any]] = []

    for degree in candidate["education"]["degrees"]:
        if source_url and degree.get("evidence_excerpt"):
            evidence.append(_evidence_record(candidate, "education", degree["evidence_excerpt"], source_url, {
                "level": degree["level"], "institution": degree.get("institution"), "status": degree["status"],
            }))
        if degree.get("qs_world_rank") is not None:
            evidence.append(_evidence_record(
                candidate,
                "qs_rank",
                f"{degree['institution']} is ranked #{degree['qs_world_rank']} in the QS World University Rankings {QS_EDITION}.",
                QS_SOURCE_URL,
                {"institution": degree["institution"], "rank": degree["qs_world_rank"], "edition": QS_EDITION},
            ))

    for exit_item in candidate["career_history"]["exits"]:
        if exit_item.get("evidence_url"):
            evidence.append(_evidence_record(
                candidate,
                "company_status_exit",
                exit_item["evidence_excerpt"],
                exit_item["evidence_url"],
                {"company_name": exit_item.get("company_name"), "outcome": exit_item["outcome"]},
            ))

    if candidate["skills"]["items"] and source_url:
        evidence.append(_evidence_record(
            candidate,
            "skills",
            candidate.get("headline") or "",
            source_url,
            {"skills": [item["name"] for item in candidate["skills"]["items"]]},
        ))

    exit_count = candidate["career_history"]["verified_prior_exit_count"]
    if exit_count is not None:
        candidate.setdefault("founder_features", {})["founder_prior_exits"] = exit_count
        missing_path = "founder_features.founder_prior_exits"
        candidate["missing_fields"] = [path for path in candidate.get("missing_fields", []) if path != missing_path]

    existing_source_ids = set(candidate.get("source_ids") or [])
    for item in evidence:
        if item["source_id"] not in existing_source_ids:
            candidate.setdefault("source_ids", []).append(item["source_id"])
            existing_source_ids.add(item["source_id"])
    return candidate, evidence


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
            count += 1
    return count


def enrich_dataset(input_dir: Path, output_dir: Path, qs_path: Path) -> dict[str, Any]:
    candidates = read_jsonl(input_dir / "sourcing_candidates.jsonl")
    existing_evidence = read_jsonl(input_dir / "evidence_registry.jsonl")
    matcher = InstitutionMatcher(load_qs_rankings_xlsx(qs_path))
    enriched: list[dict[str, Any]] = []
    new_evidence: dict[str, dict[str, Any]] = {}
    counts = Counter()
    for candidate in candidates:
        item, evidence = enrich_candidate(candidate, matcher)
        enriched.append(item)
        for record in evidence:
            new_evidence[record["source_id"]] = record
        if item["education"]["degrees"]:
            counts["founders_with_education"] += 1
        if item["education"]["best_qs_world_rank"] is not None:
            counts["founders_with_qs_rank"] += 1
        if item["career_history"]["verified_prior_exit_count"] is not None:
            counts["founders_with_verified_exit"] += 1
        if item["skills"]["documented_skill_count"]:
            counts["founders_with_documented_skills"] += 1
    combined_evidence = {item["source_id"]: item for item in existing_evidence}
    combined_evidence.update(new_evidence)
    write_jsonl(output_dir / "sourcing_candidates.jsonl", enriched)
    write_jsonl(output_dir / "evidence_registry.jsonl", combined_evidence.values())
    report = {
        "generated_at": _utc_now(),
        "input_candidates": len(candidates),
        "output_candidates": len(enriched),
        "existing_evidence_records": len(existing_evidence),
        "new_evidence_records": len(new_evidence),
        "output_evidence_records": len(combined_evidence),
        "qs_edition": QS_EDITION,
        "qs_source_url": QS_SOURCE_URL,
        "qs_ranked_institutions_loaded": len(matcher.rankings),
        "coverage": dict(counts),
        "null_policy": "Absence of public evidence remains null; no unobserved education or exit is encoded as false/zero.",
    }
    (output_dir / "enrichment_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report
