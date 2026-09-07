#!/usr/bin/env python3
"""初始化近期情绪明细（dialogue_emotion_raw）—— 大数据量批量版（独立脚本，不依赖项目内代码）。

以 Dialogue（对话明细）为分批单位，针对用户间数据量分布不均的场景优化：

- 一次性游标查询取回全部剩余明细（只扫描排序一次），再按批写入 PG，
  不受「个别用户对话数极多」的分布倾斜影响；
- 每批明细在单个 PG 事务内批量 Upsert（整批原子：一批要么全部写入、
  要么全部不写），批内按 1000 行/片分片执行；
- 失败兜底：单批内 Neo4j 查询 / PG 写入各自即时重试（最多 MAX_RETRY_ROUNDS 次，
  间隔 5 秒），重试耗尽则脚本中止——断点停在最后一个成功批的游标，
  排查后直接重跑脚本即从该游标继续，已成功批次不重复处理；
- Upsert 幂等（按 Dialogue.id 主键）：脚本可安全重复执行；
- 断点续传：游标和运行上下文以 JSON 写入 CHECKPOINT_PATH；只有环境、数据库目标、
  统计窗口和天数完全一致时才续传（删除该文件可强制全量重跑）；
- 全部批次成功后自动清除断点文件；
- 过程输出：批次开始/取回条数/写入完成/失败提醒 + 15 秒保活提示 + 汇总进度行。

独立运行：PostgreSQL/Neo4j 连接信息从环境变量读取，
不读取项目 .env，也不 import app 包。

明细不切日（一行对话一行，naive UTC），切日/聚合由查询接口按请求时区实时完成；
统计窗口截至最西时区（UTC-12）昨日末 = 北京今天 20:00，
并从该截止时间向前回溯 --recent-days 指定的天数（默认 7 天）。

Usage:
    python scripts/init_emotion_stats.py                          # 全量（默认近 7 天）
    python scripts/init_emotion_stats.py --recent-days 14         # 自定义统计窗口天数
    python scripts/init_emotion_stats.py --end-user-id <uuid>     # 仅指定用户（补数，无视断点）
    python scripts/init_emotion_stats.py --batch-size 5000        # 自定义每批条数
"""
import argparse
import asyncio
import json
import os
import pathlib
import sys
import uuid as uuid_lib
from datetime import datetime, time, timedelta, timezone
from urllib.parse import urlsplit

# 实时输出：print 在管道/重定向场景默认块缓冲，逐批进度会攒到进程结束才一次性
# 打出；改为行缓冲后每条 print 立即刷出，批次开始/完成/进度实时可见
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)

# =====================================================================
# 独立连接配置（从环境变量读取，不依赖项目 .env 或 app 包）
# =====================================================================


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"缺少必填环境变量: {name}")
    return value


APP_ENV = _required_env("APP_ENV").lower()
if APP_ENV not in {"test", "pre", "prod"}:
    raise ValueError("APP_ENV 必须是 test、pre 或 prod")

POSTGRES_CONFIG = {
    "host": _required_env("DB_HOST"),
    "port": int(os.getenv("DB_PORT", "5432")),
    "database": _required_env("DB_NAME"),
    "user": _required_env("DB_USER"),
    "password": _required_env("DB_PASSWORD"),
}

NEO4J_CONFIG = {
    "uri": _required_env("NEO4J_URI"),
    "username": _required_env("NEO4J_USERNAME"),
    "password": _required_env("NEO4J_PASSWORD"),
    "max_pool_size": int(os.getenv("NEO4J_MAX_POOL_SIZE", "30")),
    "connection_timeout": float(os.getenv("NEO4J_CONN_TIMEOUT", "30.0")),
}


def _validate_connection_config() -> None:
    """检查环境变量中的必填连接项，避免空配置发起难以定位的连接。"""
    required = {
        "POSTGRES_CONFIG": (POSTGRES_CONFIG, ("host", "database", "user", "password")),
        "NEO4J_CONFIG": (NEO4J_CONFIG, ("uri", "username", "password")),
    }
    missing = [
        f"{section}.{key}"
        for section, (config, keys) in required.items()
        for key in keys
        if not str(config.get(key, "")).strip()
    ]
    if missing:
        raise ValueError(f"连接配置缺少必填项: {', '.join(missing)}")


def _neo4j_target() -> str:
    neo4j_uri = urlsplit(NEO4J_CONFIG["uri"])
    neo4j_host = neo4j_uri.hostname or "<invalid-host>"
    neo4j_port = f":{neo4j_uri.port}" if neo4j_uri.port else ""
    return f"{neo4j_uri.scheme}://{neo4j_host}{neo4j_port}"


def _print_target_config() -> None:
    print(f"[环境] APP_ENV={APP_ENV}")
    print(
        f"[目标] PostgreSQL={POSTGRES_CONFIG['host']}:{POSTGRES_CONFIG['port']}/"
        f"{POSTGRES_CONFIG['database']}"
    )
    print(f"[目标] Neo4j={_neo4j_target()}")
    print(f"[断点] CHECKPOINT_PATH={CHECKPOINT_PATH}")

# 默认统计窗口天数（截止时间向前回溯，可通过 --recent-days 修改）
DEFAULT_RECENT_DAYS = 7

# 每批处理的明细条数（按 Dialogue 为单位分批：批与批耗时均匀、
# 进度实时可见；单批失败只重做一批）
DEFAULT_BATCH_SIZE = 1000
# 单条 Upsert 语句的最大行数（分片写入，避免单语句过大）
UPSERT_CHUNK_SIZE = 1000
# 失败批自动重试轮数上限（防死循环；达到上限仍有失败则打印明细并 exit 1）
MAX_RETRY_ROUNDS = 10
# 重试轮间隔秒数（给临时性故障如网络抖动留恢复时间）
RETRY_ROUND_INTERVAL_SECONDS = 5

# 断点默认按环境隔离；Kubernetes 跨 Pod 续跑时通过 CHECKPOINT_PATH 指向 PVC。
# 文件只保存脱敏目标和运行上下文，不保存数据库用户名或密码。
CHECKPOINT_PATH = pathlib.Path(
    os.getenv(
        "CHECKPOINT_PATH",
        f"/tmp/init_emotion_stats.{APP_ENV}.checkpoint.json",
    )
).expanduser()
CHECKPOINT_VERSION = 1


# =====================================================================
# 独立连接层（替代 app.db / Neo4jConnector）
# =====================================================================

def _pg_engine():
    """按脚本内 POSTGRES_CONFIG 创建同步 engine（psycopg2）。"""
    from sqlalchemy import create_engine
    from sqlalchemy.engine import URL

    _validate_connection_config()
    url = URL.create(
        "postgresql+psycopg2",
        username=POSTGRES_CONFIG["user"],
        password=POSTGRES_CONFIG["password"],
        host=POSTGRES_CONFIG["host"],
        port=int(POSTGRES_CONFIG["port"]),
        database=POSTGRES_CONFIG["database"],
    )
    return create_engine(url, pool_pre_ping=True)


def _neo4j_driver():
    """按脚本内 NEO4J_CONFIG 创建 neo4j driver。"""
    from neo4j import GraphDatabase

    _validate_connection_config()
    return GraphDatabase.driver(
        NEO4J_CONFIG["uri"],
        auth=(NEO4J_CONFIG["username"], NEO4J_CONFIG["password"]),
        max_connection_pool_size=int(NEO4J_CONFIG["max_pool_size"]),
        connection_timeout=float(NEO4J_CONFIG["connection_timeout"]),
    )


def _to_naive_utc(value) -> datetime:
    """Neo4j created_at 值 → naive UTC datetime（与 PG 存储口径一致）

    兼容三种返回形态：neo4j DateTime（to_native）、原生 datetime、ISO 字符串。
    aware 时间统一转 UTC 后去 tzinfo。
    """
    if hasattr(value, "to_native"):
        value = value.to_native()
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


# =====================================================================
# 独立数据访问（替代 DialogueEmotionRepository / upsert_raw_batch）
# =====================================================================

_GET_RAW_DIALOGUES_BATCH_CYPHER = """
MATCH (d:Dialogue)
WHERE d.end_user_id IN $end_user_ids
  AND d.emotion IS NOT NULL
  AND d.created_at IS NOT NULL
  AND d.id IS NOT NULL
  AND d.created_at >= $start_dt AND d.created_at < $end_dt
RETURN d.end_user_id AS end_user_id, d.id AS id,
       d.created_at AS created_at, d.emotion AS emotion
"""

# 按 Dialogue.id 游标翻页：批与批互不重叠、数据变动也不漂移（keyset 分页），
# 每批固定 LIMIT，耗时均匀；游标 = 上一批最后一行的 id
_GET_RAW_DIALOGUES_PAGE_CYPHER = """
MATCH (d:Dialogue)
WHERE d.emotion IS NOT NULL
  AND d.created_at IS NOT NULL
  AND d.id IS NOT NULL
  AND d.created_at >= $start_dt AND d.created_at < $end_dt
  AND ($last_id IS NULL OR d.id > $last_id)
RETURN d.end_user_id AS end_user_id, d.id AS id,
       d.created_at AS created_at, d.emotion AS emotion
ORDER BY d.id
LIMIT $limit
"""

_COUNT_DIALOGUES_CYPHER = """
MATCH (d:Dialogue)
WHERE d.emotion IS NOT NULL
  AND d.created_at IS NOT NULL
  AND d.id IS NOT NULL
  AND d.created_at >= $start_dt AND d.created_at < $end_dt
RETURN count(*) AS cnt
"""

_COUNT_DIALOGUES_UPTO_CYPHER = """
MATCH (d:Dialogue)
WHERE d.emotion IS NOT NULL
  AND d.created_at IS NOT NULL
  AND d.id IS NOT NULL
  AND d.created_at >= $start_dt AND d.created_at < $end_dt
  AND d.id <= $last_id
RETURN count(*) AS cnt
"""

# execute_values 专用模板（%s 由 psycopg2 批量展开，单语句一次网络往返，
# 替代 executemany 的逐行往返——1000 行写入从 ~19 秒降到毫秒级）
_UPSERT_SQL_VALUES = """
INSERT INTO dialogue_emotion_raw
    (id, dialogue_id, end_user_id, created_at, emotion)
VALUES %s
ON CONFLICT (id) DO UPDATE SET
    dialogue_id = EXCLUDED.dialogue_id,
    end_user_id = EXCLUDED.end_user_id,
    created_at = EXCLUDED.created_at,
    emotion = EXCLUDED.emotion
"""

_PG_ENGINE = _pg_engine()


def dialogue_row_uuid(dialogue_id) -> uuid_lib.UUID:
    """Neo4j Dialogue.id → 确定性 uuid 主键（uuid5，同一对话恒同一主键）

    与 app.repositories.dialogue_emotion_raw_repository.dialogue_row_uuid
    保持完全相同的算法（NAMESPACE_URL + 原始字符串），保证独立脚本与
    服务端两条写入链路对同一对话生成同一主键，幂等不冲突。
    """
    return uuid_lib.uuid5(uuid_lib.NAMESPACE_URL, str(dialogue_id))


def upsert_raw_batch(rows: list) -> tuple:
    """批量 Upsert 一批明细（raw_connection + execute_values，整批单事务）

    Returns:
        (written, skipped_uids): 成功写入行数 + 因 end_user_id 非 uuid
        格式被跳过的明细的用户列表（原始值，可能重复）
    """
    from psycopg2.extras import execute_values

    def _to_uuid(value):
        try:
            return uuid_lib.UUID(str(value))
        except (ValueError, AttributeError, TypeError):
            return None

    valid: list = []
    skipped_uids: list = []
    for r in rows:
        uid = _to_uuid(r["end_user_id"])
        if uid is None:
            skipped_uids.append(str(r["end_user_id"]))
            continue
        valid.append(
            (
                dialogue_row_uuid(r["id"]),
                str(r["id"]),
                uid,
                r["created_at"],
                r["emotion"],
            )
        )

    written = 0
    if valid:
        conn = _PG_ENGINE.raw_connection()
        try:
            cur = conn.cursor()
            for i in range(0, len(valid), UPSERT_CHUNK_SIZE):
                chunk = valid[i : i + UPSERT_CHUNK_SIZE]
                execute_values(
                    cur,
                    _UPSERT_SQL_VALUES,
                    chunk,
                    page_size=UPSERT_CHUNK_SIZE,
                )
                written += len(chunk)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    return written, skipped_uids


async def get_raw_dialogues_batch(driver, end_user_ids: list, start_dt, end_dt) -> list:
    """一次 IN 查询整组用户的原始对话明细（async 接口，内部走线程）"""
    def _run() -> list:
        with driver.session() as s:
            records = s.run(
                _GET_RAW_DIALOGUES_BATCH_CYPHER,
                end_user_ids=end_user_ids,
                start_dt=start_dt,
                end_dt=end_dt,
            ).data()
        return [
            {
                "end_user_id": r["end_user_id"],
                "id": r["id"],
                "created_at": _to_naive_utc(r["created_at"]),
                "emotion": r["emotion"],
            }
            for r in records
        ]

    return await asyncio.to_thread(_run)


async def count_dialogues(driver, start_dt, end_dt, upto_id: str | None = None) -> int:
    """统计窗口内（可选仅统计 id <= upto_id）带情绪的 Dialogue 总数"""
    def _run() -> int:
        with driver.session() as s:
            cypher = _COUNT_DIALOGUES_UPTO_CYPHER if upto_id else _COUNT_DIALOGUES_CYPHER
            return s.run(
                cypher, start_dt=start_dt, end_dt=end_dt, last_id=upto_id
            ).single()["cnt"]

    return await asyncio.to_thread(_run)


async def fetch_raw_dialogues_page(
    driver, start_dt, end_dt, last_id: str | None, limit: int
) -> list:
    """按 Dialogue.id 游标取一页明细（last_id=None 表示从头开始）"""
    def _run() -> list:
        with driver.session() as s:
            records = s.run(
                _GET_RAW_DIALOGUES_PAGE_CYPHER,
                start_dt=start_dt,
                end_dt=end_dt,
                last_id=last_id,
                limit=limit,
            ).data()
        return [
            {
                "end_user_id": r["end_user_id"],
                "id": r["id"],
                "created_at": _to_naive_utc(r["created_at"]),
                "emotion": r["emotion"],
            }
            for r in records
        ]

    return await asyncio.to_thread(_run)


# =====================================================================
# 窗口计算（替代 EmotionStatsService.get_yesterday_beijing_window）
# =====================================================================

def get_yesterday_beijing_window(now_utc: datetime | None = None):
    """北京时间「昨天」对应的 UTC 窗口 [start, end)"""
    now_utc = now_utc or datetime.now(timezone.utc)
    beijing_today = (now_utc + timedelta(hours=8)).date()
    yesterday_beijing = beijing_today - timedelta(days=1)
    start_dt = datetime.combine(yesterday_beijing, time.min) - timedelta(hours=8)
    return start_dt, start_dt + timedelta(days=1)


# =====================================================================
# 断点续传
# =====================================================================

def _checkpoint_context(
    start_dt: datetime, end_dt: datetime, recent_days: int
) -> dict[str, object]:
    """构建可安全写入磁盘的运行上下文，不包含用户名和密码。"""
    return {
        "app_env": APP_ENV,
        "postgres_target": (
            f"{POSTGRES_CONFIG['host']}:{POSTGRES_CONFIG['port']}/"
            f"{POSTGRES_CONFIG['database']}"
        ),
        "neo4j_target": _neo4j_target(),
        "start_dt": start_dt.isoformat(),
        "end_dt": end_dt.isoformat(),
        "recent_days": recent_days,
    }


def _load_checkpoint_cursor(expected_context: dict[str, object]) -> str | None:
    """仅在版本和运行上下文完全匹配时返回断点游标。"""
    if not CHECKPOINT_PATH.exists():
        return None

    try:
        payload = json.loads(CHECKPOINT_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"[WARN] 断点文件无法读取，忽略并从头开始: {exc}")
        return None

    if not isinstance(payload, dict) or payload.get("version") != CHECKPOINT_VERSION:
        print("[WARN] 断点文件版本不兼容，忽略并从头开始")
        return None

    actual_context = payload.get("context")
    if actual_context != expected_context:
        actual_context = actual_context if isinstance(actual_context, dict) else {}
        changed = sorted(
            key
            for key in set(actual_context) | set(expected_context)
            if actual_context.get(key) != expected_context.get(key)
        )
        changed_text = ", ".join(changed) or "未知字段"
        print(f"[WARN] 断点运行上下文不匹配（{changed_text}），忽略并从头开始")
        return None

    cursor = payload.get("cursor")
    if not isinstance(cursor, str) or not cursor.strip():
        print("[WARN] 断点文件缺少有效游标，忽略并从头开始")
        return None
    return cursor.strip()


def _save_checkpoint_cursor(
    last_id: str, checkpoint_context: dict[str, object]
) -> None:
    """原子写入游标和运行上下文，避免中断留下不完整文件。"""
    payload = {
        "version": CHECKPOINT_VERSION,
        "context": checkpoint_context,
        "cursor": last_id,
    }
    CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = CHECKPOINT_PATH.with_name(
        f".{CHECKPOINT_PATH.name}.{uuid_lib.uuid4().hex}.tmp"
    )
    try:
        with temp_path.open("w", encoding="utf-8") as checkpoint_file:
            json.dump(payload, checkpoint_file, ensure_ascii=False, indent=2)
            checkpoint_file.write("\n")
            checkpoint_file.flush()
            os.fsync(checkpoint_file.fileno())
        os.replace(temp_path, CHECKPOINT_PATH)
    finally:
        temp_path.unlink(missing_ok=True)


async def _upsert_rows(rows) -> tuple:
    """批量 Upsert 一批对话明细到 PG（同步 DB 操作放线程执行）

    Returns:
        (written, skipped_uids): 见 upsert_raw_batch
    """
    if not rows:
        return 0, []

    return await asyncio.to_thread(upsert_raw_batch, rows)


async def run(
    only_end_user_id: str | None, batch_size: int, recent_days: int
) -> None:
    driver = _neo4j_driver()
    try:
        # 统计窗口：最西时区（UTC-12）昨日末（= 北京今天 20:00）
        # 从截止时间向前回溯 recent_days 天，覆盖对应天数内所有时区的数据。
        _, beijing_today_start = get_yesterday_beijing_window()
        end_dt = beijing_today_start + timedelta(hours=20)
        start_dt = end_dt - timedelta(days=recent_days)
        t0 = datetime.now(timezone.utc)

        def _elapsed() -> str:
            """格式化已用时长 mm分ss秒"""
            secs = int((datetime.now(timezone.utc) - t0).total_seconds())
            return f"{secs // 60}分{secs % 60:02d}秒"

        async def _heartbeat(label: str) -> None:
            """长耗时阶段保活提示：每 15 秒打印一次，避免终端长时间静默"""
            start = datetime.now(timezone.utc)
            while True:
                await asyncio.sleep(15)
                secs = int((datetime.now(timezone.utc) - start).total_seconds())
                print(f"  ... {label}进行中，已等 {secs} 秒")

        async def _fetch_with_heartbeat(label: str, coro) -> list:
            """执行取数协程，期间每 15 秒打印保活提示"""
            hb = asyncio.create_task(_heartbeat(label))
            try:
                return await coro
            finally:
                hb.cancel()
                try:
                    await hb
                except asyncio.CancelledError:
                    pass

        async def _write_rows(rows: list) -> int:
            """Upsert 一批明细，打印跳过名单（end_user_id 非 uuid 的行），返回写入条数"""
            written, skipped_uids = await _upsert_rows(rows)
            if skipped_uids:
                uniq = sorted(set(skipped_uids))
                preview = ", ".join(uniq[:10])
                more = f" ...等共 {len(uniq)} 个用户" if len(uniq) > 10 else ""
                print(
                    f"跳过 {len(skipped_uids)} 条明细（end_user_id 非 uuid 格式，"
                    f"去重 {len(uniq)} 个用户）: {preview}{more}"
                )
            return written

        total_dialogues = 0

        # 单用户补数模式：一次取该用户全量明细，强制重算，不受断点续传影响
        if only_end_user_id:
            print(f"单用户模式: {only_end_user_id}，统计窗口 [{start_dt}, {end_dt})")
            rows = await _fetch_with_heartbeat(
                "单用户 Neo4j 明细查询",
                get_raw_dialogues_batch(
                    driver, [only_end_user_id], start_dt=start_dt, end_dt=end_dt
                ),
            )
            print(f"取回 {len(rows)} 条明细，开始写入 PG")
            written = await _write_rows(rows)
            print(f"\n完成: 单用户同步 {written} 条情绪 Dialogue")
            return

        # 全量模式：一次性游标查询取回全部剩余明细（只排序一次），
        # 再按 batch_size 条/批写入 PG——每批写完即落断点、打进度
        total_cnt = await count_dialogues(driver, start_dt, end_dt)
        checkpoint_context = _checkpoint_context(start_dt, end_dt, recent_days)
        cursor = _load_checkpoint_cursor(checkpoint_context)
        done_before = (
            await count_dialogues(driver, start_dt, end_dt, upto_id=cursor)
            if cursor
            else 0
        )
        remaining = total_cnt - done_before
        print(
            f"统计窗口 [{start_dt}, {end_dt})，窗口内明细 {total_cnt} 条，"
            f"每批写入 {batch_size} 条"
        )
        if cursor:
            print(
                f"断点续传: 从游标 {cursor} 继续（已入库约 {done_before} 条，"
                f"剩 {remaining} 条；删除 {CHECKPOINT_PATH.name} 可强制全量重跑）"
            )
        if remaining <= 0:
            CHECKPOINT_PATH.unlink(missing_ok=True)
            print("全部明细均已入库，无需处理；断点文件已清除")
            return
        est_batches = (remaining + batch_size - 1) // batch_size

        def _progress(batch_no: int) -> None:
            print(
                f"[进度] 累计写入 {total_dialogues}/{remaining} 条 | "
                f"第 {batch_no}/{est_batches} 批完成 | 已用 {_elapsed()}"
            )

        # —— Neo4j 一次性取回剩余明细（即时重试；耗尽则中止）——
        rows, fetch_err = None, None
        for attempt in range(1, MAX_RETRY_ROUNDS + 1):
            try:
                rows = await _fetch_with_heartbeat(
                    "Neo4j 明细查询（一次性游标读取）",
                    fetch_raw_dialogues_page(
                        driver, start_dt, end_dt, cursor, remaining
                    ),
                )
                break
            except Exception as e:
                fetch_err = e
                print(
                    f"\n[FAIL] Neo4j 查询失败（第 {attempt}/{MAX_RETRY_ROUNDS} 次）: {e}"
                )
                if attempt < MAX_RETRY_ROUNDS:
                    print(f"[FAIL] {RETRY_ROUND_INTERVAL_SECONDS} 秒后自动重试，无需人工干预\n")
                    await asyncio.sleep(RETRY_ROUND_INTERVAL_SECONDS)
        if rows is None:
            print(
                f"\n[FAIL] 查询重试耗尽，脚本中止。最后错误: {fetch_err}。"
                "排查网络/Neo4j 后直接重跑脚本即可"
            )
            sys.exit(1)

        print(f"查询完成，待写入 {len(rows)} 条明细，按 {batch_size} 条/批开始写入 PG")

        # —— 按 batch_size 条/批写入（批内即时重试；整批事务原子）——
        cursor_now = cursor
        batch_no = 0
        for i in range(0, len(rows), batch_size):
            batch = rows[i:i + batch_size]
            batch_no += 1
            print(f"[RUN] 第 {batch_no}/{est_batches} 批开始写入（{len(batch)} 条）")
            written, upsert_err = 0, None
            write_ok = False
            for attempt in range(1, MAX_RETRY_ROUNDS + 1):
                try:
                    written = await _write_rows(batch)
                    write_ok = True
                    break
                except Exception as e:
                    upsert_err = e
                    print(
                        f"\n[FAIL] 第 {batch_no} 批写入 PG 失败"
                        f"（第 {attempt}/{MAX_RETRY_ROUNDS} 次）: {e}"
                    )
                    if attempt < MAX_RETRY_ROUNDS:
                        print(
                            f"[FAIL] {RETRY_ROUND_INTERVAL_SECONDS} 秒后自动重试，"
                            f"无需人工干预\n"
                        )
                        await asyncio.sleep(RETRY_ROUND_INTERVAL_SECONDS)
            # 注意：不能用 written == 0 判定失败——一批全为「end_user_id 非 uuid
            # 被跳过」的行时 written 合法为 0，误判会 exit 1 且断点不推进，
            # 导致重跑永远卡在同一批
            if not write_ok:
                print(
                    f"\n[FAIL] 第 {batch_no} 批写入重试耗尽，脚本中止。"
                    f"最后错误: {upsert_err}。"
                    f"断点停在游标 {cursor_now or '起点'}，"
                    f"排查后直接重跑脚本将从该处继续"
                )
                sys.exit(1)

            cursor_now = batch[-1]["id"]
            total_dialogues += written
            print(f"[OK] 第 {batch_no} 批完成（写入 {written} 条）")
            _progress(batch_no)
            await asyncio.to_thread(
                _save_checkpoint_cursor, cursor_now, checkpoint_context
            )

        # 完成后对账：count 与一次性取数（LIMIT=remaining）之间窗口数据变动时，
        # id 序尾部的明细会被截断漏采——漏的可能是新写入行（次日定时同步覆盖），
        # 也可能是被新行挤出尾部的存量行（7 天内任意日期，48h 定时同步不覆盖，
        # 只能重跑本脚本补齐），故仅告警不阻断
        final_cnt = await count_dialogues(driver, start_dt, end_dt)
        if final_cnt != total_cnt:
            print(
                f"[WARN] 运行期间窗口明细数发生变化（{total_cnt} → {final_cnt}），"
                f"id 序尾部明细可能漏采：新写入行次日定时同步自愈；"
                f"被挤出的存量行需重跑本脚本补齐（Upsert 幂等，重跑安全）"
            )

        print(
            f"\n完成: 共 {batch_no} 批，同步 {total_dialogues} 条情绪 Dialogue"
            f"（窗口明细 {total_cnt} 条），已用 {_elapsed()}"
        )
        CHECKPOINT_PATH.unlink(missing_ok=True)
        print("断点文件已清除（全部批次成功）")
    finally:
        driver.close()


def main():
    _print_target_config()
    parser = argparse.ArgumentParser(description="初始化近期情绪明细（dialogue_emotion_raw，独立脚本）")
    parser.add_argument(
        "--end-user-id",
        default=None,
        help="仅同步指定终端用户（默认全量所有用户）",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"每批处理的明细条数（按 Dialogue 分批，默认 {DEFAULT_BATCH_SIZE}）",
    )
    parser.add_argument(
        "--recent-days",
        type=int,
        default=DEFAULT_RECENT_DAYS,
        help=f"统计窗口天数（截止时间向前回溯，默认 {DEFAULT_RECENT_DAYS} 天）",
    )
    args = parser.parse_args()
    if args.recent_days < 1:
        parser.error("--recent-days 必须大于等于 1")
    asyncio.run(run(args.end_user_id, max(1, args.batch_size), args.recent_days))


if __name__ == "__main__":
    main()
