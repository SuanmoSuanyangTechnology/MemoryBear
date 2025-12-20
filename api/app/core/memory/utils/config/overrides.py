"""
运行时配置覆写工具 - 统一配置加载器

本模块作为统一的配置加载器，负责从多个来源加载配置并按优先级覆写。

配置来源优先级（从高到低）：
1. 数据库配置（PostgreSQL data_config 表）
2. 环境变量配置（.env 文件）
3. 默认配置（runtime.json 文件）

支持的配置加载方式：
- 基于 config_id 的配置加载（从 dbrun.json 读取或前端传入）
- 基于 group_id 的配置加载（从 dbrun.json 读取）
- 环境变量覆写（支持 INTERNAL/EXTERNAL 网络模式）

主要功能：
- 从 PostgreSQL 数据库读取配置
- 从环境变量读取配置
- 从 runtime.json 读取默认配置
- 按优先级覆写配置项（仅在内存中，不修改文件）
- 支持多种配置字段：selections、statement_extraction、deduplication、forgetting_engine、pruning、reflexion

使用场景：
- 应用启动时自动加载配置
- 前端切换配置时动态重新加载
- 多租户场景下的配置隔离
- 内外网环境自动切换
"""
import os
import json
from typing import Optional, Dict, Any, Literal

NetworkMode = Literal['internal', 'external']


def _set_if_present(target: Dict[str, Any], target_key: str, src: Dict[str, Any], src_key: str, caster):
    """安全地设置目标字典的值（如果源字典中存在且不为 None）

    Args:
        target: 目标字典
        target_key: 目标字典的键
        src: 源字典
        src_key: 源字典的键
        caster: 类型转换函数
    """
    try:
        if src_key in src and src.get(src_key) is not None:
            try:
                target[target_key] = caster(src.get(src_key))
            except Exception:
                pass
    except Exception:
        pass


def _to_bool(val: Any) -> bool:
    """将各种类型的值转换为布尔值

    支持的输入：
    - bool: 直接返回
    - int/float: 非零为 True
    - str: "true", "1", "on", "yes" 为 True；"false", "0", "off", "no" 为 False

    Args:
        val: 要转换的值

    Returns:
        bool: 转换后的布尔值
    """
    try:
        if isinstance(val, bool):
            return val
        if isinstance(val, (int, float)):
            return bool(val)
        if isinstance(val, str):
            m = val.strip().lower()
            if m in {"true", "1", "on", "yes"}:
                return True
            if m in {"false", "0", "off", "no"}:
                return False
        return bool(val)
    except Exception:
        return False


def _make_pgsql_conn() -> Optional[object]:
    """创建 PostgreSQL 数据库连接

    使用环境变量配置连接参数：
    - DB_HOST: 数据库主机地址（默认 localhost）
    - DB_PORT: 数据库端口（默认 5432）
    - DB_USER: 数据库用户名
    - DB_PASSWORD: 数据库密码
    - DB_NAME: 数据库名称

    Returns:
        Optional[object]: 数据库连接对象，失败时返回 None
    """
    host = os.getenv("DB_HOST", "localhost")
    user = os.getenv("DB_USER")
    password = os.getenv("DB_PASSWORD")
    dbname = os.getenv("DB_NAME")
    port_str = os.getenv("DB_PORT")

    try:
        import psycopg2  # type: ignore

        port = int(port_str) if port_str else 5432
        conn = psycopg2.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            dbname=dbname,
        )
        conn.autocommit = True
        return conn
    except Exception:
        return None


def _fetch_db_config_by_group_id(group_id: str) -> Optional[Dict[str, Any]]:
    """根据 group_id 从数据库查询配置

    Args:
        group_id: 组标识符

    Returns:
        Optional[Dict[str, Any]]: 配置字典，未找到时返回 None
    """
    conn = _make_pgsql_conn()
    if conn is None:
        return None

    try:
        from psycopg2.extras import RealDictCursor  # type: ignore
        cur = conn.cursor(cursor_factory=RealDictCursor)

        try:
            cur.execute("SET TIME ZONE %s", ("Asia/Shanghai",))
        except Exception:
            pass

        sql = (
            "SELECT group_id, user_id, apply_id, chunker_strategy, "
            "       enable_llm_dedup_blockwise, enable_llm_disambiguation "
            "FROM data_config WHERE group_id = %s ORDER BY updated_at DESC LIMIT 1"
        )
        cur.execute(sql, (group_id,))
        row = cur.fetchone()
        return row if row else None
    except Exception:
        return None
    finally:
        try:
            cur.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass


def _fetch_db_config_by_config_id(config_id: int | str) -> Optional[Dict[str, Any]]:
    """根据 config_id 从数据库查询配置

    Args:
        config_id: 配置标识符（整数或字符串，会自动转换为整数）

    Returns:
        Optional[Dict[str, Any]]: 配置字典，未找到时返回 None
    """
    conn = _make_pgsql_conn()
    if conn is None:
        try:
            pass
        except Exception:
            pass
        return None

    try:
        from psycopg2.extras import RealDictCursor  # type: ignore
        cur = conn.cursor(cursor_factory=RealDictCursor)

        try:
            cur.execute("SET TIME ZONE %s", ("Asia/Shanghai",))
        except Exception:
            pass

        # config_id 在数据库中是 Integer 类型，需要转换
        try:
            config_id_int = int(config_id)
        except (ValueError, TypeError):
            try:
                pass
            except Exception:
                pass
            return None

        sql = (
            "SELECT config_id, group_id, user_id, apply_id, chunker_strategy, "
            "       enable_llm_dedup_blockwise, enable_llm_disambiguation, "
            "       deep_retrieval, t_type_strict, t_name_strict, t_overall, state, "
            "       statement_granularity, include_dialogue_context, max_context, "
            "       \"offset\" AS offset, lambda_time, lambda_mem, "
            "       pruning_enabled, pruning_scene, pruning_threshold, "
            "       llm_id, embedding_id, rerank_id "
            "FROM data_config WHERE config_id = %s LIMIT 1"
        )
        cur.execute(sql, (config_id_int,))
        row = cur.fetchone()
        
        if row:
            try:
                pass
            except Exception:
                pass
        else:
            pass
        
        return row if row else None
    except Exception:
        pass
        return None
    finally:
        try:
            cur.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass


def _load_dbrun_group_id(project_root: str) -> Optional[str]:
    """从 dbrun.json 读取 group_id

    Args:
        project_root: 项目根目录路径

    Returns:
        Optional[str]: group_id，未找到时返回 None
    """
    try:
        path = os.path.join(project_root, "dbrun.json")
        if not os.path.isfile(path):
            return None

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, dict):
            if "group_id" in data:
                return str(data.get("group_id"))
            sel = data.get("selections", {})
            if isinstance(sel, dict) and "group_id" in sel:
                return str(sel.get("group_id"))

        return None
    except Exception:
        return None


def _load_dbrun_config_id(project_root: str) -> Optional[str]:
    """从 dbrun.json 读取 config_id

    Args:
        project_root: 项目根目录路径

    Returns:
        Optional[str]: config_id，未找到时返回 None
    """
    try:
        path = os.path.join(project_root, "dbrun.json")
        if not os.path.isfile(path):
            return None

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, dict):
            if "config_id" in data:
                return str(data.get("config_id"))
            sel = data.get("selections", {})
            if isinstance(sel, dict) and "config_id" in sel:
                return str(sel.get("config_id"))

        return None
    except Exception:
        return None


def _apply_overrides_from_db_row(
    runtime_cfg: Dict[str, Any],
    db_row: Optional[Dict[str, Any]],
    identifier: str,
    identifier_type: str = "config_id"
) -> Dict[str, Any]:
    """从数据库行数据覆写运行时配置（统一处理函数）

    Args:
        runtime_cfg: 运行时配置字典
        db_row: 数据库查询结果行
        identifier: 标识符值（group_id 或 config_id）
        identifier_type: 标识符类型（"group_id" 或 "config_id"）

    Returns:
        Dict[str, Any]: 覆写后的运行时配置
    """
    try:
        selections = runtime_cfg.setdefault("selections", {})
        selections[identifier_type] = identifier

        if not db_row:
            return runtime_cfg

        # 覆写 selections 字段
        for tk in ("group_id", "user_id", "apply_id", "chunker_strategy", "state",
                   "t_type_strict", "t_name_strict", "t_overall",
                   "statement_granularity", "include_dialogue_context"):
            _set_if_present(selections, tk, db_row, tk, str)
        
        # 特殊处理 UUID 字段，确保转换为字符串格式
        for uuid_field in ("llm_id", "embedding_id", "rerank_id"):
            if uuid_field in db_row and db_row.get(uuid_field) is not None:
                try:
                    value = db_row.get(uuid_field)
                    # 如果是 UUID 对象，转换为字符串（带连字符的标准格式）
                    if hasattr(value, 'hex'):
                        selections[uuid_field] = str(value)
                    else:
                        selections[uuid_field] = str(value)
                except Exception:
                    pass

        # 覆写 statement_extraction 字段
        stmt = runtime_cfg.setdefault("statement_extraction", {})
        _set_if_present(stmt, "statement_granularity", db_row, "statement_granularity", int)
        _set_if_present(stmt, "include_dialogue_context", db_row, "include_dialogue_context", _to_bool)
        _set_if_present(stmt, "max_dialogue_context_chars", db_row, "max_context", int)

        # 覆写 deduplication 字段
        dedup = runtime_cfg.setdefault("deduplication", {})
        for tk in ("enable_llm_dedup_blockwise", "enable_llm_disambiguation"):
            _set_if_present(dedup, tk, db_row, tk, _to_bool)
        _set_if_present(dedup, "deep_retrieval", db_row, "deep_retrieval", _to_bool)

        # 覆写 forgetting_engine 字段
        forgetting = runtime_cfg.setdefault("forgetting_engine", {})
        _set_if_present(forgetting, "offset", db_row, "offset", float)
        _set_if_present(forgetting, "lambda_time", db_row, "lambda_time", float)
        _set_if_present(forgetting, "lambda_mem", db_row, "lambda_mem", float)

        # 覆写 pruning 字段
        pruning = runtime_cfg.setdefault("pruning", {})
        _set_if_present(pruning, "enabled", db_row, "pruning_enabled", _to_bool)
        _set_if_present(pruning, "scene", db_row, "pruning_scene", str)

        # 阈值需要转为 float，且限制在 [0.0, 0.9]
        try:
            if "pruning_threshold" in db_row and db_row.get("pruning_threshold") is not None:
                thr = float(db_row.get("pruning_threshold"))
                thr = max(0.0, min(0.9, thr))  # 限制在 [0.0, 0.9]
                pruning["threshold"] = thr
        except Exception:
            pass

        return runtime_cfg
    except Exception:
        pass
        return runtime_cfg


def apply_runtime_overrides_by_group(project_root: str, runtime_cfg: Dict[str, Any]) -> Dict[str, Any]:
    """基于 group_id 从数据库覆写运行时配置

    工作流程：
    1. 从 dbrun.json 读取 group_id
    2. 根据 group_id 查询数据库配置
    3. 覆写运行时配置（仅在内存中）

    Args:
        project_root: 项目根目录路径
        runtime_cfg: 运行时配置字典

    Returns:
        Dict[str, Any]: 覆写后的运行时配置
    """
    try:
        selected_gid = _load_dbrun_group_id(project_root)
        if not selected_gid:
            return runtime_cfg

        db_row = _fetch_db_config_by_group_id(selected_gid)
        if not db_row:
            # 如果数据库中没有配置，仍然设置 group_id
            runtime_cfg.setdefault("selections", {})["group_id"] = selected_gid
            return runtime_cfg

        return _apply_overrides_from_db_row(runtime_cfg, db_row, selected_gid, "group_id")
    except Exception:
        return runtime_cfg


def apply_runtime_overrides_by_config(project_root: str, runtime_cfg: Dict[str, Any]) -> Dict[str, Any]:
    """基于 config_id 从数据库覆写运行时配置（从 dbrun.json 读取）

    工作流程：
    1. 从 dbrun.json 读取 config_id
    2. 根据 config_id 查询数据库配置
    3. 覆写运行时配置（仅在内存中）

    Args:
        project_root: 项目根目录路径
        runtime_cfg: 运行时配置字典

    Returns:
        Dict[str, Any]: 覆写后的运行时配置
    """
    try:
        selected_cid = _load_dbrun_config_id(project_root)
        if not selected_cid:
            return runtime_cfg

        db_row = _fetch_db_config_by_config_id(selected_cid)
        return _apply_overrides_from_db_row(runtime_cfg, db_row, selected_cid, "config_id")
    except Exception:
        return runtime_cfg


def apply_runtime_overrides_with_config_id(
    project_root: str,
    runtime_cfg: Dict[str, Any],
    config_id: str
) -> tuple[Dict[str, Any], bool]:
    """使用指定的 config_id 从数据库覆写运行时配置（不读 dbrun.json）

    用于前端动态切换配置的场景。

    Args:
        project_root: 项目根目录路径
        runtime_cfg: 运行时配置字典
        config_id: 配置标识符

    Returns:
        tuple[Dict[str, Any], bool]: (覆写后的运行时配置, 是否成功从数据库加载)
    """
    try:
        selected_cid = str(config_id).strip()
        if not selected_cid:
            return runtime_cfg, False

        db_row = _fetch_db_config_by_config_id(selected_cid)
        if db_row is None:
            return runtime_cfg, False
        
        updated_cfg = _apply_overrides_from_db_row(runtime_cfg, db_row, selected_cid, "config_id")
        return updated_cfg, True
    except Exception:
        pass
        return runtime_cfg, False


# ============================================================================
# 以下函数已注释：不再需要网络模式自动检测功能
# ============================================================================

# def get_server_ip() -> str:
#     """
#     获取当前服务器的IP地址
#     
#     Returns:
#         服务器IP地址字符串
#     """
#     try:
#         # 方式1：从环境变量获取（优先）
#         server_ip = os.getenv('SERVER_IP')
#         if server_ip and server_ip not in ['127.0.0.1', 'localhost', '0.0.0.0']:
#             return server_ip
#         
#         # 方式2：通过socket获取
#         hostname = socket.gethostname()
#         ip_address = socket.gethostbyname(hostname)
#         
#         # 如果是本地回环地址，尝试获取真实IP
#         if ip_address.startswith('127.'):
#             # 尝试连接外部地址来获取本机IP
#             s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
#             try:
#                 s.connect(('8.8.8.8', 80))
#                 ip_address = s.getsockname()[0]
#             finally:
#                 s.close()
#         
#         return ip_address
#     except Exception as e:
#         print(f"[overrides] 获取服务器IP失败: {e}，使用默认值 127.0.0.1")
#         return '127.0.0.1'


# def auto_detect_network_mode() -> NetworkMode:
#     """
#     自动检测网络模式（基于服务器IP）
#     
#     规则：
#     - 如果服务器IP在内网IP列表中 → internal（内网）
#     - 其他IP → external（外网）
#     
#     可以通过环境变量 INTERNAL_SERVER_IPS 自定义内网IP列表（逗号分隔）
#     
#     Returns:
#         'internal' 或 'external'
#     """
#     server_ip = get_server_ip()
#     
#     # 从环境变量获取内网IP列表（支持多个IP，逗号分隔）
#     internal_ips_str = os.getenv('INTERNAL_SERVER_IPS', '119.45.181.55')
#     internal_ips = [ip.strip() for ip in internal_ips_str.split(',')]
#     
#     # 判断当前IP是否在内网IP列表中
#     if server_ip in internal_ips:
#         print(f"[overrides]  自动检测：服务器IP {server_ip} 属于内网，使用 INTERNAL 配置")
#         return 'internal'
#     else:
#         print(f"[overrides]  自动检测：服务器IP {server_ip} 属于外网，使用 EXTERNAL 配置")
#         return 'external'


# ============================================================================
# 环境变量覆写功能已废弃 - 不再使用
# ============================================================================
# def _apply_env_var_overrides(runtime_cfg: Dict[str, Any], network_mode: NetworkMode = None, force_override: bool = False) -> Dict[str, Any]:
#     """
#     从环境变量覆写配置（已废弃）
#     """
#     return runtime_cfg


def load_unified_config(
    project_root: str,
    config_id: Optional[int | str] = None,
    group_id: Optional[str] = None,
    network_mode: NetworkMode = None,
    env_override_models: bool = True
) -> Dict[str, Any]:
    """
    统一配置加载器 - 按优先级加载配置
    
    配置加载优先级：
    1. PG数据库配置（最高优先级，通过 dbrun.json 中的 config_id 读取）
    2. runtime.json 默认配置（最低优先级）
    
    Args:
        project_root: 项目根目录路径
        config_id: 配置ID（整数或字符串，可选，优先从 dbrun.json 读取）
        group_id: 组ID（可选）
        network_mode: 已废弃，保留参数仅为向后兼容
        env_override_models: 已废弃，保留参数仅为向后兼容
    
    Returns:
        Dict[str, Any]: 最终的运行时配置
    """
    try:
        # 步骤 1: 加载 runtime.json 作为基础配置
        runtime_config_path = os.path.join(project_root, "runtime.json")
        try:
            with open(runtime_config_path, "r", encoding="utf-8") as f:
                runtime_cfg = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            runtime_cfg = {"selections": {}}
        
        # 步骤 2: 尝试从 dbrun.json 读取 config_id 并应用数据库配置（最高优先级）
        if config_id:
            # 优先使用传入的 config_id
            db_row = _fetch_db_config_by_config_id(config_id)
            if db_row:
                runtime_cfg = _apply_overrides_from_db_row(runtime_cfg, db_row, config_id, "config_id")
                pass
        elif group_id:
            # 其次使用 group_id
            db_row = _fetch_db_config_by_group_id(group_id)
            if db_row:
                runtime_cfg = _apply_overrides_from_db_row(runtime_cfg, db_row, group_id, "group_id")
                pass
        else:
            # 尝试从 dbrun.json 读取
            dbrun_config_id = _load_dbrun_config_id(project_root)
            if dbrun_config_id:
                db_row = _fetch_db_config_by_config_id(dbrun_config_id)
                if db_row:
                    runtime_cfg = _apply_overrides_from_db_row(runtime_cfg, db_row, dbrun_config_id, "config_id")
                    pass
            else:
                dbrun_group_id = _load_dbrun_group_id(project_root)
                if dbrun_group_id:
                    db_row = _fetch_db_config_by_group_id(dbrun_group_id)
                    if db_row:
                        runtime_cfg = _apply_overrides_from_db_row(runtime_cfg, db_row, dbrun_group_id, "group_id")
                        pass
        return runtime_cfg
        
    except Exception:
        return {"selections": {}}


# 向后兼容的别名
apply_runtime_overrides = apply_runtime_overrides_by_config
