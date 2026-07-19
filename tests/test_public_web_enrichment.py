import sys
from pathlib import Path


SCREENING_DIR = Path(__file__).parents[1] / "Scripts" / "01_Screening"
sys.path.insert(0, str(SCREENING_DIR))

from founder_screening.public_web_enrichment import (
    _linkedin_anchor,
    discover_team_links,
    founder_bio_snippets,
)


def test_linkedin_is_used_as_normalized_anchor_only():
    candidate = {"profile_urls": ["https://www.linkedin.com/in/maya-chen/?trk=test"]}
    assert _linkedin_anchor(candidate) == (
        "maya-chen",
        "https://www.linkedin.com/in/maya-chen",
    )


def test_founder_snippet_is_scoped_to_nearest_bio_container():
    html = """
    <html><body>
      <nav>Maya Chen menu</nav>
      <section class="team"><article>
        <h2>Maya Chen</h2>
        <p>Maya earned a PhD in Robotics from Stanford University.</p>
      </article></section>
      <section><h2>Company technology</h2><p>Unrelated machine learning marketing.</p></section>
    </body></html>
    """
    snippets = founder_bio_snippets(html, ["Maya Chen", "Someone Else"])
    assert "PhD in Robotics" in snippets["Maya Chen"]
    assert "Unrelated machine learning marketing" not in snippets["Maya Chen"]
    assert "Someone Else" not in snippets


def test_team_link_discovery_stays_on_company_host():
    html = """
    <a href="/about">About us</a>
    <a href="/team">Leadership team</a>
    <a href="https://outside.test/team">Other team</a>
    """
    assert discover_team_links(html, "https://example.test/", limit=2) == [
        "https://example.test/team",
        "https://example.test/about",
    ]
