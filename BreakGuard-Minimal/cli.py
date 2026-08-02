"""Argument parsing shared by all five pipeline scripts.

Each phase script calls parse_common_args() before doing anything else, so
--model/--data-root is the single way paths get resolved (see config.py) -
no more editing hardcoded path constants per model.
"""
import argparse
import os
import sys
from pathlib import Path

import config as pkg_config


def add_common_args(parser: argparse.ArgumentParser, include_model: bool = True):
    if include_model:
        parser.add_argument(
            "--model",
            required=True,
            choices=sorted(pkg_config.MODELS),
            help="Which LLM's generated tests to process.",
        )
    parser.add_argument(
        "--data-root",
        default=os.environ.get("DATA_ROOT"),
        help="Root folder containing FilteredDataset/... (or set the DATA_ROOT env var).",
    )
    parser.add_argument(
        "--results-root",
        default=os.environ.get("RESULTS_ROOT"),
        help="Root folder to write results/logs under (default: same as --data-root).",
    )
    parser.add_argument(
        "--results-namespace",
        default=os.environ.get("RESULTS_NAMESPACE", "Replication2"),
        help=(
            "Subfolder under --results-root that isolates this run's results/logs "
            "from the original experiment data already on that drive (default: Replication2)."
        ),
    )


def resolve_paths(args):
    if not args.data_root:
        sys.exit("ERROR: --data-root is required (or set the DATA_ROOT environment variable).")
    results_root = Path(args.results_root) if args.results_root else Path(args.data_root)
    return pkg_config.get_paths(args.model, Path(args.data_root), results_root, args.results_namespace)


def parse_common_args(description: str):
    parser = argparse.ArgumentParser(description=description)
    add_common_args(parser)
    args = parser.parse_args()
    return args, resolve_paths(args)
