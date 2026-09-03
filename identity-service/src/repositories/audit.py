"""audit_logs 落库（网关无 DB 层，审计经 identity 消费者批量写入）。"""
import json

from sqlalchemy import text


async def insert_audit_logs(session, items: list[dict]) -> None:
    rows = json.dumps(items, ensure_ascii=False)
    # event_id 幂等键：消费者 PEL 重投（崩溃/XACK 失败）时 ON CONFLICT 去重，不产生重复行
    await session.execute(text("""
        INSERT INTO audit_logs (event_type, actor_id, tenant_id, target, result, detail, ts, event_id)
        SELECT * FROM jsonb_to_recordset(CAST(:rows AS jsonb))
            AS x(event_type text, actor_id text, tenant_id text, target text, result text,
                 detail jsonb, ts timestamptz, event_id uuid)
        ON CONFLICT (event_id) DO NOTHING
    """), {"rows": rows})
    await session.commit()
