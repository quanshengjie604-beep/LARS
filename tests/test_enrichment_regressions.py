import sys
from pathlib import Path


SCREENING_DIR = Path(__file__).parents[1] / "Scripts" / "01_Screening"
sys.path.insert(0, str(SCREENING_DIR))

from founder_screening.enrichment import InstitutionMatcher, extract_education
from founder_screening.public_web_enrichment import founder_bio_snippets


def test_person_surname_ma_is_not_a_master_degree():
    matcher = InstitutionMatcher({"Stanford University": 2})
    result = extract_education("Taro Ma — GTM", "https://example.test/team", matcher)
    assert result["degrees"] == []


def test_short_individual_team_row_beats_shared_team_container():
    html = """
    <section>
      <div><span>Andy Li</span><span>Co-founder, CEO</span></div>
      <div><span>Raymond Huang</span><span>Co-founder, CTO</span></div>
      <div><span>Taro Ma</span><span>GTM</span></div>
    </section>
    """
    snippets = founder_bio_snippets(html, ["Andy Li", "Raymond Huang"])
    assert "Raymond Huang" not in snippets["Andy Li"]
    assert "Taro Ma" not in snippets["Raymond Huang"]
