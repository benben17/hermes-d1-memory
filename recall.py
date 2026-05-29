import json
import time
from typing import Dict, List

DEFAULT_ALLOWED_KINDS = [
    "user_preference",
    "environment_fact",
    "project_convention",
    "correction",
    "stable_fact",
    "manual_note",
]


def rank_results(rows: List[Dict]) -> List[Dict]:
    now = int(time.time())
    ranked = []
    for row in rows:
        metadata = {}
        try:
            metadata = json.loads(row.get("metadata") or "{}")
        except Exception:
            metadata = {}
        age_days = max(0, (now - int(row.get("updated_at") or row.get("created_at") or now)) // 86400)
        recency_bonus = max(0, 30 - min(age_days, 30)) / 100.0
        importance_bonus = (int(row.get("importance") or 3) - 3) * 0.25
        exact_topic_bonus = 0.2 if row.get("topic") else 0.0
        bm25_score = float(row.get("score") or 0.0)
        total = (-bm25_score) + importance_bonus + recency_bonus + exact_topic_bonus
        item = dict(row)
        item["metadata_obj"] = metadata
        item["total_score"] = total
        ranked.append(item)
    ranked.sort(key=lambda x: x["total_score"], reverse=True)
    return ranked


def build_prompt_block(rows: List[Dict], max_items: int = 4, max_chars: int = 800) -> str:
    if not rows:
        return ""
    out = ["Relevant durable memories (Cloudflare D1):"]
    total = len(out[0])
    for row in rows[:max_items]:
        kind = row.get("kind") or "fact"
        topic = row.get("topic") or ""
        prefix = f"- [{kind}] "
        if topic:
            prefix += f"({topic}) "
        line = prefix + (row.get("content") or "")
        if total + len(line) + 1 > max_chars:
            break
        out.append(line)
        total += len(line) + 1
    return "\n".join(out)
