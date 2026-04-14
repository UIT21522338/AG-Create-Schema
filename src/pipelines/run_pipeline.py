from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.agents.confirmed_rules_builder import build_confirmed_rules
from src.agents.gold_schema_designer_agent import build_gold_design_spec
from src.agents.metric_mapping_agent import build_metric_mapping
from src.agents.metric_parser_agent import (
    build_parsed_metrics_yaml,
    parse_dashboard_spec_raw_to_yaml,
)
from src.agents.relevant_tables_selector_agent import build_relevant_tables_and_filtered_specs
from src.agents.schema_merge_agent import build_source_schema_merged
from src.agents.semantic_enricher_agent import run_semantic_enrichment
from src.utils.path_utils import set_active_run_id

logger = logging.getLogger(__name__)


def _generate_run_id() -> str:
    return datetime.utcnow().strftime("run_%Y-%m-%d_%H%M%S")


def run_pipeline(run_id: Optional[str] = None) -> dict[str, Optional[Path]]:
    """
    Execute end-to-end DW design pipeline for a given run.

    Steps executed here:
    - Step 0: Source Schema Merge
    - Step 1: Semantic Enrichment
    - Step 2.1: Parse raw dashboard text to dashboard_spec.yaml
    - Step 2.2: Normalize metric mapping to parsed_metrics.yaml
    - Step 3: Metric Mapping & Gap Detection
    - Step 4: Confirmed Rules Builder (if confirm_questions.yaml is available)
    - Step 5: Relevant Tables + Filtered Bronze/Silver Specs
    - Step 6: Gold Schema Design
    """
    run_id = run_id or _generate_run_id()
    set_active_run_id(run_id)
    logger.info("Starting pipeline for run_id=%s", run_id)

    try:
        step0_output = build_source_schema_merged(run_id)
        logger.info("Step 0 output path: %s", step0_output)

        step1_output = run_semantic_enrichment(run_id)
        logger.info("Step 1 output path: %s", step1_output)

        dashboard_spec_path = parse_dashboard_spec_raw_to_yaml(run_id)
        logger.info("Step 2.1 output path: %s", dashboard_spec_path)

        parsed_metrics_path = build_parsed_metrics_yaml(run_id)
        logger.info("Step 2.2 output path: %s", parsed_metrics_path)

        metric_mapping_path, gap_report_path = build_metric_mapping(run_id)
        logger.info("Step 3 metric mapping output path: %s", metric_mapping_path)
        logger.info("Step 3 gap report output path: %s", gap_report_path)

        confirmed_rules_path: Optional[Path] = None
        try:
            confirmed_rules_path = build_confirmed_rules(run_id)
            logger.info("Step 4 confirmed rules output path: %s", confirmed_rules_path)
        except FileNotFoundError:
            logger.warning(
                "Step 4 skipped for run_id=%s because confirm_questions.yaml is missing. "
                "Please fill artifacts/%s/phase4/confirm_questions.yaml and rerun.",
                run_id,
                run_id,
            )

        relevant_paths = build_relevant_tables_and_filtered_specs(run_id)
        logger.info("Step 5 relevant tables output path: %s", relevant_paths["relevant_tables_path"])
        logger.info("Step 5 filtered bronze spec output path: %s", relevant_paths["filtered_bronze_spec_path"])
        logger.info("Step 5 filtered silver spec output path: %s", relevant_paths["filtered_silver_spec_path"])
        logger.info("Step 5 bundle directory: %s", relevant_paths["bundle_dir"])

        gold_design_path = build_gold_design_spec(run_id)
        logger.info("Step 6 gold design output path: %s", gold_design_path)

        logger.info("Pipeline completed for run_id=%s", run_id)
        return {
            "source_schema_merged": step0_output,
            "semantic_enrichment": step1_output,
            "dashboard_spec": dashboard_spec_path,
            "parsed_metrics": parsed_metrics_path,
            "metric_mapping": metric_mapping_path,
            "gap_report": gap_report_path,
            "confirmed_rules": confirmed_rules_path,
            "relevant_tables": Path(relevant_paths["relevant_tables_path"]),
            "filtered_bronze_spec": Path(relevant_paths["filtered_bronze_spec_path"]),
            "filtered_silver_spec": Path(relevant_paths["filtered_silver_spec_path"]),
            "gold_design_spec": gold_design_path,
        }
    except Exception:
        logger.exception("Pipeline failed for run_id=%s", run_id)
        raise
