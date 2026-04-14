from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml

logger = logging.getLogger(__name__)

TECHNICAL_COLUMNS: List[Dict[str, Any]] = [
    {"name": "ingest_ts", "type": "timestamp", "nullable": False},
    {"name": "batch_id", "type": "string", "nullable": False},
    {"name": "source_system", "type": "string", "nullable": True},
    {"name": "record_hash", "type": "string", "nullable": True},
]

DEFAULT_SILVER_QUALITY_RULES: List[str] = [
    "trim_all_strings",
    "cast_numeric_types",
    "drop_records_with_null_primary_key",
    "deduplicate_on_dedup_key_keep_latest",
]

FACT_TAG_HINTS = {
    "sellout_transaction",
    "sales_order",
    "order_header",
    "order_detail",
    "invoice",
    "transaction",
    "visit",
    "mcp",
}

DIM_TAG_HINTS = {
    "outlet",
    "customer",
    "salesman",
    "salesperson",
    "zone",
    "territory",
    "channel",
    "supplier",
    "inventory",
    "product",
    "master_data",
}


def _normalize_identifier(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", value or "")
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned.lower()


def _parse_source_table_name(table_name: str) -> Tuple[str, str]:
    if "." in table_name:
        schema_name, base_name = table_name.split(".", 1)
        return _normalize_identifier(schema_name), _normalize_identifier(base_name)
    return "src", _normalize_identifier(table_name)


def _extract_tables(schema_dict: Dict[str, Any]) -> List[Dict[str, Any]]:
    tables = schema_dict.get("tables")
    if isinstance(tables, list):
        return [table for table in tables if isinstance(table, dict)]
    return []


def _extract_columns(table: Dict[str, Any]) -> List[Dict[str, Any]]:
    columns = table.get("columns")
    if isinstance(columns, list):
        return [column for column in columns if isinstance(column, dict)]
    return []


def _extract_primary_key(table: Dict[str, Any]) -> List[str]:
    for key_name in ("primary_key", "primary_keys", "pk"):
        key_value = table.get(key_name)
        if isinstance(key_value, list):
            return [str(item) for item in key_value if item is not None]
    return []


def _derive_catalog_from_schema(source_schema: Dict[str, Any]) -> Dict[str, Any]:
    tables: List[Dict[str, Any]] = []
    for table in _extract_tables(source_schema):
        table_name = str(table.get("name") or table.get("table") or "").strip()
        if not table_name:
            continue
        tables.append(
            {
                "name": table_name,
                "business_summary": str(table.get("business_summary") or "").strip(),
                "entity_tags": table.get("entity_tags", []),
            }
        )
    return {"tables": tables}


def _load_schema_any(path: Path) -> Dict[str, Any]:
    if path.suffix.lower() == ".json":
        return json.loads(path.read_text(encoding="utf-8"))
    if path.suffix.lower() in {".yaml", ".yml"}:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raise ValueError(f"Unsupported schema file extension: {path}")


def _load_catalog_any(path: Path) -> Dict[str, Any]:
    if path.suffix.lower() == ".json":
        return json.loads(path.read_text(encoding="utf-8"))
    if path.suffix.lower() in {".yaml", ".yml"}:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raise ValueError(f"Unsupported catalog file extension: {path}")


def _build_catalog_index(table_catalog: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    index: Dict[str, Dict[str, Any]] = {}
    tables = table_catalog.get("tables", [])
    if not isinstance(tables, list):
        return index

    for item in tables:
        if not isinstance(item, dict):
            continue
        table_name = str(item.get("name") or item.get("table") or "").strip()
        if not table_name:
            continue
        normalized_keys = {
            table_name,
            table_name.lower(),
            _normalize_identifier(table_name),
        }
        if "." in table_name:
            _, only_table = table_name.split(".", 1)
            normalized_keys.add(_normalize_identifier(only_table))
            normalized_keys.add(only_table.lower())

        for lookup_key in normalized_keys:
            index[lookup_key] = item

    return index


def _lookup_catalog(table_name: str, catalog_index: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    candidates = [
        table_name,
        table_name.lower(),
        _normalize_identifier(table_name),
    ]
    if "." in table_name:
        _, only_table = table_name.split(".", 1)
        candidates.extend([only_table, only_table.lower(), _normalize_identifier(only_table)])

    for candidate in candidates:
        if candidate in catalog_index:
            return catalog_index[candidate]
    return {}


def _normalize_source_type(source_type: str) -> str:
    normalized = (source_type or "string").strip().lower()
    return normalized if normalized else "string"


def _to_bronze_column(column: Dict[str, Any]) -> Dict[str, Any]:
    name = str(column.get("name") or column.get("column_name") or "").strip()
    source_type = str(column.get("type") or column.get("data_type") or "string")
    nullable_value = column.get("nullable", True)
    nullable = bool(nullable_value) if isinstance(nullable_value, bool) else True

    return {
        "name": name,
        "type": _normalize_source_type(source_type),
        "nullable": nullable,
    }


def _derive_silver_name(bronze_name: str, source_table: str) -> str:
    _, table_only = _parse_source_table_name(source_table)
    return f"silver.{table_only}"


def _derive_entity_role(entity_tags: List[str], table_name: str) -> str:
    tags = {str(tag).strip().lower() for tag in entity_tags}
    if tags & FACT_TAG_HINTS:
        return "fact_source"
    if tags & DIM_TAG_HINTS:
        return "dimension_source"

    table_lower = table_name.lower()
    if any(token in table_lower for token in ["sales", "order", "invoice", "trans", "visit"]):
        return "fact_source"
    if any(token in table_lower for token in ["customer", "outlet", "zone", "territory", "channel", "product", "supplier"]):
        return "dimension_source"
    return "bridge"


def _derive_latest_rule(columns: List[Dict[str, Any]]) -> List[str]:
    column_names = {str(col.get("name", "")) for col in columns}
    rules: List[str] = []
    for candidate in ["LUpd_DateTime", "updated_datetime", "TranDate", "OrderDate", "Crtd_DateTime", "created_datetime"]:
        if candidate in column_names:
            rules.append(f"{candidate} desc")

    if not rules:
        rules = ["ingest_ts desc"]
    return rules


def _is_amount_like(name: str) -> bool:
    lowered = name.lower()
    return any(token in lowered for token in ["amt", "amount", "total", "price", "cost", "value"])


def _normalize_silver_column_type(column: Dict[str, Any]) -> str:
    col_type = str(column.get("type", "string")).lower()
    col_name = str(column.get("name", ""))
    if _is_amount_like(col_name) and "decimal" not in col_type:
        return "decimal(18,4)"
    if col_type in {"float", "real", "double"}:
        return "decimal(18,4)"
    return col_type


def load_inputs_for_bronze_silver(
    project_root: str,
    db_name: str,
    project_id: str,
    version: str,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Đọc:
      - schema_subset.json (nếu có, ưu tiên)
      - nếu không có thì đọc source_schema.json (full)
    Và:
      - table_catalog_subset.yaml (nếu có)
      - nếu không có thì table_catalog.yaml (full)
    Trả về (source_schema, table_catalog) dạng dict.
    """
    root = Path(project_root)
    version_dir = root / "artifacts" / db_name / project_id / version

    schema_candidates = [
        version_dir / "schema_subset.json",
        version_dir / "phase2" / "schema_subset.json",
        root / "source_schema.json",
        root / "inputs" / "source_schema" / "source_schema.yaml",
    ]

    catalog_candidates = [
        version_dir / "table_catalog_subset.yaml",
        version_dir / "phase2" / "table_catalog_subset.yaml",
        root / "table_catalog.yaml",
        root / "inputs" / "source_schema" / "source_schema.yaml",
    ]

    schema_path = next((path for path in schema_candidates if path.exists()), None)
    if schema_path is None:
        raise FileNotFoundError(
            "Cannot find schema_subset.json or source_schema.json in expected locations"
        )

    catalog_path = next((path for path in catalog_candidates if path.exists()), None)
    if catalog_path is None:
        raise FileNotFoundError(
            "Cannot find table_catalog_subset.yaml or table_catalog.yaml in expected locations"
        )

    source_schema = _load_schema_any(schema_path)
    table_catalog = _load_catalog_any(catalog_path)

    # If catalog source is actually the same schema file, derive a minimal catalog view.
    if catalog_path == schema_path or not isinstance(table_catalog.get("tables"), list):
        table_catalog = _derive_catalog_from_schema(source_schema)

    source_schema.setdefault("_context", {})
    source_schema["_context"].update(
        {
            "project": project_id,
            "db_name": db_name,
            "version": version,
        }
    )

    table_catalog.setdefault("_context", {})
    table_catalog["_context"].update(
        {
            "project": project_id,
            "db_name": db_name,
            "version": version,
        }
    )

    logger.info("Loaded schema from %s", schema_path)
    logger.info("Loaded table catalog from %s", catalog_path)
    return source_schema, table_catalog


def build_bronze_spec(
    source_schema: Dict[str, Any],
    table_catalog: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Sinh ra cấu trúc bronze_spec (dict) theo schema_subset/source_schema.
    """
    context = source_schema.get("_context", {})
    catalog_index = _build_catalog_index(table_catalog)

    bronze_tables: List[Dict[str, Any]] = []
    for table in _extract_tables(source_schema):
        source_table = str(table.get("name") or table.get("table") or "").strip()
        if not source_table:
            continue

        schema_name, base_name = _parse_source_table_name(source_table)
        bronze_name = f"bronze.{schema_name}_{base_name}_raw"
        pk = _extract_primary_key(table)

        catalog_entry = _lookup_catalog(source_table, catalog_index)
        business_summary = str(catalog_entry.get("business_summary") or "").strip()
        business_purpose = (
            business_summary if business_summary else f"Raw copy of {source_table}."
        )

        source_columns = [_to_bronze_column(column) for column in _extract_columns(table)]
        bronze_columns = source_columns + TECHNICAL_COLUMNS

        bronze_tables.append(
            {
                "name": bronze_name,
                "source_table": source_table,
                "business_purpose": business_purpose,
                "load_type": "append_only",
                "primary_key": pk,
                "columns": bronze_columns,
            }
        )

    return {
        "project": context.get("project", "unknown_project"),
        "db_name": context.get("db_name", "unknown_db"),
        "layer": "bronze",
        "tables": bronze_tables,
    }


def build_silver_spec(
    source_schema: Dict[str, Any],
    table_catalog: Dict[str, Any],
    bronze_spec: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Sinh ra cấu trúc silver_spec (dict).
    """
    context = source_schema.get("_context", {})
    catalog_index = _build_catalog_index(table_catalog)

    silver_tables: List[Dict[str, Any]] = []
    for bronze_table in bronze_spec.get("tables", []):
        if not isinstance(bronze_table, dict):
            continue

        source_table = str(bronze_table.get("source_table") or "").strip()
        if not source_table:
            continue

        catalog_entry = _lookup_catalog(source_table, catalog_index)
        entity_tags = catalog_entry.get("entity_tags", [])
        if not isinstance(entity_tags, list):
            entity_tags = []

        silver_name = _derive_silver_name(str(bronze_table.get("name") or ""), source_table)
        entity_role = _derive_entity_role(entity_tags, source_table)

        bronze_columns = bronze_table.get("columns", [])
        silver_columns: List[Dict[str, Any]] = []

        if entity_role == "dimension_source":
            sk_name = f"{silver_name.split('.', 1)[1]}_sk"
            silver_columns.append({"name": sk_name, "type": "bigint", "nullable": False})

        for column in bronze_columns:
            if not isinstance(column, dict):
                continue
            normalized_column = {
                "name": column.get("name"),
                "type": _normalize_silver_column_type(column),
                "nullable": bool(column.get("nullable", True)),
            }
            silver_columns.append(normalized_column)

        natural_key = bronze_table.get("primary_key", []) or catalog_entry.get("business_keys", [])
        if not isinstance(natural_key, list):
            natural_key = []

        dedup_key = list(natural_key)
        latest_rule = _derive_latest_rule(silver_columns)

        business_summary = str(catalog_entry.get("business_summary") or "").strip()
        business_purpose = (
            business_summary
            if business_summary
            else f"Cleaned and conformed data from {bronze_table.get('name')}."
        )

        silver_tables.append(
            {
                "name": silver_name,
                "source_table": bronze_table.get("name"),
                "entity_role": entity_role,
                "business_purpose": business_purpose,
                "load_type": "merge_upsert",
                "natural_key": natural_key,
                "dedup_key": dedup_key,
                "latest_rule": latest_rule,
                "columns": silver_columns,
                "quality_rules": DEFAULT_SILVER_QUALITY_RULES,
            }
        )

    return {
        "project": context.get("project", "unknown_project"),
        "db_name": context.get("db_name", "unknown_db"),
        "layer": "silver",
        "tables": silver_tables,
    }


def save_bronze_silver_specs(
    project_root: str,
    db_name: str,
    project_id: str,
    version: str,
    bronze_spec: Dict[str, Any],
    silver_spec: Dict[str, Any],
) -> Dict[str, str]:
    """
    Ghi 2 file YAML:
      - bronze_spec.yaml
      - silver_spec.yaml
    vào thư mục design/ của version.
    Thêm header:
      generated_by: "rulebase"
      layer: "bronze" / "silver"
    Trả về dict { "bronze_spec_path": ..., "silver_spec_path": ... }.
    """
    design_dir = (
        Path(project_root)
        / "artifacts"
        / db_name
        / project_id
        / version
        / "design"
    )
    design_dir.mkdir(parents=True, exist_ok=True)

    bronze_path = design_dir / "bronze_spec.yaml"
    silver_path = design_dir / "silver_spec.yaml"

    bronze_payload = {"generated_by": "rulebase", "layer": "bronze", **bronze_spec}
    silver_payload = {"generated_by": "rulebase", "layer": "silver", **silver_spec}

    bronze_path.write_text(
        yaml.safe_dump(bronze_payload, allow_unicode=True, sort_keys=False, indent=2),
        encoding="utf-8",
    )
    silver_path.write_text(
        yaml.safe_dump(silver_payload, allow_unicode=True, sort_keys=False, indent=2),
        encoding="utf-8",
    )

    logger.info("Saved bronze spec to %s", bronze_path)
    logger.info("Saved silver spec to %s", silver_path)

    return {
        "bronze_spec_path": str(bronze_path),
        "silver_spec_path": str(silver_path),
    }


def run_bronze_silver_spec(
    project_root: str,
    db_name: str,
    project_id: str,
    version: str,
) -> Dict[str, Any]:
    """
    Orchestrator:
      - source_schema, table_catalog = load_inputs_for_bronze_silver(...)
      - bronze_spec = build_bronze_spec(...)
      - silver_spec = build_silver_spec(...)
      - paths = save_bronze_silver_specs(...)
    Trả về dict gồm:
      {
        "bronze_spec_path": ...,
        "silver_spec_path": ...,
        "bronze_spec": bronze_spec,
        "silver_spec": silver_spec,
      }
    """
    source_schema, table_catalog = load_inputs_for_bronze_silver(
        project_root=project_root,
        db_name=db_name,
        project_id=project_id,
        version=version,
    )

    bronze_spec = build_bronze_spec(source_schema=source_schema, table_catalog=table_catalog)
    silver_spec = build_silver_spec(
        source_schema=source_schema,
        table_catalog=table_catalog,
        bronze_spec=bronze_spec,
    )

    paths = save_bronze_silver_specs(
        project_root=project_root,
        db_name=db_name,
        project_id=project_id,
        version=version,
        bronze_spec=bronze_spec,
        silver_spec=silver_spec,
    )

    return {
        "bronze_spec_path": paths["bronze_spec_path"],
        "silver_spec_path": paths["silver_spec_path"],
        "bronze_spec": bronze_spec,
        "silver_spec": silver_spec,
    }
