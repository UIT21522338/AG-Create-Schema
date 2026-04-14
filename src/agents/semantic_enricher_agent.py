from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List

import yaml

from src.agents.schema_merge_agent import build_source_schema_merged
from src.agents.table_filter_agent import build_source_schema_filtered
from src.utils.llm_client import call_llm
from src.utils.path_utils import ensure_run_subdir

logger = logging.getLogger(__name__)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _read_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Required input file not found: {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _strip_yaml_code_fence(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    return cleaned


def _to_snake_case(value: str) -> str:
    normalized: List[str] = []
    for ch in value:
        if ch.isalnum():
            normalized.append(ch.lower())
        else:
            normalized.append("_")
    out = "".join(normalized)
    while "__" in out:
        out = out.replace("__", "_")
    return out.strip("_")


def _fallback_semantic_role(column: Dict[str, Any]) -> str:
    name = str(column.get("name") or "").lower()
    col_type = str(column.get("type") or "").lower()
    meaning = str(column.get("business_meaning") or "").lower()

    if any(token in name for token in ["date", "time", "dt", "ngay"]) or "date" in col_type:
        return "date"
    if any(token in name for token in ["status", "type", "flag"]):
        return "status"
    if any(token in col_type for token in ["int", "float", "decimal", "money"]) and any(
        token in name or token in meaning for token in ["amt", "amount", "qty", "total", "volume", "doanh thu"]
    ):
        return "amount"
    if any(token in name for token in ["id", "nbr", "number", "code", "key"]):
        return "key"
    if any(token in name for token in ["crtd", "lupd", "tstamp", "created", "updated"]):
        return "technical"
    return "attribute"


def _role_to_tag(role: str) -> str:
    mapping = {
        "key": "natural_key",
        "date": "date_candidate",
        "status": "status_flag",
        "amount": "measure",
        "attribute": "attribute",
        "technical": "audit",
    }
    return mapping.get(role, "attribute")


def _enrich_table_with_llm(table: Dict[str, Any]) -> Dict[str, Any]:
    table_yaml = yaml.safe_dump(table, allow_unicode=True, sort_keys=False, indent=2)

    system_prompt = """
You are a deterministic schema semantic enricher.
Input is ONE source table section from source_schema_filtered.yaml.

Tasks:
1) Identify semantic role of each column.
2) Return YAML only.

semantic_role enum: [key, date, status, amount, attribute, technical]

Output schema:
table_name: <string>
enriched_columns:
  - column_name: <string>
    semantic_role: <enum>
    usable_for_gold: <bool>
    reason: <string>
""".strip()

    user_prompt = f"""
Input table section:

```yaml
{table_yaml}
```

Return YAML only.
""".strip()

    try:
        llm_response = call_llm(system_prompt=system_prompt, user_prompt=user_prompt)
        parsed = yaml.safe_load(_strip_yaml_code_fence(llm_response)) or {}
        if not isinstance(parsed, dict):
            raise ValueError("Semantic enrichment response must be a mapping")
        columns = parsed.get("enriched_columns", [])
        if not isinstance(columns, list):
            raise ValueError("enriched_columns must be a list")
        return parsed
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning(
            "LLM enrichment failed for table %s, fallback to rule-based: %s",
            table.get("name"),
            exc,
        )

    fallback_columns = []
    for col in table.get("columns", []) or []:
        if not isinstance(col, dict):
            continue
        role = _fallback_semantic_role(col)
        fallback_columns.append(
            {
                "column_name": col.get("name"),
                "semantic_role": role,
                "usable_for_gold": True,
                "reason": "fallback_rule",
            }
        )
    return {
        "table_name": table.get("name"),
        "enriched_columns": fallback_columns,
    }


def _build_enriched_table(table: Dict[str, Any], table_enrichment: Dict[str, Any]) -> Dict[str, Any]:
    col_semantic_index: Dict[str, Dict[str, Any]] = {}
    for item in table_enrichment.get("enriched_columns", []) or []:
        if not isinstance(item, dict):
            continue
        col_name = str(item.get("column_name") or "").strip()
        if not col_name:
            continue
        col_semantic_index[col_name] = item

    cols = []
    natural_keys = []
    foreign_keys = []
    dates = []
    statuses = []
    measures = []

    for col in table.get("columns", []) or []:
        if not isinstance(col, dict):
            continue
        col_name = str(col.get("name") or "").strip()
        if not col_name:
            continue
        semantic = col_semantic_index.get(col_name, {})
        semantic_role = str(semantic.get("semantic_role") or _fallback_semantic_role(col)).strip().lower()
        usable = bool(semantic.get("usable_for_gold", True))

        tag = "skip" if not usable else _role_to_tag(semantic_role)
        silver_name = _to_snake_case(col_name)

        enriched_col = {
            "source_name": col_name,
            "silver_name": silver_name,
            "tag": tag,
            "business_meaning": str(col.get("business_meaning") or ""),
            "group_hint": None,
        }
        cols.append(enriched_col)

        if tag == "natural_key":
            key_obj = {"source_name": col_name, "silver_name": silver_name}
            natural_keys.append(key_obj)
            if col_name.lower().endswith("id"):
                foreign_keys.append(key_obj)
        elif tag == "date_candidate":
            dates.append({"source_name": col_name, "silver_name": silver_name, "role_hint": ["event_date"]})
        elif tag == "status_flag":
            statuses.append({"source_name": col_name, "silver_name": silver_name})
        elif tag == "measure":
            measures.append({"source_name": col_name, "silver_name": silver_name, "measure_type_hint": "amount"})

    table_name = str(table.get("name") or "")
    return {
        "table": table_name,
        "entity_tags": table.get("entity_tags", []) or [],
        "business_summary": str(table.get("business_summary") or ""),
        "columns": cols,
        "natural_keys": natural_keys,
        "foreign_keys": foreign_keys,
        "dates": dates,
        "statuses": statuses,
        "measures": measures,
        "skipped_columns": [
            c["source_name"] for c in cols if c.get("tag") == "skip"
        ],
        "confidence": {
            "table_level": 0.9,
            "notes": ["LLM per-table semantic tagging with fallback rules"],
        },
    }


def run_semantic_enrichment(run_id: str) -> Path:
    """
    Step 1 semantic enrichment over filtered source schema.

    Flow:
    - Ensure phase0/source_schema_merged.yaml exists (build if missing)
    - Ensure phase1/source_schema_filtered.yaml exists (build if missing)
    - Loop through filtered tables and call LLM per table
    - Aggregate to phase1/enriched_schema.yaml
    """
    root = _repo_root()
    merged_path = root / "artifacts" / run_id / "phase0" / "source_schema_merged.yaml"
    if not merged_path.exists():
        build_source_schema_merged(run_id)

    filtered_path = root / "artifacts" / run_id / "phase1" / "source_schema_filtered.yaml"
    if not filtered_path.exists():
        build_source_schema_filtered(run_id)

    logger.info("Step 1 input filtered schema path: %s", filtered_path)
    filtered_schema = _read_yaml(filtered_path)

    enriched_tables: List[Dict[str, Any]] = []
    for table in filtered_schema.get("tables", []) or []:
        if not isinstance(table, dict):
            continue
        table_name = str(table.get("name") or "").strip()
        if not table_name:
            continue
        table_enrichment = _enrich_table_with_llm(table)
        enriched_tables.append(_build_enriched_table(table, table_enrichment))

    payload = {"tables": enriched_tables}
    phase1_dir = ensure_run_subdir(run_id=run_id, phase="phase1")
    output_path = phase1_dir / "enriched_schema.yaml"
    output_path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False, indent=2),
        encoding="utf-8",
    )
    logger.info("Step 1 output enriched schema path: %s", output_path)
    return output_path
