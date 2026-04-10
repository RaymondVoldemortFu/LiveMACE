# eval/ — Offline Evaluation Scripts

Scripts in this directory are standalone tools for post-hoc evaluation.
They can be run independently of the main backend server.

---

## offline_llm_audit.py

Scans `rule_evaluation_results` for records where `llm_audit_score IS NULL`
(i.e., the live agent ran with `AUDIT_OFFLINE=True` or LLM audit was otherwise
skipped), then runs the same `LLMAuditor` used in the live pipeline and writes
the scores back to the database.

### Prerequisites

Run from the `backend/` directory with `uv`:

```bash
cd backend
```

The script reads the database from `DATABASE_URL` (or defaults to `./data.db`)
and the LLM config from `.env`, identical to the main app.

If your active DB file is `backend/alpha_arena.sqlite`, pass one of:

```bash
uv run python eval/offline_llm_audit.py --db-path ./alpha_arena.sqlite
# or
uv run python eval/offline_llm_audit.py --database-url sqlite:///./alpha_arena.sqlite
```

### Quick Start

```bash
# Audit all pending records (reads LLM config from .env)
uv run python eval/offline_llm_audit.py

# Limit to 20 records, preview without writing
uv run python eval/offline_llm_audit.py --limit 20 --dry-run

# Only audit a specific account
uv run python eval/offline_llm_audit.py --account-id 3

# Re-audit records that already have scores (overwrite)
uv run python eval/offline_llm_audit.py --force-reaudit
```

### LLM Configuration Priority

The script resolves model / key / URL in this order (first wins):

| Priority | Source |
|---|---|
| 1 (highest) | CLI flags `--model`, `--api-key`, `--base-url` |
| 2 | `AUDIT_MODEL`, `AUDIT_API_KEY`, `AUDIT_BASE_URL` in `.env` |
| 3 | `EVAL_LLM_MODEL`, `EVAL_LLM_API_KEY`, `EVAL_LLM_BASE_URL` in `.env` |
| 4 (fallback) | `API_KEY`, `BASE_URL` in `.env`, model defaults to `gpt-4.1` |

### CLI Options

| Flag | Default | Description |
|---|---|---|
| `--database-url URL` | env / default | Override `DATABASE_URL` for this run |
| `--db-path PATH` | none | SQLite file path shortcut (converted to DATABASE_URL) |
| `--model MODEL` | env / `gpt-4.1` | LLM model name |
| `--api-key KEY` | env | API key |
| `--base-url URL` | env | API base URL |
| `--rules-dir PATH` | `config/rules/` | Directory containing rule JSON files |
| `--account-id N` | all | Restrict to one account ID |
| `--limit N` | 0 (unlimited) | Max records per run |
| `--dry-run` | off | Run audits but do NOT write to DB |
| `--force-reaudit` | off | Re-audit rows that already have scores |

### What Gets Written Back

For each audited `RuleEvaluationResult` row:

| Column | Value |
|---|---|
| `llm_audit_score` | `final_normalized_score` (0–1) |
| `llm_audit_coverage` | coverage score (1–10) |
| `llm_audit_conflict` | conflict score (1–10) |
| `llm_audit_json` | full JSON response from the audit LLM |
| `s_audit` | same as `llm_audit_score` |
| `final_score` | `(s_rule_sat + s_audit) / 2` if both present, else `s_audit` |

### Typical Workflow

1. Run agents with `AUDIT_OFFLINE=True` in `.env` to skip online auditing.
2. After the trading session ends, run:
   ```bash
   cd backend
   uv run python eval/offline_llm_audit.py --limit 200
   ```
3. Results are immediately available in the frontend compliance dashboard.

---

## rule_aware_stats_report.py

Generate Rule-Aware Agent statistics directly from SQLite without starting backend.

Includes:
- Account-level summary (all-time and recent 7 days)
- LLM audit aggregates
- Trend charts for `s_rule_sat`, `s_audit`, and `final_score`

Quick start:

```bash
cd backend
conda run -n uvbench python eval/rule_aware_stats_report.py --db-path ./alpha_arena.sqlite --days 14
```

Outputs are written to `backend/eval_outputs/rule_aware_stats/` by default.

Full guide: `eval/RULE_AWARE_STATS_REPORT.md`
