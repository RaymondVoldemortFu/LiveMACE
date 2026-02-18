import argparse
import asyncio
import concurrent.futures
import json
import re
import socket
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from services.agent.llm_client import LLMClient
from database.models import AgentTrace


# ===================== MODEL CONFIG (API KEYS HERE) =====================

MODEL_CONFIG = {
    "gpt-4o-mini": {
        "api_key": "sk-Rkk4NKpusUh11QTOyf2W3tNrXuFizTaoZIm5CSTJ2jASIFzg",
        "base_url": "https://www.dmxapi.com/v1",
    },
    "qwen-plus": {
        "api_key": "sk-Rkk4NKpusUh11QTOyf2W3tNrXuFizTaoZIm5CSTJ2jASIFzg",
        "base_url": "https://www.dmxapi.com/v1",
    },
    "deepseek-v3.1": {
        "api_key": "sk-Rkk4NKpusUh11QTOyf2W3tNrXuFizTaoZIm5CSTJ2jASIFzg",
        "base_url": "https://www.dmxapi.com/v1",
    },
    # 以后要加模型只在这里加
    # "gpt-5-mini": {
    #     "api_key": "...",
    #     "base_url": "...",
    # },
}


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

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)


def get_latest_trace(db, account_id: int) -> Optional[AgentTrace]:
    return (
        db.query(AgentTrace)
        .filter(AgentTrace.account_id == account_id)
        .order_by(AgentTrace.created_at.desc())
        .first()
    )


# ===================== Score Extraction (0/1/2 ONLY) =====================

def extract_score_and_cot(response_text: str):
    text = response_text.strip()
    cot = text

    def valid(v: float) -> bool:
        return v in (0.0, 1.0, 2.0, 3.0, 4.0)

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
                        return score, cot
                for key in ("评分", "得分", "分数", "最终得分"):
                    if key in obj:
                        score = normalize_score(obj[key])
                        if score is not None:
                            return score, cot
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
                return score, cot

    for line in reversed(text.splitlines()):
        line = line.strip()
        if line in {"0", "1", "2", "3", "4"}:
            return float(line), cot

    # Final fallback: last standalone digit 0/1/2 in the text
    m = re.search(r"([0-4])(?!.*[0-4])", re.sub(r"[^0-4]", "", text))
    if m:
        return float(m.group(1)), cot

    return -1.0, text


# ===================== Prompts =====================

def load_metric_prompt(metric_name: str) -> str:
    path = Path(__file__).parent / f"{metric_name}.txt"
    if not path.exists():
        raise FileNotFoundError(path)
    return path.read_text(encoding="utf-8")


def load_summary_prompt() -> str:
    path = Path(__file__).parent / "summary_prompt.txt"
    if not path.exists():
        raise FileNotFoundError(path)
    return path.read_text(encoding="utf-8")


# ===================== LLM =====================

def evaluate_metric(
    summary: str,
    trace_text: str,
    metric_name: str,
    model: str,
    api_key: str,
    base_url: str,
) -> dict:
    prompt = load_metric_prompt(metric_name)

    messages = [
        {
            "role": "system",
            "content": prompt,
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


    llm = LLMClient(
        model=model,
        api_key=api_key,
        base_url=base_url,
    )

    response = llm.call(messages)
    score, cot = extract_score_and_cot(response.content)

    return {
        "score": score,
        "chain_of_thought": cot,
    }


def format_traces_for_llm(traces: List[AgentTrace]) -> str:
    return "\n\n".join(
        f"[Step {t.step_number}]\n{t.content.strip()}"
        for t in traces
    )


def summarize_traces(traces: List[AgentTrace]) -> str:
    messages = [
        {"role": "system", "content": load_summary_prompt()},
        {"role": "user", "content": format_traces_for_llm(traces)},
    ]

    llm = LLMClient(
        model="gpt-4o-mini",
        api_key=MODEL_CONFIG["gpt-4o-mini"]["api_key"],
        base_url="https://www.dmxapi.com/v1",
    )

    return llm.call(messages).content


# ===================== Async Scoring =====================

file_lock = asyncio.Lock()
trace_queue: asyncio.Queue[str] = asyncio.Queue()
processed_trace_ids = set()
processed_trace_ids_order = deque()
PROCESSED_TRACE_IDS_MAX = 5000
retry_counts: dict[str, int] = {}
MAX_RETRY_PER_TRACE = 2
MIN_TRACE_DATETIME_UTC: datetime | None = None


def mark_processed(trace_id: str) -> None:
    if trace_id in processed_trace_ids:
        return
    processed_trace_ids.add(trace_id)
    processed_trace_ids_order.append(trace_id)
    if len(processed_trace_ids_order) > PROCESSED_TRACE_IDS_MAX:
        old = processed_trace_ids_order.popleft()
        processed_trace_ids.discard(old)


def enqueue_retry(trace_id: str) -> None:
    current = retry_counts.get(trace_id, 0)
    if current >= MAX_RETRY_PER_TRACE:
        return
    retry_counts[trace_id] = current + 1
    try:
        trace_queue.put_nowait(trace_id)
    except asyncio.QueueFull:
        print(f"⚠️  trace_queue 满了，重评丢弃 trace_id={trace_id}")


async def score_trace_async(
    trace_id: str,
    account_id: int,
    output_path: Path,
    metric_stats: dict,
    metric_names: list,
    model: str,
    api_key: str,
    base_url: str,
):
    if output_path.exists():
        async with file_lock:
            try:
                with open(output_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for rec in data.get("records", []):
                    if rec.get("trace_id") == trace_id:
                        return True, False
            except Exception:
                pass

    db = SessionLocal()
    try:
        traces = (
            db.query(AgentTrace)
            .filter(AgentTrace.account_id == account_id)
            .filter(AgentTrace.trace_id == trace_id)
            .filter(AgentTrace.created_at >= MIN_TRACE_DATETIME_UTC)
            .filter(AgentTrace.role != "tool")
            .filter(AgentTrace.content.isnot(None))
            .order_by(AgentTrace.step_number.asc())
            .all()
        )

        if not traces:
            return False, False

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
                    metric,
                    model,
                    api_key,
                    base_url,
                )
                for metric in metric_names
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        retry_needed = False
        for metric, result in zip(metric_names, results):
            if isinstance(result, Exception):
                record["evaluations"][metric] = {"score": -1, "error": str(result)}
            else:
                record["evaluations"][metric] = result
                score = result.get("score", -1)
                if score >= 0:
                    metric_stats[metric].update(result["score"])
                else:
                    record["evaluations"][metric].setdefault(
                        "score_extraction_error",
                        "no_score_found",
                    )
                    retry_needed = True

        async with file_lock:
            with open(output_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            data["records"].append(record)
            data["metric_statistics"] = {
                k: v.to_dict() for k, v in metric_stats.items()
            }

            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

        print(f"✅ 完成 trace_id={trace_id}")
        return True, retry_needed

    finally:
        db.close()


# ===================== Monitor =====================

class TraceNotifyProtocol(asyncio.DatagramProtocol):
    def __init__(self, account_id: int):
        self.account_id = account_id

    def datagram_received(self, data: bytes, addr):
        try:
            payload = json.loads(data.decode("utf-8"))
        except Exception:
            return

        trace_id = payload.get("trace_id")
        account_id = payload.get("account_id")
        if not trace_id:
            return
        if str(account_id) != str(self.account_id):
            return
        if trace_id in processed_trace_ids:
            return
        try:
            trace_queue.put_nowait(trace_id)
        except asyncio.QueueFull:
            print(f"⚠️  trace_queue 满了，丢弃 trace_id={trace_id}")


async def trace_consumer(
    account_id: int,
    output_file: Path,
    metric_stats: dict,
    metric_names: list,
    model: str,
    api_key: str,
    base_url: str,
):
    while True:
        trace_id = await trace_queue.get()
        try:
            if trace_id in processed_trace_ids:
                continue
            ok, retry_needed = await score_trace_async(
                trace_id=trace_id,
                account_id=account_id,
                output_path=output_file,
                metric_stats=metric_stats,
                metric_names=metric_names,
                model=model,
                api_key=api_key,
                base_url=base_url,
            )
            if ok:
                mark_processed(trace_id)
                if retry_needed:
                    enqueue_retry(trace_id)
        finally:
            trace_queue.task_done()


def is_trace_finished(content: str) -> bool:
    if not content:
        return False
    # 1) Try JSON fragments first (most reliable)
    try:
        for m in re.finditer(r"\{[\s\S]*?\}", content):
            obj = json.loads(m.group(0))
            if isinstance(obj, dict) and str(obj.get("next_action", "")).lower() == "finish":
                return True
    except Exception:
        pass

    # 2) Fallback regex (tolerate quotes and spacing)
    normalized = re.sub(r"\s+", "", content)
    if re.search(r'["\']?next_action["\']?\s*[:=]\s*["\']?finish["\']?', normalized, re.IGNORECASE):
        return True

    patterns = [
        r'next_action\s*[:=]\s*["\']?finish["\']?',
        r'"next_action"\s*:\s*"finish"',
        r"'next_action'\s*:\s*'finish'",
    ]
    return any(re.search(pat, content, re.IGNORECASE) for pat in patterns)


def preload_processed_trace_ids(output_file: Path) -> None:
    if not output_file.exists():
        return
    try:
        with open(output_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        for rec in data.get("records", []):
            trace_id = rec.get("trace_id")
            if trace_id:
                mark_processed(trace_id)
    except Exception as e:
        print(f"⚠️  读取历史评测结果失败: {e}")


def repair_scores_in_file(output_file: Path) -> int:
    if not output_file.exists():
        return 0
    try:
        with open(output_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"⚠️  读取评测结果失败: {e}")
        return 0

    changed = 0
    for rec in data.get("records", []):
        evaluations = rec.get("evaluations") or {}
        for metric, res in evaluations.items():
            if not isinstance(res, dict):
                continue
            score = res.get("score")
            if score in (0, 1, 2, 3, 4, 0.0, 1.0, 2.0, 3.0, 4.0):
                continue
            cot = res.get("chain_of_thought") or ""
            new_score, _ = extract_score_and_cot(cot)
            if new_score in (0.0, 1.0, 2.0, 3.0, 4.0):
                res["score"] = new_score
                res.pop("score_extraction_error", None)
                changed += 1
            else:
                if res.get("score_extraction_error") != "no_score_found":
                    res["score_extraction_error"] = "no_score_found"
                    changed += 1

    if changed:
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    return changed


def enqueue_failed_retries_from_file(output_file: Path) -> int:
    if not output_file.exists():
        return 0
    try:
        with open(output_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return 0

    scheduled = 0
    for rec in data.get("records", []):
        trace_id = rec.get("trace_id")
        if not trace_id:
            continue
        evaluations = rec.get("evaluations") or {}
        for res in evaluations.values():
            if not isinstance(res, dict):
                continue
            if res.get("score") == -1 and res.get("score_extraction_error") == "no_score_found":
                enqueue_retry(trace_id)
                scheduled += 1
                break
    return scheduled


def enqueue_finished_traces_from_db(account_id: int) -> None:
    db = SessionLocal()
    try:
        rows = (
            db.query(AgentTrace.trace_id, AgentTrace.content)
            .filter(AgentTrace.account_id == account_id)
            .filter(AgentTrace.created_at >= MIN_TRACE_DATETIME_UTC)
            .filter(AgentTrace.role != "tool")
            .filter(AgentTrace.content.isnot(None))
            .all()
        )
        finished = set()
        for trace_id, content in rows:
            if trace_id in processed_trace_ids:
                continue
            if is_trace_finished(content):
                finished.add(trace_id)
        for trace_id in finished:
            trace_queue.put_nowait(trace_id)
    finally:
        db.close()


def parse_min_date_utc(date_str: str) -> datetime:
    return datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def resolve_listen_port(account_id: int, listen_port: int | None, listen_port_base: int) -> int:
    if listen_port is not None:
        return listen_port
    return listen_port_base + int(account_id)


async def monitor(
    account_id: int,
    model: str,
    listen_host: str,
    listen_port: int | None,
    listen_port_base: int,
    repair_existing: bool,
    min_date: str,
):
    if model not in MODEL_CONFIG:
        raise ValueError(f"Unknown model: {model}")

    api_key = MODEL_CONFIG[model]["api_key"]
    base_url = MODEL_CONFIG[model]["base_url"]
    global MIN_TRACE_DATETIME_UTC
    MIN_TRACE_DATETIME_UTC = parse_min_date_utc(min_date)

    output_dir = Path("./eval_results") / f"account_{account_id}"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"{model.replace('/', '_')}.json"

    metric_names = ["metric_a", "metric_b", "metric_c", "metric_d", "metric_e", "metric_f"]
    metric_stats = {m: RunningStats() for m in metric_names}

    if not output_file.exists():
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "account_id": account_id,
                    "model": model,
                    "records": [],
                    "metric_statistics": {},
                    "created_at": datetime.utcnow().isoformat(),
                },
                f,
                ensure_ascii=False,
                indent=2,
            )

    if repair_existing:
        changed = repair_scores_in_file(output_file)
        if changed:
            print(f"🛠️  修复了 {changed} 个历史评分")
        scheduled = enqueue_failed_retries_from_file(output_file)
        if scheduled:
            print(f"🔁 已安排 {scheduled} 条历史失败重评")
    preload_processed_trace_ids(output_file)
    enqueue_finished_traces_from_db(account_id)

    listen_port = resolve_listen_port(account_id, listen_port, listen_port_base)

    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: TraceNotifyProtocol(account_id),
        local_addr=(listen_host, listen_port),
        family=socket.AF_INET,
    )

    print(
        f"👀 开始监听 account={account_id}, model={model}, udp={listen_host}:{listen_port}"
    )

    consumer = asyncio.create_task(
        trace_consumer(
            account_id=account_id,
            output_file=output_file,
            metric_stats=metric_stats,
            metric_names=metric_names,
            model=model,
            api_key=api_key,
            base_url=base_url,
        )
    )

    try:
        await consumer
    finally:
        transport.close()


# ===================== Main =====================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--account_id", type=int, required=True)
    parser.add_argument("--model", type=str, required=True)
    parser.add_argument("--listen_host", type=str, default="127.0.0.1")
    parser.add_argument("--listen_port", type=int, default=None)
    parser.add_argument("--listen_port_base", type=int, default=9009)
    parser.add_argument("--repair_existing", action="store_true")
    parser.add_argument("--min_date", type=str, default="2026-02-08")
    args = parser.parse_args()

    asyncio.run(
        monitor(
            args.account_id,
            args.model,
            args.listen_host,
            args.listen_port,
            args.listen_port_base,
            args.repair_existing,
            args.min_date,
        )
    )


if __name__ == "__main__":
    main()
