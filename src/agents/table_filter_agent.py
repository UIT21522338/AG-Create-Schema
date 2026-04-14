from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Set

import yaml

from src.utils.path_utils import ensure_run_subdir

logger = logging.getLogger(__name__)


_ALLOWED_ENTITY_TAGS: Set[str] = {
    "sellout",
    "sellin",
    "inventory",
    "sales_order",
    "order_header",
    "order_detail",
    "visit",
    "kpi",
    "customer",
    "outlet",
    "salesperson",
    "salesman",
    "distributor",
    "product",
    "brand",
    "productclass",
    "territory",
    "zone",
    "channel",
    "shoptype",
    "country",
    "state",
}

_FALLBACK_TABLE_PATTERNS = [
    r"OM_SalesOrd",
    r"OM_SalesOrdDet",
    r"AR_Customer",
    r"HR_Employee",
    r"IM_Item.*",
    r"OM_Territory",
    r"OM_Zone",
]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _read_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Required input file not found: {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _matches_fallback_patterns(table_name: str) -> bool:
    for pattern in _FALLBACK_TABLE_PATTERNS:
        if re.search(pattern, table_name, re.IGNORECASE):
            return True
    return False


def build_source_schema_filtered(run_id: str) -> Path:
    """
    Build a reduced source schema for semantic enrichment.

    Input:
    - artifacts/run_{run_id}/phase0/source_schema_merged.yaml

    Output:
    - artifacts/run_{run_id}/phase1/source_schema_filtered.yaml
    """
    root = _repo_root()
    merged_path = root / "artifacts" / run_id / "phase0" / "source_schema_merged.yaml"
    known_patterns_path = root / "inputs" / "patterns" / "known_patterns.yaml"
    metric_dictionary_path = root / "inputs" / "metrics" / "metric_dictionary.yaml"
    parsed_metrics_path = root / "artifacts" / run_id / "phase2" / "parsed_metrics.yaml"

    # The current version hard-codes the scope tags; pattern/metric files are logged for traceability.
    logger.info("Step 1.filter input merged schema path: %s", merged_path)
    logger.info("Step 1.filter input known patterns path: %s", known_patterns_path)
    logger.info("Step 1.filter input metric dictionary path: %s", metric_dictionary_path)
    if parsed_metrics_path.exists():
        logger.info("Step 1.filter optional parsed metrics path: %s", parsed_metrics_path)

    merged = _read_yaml(merged_path)

    filtered_tables: List[Dict[str, Any]] = []
    for table in merged.get("tables", []) or []:
        if not isinstance(table, dict):
            continue
        table_name = str(table.get("name") or "").strip()
        if not table_name:
            continue

        tags = {str(tag).strip().lower() for tag in (table.get("entity_tags") or [])}
        keep = bool(tags & _ALLOWED_ENTITY_TAGS) or _matches_fallback_patterns(table_name)
        if keep:
            filtered_tables.append(table)

    payload = {
        "db_name": merged.get("db_name"),
        "captured_at": merged.get("captured_at"),
        "tables": filtered_tables,
    }

    phase1_dir = ensure_run_subdir(run_id=run_id, phase="phase1")
    output_path = phase1_dir / "source_schema_filtered.yaml"
    output_path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False, indent=2),
        encoding="utf-8",
    )
    logger.info("Step 1.filter output filtered source schema path: %s", output_path)
    return output_path
