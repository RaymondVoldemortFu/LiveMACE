from datetime import datetime
from pathlib import Path
from typing import List, Optional
import concurrent
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from services.evaluation.data_loader import EvaluationDataLoader
from database.models import AgentTrace

class RunningStats:
    def __init__(self):
        self.count = 0
        self.mean = 0.0
        self.M2 = 0.0

    def update(self, x: float):
        self.count += 1
        delta = x - self.mean
        self.mean += delta / self.count
        delta2 = x - self.mean
        self.M2 += delta * delta2

    @property
    def variance(self):
        if self.count < 2:
            return 0.0
        return self.M2 / (self.count - 1)

    def to_dict(self):
        return {
            "count": self.count,
            "mean": self.mean,
            "variance": self.variance,
        }

def get_latest_trace(db, account_id: int) -> Optional[AgentTrace]:
    return (
        db.query(AgentTrace)
        .filter(AgentTrace.account_id == account_id)
        .order_by(AgentTrace.created_at.desc())
        .first()
    )

import asyncio
import concurrent.futures

async def score_trace_async(
    trace_id: str,
    account_id: int,
    db_factory,
    output_path: Path,
    metric_stats: dict,
    metric_names: list,
):
    db = db_factory()
    try:
        traces = (
            db.query(AgentTrace)
            .filter(AgentTrace.account_id == account_id)
            .filter(AgentTrace.trace_id == trace_id)
            .filter(AgentTrace.role != "tool")
            .filter(AgentTrace.content.isnot(None))
            .order_by(AgentTrace.step_number.asc())
            .all()
        )

        if not traces:
            return

        summary = summarize_traces(traces)
        trace_text = format_traces_for_llm(traces)

        record = {
            "trace_id": trace_id,
            "summary": summary,
            "evaluations": {},
            "created_at": datetime.utcnow().isoformat(),
        }

        loop = asyncio.get_running_loop()

        with concurrent.futures.ThreadPoolExecutor() as pool:
            tasks = [
                loop.run_in_executor(
                    pool,
                    evaluate_metric,
                    summary,
                    trace_text,
                    metric
                )
                for metric in metric_names
            ]

            results = await asyncio.gather(*tasks, return_exceptions=True)

        for metric, result in zip(metric_names, results):
            if isinstance(result, Exception):
                score = -1
                record["evaluations"][metric] = {"score": -1, "error": str(result)}
            else:
                score = result["score"]
                record["evaluations"][metric] = result

            if score >= 0:
                metric_stats[metric].update(score)

        # ===== 持久化 =====
        with open(output_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        data["records"].append(record)
        data["metric_statistics"] = {
            k: v.to_dict() for k, v in metric_stats.items()
        }

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        print(f"✅ 完成 trace_id={trace_id} 的评分")

    finally:
        db.close()

import argparse
import time

async def monitor(account_id: int):
    output_dir = Path("./eval_results")
    output_dir.mkdir(exist_ok=True)

    output_file = output_dir / f"monitor_eval_{account_id}.json"

    metric_names = ['metric_a', 'metric_b', 'metric_c', 'metric_d', 'metric_e', 'metric_f']
    metric_stats = {m: RunningStats() for m in metric_names}

    if not output_file.exists():
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump({
                "account_id": account_id,
                "records": [],
                "metric_statistics": {},
                "created_at": datetime.utcnow().isoformat(),
            }, f, ensure_ascii=False, indent=2)

    last_seen_trace_id = None

    print(f"开始监控 account_id={account_id}")

    while True:
        db = SessionLocal()
        try:
            latest = get_latest_trace(db, account_id)
            if latest:
                current_trace_id = latest.trace_id

                if last_seen_trace_id is None:
                    last_seen_trace_id = current_trace_id

                elif current_trace_id != last_seen_trace_id:
                    # 上一条 trace 结束
                    asyncio.create_task(
                        score_trace_async(
                            trace_id=last_seen_trace_id,
                            account_id=account_id,
                            db_factory=SessionLocal,
                            output_path=output_file,
                            metric_stats=metric_stats,
                            metric_names=metric_names,
                        )
                    )
                    last_seen_trace_id = current_trace_id

        finally:
            db.close()

        await asyncio.sleep(3)


def load_metric_prompt(metric_name: str) -> str:
    prompt_path = Path(__file__).parent / f"{metric_name}.txt"
    if not prompt_path.exists():
        raise FileNotFoundError(f"{metric_name}.txt not found at {prompt_path}")
    return prompt_path.read_text(encoding="utf-8")

import re
import json

def extract_score_and_cot(response_text: str):
    """
    只允许 score ∈ {0, 1, 2, 3, 4}
    任何其他情况直接失败
    """
    text = response_text.strip()
    cot = text
    def valid(score: float) -> bool:
        return score in (0.0, 1.0, 2.0, 3.0, 4.0)

    def normalize_score(value):
        try:
            v = float(value)
        except Exception:
            return None
        if valid(v):
            return v
        if v in (0, 1, 2, 3, 4):
            return float(v)
        return None

    # ---------- 1. 严格 JSON ----------
    try:
        for m in re.finditer(r"\{[\s\S]*?\}", text):
            obj = json.loads(m.group(0))
            if isinstance(obj, dict):
                if "score" in obj:
                    score = normalize_score(obj["score"])
                    if score is not None:
                        return score, cot
                for key in ("评分", "得分", "分数", "最终得分"):
                    if key in obj:
                        score = normalize_score(obj[key])
                        if score is not None:
                            return score, cot
    except Exception:
        pass

    # ---------- 2. 明确字段 ----------
    explicit_patterns = [
        r"(?:^|\n)\s*(?:score|final\s*score|评分|得分|分数|最终得分)\s*[:=为\-]?\s*([0-4])\b",
        r"(?:the\s+score\s+is)\s*([0-4])\b",
        r"(?:score\s+is)\s*([0-4])\b",
        r"(?:the\s+score\s+is)\s*\*\*([0-4])\*\*",
        r"(?:score\s+is)\s*\*\*([0-4])\*\*",
        r"\*\*score\*\*\s*[:=]\s*\*\*([0-4])\*\*",
        r"\*\*评分\*\*\s*[:=为]?\s*\*\*([0-4])\*\*",
        r"(?:assign\s+(?:a\s+)?score\s+of)\s*([0-4])\b",
        r"(?:i\s+would\s+assign\s+(?:a\s+)?score\s+of)\s*([0-4])\b",
        r"(?:i\s+would\s+give\s+it\s+(?:a\s+)?score\s+of)\s*([0-4])\b",
    ]

    for pat in explicit_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            score = normalize_score(m.group(1))
            if score is not None:
                return score, cot

    # ---------- 3. 独立一行（最可靠兜底） ----------
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line in {"0", "1", "2", "3", "4"}:
            return float(line), cot

    m = re.search(r"([0-4])(?!.*[0-4])", re.sub(r"[^0-4]", "", text))
    if m:
        return float(m.group(1)), cot

    # ---------- 4. 失败 ----------
    return -1.0, text


def evaluate_metric(
    summary: str,
    trace_text: str,
    metric_name: str,
    model: str = "gpt-4o-mini",
    api_key: str = "sk-Rgo46oxW3W4KxdTzqTrMbUKKhgqx0CQdkFXl00QBsrLiHPIn",
    base_url: str = "https://yibuapi.com/v1"
) -> dict:

    metric_prompt = load_metric_prompt(metric_name)

    messages = [
        {
            "role": "system",
            "content": metric_prompt,
        },
        {
            "role": "assistant",
            "content": f"=== Conversation Summary ===\n{summary}",
        },
        {
            "role": "assistant",
            "content": f"=== Raw Conversation Trace ===\n{trace_text}",
        },
        {
            "role": "user",
            "content": "Please evaluate based on BOTH the summary and raw trace.",
        },
    ]

    llm = LLMClient(model=model, api_key=api_key, base_url=base_url)
    response_message = llm.call(messages)

    response_text = response_message.content.strip()
    score, cot = extract_score_and_cot(response_text)

    return {
        "chain_of_thought": cot,
        "score": score
    }



def load_summary_prompt() -> str:
    prompt_path = Path(__file__).parent / "summary_prompt.txt"
    if not prompt_path.exists():
        raise FileNotFoundError(f"summary_prompt.txt not found at {prompt_path}")
    return prompt_path.read_text(encoding="utf-8")


def format_traces_for_llm(traces: List[AgentTrace]) -> str:
    blocks = []
    for t in traces:
        blocks.append(
            f"[Step {t.step_number}]\n{t.content.strip()}"
        )
    return "\n\n".join(blocks)

from services.agent.llm_client import LLMClient
import os

def summarize_traces(
    traces: List[AgentTrace],
) -> str:
    """
    将 trace + summary_prompt.txt 发送给 gpt5-mini，返回 summary 文本
    """

    summary_prompt = load_summary_prompt()
    trace_text = format_traces_for_llm(traces)

    messages = [
        {
            "role": "system",
            "content": summary_prompt,
        },
        {
            "role": "user",
            "content": trace_text,
        },
    ]

    llm = LLMClient(
        model="gpt-4o-mini",
        api_key="sk-PC2XeFcg5Lh0z1m9mCAUe0s06cHo4QWNsBZypnDRDxQ4ZhN9",
        base_url="https://www.dmxapi.cn/v1"  # 如果你用中转平台
    )

    response_message = llm.call(messages)

    return response_message.content


BASE_DIR = Path(__file__).resolve().parents[2]
DATABASE_URL = f"sqlite:///{BASE_DIR / 'data.db'}"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False}
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)


from datetime import datetime
from typing import List, Optional
from database.models import AgentTrace

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--account_id", type=int, required=True)
    args = parser.parse_args()

    asyncio.run(monitor(args.account_id))

if __name__ == "__main__":
    main()
