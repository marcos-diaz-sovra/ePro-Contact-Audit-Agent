"""CLI for the ePro Contact Audit Agent."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from epro.config import default_site, resolve_state
from epro.pipeline import run_batch


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Scrape public ePro contract pages and agency attachments, extract "
            "name/phone/email, and write an enriched workbook plus new-contact discrepancies."
        )
    )
    parser.add_argument(
        "--base-url",
        help="Public ePro origin or a sample PO URL, e.g. https://oregonbuys.gov",
    )
    parser.add_argument("--ext", choices=["sdo", "sda"], help="Page extension if it cannot be inferred")
    parser.add_argument("--input", "-i", required=True, help="CSV or Excel of contract IDs and/or URLs")
    parser.add_argument("--output", "-o", default="outputs", help="Output directory (default: outputs)")
    parser.add_argument(
        "--extract",
        choices=["regex", "llm", "hybrid"],
        default="hybrid",
        help="Contact extraction mode (default: hybrid)",
    )
    parser.add_argument("--limit", type=int, default=None, help="Process only the first N contracts")
    parser.add_argument("--delay", type=float, default=1.5, help="Seconds between contracts (default: 1.5)")
    parser.add_argument("--timeout", type=int, default=30, help="Page load timeout in seconds")
    parser.add_argument("--api-key", default="", help="Anthropic API key (or ANTHROPIC_API_KEY)")
    parser.add_argument("--resume", action="store_true", help="Skip contracts already saved from a previous run")
    parser.add_argument("--start-over", action="store_true", help="Ignore saved progress and start from the first contract")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    input_path = Path(args.input)
    if not input_path.is_file():
        print(f"Error: input file not found: {input_path}", file=sys.stderr)
        return 1

    state = default_site()
    if args.base_url:
        try:
            state = resolve_state(base_url=args.base_url, ext=args.ext)
        except (KeyError, ValueError, FileNotFoundError) as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

    api_key = args.api_key.strip() or os.environ.get("ANTHROPIC_API_KEY", "")
    if args.extract in ("llm", "hybrid") and not api_key:
        print(
            "Warning: no Anthropic API key set; hybrid/llm will fall back to regex-only names.",
            file=sys.stderr,
        )

    output_dir = Path(args.output)
    if args.start_over:
        from epro.checkpoint import clear_progress

        clear_progress(output_dir, input_path)

    async def _run():
        return await run_batch(
            state=state,
            input_path=input_path,
            output_dir=output_dir,
            extract_mode=args.extract,
            limit=args.limit,
            delay=args.delay,
            api_key=api_key or None,
            timeout=args.timeout,
            log=lambda msg: print(msg, flush=True),
            resume=bool(args.resume) and not args.start_over,
        )

    try:
        paths = asyncio.run(_run())
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print(f"\nDone. Workbook: {paths['xlsx']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
