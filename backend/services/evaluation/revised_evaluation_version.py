import argparse
import asyncio
import concurrent.futures
import json
import re
from datetime import datetime
from pathlib import Path
from typing import List

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from services.agent.llm_client import LLMClient
from database.models import AgentTrace


# ===================== MODEL CONFIG =====================

MODEL_CONFIG = {
    "gpt-4o-mini": {
        "api_key": "sk-xxxx",
        "base_url": "https://www.dmxapi.com/v1",
    },
    "qwen-plus": {
        "api_key": "sk-xxxx",
        "base_url": "https://www.dmxapi.com/v1",
    },
    "deepseek-v3.1": {
        "api_key": "sk-xxxx",
        "base_url": "https://www.dmxapi.com/v1",
    },
}

# ===================== GLOBAL EXECUTOR =====================

executor = concurrent.futures.ThreadPoolExecutor(max_workers=6)

# ===================== Running Stats =====================

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


# ===================== DB =====================

BASE_DIR = Path(__file__).resolve().parents[2]
DATABASE_URL = f"sqlite:///{BASE_DIR / 'data.db'}"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)

SessionLocal = sessionmaker(bind=engine)

# ===================== FINISH DETECTION =====================

def is_trace_finished(content: str) -> bool:
    if not content:
        return False
    normalized = re.sub(r"\s+", "", content)
    return 'next_action:"finish"' in normalized


# ===================== Prompt Loading =====================

def load_metric_prompt(metric_name: str) -> str:
    path = Path(__file__).parent / f"{metric_name}.txt"
    return path.read_text(encoding="utf-8")


def load_summary_prompt() -> str:
    path = Path(__file__).parent / "summary_prompt.txt"
    return path.read_text(encoding="utf-8")


# ===================== Score Extraction =====================

def extract_score_and_cot(response_text: str):
    text = response_text.strip()

    def normalize_score(value):
        try:
            v = float(value)
        except Exception:
            return None
        if v in (0.0, 1.0, 2.0, 3.0, 4.0):
            return v
        if v in (0, 1, 2, 3, 4):
            return float(v)
        return None

    try:
        for m in re.finditer(r"\{[\s\S]*?\}", text):
            obj = json.loads(m.group(0))
            if isinstance(obj, dict):
                if "score" in obj:
                    score = normalize_score(obj["score"])
                    if score is not None:
                        return score, text
                for key in ("评分", "得分", "分数", "最终得分"):
                    if key in obj:
                        score = normalize_score(obj[key])
                        if score is not None:
                            return score, text
    except Exception:
        pass

    patterns = [
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

    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            score = normalize_score(m.group(1))
            if score is not None:
                return score, text

    for line in reversed(text.splitlines()):
        line = line.strip()
        if line in {"0", "1", "2", "3", "4"}:
            return float(line), text

    m = re.search(r"([0-4])(?!.*[0-4])", re.sub(r"[^0-4]", "", text))
    if m:
        return float(m.group(1)), text

    return -1.0, text


# ===================== LLM =====================

def evaluate_metric(summary, trace_text, metric_name, model, api_key, base_url):
    prompt = load_metric_prompt(metric_name)

    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": f"Summary:\n{summary}\n\nTrace:\n{trace_text}"}
    ]

    llm = LLMClient(model=model, api_key=api_key, base_url=base_url)

    resp = llm.call(messages)
    score, cot = extract_score_and_cot(resp.content)

    return {"score": score, "chain_of_thought": cot}


def format_traces_for_llm(traces):
    return "\n\n".join(
        f"[Step {t.step_number}]\n{t.content.strip()}"
        for t in traces
    )


def summarize_traces(traces):
    llm = LLMClient(
        model="gpt-4o-mini",
        api_key=MODEL_CONFIG["gpt-4o-mini"]["api_key"],
        base_url=MODEL_CONFIG["gpt-4o-mini"]["base_url"],
    )

    messages = [
        {"role": "system", "content": load_summary_prompt()},
        {"role": "user", "content": format_traces_for_llm(traces)}
    ]

    return llm.call(messages).content


# ===================== Async Scoring =====================

file_lock = asyncio.Lock()

async def score_trace_async(
    trace_id,
    account_id,
    output_path,
    metric_stats,
    metric_names,
    model,
    api_key,
    base_url,
):
    db = SessionLocal()

    try:
        traces = (
            db.query(AgentTrace)
            .filter(AgentTrace.account_id == account_id)
            .filter(AgentTrace.trace_id == trace_id)
            .order_by(AgentTrace.step_number.asc())
            .all()
        )

        if not traces:
            return

        # -------- summary --------
        loop = asyncio.get_running_loop()

        summary = await loop.run_in_executor(
            executor,
            summarize_traces,
            traces
        )

        trace_text = format_traces_for_llm(traces)

        # -------- 评分 --------
        tasks = [
            loop.run_in_executor(
                executor,
                evaluate_metric,
                summary,
                trace_text,
                metric,
                model,
                api_key,
                base_url,
            )
            for metric in metric_names
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        record = {
            "trace_id": trace_id,
            "summary": summary,
            "evaluations": {},
            "created_at": datetime.utcnow().isoformat(),
        }

        # -------- metric update --------
        for metric, result in zip(metric_names, results):

            if isinstance(result, Exception):
                record["evaluations"][metric] = {
                    "score": -1,
                    "error": str(result),
                }
                continue

            record["evaluations"][metric] = result

            if result["score"] >= 0:
                metric_stats[metric].update(result["score"])

        # -------- 写文件 --------
        async with file_lock:

            with open(output_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            data["records"].append(record)

            # ⭐ 恢复统计输出
            data["metric_statistics"] = {
                k: v.to_dict()
                for k, v in metric_stats.items()
            }

            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

        print(f"✅ 完成 {trace_id}")

    finally:
        db.close()



# ===================== QUEUE =====================

trace_queue = asyncio.Queue()
processed_trace_ids = set()

# ===================== PRODUCER =====================

async def trace_producer(account_id: int):
    print("📡 Producer started")

    while True:
        db = SessionLocal()
        try:
            rows = (
                db.query(AgentTrace.trace_id, AgentTrace.content)
                .filter(AgentTrace.account_id == account_id)
                .filter(AgentTrace.role != "tool")
                .filter(AgentTrace.content.isnot(None))

                .all()
            )

            finished_map = {}

            for trace_id, content in rows:
                if is_trace_finished(content):
                    finished_map[trace_id] = True

            for trace_id in finished_map.keys():
                if trace_id not in processed_trace_ids:
                    await trace_queue.put(trace_id)
                    processed_trace_ids.add(trace_id)

        finally:
            db.close()

        await asyncio.sleep(2)


# ===================== CONSUMER =====================

async def trace_consumer(
    account_id,
    output_file,
    metric_stats,
    metric_names,
    model,
    api_key,
    base_url,
):
    while True:
        trace_id = await trace_queue.get()

        try:
            await score_trace_async(
                trace_id,
                account_id,
                output_file,
                metric_stats,
                metric_names,
                model,
                api_key,
                base_url,
            )
        except Exception as e:
            print("❌ error", e)

        trace_queue.task_done()


# ===================== MONITOR =====================

async def monitor(account_id: int, model: str):

    api_key = MODEL_CONFIG[model]["api_key"]
    base_url = MODEL_CONFIG[model]["base_url"]

    output_dir = Path("./eval_results") / f"account_{account_id}"
    output_dir.mkdir(parents=True, exist_ok=True)

    output_file = output_dir / f"{model}.json"

    if not output_file.exists():
        with open(output_file, "w") as f:
            json.dump({"records": []}, f)

    metric_names = ["metric_a", "metric_b"]
    metric_stats = {m: RunningStats() for m in metric_names}

    producer = asyncio.create_task(trace_producer(account_id))

    consumers = [
        asyncio.create_task(
            trace_consumer(
                account_id,
                output_file,
                metric_stats,
                metric_names,
                model,
                api_key,
                base_url,
            )
        )
        for _ in range(3)
    ]

    await asyncio.gather(producer, *consumers)


# ===================== MAIN =====================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--account_id", type=int, required=True)
    parser.add_argument("--model", type=str, required=True)
    args = parser.parse_args()

    asyncio.run(monitor(args.account_id, args.model))


if __name__ == "__main__":
    main()
