from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

from src.utils.llm_client import call_llm
from src.utils.path_utils import ensure_run_subdir

logger = logging.getLogger(__name__)


_COMPACT_COLUMN_TAGS = {
    "measure",
    "date_candidate",
    "natural_key",
    "foreign_key_candidate",
    "status_flag",
    "audit",
    "skip",
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _read_text(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Required input file not found: {path}")
    return path.read_text(encoding="utf-8")


def _load_yaml(text: str) -> Dict[str, Any]:
    if yaml is None:
        raise ImportError("PyYAML is required for Step 6")
    return yaml.safe_load(text) or {}


def _dump_yaml(value: Any) -> str:
    if yaml is None:
        raise ImportError("PyYAML is required for Step 6")
    return yaml.safe_dump(value, allow_unicode=True, sort_keys=False, width=160)


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


def _is_valid_gold_yaml(text: str) -> bool:
    if yaml is None:
        return True
    try:
        parsed = yaml.safe_load(text)
    except Exception:  # pylint: disable=broad-except
        return False
    return isinstance(parsed, dict) and "dimensions" in parsed and "facts" in parsed


def _validate_gold_yaml(text: str) -> None:
    if not _is_valid_gold_yaml(text):
        raise ValueError("Gold design YAML must contain dimensions and facts and be valid YAML")


def _repair_gold_yaml(invalid_text: str) -> str:
    repair_system_prompt = """
You are a YAML formatter and validator.
Convert the provided content into valid YAML only.
Rules:
- Keep the original meaning and fields.
- Do not add markdown fences.
- Ensure top-level keys are exactly dimensions, facts, metric_coverage, risks.
- Quote any multi-word scalar that could be misread by YAML.
- Output a single YAML document.
""".strip()

    repair_user_prompt = f"""
Fix this Gold design output into valid YAML:

{invalid_text}
""".strip()

    repaired = call_llm(system_prompt=repair_system_prompt, user_prompt=repair_user_prompt)
    return _strip_yaml_code_fence(repaired)


def _ensure_valid_gold_yaml(text: str) -> str:
    cleaned = _strip_yaml_code_fence(text)
    if _is_valid_gold_yaml(cleaned):
        return cleaned

    logger.warning("Attempting Gold YAML repair")
    repaired = _repair_gold_yaml(cleaned)
    if not repaired or not repaired.strip():
        raise ValueError("Gold YAML repair failed with empty output")
    if not _is_valid_gold_yaml(repaired):
        raise ValueError("Gold YAML remains invalid after repair")
    return repaired


def _collect_relevant_source_tables(relevant_tables_yaml: str) -> List[str]:
    parsed = _load_yaml(relevant_tables_yaml)
    relevant = parsed.get("relevant_tables", {})
    if not isinstance(relevant, dict):
        return []

    names: List[str] = []
    for bucket_name in ("facts", "dims"):
        bucket = relevant.get(bucket_name, [])
        if not isinstance(bucket, list):
            continue
        for item in bucket:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if name:
                names.append(name)
    return sorted(dict.fromkeys(names))


def _collect_metric_modules(parsed_metrics_yaml: str) -> Set[str]:
    parsed = _load_yaml(parsed_metrics_yaml)
    modules: Set[str] = set()
    for metric in parsed.get("metrics", []):
        if isinstance(metric, dict):
            module = str(metric.get("module") or "").strip().lower()
            if module:
                modules.add(module)
    return modules


def _summarize_metric_used(metric_used: Any) -> List[Dict[str, Any]]:
    summarized: List[Dict[str, Any]] = []
    if not isinstance(metric_used, list):
        return summarized
    for item in metric_used:
        if not isinstance(item, dict):
            continue
        summarized.append(
            {
                "component_type": item.get("component_type"),
                "component_id": item.get("component_id"),
                "aggregation": item.get("aggregation"),
                "dimensions_sliced_by": item.get("dimensions_sliced_by", []),
                "required_grain": item.get("required_grain"),
                "filters_applied": item.get("filters_applied", []),
            }
        )
    return summarized


def _summarize_parsed_metrics(parsed_metrics_yaml: str) -> str:
    parsed = _load_yaml(parsed_metrics_yaml)
    compact_metrics: List[Dict[str, Any]] = []
    for metric in parsed.get("metrics", []):
        if not isinstance(metric, dict):
            continue
        compact_metrics.append(
            {
                "metric_id": metric.get("metric_id"),
                "module": metric.get("module"),
                "label": metric.get("label"),
                "business_definition": metric.get("business_definition"),
                "default_fact": metric.get("default_fact"),
                "preferred_fact": metric.get("preferred_fact"),
                "supporting_facts": metric.get("supporting_facts", []),
                "grain": metric.get("grain"),
                "used_in": _summarize_metric_used(metric.get("used_in", [])),
            }
        )
    return _dump_yaml({"metrics": compact_metrics})


def _summarize_metric_mapping(metric_mapping_yaml: str) -> str:
    parsed = _load_yaml(metric_mapping_yaml)
    compact_metrics: List[Dict[str, Any]] = []
    metric_mapping = parsed.get("metric_mapping", {})
    if isinstance(metric_mapping, dict):
        for metric in metric_mapping.get("metrics", []):
            if not isinstance(metric, dict):
                continue
            compact_metrics.append(
                {
                    "metric_id": metric.get("metric_id"),
                    "module": metric.get("module"),
                    "target_fact": metric.get("target_fact"),
                    "suggested_source_table": metric.get("suggested_source_table"),
                    "source_measures": metric.get("source_measures", []),
                    "source_filters": metric.get("source_filters", []),
                    "primary_date": metric.get("primary_date", {}),
                    "grain_hint": metric.get("grain_hint"),
                    "default_dimensions": metric.get("default_dimensions", []),
                    "confidence": metric.get("confidence"),
                    "notes": metric.get("notes", []),
                }
            )
    return _dump_yaml({"metric_mapping": {"metrics": compact_metrics}})


def _summarize_table(table: Dict[str, Any]) -> Dict[str, Any]:
    compact_columns: List[Dict[str, Any]] = []
    for column in table.get("columns", []) or []:
        if not isinstance(column, dict):
            continue
        column_tag = str(column.get("tag", "")).lower()
        if column_tag in _COMPACT_COLUMN_TAGS:
            compact_columns.append(
                {
                    "source_name": column.get("source_name"),
                    "silver_name": column.get("silver_name"),
                    "tag": column.get("tag"),
                    "business_meaning": column.get("business_meaning"),
                }
            )

    return {
        "table": table.get("table"),
        "entity_tags": table.get("entity_tags", []),
        "business_summary": table.get("business_summary", ""),
        "natural_keys": table.get("natural_keys", []),
        "foreign_keys": table.get("foreign_keys", []),
        "dates": table.get("dates", []),
        "statuses": table.get("statuses", []),
        "measures": table.get("measures", []),
        "skipped_columns": table.get("skipped_columns", []),
        "columns": compact_columns,
        "confidence": table.get("confidence", {}),
    }


def _summarize_enriched_schema(enriched_schema_yaml: str, allowed_tables: Sequence[str]) -> str:
    enriched_schema = _load_yaml(enriched_schema_yaml)
    tables = enriched_schema.get("tables", []) or []
    indexed: Dict[str, Dict[str, Any]] = {}
    for table in tables:
        if not isinstance(table, dict):
            continue
        table_name = str(table.get("table") or "").strip()
        if table_name:
            indexed[table_name] = table

    selected_tables: List[Dict[str, Any]] = []
    for table_name in allowed_tables:
        table = indexed.get(table_name)
        if table:
            selected_tables.append(_summarize_table(table))

    return _dump_yaml({"selected_table_count": len(selected_tables), "selected_tables": selected_tables})


def _summarize_known_patterns(known_patterns_yaml: str, modules: Set[str]) -> str:
    parsed = _load_yaml(known_patterns_yaml)
    patterns = parsed.get("patterns", [])
    compact_patterns: List[Dict[str, Any]] = []
    selected_domains = {module for module in modules if module}
    if "salesforce_kpi" in selected_domains or "salesforce&kpi" in selected_domains:
        selected_domains.update({"salesforce&kpi", "salesforce_kpi"})
    if "sellout" in selected_domains:
        selected_domains.add("sellout")
    if not selected_domains:
        selected_domains = {"sellout", "salesforce&kpi", "customer"}

    for pattern in patterns:
        if not isinstance(pattern, dict):
            continue
        domain = str(pattern.get("domain") or "").strip().lower()
        if domain not in selected_domains:
            continue
        compact_patterns.append(
            {
                "id": pattern.get("id"),
                "domain": pattern.get("domain"),
                "description": pattern.get("description"),
                "source_hints": pattern.get("source_hints", {}),
                "facts": [
                    {
                        "name": fact.get("name"),
                        "grain": fact.get("grain"),
                        "module": fact.get("module"),
                    }
                    for fact in (pattern.get("facts", []) or [])
                    if isinstance(fact, dict)
                ],
                "dimensions": [
                    {
                        "name": dimension.get("name"),
                        "grain": dimension.get("grain"),
                        "scd_type": dimension.get("scd_type"),
                    }
                    for dimension in (pattern.get("dimensions", []) or [])
                    if isinstance(dimension, dict)
                ],
            }
        )
    return _dump_yaml({"patterns": compact_patterns})


def _optional_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def build_gold_design_spec(run_id: str) -> Path:
    """
    Step 6 main entry point.

    Reads:
      - enriched_schema.yaml
      - parsed_metrics.yaml
      - metric_mapping.yaml
      - confirmed_rules.yaml
      - relevant_tables.yaml
      - known_patterns.yaml

    Calls LLM to design the logical Gold schema and writes:
      - artifacts/run_{run_id}/phase6/gold_design_spec.yaml

    Returns:
      Path to gold_design_spec.yaml
    """
    root = _repo_root()
    enriched_schema_path = root / "artifacts" / run_id / "phase1" / "enriched_schema.yaml"
    parsed_metrics_path = root / "artifacts" / run_id / "phase2" / "parsed_metrics.yaml"
    metric_mapping_path = root / "artifacts" / run_id / "phase3" / "metric_mapping.yaml"
    confirmed_rules_path = root / "artifacts" / run_id / "phase4" / "confirmed_rules.yaml"
    relevant_tables_path = root / "artifacts" / run_id / "phase4" / "relevant_tables.yaml"
    known_patterns_path = root / "inputs" / "patterns" / "known_patterns.yaml"
    filtered_bronze_path = root / "artifacts" / run_id / "phase4" / "bronze_spec_filtered.yaml"
    filtered_silver_path = root / "artifacts" / run_id / "phase4" / "silver_spec_filtered.yaml"

    logger.info("Step 6 input enriched schema path: %s", enriched_schema_path)
    logger.info("Step 6 input parsed metrics path: %s", parsed_metrics_path)
    logger.info("Step 6 input metric mapping path: %s", metric_mapping_path)
    logger.info("Step 6 input confirmed rules path: %s", confirmed_rules_path)
    logger.info("Step 6 input relevant tables path: %s", relevant_tables_path)
    logger.info("Step 6 input known patterns path: %s", known_patterns_path)

    enriched_schema_yaml = _read_text(enriched_schema_path)
    parsed_metrics_yaml = _read_text(parsed_metrics_path)
    metric_mapping_yaml = _read_text(metric_mapping_path)
    confirmed_rules_yaml = _read_text(confirmed_rules_path)
    relevant_tables_yaml = _read_text(relevant_tables_path)
    known_patterns_yaml = _read_text(known_patterns_path)
    filtered_bronze_yaml = _optional_text(filtered_bronze_path)
    filtered_silver_yaml = _optional_text(filtered_silver_path)

    allowed_source_tables = _collect_relevant_source_tables(relevant_tables_yaml)
    metric_modules = _collect_metric_modules(parsed_metrics_yaml)

    enriched_schema_compact = _summarize_enriched_schema(enriched_schema_yaml, allowed_source_tables)
    parsed_metrics_compact = _summarize_parsed_metrics(parsed_metrics_yaml)
    metric_mapping_compact = _summarize_metric_mapping(metric_mapping_yaml)
    known_patterns_compact = _summarize_known_patterns(known_patterns_yaml, metric_modules)
    allowed_source_tables_block = _dump_yaml(allowed_source_tables)

    system_prompt = """
You are a senior dimensional modeling expert (Kimball style) designing the Gold layer schema.

Goal:
Given:
- enriched ERP source schema (semantically tagged),
- parsed metrics required by a specific dashboard,
- metric-to-source mapping,
- confirmed business rules (human-confirmed),
- a list of relevant source tables,
- and known dim/fact patterns for 5 modules (sellout, sellin, inventory, salesforce&kpi, customer),

you must design the logical Gold schema only (no SQL, no DDL):
- List all dimension tables (dim_*) with grain, natural key, surrogate key, and main attributes.
- List all fact tables (fct_*) with grain, foreign keys, degenerate dimensions, and measures.

Important constraints:
- DO NOT invent columns or tables that do not exist in the ERP source (`enriched_schema.yaml`) or that cannot be logically derived from them.
- You may propose logical fact/dim names (dim_*, fct_*), but when you list `source_tables` for each fact/dim, use ONLY table names that appear in `relevant_tables.yaml`.
- Respect naming conventions:
  - Gold dims start with `dim_`
  - Gold facts start with `fct_`
  - Surrogate keys end with `_sk`
  - Business keys end with `_bk`
- All dims in this project are SCD Type 1.
- Bronze already keeps full history; Silver is clean 1:1.
- Gold is where business logic, joins, and aggregations are applied.

Output a single YAML document with root keys:
- dimensions:
- facts:
- metric_coverage:
- risks:

Return only valid YAML.
""".strip()

    user_prompt = f"""
Here is compact `enriched_schema.yaml` filtered to allowed tables:

```yaml
{enriched_schema_compact}
```

Here is compact `parsed_metrics.yaml`:

```yaml
{parsed_metrics_compact}
```

Here is compact `metric_mapping.yaml`:

```yaml
{metric_mapping_compact}
```

Here is `confirmed_rules.yaml`:

```yaml
{confirmed_rules_yaml}
```

Here is `relevant_tables.yaml`:

```yaml
{relevant_tables_yaml}
```

Allowed source tables extracted from relevant_tables.yaml:

```yaml
{allowed_source_tables_block}
```

Here is compact `known_patterns.yaml` relevant to this dashboard:

```yaml
{known_patterns_compact}
```

Optional filtered bronze spec:

```yaml
{filtered_bronze_yaml}
```

Optional filtered silver spec:

```yaml
{filtered_silver_yaml}
```

Design the Gold schema using only the allowed source tables.
""".strip()

    llm_response = call_llm(system_prompt=system_prompt, user_prompt=user_prompt)
    if not llm_response or not llm_response.strip():
        raise ValueError("Empty LLM response for gold_design_spec.yaml")

    llm_response = _ensure_valid_gold_yaml(llm_response)
    _validate_gold_yaml(llm_response)

    phase6_dir = ensure_run_subdir(run_id=run_id, phase="phase6")
    output_path = phase6_dir / "gold_design_spec.yaml"
    output_path.write_text(llm_response, encoding="utf-8")
    logger.info("Step 6 output gold design path: %s", output_path)
    return output_path
