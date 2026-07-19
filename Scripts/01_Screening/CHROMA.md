# Chroma export and upload

The export keeps founder profiles and source evidence in separate collections so semantic retrieval does not lose provenance.

## Generate Chroma-ready files

```powershell
python Scripts/01_Screening/export_chroma.py
```

Outputs:

- `screening_handover/chroma/founders.chroma.jsonl` for `lars_founders`
- `screening_handover/chroma/founder_evidence.chroma.jsonl` for `lars_founder_evidence`
- `screening_handover/chroma/manifest.json` with counts and format details

Each JSONL record contains a unique string `id`, an embedding-ready `document`, flat Chroma-compatible `metadata`, and an evidence `uri`. No embeddings are precomputed; the collection embedding function handles the documents during upsert.

## Validate without uploading

```powershell
python Scripts/01_Screening/upload_chroma.py --dry-run
```

## Upload to Chroma Cloud

Do not place credentials in the repository. Set them in the local environment:

```powershell
$env:CHROMA_API_KEY = "..."
$env:CHROMA_TENANT = "..."
$env:CHROMA_DATABASE = "..."
python -m pip install -r Scripts/01_Screening/requirements-chroma.txt
python Scripts/01_Screening/upload_chroma.py --mode cloud
```

The uploader uses batches of 100 and `upsert`, so rerunning it is idempotent for the same record IDs.

## Local verification

```powershell
python Scripts/01_Screening/upload_chroma.py --mode local --local-path .chroma
```

The generated JSONL is intended for the Chroma SDK uploader. Dashboard file upload treats a file as content to chunk and does not preserve this record-level metadata structure.
