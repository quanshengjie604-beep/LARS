"""
intake.py
=========

Reads in a founder / company questionnaire submission.

For now this is deliberately a dead end: ``read_profile_submission`` structures
and validates the incoming data (including the uploaded slide deck) and returns
a small confirmation dict, but **nothing downstream happens** with it yet. This
is the hook where you'll later kick off whatever processing the profile should
drive (persist it, parse the deck, feed the MC model, etc.).

The web layer (``server.py``) is responsible for parsing the multipart form and
reading the uploaded file's bytes; it hands this function plain Python values so
the read-in logic stays independent of FastAPI.
"""

from __future__ import annotations

from typing import Any


# Text fields we expect from the questionnaire (everything except the file).
PROFILE_FIELDS = (
    "first_name",
    "last_name",
    "linkedin",
    "github",
    "website",
    "company_name",
    "company_sector",
    "email",
)


def read_profile_submission(
    fields: dict[str, str],
    deck_filename: str | None = None,
    deck_bytes: bytes | None = None,
    deck_content_type: str | None = None,
) -> dict[str, Any]:
    """Read in one questionnaire submission.

    Parameters
    ----------
    fields:
        The text answers from the form. Missing keys are treated as empty.
    deck_filename / deck_bytes / deck_content_type:
        The uploaded slide deck, already read out of the multipart request by
        the server. ``deck_bytes is None`` means no file was attached.

    Returns
    -------
    dict
        A JSON-serialisable confirmation of what was received. Nothing is
        persisted or processed further at this stage.
    """
    # Normalise every expected text field to a stripped string.
    clean = {key: str(fields.get(key, "") or "").strip() for key in PROFILE_FIELDS}

    deck_info: dict[str, Any] = {"attached": deck_bytes is not None}
    if deck_bytes is not None:
        deck_info.update(
            filename=deck_filename,
            content_type=deck_content_type,
            size_bytes=len(deck_bytes),
            is_pdf=(
                (deck_content_type == "application/pdf")
                or (bool(deck_filename) and deck_filename.lower().endswith(".pdf"))
            ),
        )

    profile = {
        "name": f"{clean['first_name']} {clean['last_name']}".strip(),
        "email": clean["email"],
        "links": {
            "linkedin": clean["linkedin"],
            "github": clean["github"],
            "website": clean["website"],
        },
        "company": {
            "name": clean["company_name"],
            "sector": clean["company_sector"],
        },
        "deck": deck_info,
    }

    # Visible confirmation in the server console that the read-in happened.
    print(
        "[intake] read profile submission: "
        f"name={profile['name']!r}, email={profile['email']!r}, "
        f"company={profile['company']['name']!r}, deck={deck_info}"
    )

    return {
        "message": "Profile read in. (No downstream processing yet.)",
        "profile": profile,
    }


if __name__ == "__main__":
    # Quick manual check: `python intake.py`
    import json

    demo = read_profile_submission(
        {
            "first_name": "Ada",
            "last_name": "Lovelace",
            "linkedin": "https://linkedin.com/in/ada",
            "github": "https://github.com/ada",
            "website": "https://example.com",
            "company_name": "Analytical Engines",
            "company_sector": "Deep Tech",
            "email": "ada@example.com",
        },
        deck_filename="pitch.pdf",
        deck_bytes=b"%PDF-1.4 fake",
        deck_content_type="application/pdf",
    )
    print(json.dumps(demo, indent=2))
