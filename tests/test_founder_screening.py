import json
import sys
from pathlib import Path

SCREENING_DIR = Path(__file__).parents[1] / "Scripts" / "01_Screening"
sys.path.insert(0, str(SCREENING_DIR))

from founder_screening.collectors import ArxivCollector
from founder_screening.company_collectors import YCombinatorCollector
from founder_screening.models import Candidate, CollectedCandidate, Evidence, FOUNDER_FEATURE_PATHS
from founder_screening.pipeline import CandidateStore, validate_candidate


ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>https://arxiv.org/abs/2601.12345</id>
    <published>2026-01-15T00:00:00Z</published>
    <title>Efficient inference for adaptive models</title>
    <summary>A reproducible systems method.</summary>
    <author><name>Maya Chen</name></author>
    <author><name>Jonas Weber</name></author>
    <category term="cs.LG"/>
  </entry>
</feed>"""


def evidence(candidate, source_id="source_1"):
    return Evidence(source_id, candidate.candidate_id, "manual_research", "test", f"https://example.test/{source_id}",
                    "2026-01-15", "2026-07-18T00:00:00Z", "metadata", "Public evidence")


def mark_verified_founder(candidate, source_id="source_1"):
    company_url = "https://example-company.test"
    evidence_url = "https://example.test/company/founders"
    candidate.company_name = "Example Company"
    candidate.company_url = company_url
    candidate.founder_role = "Co-Founder & CTO"
    candidate.founder_relationship_evidence_url = evidence_url
    candidate.founded_companies.append({
        "company_name": candidate.company_name,
        "company_url": company_url,
        "founder_role": candidate.founder_role,
        "relationship_evidence_url": evidence_url,
    })
    candidate.source_ids.append(source_id)
    return candidate


def test_arxiv_atom_parsing():
    records = ArxivCollector.parse_feed(ATOM)
    assert records[0]["authors"] == ["Maya Chen", "Jonas Weber"]
    assert records[0]["categories"] == ["cs.LG"]


def test_all_nullable_founder_fields_are_declared_missing():
    candidate = Candidate.from_name("Maya Chen", "arxiv")
    ev = evidence(candidate)
    mark_verified_founder(candidate, ev.source_id)
    assert not validate_candidate(candidate)
    assert set(FOUNDER_FEATURE_PATHS).issubset(candidate.missing_fields)
    assert all(value is None for value in candidate.founder_features.values())


def test_same_source_exact_name_deduplicates_and_keeps_evidence():
    first = Candidate.from_name("Maya Chen", "paper-1")
    first.discovery_sources.append("arxiv")
    second = Candidate.from_name("Maya Chen", "paper-2")
    second.discovery_sources.append("arxiv")
    store = CandidateStore()
    store.add(CollectedCandidate(first, [evidence(first, "s1")]))
    store.add(CollectedCandidate(second, [evidence(second, "s2")]))
    assert len(store.candidates) == 1
    assert len(store.evidence) == 2


def test_candidate_json_is_serializable():
    candidate = Candidate.from_name("Maya Chen", "github:maya")
    assert json.loads(json.dumps(candidate.to_dict()))["full_name"] == "Maya Chen"


def test_unverified_person_is_rejected_even_with_generic_evidence():
    candidate = Candidate.from_name("Maya Chen", "arxiv")
    candidate.source_ids.append("source_1")
    errors = validate_candidate(candidate)
    assert any("publicly verified founded company" in error for error in errors)
    assert any("company_name and company_url" in error for error in errors)


def test_yc_founder_card_parsing_requires_explicit_founder_role():
    page = """
    <section>
      <div><div>Active Founders</div></div>
      <div class="ycdc-card-new">
        <div class="hidden gap-4 md:flex">
          <div class="text-xl font-bold">Maya Chen</div>
          <div class="text-gray-600">Co-Founder &amp; CTO</div>
          <div class="prose">Researcher turned company builder.</div>
          <a href="https://www.linkedin.com/in/maya">LinkedIn</a>
        </div>
      </div>
      <div class="ycdc-card-new">
        <div class="hidden gap-4 md:flex">
          <div class="text-xl font-bold">Not A Founder</div>
          <div class="text-gray-600">Engineer</div>
        </div>
      </div>
    </section>
    """
    founders = YCombinatorCollector.parse_founders(page)
    assert founders == [{
        "full_name": "Maya Chen",
        "founder_role": "Co-Founder & CTO",
        "bio": "Researcher turned company builder.",
        "profile_urls": ["https://www.linkedin.com/in/maya"],
    }]