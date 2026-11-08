"""Home show bench.

    python -m orng_led.show_bench --mode offline --out ../show-bench/runs/before
    python -m orng_led.show_bench --mode realtime --limit-s 20 --out ../show-bench/runs/before-realtime

Run from backend/. Audio and video stay in show-bench/ and out of the safe ZIP.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from orng_led.show_bench.session import ensure_corpus, run_session

REPO = Path(__file__).resolve().parents[3]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("offline", "realtime"), default="offline")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--limit-s", type=float, default=None)
    parser.add_argument("--corpus", type=Path, default=REPO / "show-bench" / "corpus")
    args = parser.parse_args()
    samples, rate, manifest = ensure_corpus(args.corpus)
    summary = run_session(
        samples,
        rate,
        mode=args.mode,
        out_dir=args.out,
        manifest=manifest,
        limit_s=args.limit_s,
        video=True,
    )
    print(json.dumps({key: summary[key] for key in ("mode", "frames", "program_s", "wall_s", "dark_s", "video")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
