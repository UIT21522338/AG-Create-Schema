from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict

import yaml

from src.utils.path_utils import ensure_run_subdir

logger = logging.getLogger(__name__)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Required input file not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _read_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Required input file not found: {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _build_catalog_index(table_catalog: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    index: Dict[str, Dict[str, Any]] = {}
    for table in table_catalog.get("tables", []) or []:
        if not isinstance(table, dict):
            continue
        table_name = str(table.get("name") or "").strip()
        if not table_name:
            continue
        index[table_name] = table
    return index


def _build_catalog_column_index(catalog_table: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    index: Dict[str, Dict[str, Any]] = {}
    for column in catalog_table.get("columns", []) or []:
        if not isinstance(column, dict):
            continue
        column_name = str(column.get("name") or "").strip()
        if not column_name:
            continue
        index[column_name] = column
    return index


def build_source_schema_merged(run_id: str) -> Path:
    """
    Merge raw source schema JSON with business table catalog YAML.

    Output:
    - artifacts/run_{run_id}/phase0/source_schema_merged.yaml
    """
    root = _repo_root()
    source_schema_path = root / "inputs" / "source_schema" / "source_schema.yaml"
    table_catalog_path = root / "inputs" / "table_catalog" / "table_catalog.yaml"

    logger.info("Step 0 input source schema path: %s", source_schema_path)
    logger.info("Step 0 input table catalog path: %s", table_catalog_path)

    source_schema = _read_json(source_schema_path)
    table_catalog = _read_yaml(table_catalog_path)
    catalog_index = _build_catalog_index(table_catalog)

    merged_tables = []
    for raw_table in source_schema.get("tables", []) or []:
        if not isinstance(raw_table, dict):
            continue

        table_name = str(raw_table.get("name") or "").strip()
        if not table_name:
            continue

        catalog_table = catalog_index.get(table_name, {})
        catalog_column_index = _build_catalog_column_index(catalog_table)

        merged_columns = []
        for raw_col in raw_table.get("columns", []) or []:
            if not isinstance(raw_col, dict):
                continue
            column_name = str(raw_col.get("name") or "").strip()
            if not column_name:
                continue
            catalog_col = catalog_column_index.get(column_name, {})
            merged_columns.append(
                {
                    "name": column_name,
                    "type": raw_col.get("type"),
                    "nullable": bool(raw_col.get("nullable", True)),
                    "business_meaning": str(catalog_col.get("business_meaning") or ""),
                }
            )

        merged_tables.append(
            {
                "name": table_name,
                "schema": raw_table.get("schema"),
                "business_summary": str(catalog_table.get("business_summary") or ""),
                "entity_tags": catalog_table.get("entity_tags", []) or [],
                "importance": str(catalog_table.get("importance") or ""),
                "primary_key": raw_table.get("primary_key", []) or [],
                "columns": merged_columns,
            }
        )

    payload = {
        "db_name": source_schema.get("db_name"),
        "captured_at": source_schema.get("captured_at"),
        "tables": merged_tables,
    }

    phase0_dir = ensure_run_subdir(run_id=run_id, phase="phase0")
    output_path = phase0_dir / "source_schema_merged.yaml"
    output_path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False, indent=2),
        encoding="utf-8",
    )
    logger.info("Step 0 output merged source schema path: %s", output_path)
    return output_path
