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

# Evaluate only and save JSON/CSV outputs without touching the DB
uv run python eval/offline_llm_audit.py --limit 20 --no-write-db

# Save JSON/CSV outputs while also writing DB scores
uv run python eval/offline_llm_audit.py --limit 20 --output-dir ./eval_output/standard_sample

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

For Qwen thinking models, you can disable thinking in either of these ways:

```bash
# One-off CLI flag
uv run python eval/offline_llm_audit.py --disable-thinking

# Or persistent .env setting
AUDIT_ENABLE_THINKING=False
```

By default this sends OpenAI-compatible extra body
`{"enable_thinking": false}`. If your gateway is vLLM-style, set:

```bash
AUDIT_ENABLE_THINKING=False
AUDIT_THINKING_PARAM_STYLE=vllm
```

You can also pass arbitrary provider-specific fields:

```bash
uv run python eval/offline_llm_audit.py --extra-body-json '{"enable_thinking": false}'
```

### CLI Options

| Flag | Default | Description |
|---|---|---|
| `--database-url URL` | env / default | Override `DATABASE_URL` for this run |
| `--db-path PATH` | none | SQLite file path shortcut (converted to DATABASE_URL) |
| `--model MODEL` | env / `gpt-4.1` | LLM model name |
| `--api-key KEY` | env | API key |
| `--base-url URL` | env | API base URL |
| `--disable-thinking` | off / env | Add Qwen-style `extra_body={"enable_thinking": false}` |
| `--extra-body-json JSON` | env | Merge provider-specific JSON into `chat.completions.create` |
| `--rules-dir PATH` | `config/rules/` | Directory containing rule JSON files |
| `--account-id N` | all | Restrict to one account ID |
| `--limit N` | 0 (unlimited) | Max records per run |
| `--dry-run` | off | Run audits but do NOT write to DB; creates output files |
| `--no-write-db`, `--output-only` | off | Alias for evaluate-only mode |
| `--output-dir PATH` | none / timestamped for no-write mode | Write `results.json`, `results.csv`, `summary.json`, `summary.csv` |
| `--force-reaudit` | off | Re-audit rows that already have scores; overwrites DB fields if DB writing is enabled |

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

When `--dry-run` / `--no-write-db` is used, these values are computed but not
persisted. They are exported under the output directory instead.

### Typical Workflow

1. Run agents with `AUDIT_OFFLINE=True` in `.env` to skip online auditing.
2. After the trading session ends, run:
   ```bash
   cd backend
   uv run python eval/offline_llm_audit.py --limit 200
   ```
3. Results are immediately available in the frontend compliance dashboard.

---

## offline_llm_audit_rule_informed.py  *(experimental)*

Variant of `offline_llm_audit.py` that also passes the **mechanical rule check
results** (violations, gate status, R2 soft scores) from the database to the
LLM, giving it objective ground truth to work against.

### Key differences from `offline_llm_audit.py`

| | Standard (`offline_llm_audit.py`) | Rule-Informed (this script) |
|---|---|---|
| LLM inputs | Rules + market state + agent output | + mechanical violations + R2 scores |
| Scoring dimensions | S_cov + S_con | S_cov + S_con (same formula) |
| `final_normalized_score` formula | (cov + con) / 20 | (cov + con) / 20 |
| DB written by default | Yes | **No** (directory output only) |
| Extra outputs | — | Trend charts (PNG) + per-model summary |

### Outputs (in `eval_output/rule_informed_<timestamp>/`)

| File | Contents |
|---|---|
| `results.json` | Full per-record audit results with original vs new scores |
| `summary.json` | Per-account aggregate: avg coverage, conflict, LLM audit, derived final |
| `trend_llm_audit.png` | New LLM audit score over time (one line per account) |
| `trend_final.png` | LLM audit vs s_rule_sat vs derived final score, per account |

### Quick Start

```bash
cd backend

# Default: output to eval_output/rule_informed_<ts>/, no DB writes
uv run python eval/offline_llm_audit_rule_informed.py --db-path ./alpha_arena.sqlite

# Limit to 20 records
uv run python eval/offline_llm_audit_rule_informed.py --db-path ./alpha_arena.sqlite --limit 20

# Re-audit records that already have scores (compare both methods)
uv run python eval/offline_llm_audit_rule_informed.py --db-path ./alpha_arena.sqlite --force-reaudit

# Also write new scores back to DB (overwrites existing llm_audit_* fields)
uv run python eval/offline_llm_audit_rule_informed.py --db-path ./alpha_arena.sqlite --write-db

# Qwen thinking model: disable thinking for faster offline audit
uv run python eval/offline_llm_audit_rule_informed.py --db-path ./alpha_arena.sqlite --write-db --disable-thinking

# Skip chart generation
uv run python eval/offline_llm_audit_rule_informed.py --db-path ./alpha_arena.sqlite --no-charts
```

### CLI Options

All options from `offline_llm_audit.py` are supported, plus:

| Flag | Default | Description |
|---|---|---|
| `--output-dir PATH` | `eval_output/rule_informed_<ts>/` | Output directory for results + charts |
| `--write-db` | off | Overwrite `llm_audit_*` fields in DB (default: output dir only) |
| `--no-charts` | off | Skip PNG chart generation |

### Typical Comparison Workflow

1. Run the standard audit first (populates baseline scores in DB):
   ```bash
   uv run python eval/offline_llm_audit.py --db-path ./alpha_arena.sqlite
   ```
2. Run the rule-informed audit (DB not overwritten by default):
   ```bash
   uv run python eval/offline_llm_audit_rule_informed.py --db-path ./alpha_arena.sqlite --force-reaudit
   ```
3. Compare `original_llm_audit_score` vs `new_final_normalized_score` in `results.json`
   and review the trend charts and summary table.

---

## rule_aware_stats_report.py

Generate Rule-Aware Agent statistics directly from SQLite without starting backend.

Includes:
- Account-level summary (all-time and recent 7 days)
- Final performance metrics from `account_snapshots`: final equity, PnL, return %, max drawdown
- LLM audit aggregates
- Trend charts for `s_rule_sat`, `s_audit`, `final_score`, and final return %

Quick start:

```bash
cd backend
conda run -n uvbench python eval/rule_aware_stats_report.py --db-path ./alpha_arena.sqlite --days 14
```

Outputs are written to `backend/eval_outputs/rule_aware_stats/` by default.

Full guide: `eval/RULE_AWARE_STATS_REPORT.md`
