from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Dict, List, Set

import yaml

from src.agent3_phase3_design.bronze_silver_spec import run_bronze_silver_spec


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _extract_existing_tables(enriched_schema: Dict[str, Any]) -> Dict[str, Set[str]]:
    existing: Dict[str, Set[str]] = {}
    for table in enriched_schema.get("tables", []):
        if not isinstance(table, dict):
            continue
        name = str(table.get("table") or "").strip()
        if not name:
            continue
        tags = {str(tag).strip().lower() for tag in (table.get("entity_tags") or [])}
        existing[name] = tags
    return existing


def _flatten_string_values(obj: Any) -> List[str]:
    values: List[str] = []
    if isinstance(obj, dict):
        for value in obj.values():
            values.extend(_flatten_string_values(value))
    elif isinstance(obj, list):
        for item in obj:
            values.extend(_flatten_string_values(item))
    elif isinstance(obj, str):
        values.append(obj)
    return values


def _table_role_from_tags(tags: Set[str]) -> str:
    if "sellout" in tags and "order_header" in tags:
        return "sellout_header"
    if "sellout" in tags and ("order_detail" in tags or "order_line" in tags):
        return "sellout_detail"
    if "customer" in tags and "outlet" in tags:
        return "dim_outlet"
    if "salesperson" in tags or "salesman" in tags:
        return "dim_salesperson"
    if "territory" in tags:
        return "dim_territory"
    if "zone" in tags:
        return "dim_zone"
    return "unknown"


def _is_fact_candidate(tags: Set[str], suggested_as_fact: bool) -> bool:
    if suggested_as_fact:
        return True
    fact_hints = {"sellout", "order_header", "order_detail", "order_line", "sales_order"}
    return bool(tags & fact_hints)


def build_relevant_tables(run_id: str) -> Path:
    """
    Build relevant_tables.yaml for one run.

    Hard constraints:
    - Only select table names that exist in phase1/enriched_schema.yaml.
    - Never create logical/virtual table names.
    """
    root = _repo_root()
    run_dir = root / "artifacts" / run_id

    enriched_schema = _load_yaml(run_dir / "phase1" / "enriched_schema.yaml")
    parsed_metrics = _load_yaml(run_dir / "phase2" / "parsed_metrics.yaml")
    metric_mapping = _load_yaml(run_dir / "phase3" / "metric_mapping.yaml")
    known_patterns = _load_yaml(root / "inputs" / "patterns" / "known_patterns.yaml")
    confirmed_rules = _load_yaml(run_dir / "phase4" / "confirmed_rules.yaml")

    existing_tables = _extract_existing_tables(enriched_schema)
    if not existing_tables:
        raise FileNotFoundError(
            f"No table list found in {run_dir / 'phase1' / 'enriched_schema.yaml'}"
        )

    candidate_tables: Set[str] = set()
    suggested_fact_tables: Set[str] = set()

    # 1) Seed from metric_mapping.suggested_source_table, but only if table exists.
    for metric in metric_mapping.get("metric_mapping", {}).get("metrics", []):
        if not isinstance(metric, dict):
            continue
        suggested = metric.get("suggested_source_table")
        if isinstance(suggested, str) and suggested in existing_tables:
            candidate_tables.add(suggested)
            target_fact = str(metric.get("target_fact") or "").strip().lower()
            if target_fact.startswith("fact_"):
                suggested_fact_tables.add(suggested)

    # 2) Derive needed tags from parsed_metrics + known_patterns hints.
    needed_fact_tags = {"sellout", "sales_order", "order_header", "order_detail", "order_line"}
    needed_dim_tags = {"customer", "outlet", "salesperson", "salesman", "territory", "zone"}

    metric_modules = {
        str(metric.get("module") or "").strip().lower()
        for metric in parsed_metrics.get("metrics", [])
        if isinstance(metric, dict)
    }
    if "sellout" in metric_modules:
        needed_fact_tags.update({"sellout", "order_header", "order_detail", "order_line"})

    pattern_text = " ".join(_flatten_string_values(known_patterns)).lower()
    if "territory" in pattern_text:
        needed_dim_tags.add("territory")
    if "zone" in pattern_text:
        needed_dim_tags.add("zone")
    if "salesperson" in pattern_text or "salesman" in pattern_text:
        needed_dim_tags.update({"salesperson", "salesman"})
    if "outlet" in pattern_text:
        needed_dim_tags.update({"customer", "outlet"})

    for table_name, tags in existing_tables.items():
        if tags & needed_fact_tags:
            candidate_tables.add(table_name)
        if tags & needed_dim_tags:
            candidate_tables.add(table_name)

    # 3) Optional confirmed_rules table values, but include only if they exist in schema.
    for value in _flatten_string_values(confirmed_rules):
        if value in existing_tables:
            candidate_tables.add(value)

    facts: List[Dict[str, str]] = []
    dims: List[Dict[str, str]] = []
    for table_name in sorted(candidate_tables):
        tags = existing_tables.get(table_name, set())
        role = _table_role_from_tags(tags)

        if role.startswith("dim_"):
            dims.append({"name": table_name, "role": role})
            continue
        if role in {"sellout_header", "sellout_detail"}:
            facts.append({"name": table_name, "role": role})
            continue

        if _is_fact_candidate(tags, suggested_as_fact=table_name in suggested_fact_tables):
            facts.append({"name": table_name, "role": "unknown"})
        else:
            dims.append({"name": table_name, "role": "unknown"})

    payload = {"relevant_tables": {"facts": facts, "dims": dims}}

    phase4_dir = run_dir / "phase4"
    phase4_dir.mkdir(parents=True, exist_ok=True)
    output_path = phase4_dir / "relevant_tables.yaml"
    output_path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, indent=2),
        encoding="utf-8",
    )
    return output_path


def build_relevant_tables_and_filtered_specs(
    run_id: str,
    db_name: str = "erp_core",
    project_id: str = "sellout_performance",
    project_root: str | None = None,
    output_folder_name: str = "phase5_relevant_design",
) -> Dict[str, str]:
    root = Path(project_root) if project_root else _repo_root()
    relevant_tables_path = build_relevant_tables(run_id)

    spec_result = run_bronze_silver_spec(
        project_root=str(root),
        db_name=db_name,
        project_id=project_id,
        version=run_id,
        relevant_tables_path=str(relevant_tables_path),
    )

    phase4_dir = root / "artifacts" / run_id / "phase4"
    phase4_dir.mkdir(parents=True, exist_ok=True)
    filtered_bronze_path = phase4_dir / "bronze_spec_filtered.yaml"
    filtered_silver_path = phase4_dir / "silver_spec_filtered.yaml"
    shutil.copy2(Path(spec_result["bronze_spec_path"]), filtered_bronze_path)
    shutil.copy2(Path(spec_result["silver_spec_path"]), filtered_silver_path)

    bundle_dir = root / "artifacts" / run_id / output_folder_name
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
        "filtered_bronze_spec_path": str(filtered_bronze_path),
        "filtered_silver_spec_path": str(filtered_silver_path),
        "bundle_dir": str(bundle_dir),
        "bundled_relevant_tables_path": str(bundled_relevant),
        "bundled_bronze_spec_path": str(bundled_bronze),
        "bundled_silver_spec_path": str(bundled_silver),
    }
