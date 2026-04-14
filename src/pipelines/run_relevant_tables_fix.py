from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Dict

from src.agent3_phase3_design.bronze_silver_spec import run_bronze_silver_spec
from src.agents.relevant_tables_selector_agent import build_relevant_tables


def run_relevant_tables_fix(
    run_id: str,
    db_name: str,
    project_id: str,
    project_root: str | None = None,
    output_folder_name: str = "phase5_relevant_design",
) -> Dict[str, Any]:
    """
    Fix flow:
    1) Build relevant_tables.yaml from current run artifacts.
    2) Generate bronze_spec/silver_spec only from relevant source tables.
    """
    root = project_root or str(Path(__file__).resolve().parents[2])

    relevant_tables_path = build_relevant_tables(run_id)
    spec_result = run_bronze_silver_spec(
        project_root=root,
        db_name=db_name,
        project_id=project_id,
        version=run_id,
        relevant_tables_path=str(relevant_tables_path),
    )

    run_dir = Path(root) / "artifacts" / run_id
    bundle_dir = run_dir / output_folder_name
    bundle_dir.mkdir(parents=True, exist_ok=True)

    bundled_relevant = bundle_dir / "relevant_tables.yaml"
    bundled_bronze = bundle_dir / "bronze_spec.yaml"
    bundled_silver = bundle_dir / "silver_spec.yaml"

    shutil.copy2(relevant_tables_path, bundled_relevant)
    shutil.copy2(Path(spec_result["bronze_spec_path"]), bundled_bronze)
    shutil.copy2(Path(spec_result["silver_spec_path"]), bundled_silver)

    return {
        "relevant_tables_path": str(relevant_tables_path),
        "bronze_spec_path": spec_result["bronze_spec_path"],
        "silver_spec_path": spec_result["silver_spec_path"],
        "bundle_dir": str(bundle_dir),
        "bundled_relevant_tables_path": str(bundled_relevant),
        "bundled_bronze_spec_path": str(bundled_bronze),
        "bundled_silver_spec_path": str(bundled_silver),
        "bronze_spec": spec_result["bronze_spec"],
        "silver_spec": spec_result["silver_spec"],
    }
