import json
import sys
from pathlib import Path

SCREENING_DIR = Path(__file__).parents[1] / "Scripts" / "01_Screening"
sys.path.insert(0, str(SCREENING_DIR))

from founder_screening.collectors import ArxivCollector
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


def test_arxiv_atom_parsing():
    records = ArxivCollector.parse_feed(ATOM)
    assert records[0]["authors"] == ["Maya Chen", "Jonas Weber"]
    assert records[0]["categories"] == ["cs.LG"]


def test_all_nullable_founder_fields_are_declared_missing():
    candidate = Candidate.from_name("Maya Chen", "arxiv")
    ev = evidence(candidate)
    candidate.source_ids.append(ev.source_id)
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
