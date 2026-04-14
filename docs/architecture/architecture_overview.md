# Architecture Overview

Scope: convert stable inputs into logical Bronze/Silver/Gold schema specifications.

Principles:
- Inputs are immutable source-of-truth for each run.
- Each run writes only into artifacts/run_{run_id}/.
- Gold output is logical model only (no SQL/DDL/ETL).
