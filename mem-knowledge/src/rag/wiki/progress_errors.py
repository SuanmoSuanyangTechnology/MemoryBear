"""Wiki 失败进度的用户文案；原始异常与模型输出仅保留在诊断日志。"""

from ...i18n import load_catalogs, translate
from .config import WikiError

_REASONS = {
    "WIKI_MODEL_REQUIRED": "未配置 Wiki 生成所需的语言模型",
    "WIKI_MODEL_OUTPUT_INVALID": "模型生成的内容不符合 Wiki 格式要求",
    "WIKI_MODEL_OUTPUT_TRUNCATED": "模型输出达到长度上限，生成内容不完整",
    "WIKI_CONTEXT_BUDGET_EXCEEDED": "待处理内容超过 Wiki 模型的输入长度限制",
    "WIKI_SOURCE_BUDGET_EXCEEDED": "文档来源内容超过单次 Wiki 处理上限",
    "WIKI_TOPIC_BUDGET_EXCEEDED": "提取的主题数量超过 Wiki 处理上限",
    "WIKI_EVIDENCE_ITEM_TOO_LARGE": "单条来源内容过长，无法生成 Wiki",
    "WIKI_COMPACTION_BUDGET_EXCEEDED": "内容压缩后仍超过 Wiki 处理上限",
    "WIKI_SOURCE_INCOMPLETE": "文档内容未完整写入，Wiki 缺少完整来源",
    "WIKI_SOURCE_ID_MISSING": "文档来源缺少标识，无法建立 Wiki 引用",
    "WIKI_SOURCE_NOT_VISIBLE": "已写入的文档内容暂时无法从索引中读取",
    "WIKI_SOURCE_NOT_READY": "文档来源尚未准备完成",
    "WIKI_SOURCE_FAILED": "文档来源处理失败，无法继续生成 Wiki",
    "WIKI_SOURCE_ALREADY_FAILED": "文档来源已处理失败，无法继续生成 Wiki",
    "WIKI_SOURCE_UNAVAILABLE": "Wiki 引用的文档来源已不可用",
    "WIKI_TOPIC_WITHOUT_EVIDENCE": "主题没有可用的文档依据，无法生成 Wiki 页面",
    "WIKI_DISPATCH_FAILED": "Wiki 任务未能加入处理队列",
    "WIKI_REDUCE_DISPATCH_FAILED": "Wiki 页面生成任务未能加入处理队列",
    "WIKI_HANDOFF_FAILED": "文档处理结果未能交接给 Wiki 任务",
    "WIKI_REDIS_UNAVAILABLE": "Wiki 任务协调服务暂时不可用",
    "WIKI_STATE_UNAVAILABLE": "无法读取或保存 Wiki 处理状态",
    "WIKI_EXECUTION_FAILED": "Wiki 处理发生异常，详细原因已记录到服务日志",
    "WIKI_WORKER_LOSS_EXHAUSTED": "Wiki 处理进程中断，恢复次数已用尽",
    "WIKI_MAP_BUSY": "该文档已有 Wiki 内容提取任务正在处理",
    "WIKI_PAGE_BUSY": "该 Wiki 页面正在被其他任务更新",
    "WIKI_TAXONOMY_BUSY": "Wiki 目录正在被其他任务更新",
    "WIKI_LEASE_LOST": "当前任务的 Wiki 处理权限已失效",
    "WIKI_LEASE_UNKNOWN": "暂时无法确认当前任务是否仍可继续处理 Wiki",
    "WIKI_CAS_CONFLICT": "Wiki 内容已被其他任务更新，本次结果无法保存",
    "WIKI_CONFIG_CHANGED": "Wiki 配置已变更，本次结果需要重新生成",
    "WIKI_INPUT_CHANGED": "文档内容已变更，本次结果需要重新生成",
    "WIKI_SOURCE_CHANGED": "Wiki 来源已变更，本次结果无法沿用",
    "WIKI_PLAN_CHANGED": "Wiki 页面生成计划已变更",
    "WIKI_PARTICIPANT_CHANGED": "参与 Wiki 页面生成的文档已变更",
    "WIKI_IDENTITY_CHANGED": "Wiki 主题归属已变更",
    "WIKI_TAXONOMY_CHANGED": "Wiki 目录已变更，本次分类结果无法沿用",
    "WIKI_TAXONOMY_TARGET_MISMATCH": "Wiki 分类结果与待处理主题不一致",
    "WIKI_MATERIAL_PLAN_MISMATCH": "Wiki 来源素材与页面生成计划不一致",
    "WIKI_DOCUMENT_UNAVAILABLE": "待处理文档已不可用",
    "WIKI_KNOWLEDGE_UNAVAILABLE": "所属知识库已不可用",
    "WIKI_WORKSPACE_UNAVAILABLE": "所属工作空间已不可用",
    "WIKI_SCOPE_MISMATCH": "文档不属于当前 Wiki 处理范围",
    "WIKI_NOT_FOUND": "待处理的 Wiki 内容不存在",
}

_STAGES = {
    "wiki_taxonomy_v2": "Wiki 目录分类",
    "wiki_summary_markdown_v1": "Wiki 摘要生成",
    "wiki_identity_v2": "Wiki 主题匹配",
    "wiki_chunk_citations_v2": "Wiki 来源引用提取",
    "wiki_page_original_v2": "Wiki 页面生成",
    "map_pending": "Wiki 内容提取",
    "reducing": "Wiki 页面生成",
}


def failure_message(error: WikiError, *, retry: bool, stage: str = "", page_id=None) -> str:
    detail = error.diagnostics
    label = _STAGES.get(detail.get("stage"), _STAGES.get(stage, "Wiki 处理"))
    reason = _failure_reason(error)
    outcome = "系统将自动重试。" if retry else "本次处理未完成，系统不会继续自动重试。"
    page = f"（页面 ID：{page_id}）" if page_id is not None else ""
    return f"{label}{page}：{reason.rstrip('。')}。{outcome}（错误码：{error.code}）"


def _failure_reason(error: WikiError) -> str:
    detail = error.diagnostics
    if error.code == "WIKI_MODEL_OUTPUT_INVALID":
        message = detail.get("error_message", "")
        if "Invalid navigation category" in message:
            return "模型生成的目录名称不符合分类规则"
        if message.startswith("Unknown or missing evidence handles:"):
            return "模型生成的来源引用或页面链接无效，或缺少必需引用"
        if message in {"Evidence was omitted during compaction",
                       "Wiki page has no valid evidence handles after filtering"}:
            return "模型生成的内容缺少有效来源依据"
        if "page link" in message.lower() or "页面链接" in message:
            return "模型生成的页面链接目标不在允许的主题范围内"
        if detail.get("validation_errors") or detail.get("json_error"):
            return "模型返回的数据格式或字段不符合要求"
        if "must not be blank" in message or "模型摘要正文为空" in message:
            return "模型没有生成有效的 Wiki 正文或摘要"
        if message == "Raw HTML and external or executable links are not allowed":
            return "模型生成的内容包含不允许的 HTML 或外部链接"
    if error.code == "KB_MODEL_UNAVAILABLE":
        for cause in [detail, *detail.get("causes", [])]:
            status = cause.get("http_status") or cause.get("status_code")
            if status == 401:
                return "模型服务身份验证失败，请检查模型凭据"
            if status == 403:
                return "模型服务拒绝访问，请检查模型使用权限"
            if status == 402:
                return "模型服务因计费或额度问题拒绝请求，请检查账户状态"
            if status == 429:
                return "模型服务限制了当前请求，请检查调用频率或可用额度"
            if cause.get("error_type") in {
                "TimeoutError", "APITimeoutError", "ReadTimeout", "ConnectTimeout",
                "ModelInvokeTimeoutError",
            }:
                return "等待模型响应超时"
            if cause.get("error_type") in {
                "APIConnectionError", "ConnectError", "ModelInvokeUnavailableError",
            }:
                return "暂时无法连接模型服务"
    if error.code in _REASONS:
        return _REASONS[error.code]
    if error.code in load_catalogs()["zh"]:
        return translate(error.code, "zh")
    return "Wiki 处理未能完成，详细原因已记录到服务日志"
