from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

from src.utils.llm_client import call_llm
from src.utils.path_utils import ensure_run_subdir, get_active_run_id

logger = logging.getLogger(__name__)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _read_text(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Required input file not found: {path}")
    return path.read_text(encoding="utf-8")


def _validate_yaml_or_warn(yaml_text: str, context: str) -> None:
    if yaml is None:
        logger.info("PyYAML not installed, skip YAML validation for %s", context)
        return
    try:
        yaml.safe_load(yaml_text)
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("LLM output is not valid YAML for %s: %s", context, exc)


def _strip_yaml_code_fence(text: str) -> str:
    """Normalize common markdown-wrapped YAML output from LLMs."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    return cleaned


def _resolve_run_id(run_id: Optional[str]) -> str:
    if run_id:
        return run_id
    resolved_run_id = get_active_run_id()
    logger.info("No run_id provided; reusing active run_id=%s", resolved_run_id)
    return resolved_run_id


def parse_dashboard_spec_raw_to_yaml(run_id: Optional[str] = None) -> Path:
    """
    Read dashboard_spec_raw.txt and metric_dictionary.yaml from inputs,
    call LLM to generate a structured dashboard_spec.yaml,
    and write it under artifacts/run_{run_id}/phase2/dashboard_spec.yaml.
    Return the path to the generated file.
    """
    run_id = _resolve_run_id(run_id)
    root = _repo_root()
    raw_input_path = root / "inputs" / "report_specs" / "dashboard_spec_raw.txt"
    metric_dict_path = root / "inputs" / "metrics" / "metric_dictionary.yaml"

    logger.info("Step 2.1 input dashboard spec raw path: %s", raw_input_path)
    logger.info("Step 2.1 input metric dictionary path: %s", metric_dict_path)

    dashboard_spec_raw = _read_text(raw_input_path)
    metric_dictionary_yaml = _read_text(metric_dict_path)

    system_prompt = """
You are a BI product manager and requirements analyst.

Goal:
Given raw Vietnamese business text that describes a dashboard (filters, KPIs, charts, tables),
extract a clean, structured YAML spec `dashboard_spec.yaml`.

Important:
- Do NOT invent new metrics or dimensions that are not clearly implied.
- Try to reuse existing metric IDs from a metric dictionary if provided.
- Output must be valid YAML with a single root object.
""".strip()

    user_prompt = f"""
Here is the raw dashboard specification from the business user (Vietnamese):

```text
{dashboard_spec_raw}
```

Here is the central metric dictionary (YAML) with metric IDs and business definitions:

```yaml
{metric_dictionary_yaml}
```

[Then paste the instructions about tasks and output format exactly as we designed before.]
""".strip()

    llm_response = call_llm(system_prompt=system_prompt, user_prompt=user_prompt)
    if not llm_response or not llm_response.strip():
        logger.error("Step 2.1 failed: empty LLM response")
        raise ValueError("Empty LLM response for dashboard_spec.yaml")

    llm_response = _strip_yaml_code_fence(llm_response)

    _validate_yaml_or_warn(llm_response, context="dashboard_spec.yaml")

    phase2_dir = ensure_run_subdir(run_id=run_id, phase="phase2")
    output_path = phase2_dir / "dashboard_spec.yaml"
    output_path.write_text(llm_response, encoding="utf-8")
    logger.info("Step 2.1 output dashboard spec path: %s", output_path)

    return output_path


def build_parsed_metrics_yaml(run_id: Optional[str] = None) -> Path:
    """
    Read dashboard_spec.yaml (from artifacts/run_{run_id}/phase2),
    and metric_dictionary.yaml from inputs,
    call LLM to generate parsed_metrics.yaml,
    and write it under artifacts/run_{run_id}/phase2/parsed_metrics.yaml.
    Return the path to the generated file.
    """
    run_id = _resolve_run_id(run_id)
    root = _repo_root()
    phase2_dir = ensure_run_subdir(run_id=run_id, phase="phase2")
    dashboard_spec_path = phase2_dir / "dashboard_spec.yaml"
    metric_dict_path = root / "inputs" / "metrics" / "metric_dictionary.yaml"

    logger.info("Step 2.2 input dashboard spec path: %s", dashboard_spec_path)
    logger.info("Step 2.2 input metric dictionary path: %s", metric_dict_path)

    dashboard_spec_yaml = _read_text(dashboard_spec_path)
    metric_dictionary_yaml = _read_text(metric_dict_path)

    system_prompt = """
You are a BI metrics modeling assistant.

Goal:
Given a structured `dashboard_spec.yaml` and a global `metric_dictionary.yaml`,
produce a normalized `parsed_metrics.yaml` that explicitly lists:
- which metrics are used,
- how they are aggregated,
- which dimensions they are sliced by,
- what grain they require.

Output must be valid YAML.
""".strip()

    user_prompt = f"""
Here is the dashboard specification in YAML:

```yaml
{dashboard_spec_yaml}
```

Here is the global metric dictionary:

```yaml
{metric_dictionary_yaml}
```

[Then paste the instructions about tasks and output format exactly as we designed before.]
""".strip()

    llm_response = call_llm(system_prompt=system_prompt, user_prompt=user_prompt)
    if not llm_response or not llm_response.strip():
        logger.error("Step 2.2 failed: empty LLM response")
        raise ValueError("Empty LLM response for parsed_metrics.yaml")

    llm_response = _strip_yaml_code_fence(llm_response)

    _validate_yaml_or_warn(llm_response, context="parsed_metrics.yaml")

    output_path = phase2_dir / "parsed_metrics.yaml"
    output_path.write_text(llm_response, encoding="utf-8")
    logger.info("Step 2.2 output parsed metrics path: %s", output_path)

    return output_path
