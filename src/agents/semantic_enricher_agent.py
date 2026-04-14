from __future__ import annotations

import shutil
from pathlib import Path

from src.utils.path_utils import ensure_run_subdir


def run_semantic_enrichment(run_id: str) -> Path:
    """
    Adapter for Step 1 output.

    If your project already has a full semantic enrichment implementation,
    replace this adapter with the actual execution logic.
    """
    phase1_dir = ensure_run_subdir(run_id=run_id, phase="phase1")
    output_path = phase1_dir / "enriched_schema.yaml"
    if not output_path.exists():
        source_path = Path(__file__).resolve().parents[2] / "enriched_schema.yaml"
        if not source_path.exists():
            raise FileNotFoundError(
                f"Step 1 output not found: {output_path} and seed artifact missing: {source_path}"
            )
        shutil.copyfile(source_path, output_path)
    return output_path
