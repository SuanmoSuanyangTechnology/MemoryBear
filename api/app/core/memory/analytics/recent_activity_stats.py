import os
import re
import glob
import json
from typing import Tuple

try:
    from app.core.memory.utils.config.definitions import PROJECT_ROOT
except Exception:
    # Fallback: derive project root from this file location
    # 当前文件在 api/app/core/memory/analytics/recent_activity_stats.py
    # 需要向上 5 级到达 api/ 目录
    current_file = os.path.abspath(__file__)
    PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(current_file)))))


def _get_latest_prompt_log_path() -> str | None:
    """Return the latest prompt log file path under PROJECT_ROOT/logs, or None."""
    logs_dir = os.path.join(PROJECT_ROOT, "logs", "prompts")
    if not os.path.isdir(logs_dir):
        return None

    files = glob.glob(os.path.join(logs_dir, "prompt_logs-*.log"))
    if not files:
        return None

    # Choose by modified time descending
    files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return files[0]


def _get_all_prompt_logs() -> list[str]:
    """Return all log file paths under logs dirs sorted by mtime ascending.

    It checks both PROJECT_ROOT/logs/prompts and CWD/logs/prompts to be robust.
    """
    candidates = []
    pr_logs = os.path.join(PROJECT_ROOT, "logs", "prompts")
    cwd_logs = os.path.join(os.getcwd(), "logs", "prompts")
    for d in [pr_logs, cwd_logs]:
        if os.path.isdir(d):
            candidates.extend(glob.glob(os.path.join(d, "prompt_logs-*.log")))

    # Deduplicate and sort
    files = sorted(set(candidates), key=lambda p: os.path.getmtime(p))
    return files


def _get_any_logs_recursive() -> list[str]:
    """Fallback: search for any .log files under PROJECT_ROOT recursively."""
    files = glob.glob(os.path.join(PROJECT_ROOT, "**", "*.log"), recursive=True)
    files.sort(key=lambda p: os.path.getmtime(p))
    return files


def parse_stats_from_log(log_path: str) -> dict:
    """
    Parse required statistics from a prompt log file.

    Returns dict with keys:
      - chunk_count: int (count of chunks processed)
      - statements_count: int (total statements processed for triplets)
      - triplet_entities_count: int (total entities extracted)
      - triplet_relations_count: int (total triplets/relations extracted)
      - temporal_count: int (extracted valid temporal ranges)
    """
    chunk_count = 0
    statements_count = 0
    triplet_entities_count = 0
    triplet_relations_count = 0
    temporal_count = 0

    # 正则表达式模式 - 匹配当前日志格式
    pat_chunk_render = re.compile(r"===\s*RENDERED\s*STATEMENT\s*EXTRACTION\s*PROMPT\s*===")
    pat_triplet_started = re.compile(r"\[Triplet\]\s+Started\s+-\s+statement_id=")
    pat_triplet_completed = re.compile(
        r"\[Triplet\]\s+Completed\s+-\s+statement_id=[^,]+,\s+triplets=(\d+),\s+entities=(\d+)"
    )
    pat_temporal_completed = re.compile(
        r"\[Temporal\]\s+Completed\s+-\s+statement_id=[^,]+,\s+valid_ranges=(\d+)"
    )

    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            # 文本块数量（每个块触发一次陈述提取提示）
            if pat_chunk_render.search(line):
                chunk_count += 1
                continue

            # 陈述数量（每个 Triplet Started 代表一个陈述被处理）
            if pat_triplet_started.search(line):
                statements_count += 1
                continue

            # 三元组完成：[Triplet] Completed - statement_id=xxx, triplets=X, entities=Y
            m_triplet = pat_triplet_completed.search(line)
            if m_triplet:
                try:
                    triplet_relations_count += int(m_triplet.group(1))
                    triplet_entities_count += int(m_triplet.group(2))
                except Exception:
                    pass
                continue

            # 时间信息完成：[Temporal] Completed - statement_id=xxx, valid_ranges=X
            m_temporal = pat_temporal_completed.search(line)
            if m_temporal:
                try:
                    temporal_count += int(m_temporal.group(1))
                except Exception:
                    pass
                continue

    return {
        "chunk_count": chunk_count,
        "statements_count": statements_count,
        "triplet_entities_count": triplet_entities_count,
        "triplet_relations_count": triplet_relations_count,
        "temporal_count": temporal_count,
        "log_path": log_path,
    }


def get_recent_activity_stats() -> Tuple[dict, str]:
    """Get stats from the latest prompt log file only.

    Returns (stats_dict, message).
    """
    # 获取最新的日志文件
    latest_log = _get_latest_prompt_log_path()
    
    # 如果没有找到，尝试递归搜索
    if not latest_log:
        all_logs = _get_any_logs_recursive()
        if all_logs:
            latest_log = all_logs[-1]  # 取最新的
    
    if not latest_log:
        return (
            {
                "chunk_count": 0,
                "statements_count": 0,
                "triplet_entities_count": 0,
                "triplet_relations_count": 0,
                "temporal_count": 0,
                "log_path": None,
            },
            "未找到日志文件，请确认已运行过提取流程。",
        )

    # 只解析最新的日志文件
    stats = parse_stats_from_log(latest_log)
    
    # 添加日志文件路径信息
    stats["log_path"] = f"最新：{latest_log}"
    
    return stats, "成功读取最近一次记忆活动统计。"


def _format_summary(stats: dict) -> str:
    """Format a Chinese summary string from stats."""
    log_info = stats.get("log_path") or "(无)"
    return (
        "最近记忆活动统计\n"
        f"- 日志文件：{log_info}\n"
        f"- 数据分块：共 {stats.get('chunk_count', 0)} 块\n"
        f"- 句子提取：共 {stats.get('statements_count', 0)} 个句子\n"
        f"- 三元组提取：实体 {stats.get('triplet_entities_count', 0)} 个，关系 {stats.get('triplet_relations_count', 0)} 条\n"
        f"- 时间提取：共提取 {stats.get('temporal_count', 0)} 条时间信息\n"
    )


if __name__ == "__main__":
    stats, msg = get_recent_activity_stats()
    print(msg)
    print(_format_summary(stats))

    # --- 将结果写入统一的 Signboard.json ---
    try:
        # 使用全局配置的输出路径
        from app.core.config import settings
        settings.ensure_memory_output_dir()
        output_dir = settings.MEMORY_OUTPUT_DIR
        try:
            os.makedirs(output_dir, exist_ok=True)
        except Exception:
            pass
        signboard_path = os.path.join(output_dir, "Signboard.json")
        existing = {}
        if os.path.exists(signboard_path):
            with open(signboard_path, "r", encoding="utf-8") as rf:
                existing = json.load(rf)
        existing["recent_activity_stats"] = stats
        with open(signboard_path, "w", encoding="utf-8") as wf:
            json.dump(existing, wf, ensure_ascii=False, indent=2)
        print(f"已写入 {signboard_path} -> recent_activity_stats")
    except Exception as e:
        print(f"写入 Signboard.json 失败: {e}")
