from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from typing import Iterable

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "alpha_arena_final.sqlite"
ANALYSIS_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = ANALYSIS_DIR
DEFAULT_PLOT_DIR = ANALYSIS_DIR / "plot"
EXCLUDED_ACCOUNT_IDS = {6, 27}
HOURS_PER_YEAR = 24 * 365


ARCHITECTURE_ORDER = [
    "react_tool",
    "react_rule",
    "react_memory",
    "react_plain",
    "advanced_multi_agent",
]
BASELINE_ORDER = ["baseline_buy_hold", "baseline_grid"]
PLOT_ARCHITECTURE_ORDER = ARCHITECTURE_ORDER + BASELINE_ORDER


def bool_text(value: object) -> bool:
    return str(value).strip().lower() == "true"


def account_architecture(row: pd.Series) -> str:
    agent_type = str(row["agent_type"])
    if agent_type == "advanced_multi_agent":
        return "advanced_multi_agent"
    if agent_type == "react":
        if bool_text(row["tool_routing_enabled"]):
            return "react_tool"
        if bool_text(row["enable_rule_aware"]):
            return "react_rule"
        if bool_text(row["memory_enabled"]):
            return "react_memory"
        return "react_plain"
    return f"baseline_{agent_type}"


def connect_readonly(db_path: Path) -> sqlite3.Connection:
    resolved = db_path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"SQLite database not found: {resolved}")
    if not resolved.is_file():
        raise ValueError(f"SQLite path is not a file: {resolved}")
    conn = sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def load_accounts(conn: sqlite3.Connection) -> pd.DataFrame:
    accounts = pd.read_sql_query(
        """
        SELECT
            id AS account_id,
            name,
            agent_type,
            memory_enabled,
            tool_routing_enabled,
            enable_rule_aware,
            model,
            initial_capital,
            is_active
        FROM accounts
        ORDER BY id
        """,
        conn,
    )
    accounts = accounts[~accounts["account_id"].isin(EXCLUDED_ACCOUNT_IDS)].copy()
    accounts["architecture"] = accounts.apply(account_architecture, axis=1)
    accounts["is_llm_account"] = accounts["model"].notna()
    return accounts


def load_curves(conn: sqlite3.Connection, account_ids: Iterable[int]) -> pd.DataFrame:
    ids = sorted(int(account_id) for account_id in account_ids)
    placeholders = ",".join("?" for _ in ids)
    curves = pd.read_sql_query(
        f"""
        SELECT
            account_id,
            timestamp,
            datetime_str,
            total_assets,
            initial_capital,
            profit,
            profit_percentage,
            cash,
            positions_value
        FROM asset_curve_snapshots
        WHERE timeframe = '1h'
          AND account_id IN ({placeholders})
        ORDER BY account_id, timestamp, id
        """,
        conn,
        params=ids,
    )
    curves["datetime"] = pd.to_datetime(curves["datetime_str"], format="ISO8601", utc=True)
    numeric_columns = [
        "total_assets",
        "initial_capital",
        "profit",
        "profit_percentage",
        "cash",
        "positions_value",
    ]
    for column in numeric_columns:
        curves[column] = pd.to_numeric(curves[column], errors="raise")
    curves = curves.drop_duplicates(["account_id", "timestamp"], keep="last")
    return curves


def load_trade_stats(conn: sqlite3.Connection, account_ids: Iterable[int]) -> pd.DataFrame:
    ids = sorted(int(account_id) for account_id in account_ids)
    placeholders = ",".join("?" for _ in ids)
    trades = pd.read_sql_query(
        f"""
        SELECT
            account_id,
            COUNT(*) AS trade_count,
            SUM(price * quantity) AS traded_notional,
            SUM(commission) AS commission_total,
            COUNT(DISTINCT symbol) AS traded_symbols
        FROM trades
        WHERE account_id IN ({placeholders})
        GROUP BY account_id
        """,
        conn,
        params=ids,
    )
    return trades


def load_decision_stats(conn: sqlite3.Connection, account_ids: Iterable[int]) -> pd.DataFrame:
    ids = sorted(int(account_id) for account_id in account_ids)
    placeholders = ",".join("?" for _ in ids)
    decisions = pd.read_sql_query(
        f"""
        SELECT
            account_id,
            COUNT(*) AS decision_count,
            SUM(CASE WHEN executed = 'true' THEN 1 ELSE 0 END) AS executed_decision_count
        FROM ai_decision_logs
        WHERE account_id IN ({placeholders})
        GROUP BY account_id
        """,
        conn,
        params=ids,
    )
    return decisions


def max_drawdown_pct(values: pd.Series) -> float:
    drawdown = values / values.cummax() - 1.0
    return float(drawdown.min() * 100.0)


def cvar_5_pct(returns: pd.Series) -> float:
    threshold = returns.quantile(0.05)
    tail = returns[returns <= threshold]
    if tail.empty:
        return np.nan
    return float(tail.mean() * 100.0)


def account_metrics(curves: pd.DataFrame, accounts: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for account_id, group in curves.groupby("account_id", sort=True):
        group = group.sort_values("timestamp").copy()
        returns = group["total_assets"].pct_change().dropna()
        first = group.iloc[0]
        final = group.iloc[-1]
        average_equity = float(group["total_assets"].mean())
        hourly_std = returns.std(ddof=1)
        downside_std = returns[returns < 0].std(ddof=1)
        sharpe = np.nan if hourly_std == 0 or np.isnan(hourly_std) else returns.mean() / hourly_std * np.sqrt(HOURS_PER_YEAR)
        sortino = (
            np.nan
            if downside_std == 0 or np.isnan(downside_std)
            else returns.mean() / downside_std * np.sqrt(HOURS_PER_YEAR)
        )
        drawdown = max_drawdown_pct(group["total_assets"])
        final_return_pct = float((final["total_assets"] / first["initial_capital"] - 1.0) * 100.0)
        rows.append(
            {
                "account_id": int(account_id),
                "start_time": first["datetime"],
                "end_time": final["datetime"],
                "observations": len(group),
                "initial_capital": float(first["initial_capital"]),
                "final_assets": float(final["total_assets"]),
                "final_profit": float(final["total_assets"] - first["initial_capital"]),
                "final_return_pct": final_return_pct,
                "max_drawdown_pct": drawdown,
                "calmar": np.nan if drawdown == 0 else final_return_pct / abs(drawdown),
                "hourly_volatility_pct": float(hourly_std * 100.0),
                "annualized_volatility_pct": float(hourly_std * np.sqrt(HOURS_PER_YEAR) * 100.0),
                "sharpe_hourly_annualized": float(sharpe),
                "sortino_hourly_annualized": float(sortino),
                "hourly_win_rate_pct": float((returns > 0).mean() * 100.0),
                "hourly_var_5_pct": float(returns.quantile(0.05) * 100.0),
                "hourly_cvar_5_pct": cvar_5_pct(returns),
                "average_exposure_pct": float((group["positions_value"] / group["total_assets"]).mean() * 100.0),
                "final_cash_ratio_pct": float(final["cash"] / final["total_assets"] * 100.0),
                "average_equity": average_equity,
            }
        )

    metrics = pd.DataFrame(rows)
    metrics = accounts.merge(metrics, on="account_id", how="inner")
    return metrics


def enrich_with_activity(
    metrics: pd.DataFrame,
    trade_stats: pd.DataFrame,
    decision_stats: pd.DataFrame,
) -> pd.DataFrame:
    enriched = metrics.merge(trade_stats, on="account_id", how="left")
    enriched = enriched.merge(decision_stats, on="account_id", how="left")
    fill_zero_columns = [
        "trade_count",
        "traded_notional",
        "commission_total",
        "traded_symbols",
        "decision_count",
        "executed_decision_count",
    ]
    enriched[fill_zero_columns] = enriched[fill_zero_columns].fillna(0)
    enriched["turnover"] = enriched["traded_notional"] / enriched["average_equity"]
    enriched["executed_decision_rate_pct"] = np.where(
        enriched["decision_count"] > 0,
        enriched["executed_decision_count"] / enriched["decision_count"] * 100.0,
        np.nan,
    )
    return enriched


def ordered_architectures(values: Iterable[str]) -> list[str]:
    existing = set(values)
    known = [arch for arch in PLOT_ARCHITECTURE_ORDER if arch in existing]
    unknown = sorted(existing - set(known))
    return known + unknown


def write_csv_outputs(
    output_dir: Path,
    metrics: pd.DataFrame,
    model_variance: pd.DataFrame,
    architecture_variance: pd.DataFrame,
    final_return_pivot: pd.DataFrame,
    final_profit_pivot: pd.DataFrame,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    metrics.to_csv(output_dir / "financial_account_metrics.csv", index=False)
    model_variance.to_csv(output_dir / "model_architecture_variance.csv", index=False)
    architecture_variance.to_csv(output_dir / "architecture_model_variance.csv", index=False)
    final_return_pivot.to_csv(output_dir / "final_return_by_model_architecture.csv")
    final_profit_pivot.to_csv(output_dir / "final_profit_by_model_architecture.csv")


def save_heatmap(data: pd.DataFrame, path: Path, title: str, cbar_label: str) -> None:
    fig, ax = plt.subplots(figsize=(11, 6))
    masked = np.ma.masked_invalid(data.to_numpy(dtype=float))
    image = ax.imshow(masked, aspect="auto", cmap="RdYlGn")
    ax.set_title(title)
    ax.set_xlabel("Architecture")
    ax.set_ylabel("Model")
    ax.set_xticks(np.arange(len(data.columns)), labels=data.columns, rotation=35, ha="right")
    ax.set_yticks(np.arange(len(data.index)), labels=data.index)
    for row_idx in range(data.shape[0]):
        for col_idx in range(data.shape[1]):
            value = data.iloc[row_idx, col_idx]
            if pd.notna(value):
                ax.text(col_idx, row_idx, f"{value:.2f}", ha="center", va="center", fontsize=8)
    cbar = fig.colorbar(image, ax=ax)
    cbar.set_label(cbar_label)
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def plot_variance_bar(
    df: pd.DataFrame,
    label_col: str,
    value_col: str,
    path: Path,
    title: str,
    xlabel: str,
    ylabel: str,
) -> None:
    ordered = df.sort_values(value_col, ascending=False)
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.bar(ordered[label_col], ordered[value_col], color="#4C78A8")
    ax.set_title(title)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.tick_params(axis="x", rotation=35)
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def plot_risk_return(metrics: pd.DataFrame, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(10, 6))
    for architecture in ordered_architectures(metrics["architecture"]):
        subset = metrics[metrics["architecture"] == architecture]
        sizes = 35 + subset["turnover"].clip(lower=0, upper=20) * 8
        ax.scatter(
            subset["max_drawdown_pct"].abs(),
            subset["final_return_pct"],
            s=sizes,
            alpha=0.75,
            label=architecture,
        )
    for _, row in metrics.iterrows():
        ax.annotate(str(row["account_id"]), (abs(row["max_drawdown_pct"]), row["final_return_pct"]), fontsize=7)
    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_title("Risk-return map by account")
    ax.set_xlabel("Max drawdown (%)")
    ax.set_ylabel("Final return (%)")
    ax.legend(title="Architecture", fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def plot_equity_by_architecture(curves: pd.DataFrame, accounts: pd.DataFrame, path: Path) -> None:
    merged = curves.merge(accounts[["account_id", "architecture"]], on="account_id", how="inner")
    merged["equity_index"] = merged["total_assets"] / merged["initial_capital"]
    grouped = (
        merged.groupby(["architecture", "datetime"], as_index=False)["equity_index"]
        .mean()
        .sort_values(["architecture", "datetime"])
    )
    fig, ax = plt.subplots(figsize=(12, 6))
    for architecture in ordered_architectures(grouped["architecture"]):
        subset = grouped[grouped["architecture"] == architecture]
        ax.plot(subset["datetime"], subset["equity_index"], label=architecture, linewidth=1.8)
    ax.set_title("Mean equity index by architecture")
    ax.set_xlabel("Time (UTC)")
    ax.set_ylabel("Mean equity index (initial=1.0)")
    ax.legend(title="Architecture", fontsize=8)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def plot_equity_by_model(curves: pd.DataFrame, accounts: pd.DataFrame, path: Path) -> None:
    llm_accounts = accounts[accounts["is_llm_account"]].copy()
    merged = curves.merge(llm_accounts[["account_id", "model"]], on="account_id", how="inner")
    merged["equity_index"] = merged["total_assets"] / merged["initial_capital"]
    grouped = (
        merged.groupby(["model", "datetime"], as_index=False)["equity_index"]
        .mean()
        .sort_values(["model", "datetime"])
    )
    fig, ax = plt.subplots(figsize=(12, 6))
    for model in sorted(grouped["model"].unique()):
        subset = grouped[grouped["model"] == model]
        ax.plot(subset["datetime"], subset["equity_index"], label=model, linewidth=1.8)
    ax.set_title("Mean equity index by model across architectures")
    ax.set_xlabel("Time (UTC)")
    ax.set_ylabel("Mean equity index (initial=1.0)")
    ax.legend(title="Model", fontsize=8)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def plot_account_returns(metrics: pd.DataFrame, path: Path) -> None:
    ordered = metrics.sort_values("final_return_pct")
    colors = ["#E45756" if value < 0 else "#54A24B" for value in ordered["final_return_pct"]]
    fig, ax = plt.subplots(figsize=(11, 7))
    labels = [f"{row.account_id}: {row.name}" for row in ordered.itertuples()]
    ax.barh(labels, ordered["final_return_pct"], color=colors)
    ax.axvline(0, color="black", linewidth=0.8)
    ax.set_title("Final return by account")
    ax.set_xlabel("Final return (%)")
    ax.set_ylabel("Account")
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def plot_trade_activity(metrics: pd.DataFrame, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(10, 6))
    for architecture in ordered_architectures(metrics["architecture"]):
        subset = metrics[metrics["architecture"] == architecture]
        ax.scatter(subset["turnover"], subset["final_return_pct"], s=60, alpha=0.75, label=architecture)
    for _, row in metrics.iterrows():
        ax.annotate(str(row["account_id"]), (row["turnover"], row["final_return_pct"]), fontsize=7)
    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_title("Turnover vs final return")
    ax.set_xlabel("Turnover (traded notional / average equity)")
    ax.set_ylabel("Final return (%)")
    ax.legend(title="Architecture", fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def markdown_table(df: pd.DataFrame, columns: list[str], rename: dict[str, str], max_rows: int | None = None) -> str:
    table = df.loc[:, columns].copy()
    if max_rows is not None:
        table = table.head(max_rows)
    table = table.rename(columns=rename)
    formatted = table.copy()
    for column in formatted.columns:
        if pd.api.types.is_float_dtype(formatted[column]):
            formatted[column] = formatted[column].map(lambda value: "" if pd.isna(value) else f"{value:.2f}")
    headers = list(formatted.columns)
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for _, row in formatted.iterrows():
        lines.append(
            "| "
            + " | ".join("" if pd.isna(row[column]) else str(row[column]) for column in headers)
            + " |"
        )
    return "\n".join(lines)


def top_label(row: pd.Series) -> str:
    return f"{row['name']} ({row['final_return_pct']:.2f}%)"


def write_report(
    output_dir: Path,
    plot_dir: Path,
    metrics: pd.DataFrame,
    model_variance: pd.DataFrame,
    architecture_variance: pd.DataFrame,
    final_return_pivot: pd.DataFrame,
    final_profit_pivot: pd.DataFrame,
) -> Path:
    llm_metrics = metrics[metrics["is_llm_account"]].copy()
    baselines = metrics[~metrics["is_llm_account"]].copy()
    best_account = metrics.loc[metrics["final_return_pct"].idxmax()]
    worst_account = metrics.loc[metrics["final_return_pct"].idxmin()]
    most_stable_model = model_variance.sort_values("final_return_variance_pct2").iloc[0]
    most_sensitive_model = model_variance.sort_values("final_return_variance_pct2", ascending=False).iloc[0]
    best_architecture = architecture_variance.sort_values("mean_final_return_pct", ascending=False).iloc[0]
    most_stable_architecture = architecture_variance.sort_values("final_return_variance_pct2").iloc[0]
    start_time = metrics["start_time"].min()
    end_time = metrics["end_time"].max()

    relative_plot = plot_dir.relative_to(output_dir)
    report = f"""# LiveMACE bench 金融指标分析报告

> 数据来源：`alpha_arena_final.sqlite`  
> 样本区间：{start_time:%Y-%m-%d %H:%M UTC} 至 {end_time:%Y-%m-%d %H:%M UTC}  
> 排除账号：`account_id=6`、`account_id=27`（用户指定为异常数据）  
> 生成脚本：`backend/analysis/financial_metrics_analysis.py`

## 1. 结论摘要

- 全样本剔除异常账号后，最佳账号为 **{top_label(best_account)}**，最差账号为 **{top_label(worst_account)}**。
- 同模型跨架构的盈利方差最高的是 **{most_sensitive_model['model']}**（方差 {most_sensitive_model['final_profit_variance']:.2f}），说明该模型对架构选择最敏感；方差最低的是 **{most_stable_model['model']}**（方差 {most_stable_model['final_profit_variance']:.2f}）。
- 同架构跨模型的平均期末盈利最高的是 **{best_architecture['architecture']}**（平均 {best_architecture['mean_final_profit']:.2f}）；模型间盈利方差最低的是 **{most_stable_architecture['architecture']}**（方差 {most_stable_architecture['final_profit_variance']:.2f}）。
- 风险特征上，报告额外计算了最大回撤、年化波动、Sharpe、Sortino、Calmar、小时胜率、5% VaR/CVaR、换手率、现金占比与平均仓位暴露。

## 2. 同模型跨架构：最终盈利与架构间方差

下表按模型聚合，统计同一个模型在不同架构下的期末盈利表现。`盈利方差` 使用最终盈利金额的样本方差；收益率方差同时保留，便于与百分比图对应。

{markdown_table(
        model_variance,
        [
            "model",
            "n_architectures",
            "mean_final_profit",
            "final_profit_variance",
            "mean_final_return_pct",
            "median_final_return_pct",
            "final_return_variance_pct2",
            "final_return_std_pct",
            "min_final_return_pct",
            "max_final_return_pct",
            "best_architecture",
        ],
        {
            "model": "模型",
            "n_architectures": "架构数",
            "mean_final_profit": "平均盈利",
            "final_profit_variance": "盈利方差",
            "mean_final_return_pct": "平均收益%",
            "median_final_return_pct": "中位收益%",
            "final_return_variance_pct2": "收益率方差",
            "final_return_std_pct": "标准差",
            "min_final_return_pct": "最低收益%",
            "max_final_return_pct": "最高收益%",
            "best_architecture": "最佳架构",
        },
    )}

![同模型跨架构盈利热力图]({relative_plot / "final_profit_heatmap_model_architecture.png"})

![同模型跨架构收益热力图]({relative_plot / "final_return_heatmap_model_architecture.png"})

![同模型跨架构方差]({relative_plot / "model_architecture_variance.png"})

## 3. 同架构跨模型：最终盈利与模型间方差

下表按架构聚合，统计同一架构在不同模型上的期末盈利分布。`advanced_multi_agent` 因剔除了 `account_id=27`，有效模型数为 4。

{markdown_table(
        architecture_variance,
        [
            "architecture",
            "n_models",
            "mean_final_profit",
            "final_profit_variance",
            "mean_final_return_pct",
            "median_final_return_pct",
            "final_return_variance_pct2",
            "final_return_std_pct",
            "min_final_return_pct",
            "max_final_return_pct",
            "best_model",
        ],
        {
            "architecture": "架构",
            "n_models": "模型数",
            "mean_final_profit": "平均盈利",
            "final_profit_variance": "盈利方差",
            "mean_final_return_pct": "平均收益%",
            "median_final_return_pct": "中位收益%",
            "final_return_variance_pct2": "收益率方差",
            "final_return_std_pct": "标准差",
            "min_final_return_pct": "最低收益%",
            "max_final_return_pct": "最高收益%",
            "best_model": "最佳模型",
        },
    )}

![同架构跨模型方差]({relative_plot / "architecture_model_variance.png"})

## 4. 账号级金融特征

### 4.1 期末收益排名

{markdown_table(
        metrics.sort_values("final_return_pct", ascending=False),
        [
            "account_id",
            "name",
            "architecture",
            "model",
            "final_profit",
            "final_return_pct",
            "max_drawdown_pct",
            "sharpe_hourly_annualized",
            "calmar",
            "turnover",
            "final_cash_ratio_pct",
        ],
        {
            "account_id": "账号",
            "name": "名称",
            "architecture": "架构",
            "model": "模型",
            "final_profit": "最终盈利",
            "final_return_pct": "收益%",
            "max_drawdown_pct": "最大回撤%",
            "sharpe_hourly_annualized": "Sharpe",
            "calmar": "Calmar",
            "turnover": "换手率",
            "final_cash_ratio_pct": "期末现金%",
        },
    )}

![账号期末收益]({relative_plot / "account_final_returns.png"})

### 4.2 风险收益与交易活跃度

![风险收益散点]({relative_plot / "risk_return_scatter.png"})

![换手率与收益]({relative_plot / "turnover_vs_return.png"})

### 4.3 架构平均净值曲线

![架构平均净值曲线]({relative_plot / "mean_equity_by_architecture.png"})

### 4.4 模型跨架构平均净值曲线

该图仅纳入有 `model` 字段的 LLM 账号，并在同一模型下对不同架构的资产指数取均值。

![模型跨架构平均净值曲线]({relative_plot / "mean_equity_by_model.png"})

## 5. 基准线表现

{markdown_table(
        baselines.sort_values("final_return_pct", ascending=False),
        [
            "account_id",
            "name",
            "architecture",
            "final_return_pct",
            "max_drawdown_pct",
            "sharpe_hourly_annualized",
            "calmar",
            "trade_count",
            "turnover",
        ],
        {
            "account_id": "账号",
            "name": "名称",
            "architecture": "类型",
            "final_return_pct": "收益%",
            "max_drawdown_pct": "最大回撤%",
            "sharpe_hourly_annualized": "Sharpe",
            "calmar": "Calmar",
            "trade_count": "交易笔数",
            "turnover": "换手率",
        },
    )}

## 6. 生成文件

- 账号级指标：`backend/analysis/financial_account_metrics.csv`
- 同模型跨架构方差：`backend/analysis/model_architecture_variance.csv`
- 同架构跨模型方差：`backend/analysis/architecture_model_variance.csv`
- 模型 x 架构盈利矩阵：`backend/analysis/final_profit_by_model_architecture.csv`
- 模型 x 架构收益矩阵：`backend/analysis/final_return_by_model_architecture.csv`
- 图片目录：`backend/analysis/plot/`

## 7. 方法说明

- 期末收益率：`final_total_assets / initial_capital - 1`。
- 最大回撤：1h 资产曲线相对历史峰值的最大跌幅。
- Sharpe / Sortino：基于小时收益序列，按 `sqrt(24*365)` 年化，未扣无风险利率。
- Calmar：期末收益率除以最大回撤绝对值。
- VaR / CVaR：基于小时收益序列的 5% 分位数与左尾均值。
- 换手率：累计成交名义金额 / 样本期平均权益。
- 方差：报告主表对账号期末盈利金额使用样本方差，同时保留收益率样本方差用于百分比横向比较。
"""
    report_path = output_dir / "financial_metrics_report.md"
    report_path.write_text(report, encoding="utf-8")
    return report_path


def build_variance_tables(metrics: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    llm_metrics = metrics[metrics["is_llm_account"]].copy()
    final_return_pivot = llm_metrics.pivot(index="model", columns="architecture", values="final_return_pct")
    final_return_pivot = final_return_pivot.reindex(columns=[arch for arch in ARCHITECTURE_ORDER if arch in final_return_pivot.columns])
    final_profit_pivot = llm_metrics.pivot(index="model", columns="architecture", values="final_profit")
    final_profit_pivot = final_profit_pivot.reindex(columns=[arch for arch in ARCHITECTURE_ORDER if arch in final_profit_pivot.columns])

    model_rows: list[dict[str, object]] = []
    for model, group in llm_metrics.groupby("model", sort=True):
        best = group.loc[group["final_return_pct"].idxmax()]
        model_rows.append(
            {
                "model": model,
                "n_architectures": int(group["architecture"].nunique()),
                "mean_final_profit": float(group["final_profit"].mean()),
                "median_final_profit": float(group["final_profit"].median()),
                "final_profit_variance": float(group["final_profit"].var(ddof=1)),
                "final_profit_std": float(group["final_profit"].std(ddof=1)),
                "mean_final_return_pct": float(group["final_return_pct"].mean()),
                "median_final_return_pct": float(group["final_return_pct"].median()),
                "final_return_variance_pct2": float(group["final_return_pct"].var(ddof=1)),
                "final_return_std_pct": float(group["final_return_pct"].std(ddof=1)),
                "min_final_return_pct": float(group["final_return_pct"].min()),
                "max_final_return_pct": float(group["final_return_pct"].max()),
                "best_architecture": best["architecture"],
            }
        )

    architecture_rows: list[dict[str, object]] = []
    for architecture, group in llm_metrics.groupby("architecture", sort=True):
        best = group.loc[group["final_return_pct"].idxmax()]
        architecture_rows.append(
            {
                "architecture": architecture,
                "n_models": int(group["model"].nunique()),
                "mean_final_profit": float(group["final_profit"].mean()),
                "median_final_profit": float(group["final_profit"].median()),
                "final_profit_variance": float(group["final_profit"].var(ddof=1)),
                "final_profit_std": float(group["final_profit"].std(ddof=1)),
                "mean_final_return_pct": float(group["final_return_pct"].mean()),
                "median_final_return_pct": float(group["final_return_pct"].median()),
                "final_return_variance_pct2": float(group["final_return_pct"].var(ddof=1)),
                "final_return_std_pct": float(group["final_return_pct"].std(ddof=1)),
                "min_final_return_pct": float(group["final_return_pct"].min()),
                "max_final_return_pct": float(group["final_return_pct"].max()),
                "best_model": best["model"],
            }
        )

    model_variance = pd.DataFrame(model_rows).sort_values("final_return_variance_pct2", ascending=False)
    architecture_variance = pd.DataFrame(architecture_rows)
    architecture_variance["architecture"] = pd.Categorical(
        architecture_variance["architecture"],
        categories=ARCHITECTURE_ORDER,
        ordered=True,
    )
    architecture_variance = architecture_variance.sort_values("architecture").reset_index(drop=True)
    architecture_variance["architecture"] = architecture_variance["architecture"].astype(str)
    return model_variance, architecture_variance, final_return_pivot, final_profit_pivot


def run_analysis(db_path: Path, output_dir: Path, plot_dir: Path) -> Path:
    with connect_readonly(db_path) as conn:
        accounts = load_accounts(conn)
        curves = load_curves(conn, accounts["account_id"])
        metrics = account_metrics(curves, accounts)
        trade_stats = load_trade_stats(conn, metrics["account_id"])
        decision_stats = load_decision_stats(conn, metrics["account_id"])

    metrics = enrich_with_activity(metrics, trade_stats, decision_stats)
    model_variance, architecture_variance, final_return_pivot, final_profit_pivot = build_variance_tables(metrics)

    output_dir.mkdir(parents=True, exist_ok=True)
    plot_dir.mkdir(parents=True, exist_ok=True)
    write_csv_outputs(output_dir, metrics, model_variance, architecture_variance, final_return_pivot, final_profit_pivot)

    save_heatmap(
        final_profit_pivot,
        plot_dir / "final_profit_heatmap_model_architecture.png",
        "Final profit by model and architecture",
        "Final profit",
    )
    save_heatmap(
        final_return_pivot,
        plot_dir / "final_return_heatmap_model_architecture.png",
        "Final return by model and architecture",
        "Final return (%)",
    )
    plot_variance_bar(
        model_variance,
        "model",
        "final_profit_variance",
        plot_dir / "model_architecture_variance.png",
        "Final profit variance within each model",
        "Model",
        "Variance of final profit",
    )
    plot_variance_bar(
        architecture_variance,
        "architecture",
        "final_profit_variance",
        plot_dir / "architecture_model_variance.png",
        "Final profit variance within each architecture",
        "Architecture",
        "Variance of final profit",
    )
    plot_risk_return(metrics, plot_dir / "risk_return_scatter.png")
    plot_equity_by_architecture(curves, accounts, plot_dir / "mean_equity_by_architecture.png")
    plot_equity_by_model(curves, accounts, plot_dir / "mean_equity_by_model.png")
    plot_account_returns(metrics, plot_dir / "account_final_returns.png")
    plot_trade_activity(metrics, plot_dir / "turnover_vs_return.png")

    return write_report(
        output_dir,
        plot_dir,
        metrics,
        model_variance,
        architecture_variance,
        final_return_pivot,
        final_profit_pivot,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze LiveMACE bench final financial metrics.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="Path to alpha_arena_final.sqlite")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Directory for CSV and report outputs")
    parser.add_argument("--plot-dir", type=Path, default=DEFAULT_PLOT_DIR, help="Directory for plot image outputs")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report_path = run_analysis(args.db, args.output_dir, args.plot_dir)
    print(f"Wrote report: {report_path}")


if __name__ == "__main__":
    main()
