from __future__ import annotations

import argparse
import json

from .io import load_config, read_jsonl
from .pipeline import infer_all, train_all
from .validation import validate_features, validate_join


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="decision-engine")
    parser.add_argument("--config", default="config/default.yaml")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate")
    sub.add_parser("train")
    infer = sub.add_parser("infer")
    infer.add_argument("--output", default="artifacts/predictions.jsonl")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    if args.command == "validate":
        features = read_jsonl(config["data"]["training_features"])
        outcomes = read_jsonl(config["data"]["training_outcomes"])
        result = {
            "training_features": validate_features(features).model_dump(),
            "training_join": validate_join(features, outcomes).model_dump(),
        }
        if config["data"].get("inference_requests"):
            result["inference_requests"] = validate_features(read_jsonl(config["data"]["inference_requests"]), live=True).model_dump()
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0 if all(v["valid"] for v in result.values()) else 2
    if args.command == "train":
        print(json.dumps(train_all(config), ensure_ascii=False, indent=2))
        return 0
    result = infer_all(config, args.output)
    print(json.dumps({"output": args.output, "records": len(result)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
