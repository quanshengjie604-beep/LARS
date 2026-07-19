#!/usr/bin/env python3
"""Convert output.jl (JSON Lines) to a JSON array for the web frontend."""

import json
from pathlib import Path
from typing import List

DEFAULT_INPUT_PATH = Path("output.jl")
SCRAPY_OUTPUT_PATH = Path("scrapy-project/output.jl")
OUTPUT_PATH = Path("data/companies.json")


def load_records(path: Path) -> List[dict]:
    records: List[dict] = []
    with path.open("r", encoding="utf-8") as src:
        for line in src:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def main() -> None:
    if DEFAULT_INPUT_PATH.exists():
        input_path = DEFAULT_INPUT_PATH
    elif SCRAPY_OUTPUT_PATH.exists():
        input_path = SCRAPY_OUTPUT_PATH
    else:
        raise FileNotFoundError(
            "Could not find output.jl. Run the scraper first or specify the correct path."
        )

    records = load_records(input_path)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8") as dst:
        json.dump(records, dst, ensure_ascii=False, indent=2)
    print(f"Wrote {len(records)} records to {OUTPUT_PATH} from {input_path}")


if __name__ == "__main__":
    main()
