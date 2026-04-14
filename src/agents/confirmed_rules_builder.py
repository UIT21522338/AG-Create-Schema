from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from src.utils.path_utils import ensure_run_subdir

logger = logging.getLogger(__name__)


def _set_nested_value(target: Dict[str, Any], dotted_key: str, value: Any) -> None:
    """Set nested dictionary value by dotted key, e.g. a.b.c = value."""
    parts = [part.strip() for part in dotted_key.split(".") if part.strip()]
    if not parts:
        return

    node: Dict[str, Any] = target
    for part in parts[:-1]:
        existing = node.get(part)
        if not isinstance(existing, dict):
            node[part] = {}
        node = node[part]
    node[parts[-1]] = value


def _build_volume_definition(answer_code: str, answer_value: Optional[Any]) -> Dict[str, Any]:
    """Translate sellout volume enum code into a structured volume rule."""
    normalized_code = (answer_code or "").strip().upper()

    if normalized_code == "CASE_QTYCS":
        return {
            "unit": "case",
            "source_fact": "fact_sellout",
            "source_column": "qtycs",
        }
    if normalized_code == "UNIT_QTY":
        return {
            "unit": "unit",
            "source_fact": "fact_sellout",
            "source_column": "qty",
        }
    if normalized_code == "WEIGHT":
        return {
            "unit": "weight",
            "source_fact": "fact_sellout",
            "source_column": answer_value if answer_value else "TBD",
        }

    return {
        "unit": "custom",
        "source_fact": "fact_sellout",
        "source_column": answer_value if answer_value else "TBD",
        "answer_code": answer_code,
    }


def _apply_enum_rule(
    confirmed: Dict[str, Any],
    root: str,
    key: str,
    answer_code: str,
    answer_value: Optional[Any],
) -> None:
    """Apply enum answer to confirmed rules with special handling for known keys."""
    if root == "volume_definition" and key == "sellout_volume":
        confirmed.setdefault(root, {})
        confirmed[root][key] = _build_volume_definition(answer_code, answer_value)
        return

    if key.startswith("fact_") and answer_code == "NOT_AVAILABLE":
        logger.warning("Fact source marked NOT_AVAILABLE for %s.%s", root, key)
        return

    confirmed.setdefault(root, {})
    confirmed[root][key] = answer_code


def build_confirmed_rules(run_id: str) -> Path:
    """
    Read confirm_questions.yaml for this run, interpret the answers,
    and build confirmed_rules.yaml.

    Input:
      artifacts/run_{run_id}/phase4/confirm_questions.yaml

    Output:
      artifacts/run_{run_id}/phase4/confirmed_rules.yaml

    Returns:
      Path to confirmed_rules.yaml
    """
    phase4_dir = ensure_run_subdir(run_id=run_id, phase="phase4")
    input_path = phase4_dir / "confirm_questions.yaml"
    output_path = phase4_dir / "confirmed_rules.yaml"

    logger.info("Step 4 input confirm questions path: %s", input_path)
    logger.info("Step 4 output confirmed rules path: %s", output_path)

    if not input_path.exists():
        raise FileNotFoundError(f"confirm_questions.yaml not found: {input_path}")

    questions_data = yaml.safe_load(input_path.read_text(encoding="utf-8")) or {}
    questions = questions_data.get("questions", [])
    if not isinstance(questions, list):
        raise ValueError("confirm_questions.yaml must have root key 'questions' as a list")

    confirmed: Dict[str, Any] = {}

    for question in questions:
        if not isinstance(question, dict):
            logger.warning("Skipping non-dict question entry: %s", question)
            continue

        target_key = (question.get("recommended_target_rule_key") or "").strip()
        if not target_key:
            logger.warning("Skipping question %s: missing recommended_target_rule_key", question.get("id"))
            continue

        if "." not in target_key:
            logger.warning(
                "Skipping question %s: invalid recommended_target_rule_key=%s",
                question.get("id"),
                target_key,
            )
            continue

        root, key = target_key.split(".", 1)
        root = root.strip()
        key = key.strip()
        if not root or not key:
            logger.warning(
                "Skipping question %s: invalid recommended_target_rule_key=%s",
                question.get("id"),
                target_key,
            )
            continue

        expected_answer_type = (question.get("expected_answer_type") or "enum").strip().lower()
        answer_code = question.get("answer_code")
        answer_value = question.get("answer_value")

        if expected_answer_type == "free_text":
            if answer_value is None or str(answer_value).strip() == "":
                logger.warning("Skipping question %s: free_text answer_value is empty", question.get("id"))
                continue
            _set_nested_value(confirmed, f"{root}.{key}", answer_value)
            continue

        # Default enum behavior
        if answer_code is None or str(answer_code).strip() == "":
            logger.warning("Skipping question %s: missing answer_code", question.get("id"))
            continue

        answer_code_str = str(answer_code).strip()
        _apply_enum_rule(confirmed, root, key, answer_code_str, answer_value)

    output_yaml = yaml.safe_dump(confirmed, allow_unicode=True, sort_keys=False, width=160)
    output_path.write_text(output_yaml, encoding="utf-8")

    logger.info("Step 4 confirmed rules generated successfully: %s", output_path)
    return output_path
