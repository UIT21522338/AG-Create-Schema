from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

from src.utils.llm_client import call_llm
from src.utils.path_utils import ensure_run_subdir

logger = logging.getLogger(__name__)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _read_text(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Required input file not found: {path}")
    return path.read_text(encoding="utf-8")


def _load_yaml(text: str) -> Any:
  if yaml is None:
    raise ImportError("PyYAML is required for Step 3")
  return yaml.safe_load(text)


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


def _validate_yaml_or_warn(yaml_text: str, context: str) -> None:
    if yaml is None:
        logger.info("PyYAML not installed, skip YAML validation for %s", context)
        return
    try:
        yaml.safe_load(yaml_text)
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("LLM output is not valid YAML for %s: %s", context, exc)


def _dump_yaml(value: object) -> str:
    if yaml is None:
        raise ImportError("PyYAML is required to serialize Step 3 outputs")
    return yaml.safe_dump(value, allow_unicode=True, sort_keys=False, width=160)


def _tokenize(value: str) -> set[str]:
  normalized = "".join(ch.lower() if ch.isalnum() else " " for ch in value)
  return {token for token in normalized.split() if token}


def _build_metric_keywords(parsed_metrics: dict[str, Any]) -> set[str]:
  keywords: set[str] = set()
  for metric in parsed_metrics.get("metrics", []):
    if not isinstance(metric, dict):
      continue
    for field in ("metric_id", "module", "default_fact", "preferred_fact", "related_metric"):
      value = metric.get(field)
      if isinstance(value, str):
        keywords.update(_tokenize(value))
    supporting_facts = metric.get("supporting_facts")
    if isinstance(supporting_facts, list):
      for item in supporting_facts:
        if isinstance(item, str):
          keywords.update(_tokenize(item))
  keywords.update(
    {
      "sellout",
      "sales",
      "order",
      "orders",
      "customer",
      "outlet",
      "salesperson",
      "visit",
      "checkin",
      "checkout",
      "geo",
      "location",
      "territory",
      "zone",
      "channel",
      "shoptype",
      "sku",
      "product",
      "kpi",
      "target",
      "actual",
    }
  )
  return keywords


def _score_table(table: dict[str, Any], keywords: set[str]) -> int:
  score = 0
  table_name = str(table.get("table", "")).lower()
  entity_tags = " ".join(table.get("entity_tags", []) or []).lower()
  business_summary = str(table.get("business_summary", "")).lower()

  for keyword in keywords:
    if keyword and keyword in table_name:
      score += 8
    if keyword and keyword in entity_tags:
      score += 6
    if keyword and keyword in business_summary:
      score += 3

  if any(tag in entity_tags for tag in ["order", "order_header", "order_detail", "sellout", "customer", "outlet", "salesperson", "checkin_checkout", "gps_trace"]):
    score += 4

  for column in table.get("columns", []) or []:
    if not isinstance(column, dict):
      continue
    tag = str(column.get("tag", "")).lower()
    business_meaning = str(column.get("business_meaning", "")).lower()
    if tag in {"measure", "date_candidate", "natural_key", "foreign_key_candidate", "status_flag"}:
      score += 1
    if any(keyword in business_meaning for keyword in keywords):
      score += 1

  return score


def _summarize_enriched_schema(enriched_schema_yaml: str, parsed_metrics_yaml: str, max_tables: int = 25) -> str:
  enriched_schema = _load_yaml(enriched_schema_yaml)
  parsed_metrics = _load_yaml(parsed_metrics_yaml)
  if not isinstance(enriched_schema, dict) or not isinstance(parsed_metrics, dict):
    raise ValueError("Unexpected YAML structure in Step 3 inputs")

  keywords = _build_metric_keywords(parsed_metrics)
  tables = enriched_schema.get("tables", []) or []
  scored_tables: list[tuple[int, dict[str, Any]]] = []
  for table in tables:
    if isinstance(table, dict):
      scored_tables.append((_score_table(table, keywords), table))

  scored_tables.sort(key=lambda item: (item[0], str(item[1].get("table", ""))), reverse=True)
  selected_tables = [table for score, table in scored_tables if score > 0][:max_tables]

  if not selected_tables:
    selected_tables = [
      table
      for _, table in scored_tables[:min(max_tables, len(scored_tables))]
    ]

  compact_tables = []
  for table in selected_tables:
    compact_columns = []
    for column in table.get("columns", []) or []:
      if not isinstance(column, dict):
        continue
      column_tag = str(column.get("tag", "")).lower()
      if column_tag in {"measure", "date_candidate", "natural_key", "foreign_key_candidate", "status_flag", "audit", "skip"}:
        compact_columns.append(
          {
            "source_name": column.get("source_name"),
            "silver_name": column.get("silver_name"),
            "tag": column.get("tag"),
            "business_meaning": column.get("business_meaning"),
          }
        )

    compact_tables.append(
      {
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
    )

  summary = {
    "selected_table_count": len(compact_tables),
    "selected_tables": compact_tables,
  }
  return _dump_yaml(summary)


def build_metric_mapping(run_id: str) -> tuple[Path, Path]:
    """
    Step 3 main entry point.
    Reads:
      - phase1/enriched_schema.yaml
      - phase2/parsed_metrics.yaml
    Calls LLM to perform metric-to-source mapping and gap detection.
    Writes:
      - phase3/metric_mapping.yaml
      - phase3/gap_report.yaml
    Returns:
      (metric_mapping_path, gap_report_path)
    """
    root = _repo_root()
    phase1_input_path = root / "artifacts" / run_id / "phase1" / "enriched_schema.yaml"
    phase2_input_path = root / "artifacts" / run_id / "phase2" / "parsed_metrics.yaml"

    logger.info("Step 3 input enriched schema path: %s", phase1_input_path)
    logger.info("Step 3 input parsed metrics path: %s", phase2_input_path)

    enriched_schema_yaml = _read_text(phase1_input_path)
    parsed_metrics_yaml = _read_text(phase2_input_path)
    compact_schema_yaml = _summarize_enriched_schema(enriched_schema_yaml, parsed_metrics_yaml)

    if yaml is not None:
      compact_schema_data = yaml.safe_load(compact_schema_yaml) or {}
      selected_table_count = len(compact_schema_data.get("selected_tables", []))
      logger.info("Step 3 compact schema selected tables: %s", selected_table_count)

    system_prompt = """
You are a senior analytics engineer and data modeler.

Goal:
Given:
- an enriched ERP source schema (`enriched_schema.yaml`),
- a parsed dashboard metric spec (`parsed_metrics.yaml`),

you must:
- map each requested metric to source tables and columns,
- infer which fact tables are needed in the Gold layer,
- detect any gaps (missing tables/columns/facts),
- produce two YAML sections:
  - `metric_mapping:` for successful mappings,
  - `gap_report:` for missing/ambiguous parts.

Important constraints:
- Do NOT invent new columns or tables that are not present in `enriched_schema.yaml`.
- You may propose using multiple source tables if it is clearly required.
- If you are not at least 0.8 confident for a mapping, mark it as `low_confidence` and explain why.
- If a metric cannot be mapped, put it under `gap_report` instead of guessing.
- Output must be valid YAML with a single root consisting of:
  - `metric_mapping:`
  - `gap_report:`
""".strip()

    user_prompt = f"""
Here is `enriched_schema.yaml` (ERP source, semantically tagged):

```yaml
{compact_schema_yaml}
```

Here is `parsed_metrics.yaml` (dashboard metric usage):

```yaml
{parsed_metrics_yaml}
```

Your tasks:

1) For each `metrics_used[].metric_id` in `parsed_metrics.yaml`:
   - Identify the most appropriate source fact table(s) from `enriched_schema.yaml`.
     - Use `default_fact` and `module` from `parsed_metrics` as hints.
     - Use table names, entity_tags, and measure/date tags from `enriched_schema`.
   - For each metric:
     - Decide which source table will become the main Silver/Gold fact.
     - Decide which column(s) provide the numeric value (measure).
     - Decide primary date column (consistent with `business_rules` logic if implied).
     - Note any required dimensions (via foreign_key_candidate columns).

2) Build `metric_mapping` YAML with the following structure:

```yaml
metric_mapping:
  metrics:
    - metric_id: sellout_amount
      module: sellout
      target_fact: fact_sellout
      suggested_source_table: dbo.OM_SalesOrd
      source_measures:
        - table: dbo.OM_SalesOrd
          column: OrdAmt
          tag: measure
      source_filters:
        - column: OrderType
          include_values: ['IN','PV']
        - column: OrderType
          exclude_values: ['IR']
        - column: Status
          include_values: ['C']
      primary_date:
        column: OrderDate
        role: order_date
      grain_hint: order_line
      default_dimensions:
        - dim_date
        - dim_distributor
        - dim_salesperson
        - dim_outlet
        - dim_product
        - dim_territory
        - dim_zone
        - dim_channel
        - dim_shoptype
      confidence: 0.92
      notes:
        - "OrdAmt được mô tả là 'Thành tiền KH phải trả' phù hợp với sellout_amount."
        - "OrderType dùng IN/PV/IR theo business rule."
```

3) Build `gap_report` YAML:

For any metric or dimension that cannot be mapped with high confidence:

```yaml
gap_report:
  metrics:
    - metric_id: some_metric
      severity: CRITICAL | HIGH | MEDIUM | LOW
      reason: "Why you cannot map this metric"
      suggestions:
        - "Need a fact table with X grain and columns Y,Z"
  dimensions:
    - dimension_id: dim_kpi
      severity: MEDIUM
      reason: "parsed_metrics references KPI module but no KPI dimension table found in enriched_schema."
      suggestions:
        - "Need a KPI master table with kpi_id, kpi_name, kpi_group."
```

4) Rules to follow when mapping:

- Use `module` and `default_fact` from `parsed_metrics` as PRIMARY hints:
  - `module: sellout`, `default_fact: fact_sellout` → look for tables tagged as `sellout`, `order_header`, `order_detail`.
- Use `enriched_schema.entity_tags` and `columns[].tag`:
  - `measure` columns with amount/qty semantics → candidate for metric source.
  - `natural_key` and `foreign_key_candidate` help identify grain and dimensions.
- If multiple candidate columns fit:
  - choose the one whose `business_meaning` best matches the metric definition;
  - if still ambiguous, choose one and lower `confidence` to < 0.9 and add a note.

5) Output:

- Return ONE YAML document with this exact top-level structure:

```yaml
metric_mapping:
  metrics:
    - ...
gap_report:
  metrics:
    - ...
  dimensions:
    - ...
```

- Do NOT add explanations outside the YAML.
- Do NOT output multiple YAML documents.
""".strip()

    llm_response = call_llm(system_prompt=system_prompt, user_prompt=user_prompt)
    if not llm_response or not llm_response.strip():
        logger.error("Step 3 failed: empty LLM response")
        raise ValueError("Empty LLM response for metric mapping")

    llm_response = _strip_yaml_code_fence(llm_response)
    _validate_yaml_or_warn(llm_response, context="metric_mapping combined response")

    if yaml is None:
        raise ImportError("PyYAML is required to parse Step 3 LLM response")

    combined = yaml.safe_load(llm_response)
    if not isinstance(combined, dict):
        raise ValueError("Step 3 LLM response must be a YAML mapping")

    metric_mapping = combined.get("metric_mapping")
    gap_report = combined.get("gap_report")
    if metric_mapping is None or gap_report is None:
        raise ValueError("Step 3 LLM response must contain metric_mapping and gap_report sections")

    phase3_dir = ensure_run_subdir(run_id=run_id, phase="phase3")
    metric_mapping_path = phase3_dir / "metric_mapping.yaml"
    gap_report_path = phase3_dir / "gap_report.yaml"

    metric_mapping_yaml = _dump_yaml({"metric_mapping": metric_mapping})
    gap_report_yaml = _dump_yaml({"gap_report": gap_report})

    metric_mapping_path.write_text(metric_mapping_yaml, encoding="utf-8")
    gap_report_path.write_text(gap_report_yaml, encoding="utf-8")

    logger.info("Step 3 output metric mapping path: %s", metric_mapping_path)
    logger.info("Step 3 output gap report path: %s", gap_report_path)

    return metric_mapping_path, gap_report_path
