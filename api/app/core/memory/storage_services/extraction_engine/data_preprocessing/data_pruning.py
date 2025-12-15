"""
语义剪枝器 - 在预处理与分块之间过滤与场景不相关内容

功能：
- 对话级一次性抽取判定相关性
- 仅对"不相关对话"的消息按比例删除
- 重要信息（时间、编号、金额、联系方式、地址等）优先保留
"""

import os
import hashlib
import json
import re
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field

from app.core.memory.models.message_models import DialogData, ConversationMessage, ConversationContext
from app.core.memory.models.config_models import PruningConfig
from app.core.memory.utils.config.config_utils import get_pruning_config
from app.core.memory.utils.prompt.prompt_utils import prompt_env, log_prompt_rendering, log_template_rendering


class DialogExtractionResponse(BaseModel):
    """对话级一次性抽取的结构化返回，用于加速剪枝。

    - is_related：对话与场景的相关性判定。
    - times / ids / amounts / contacts / addresses / keywords：重要信息片段，用来在不相关对话中保留关键消息。
    """
    is_related: bool = Field(...)
    times: List[str] = Field(default_factory=list)
    ids: List[str] = Field(default_factory=list)
    amounts: List[str] = Field(default_factory=list)
    contacts: List[str] = Field(default_factory=list)
    addresses: List[str] = Field(default_factory=list)
    keywords: List[str] = Field(default_factory=list)


class SemanticPruner:
    """语义剪枝：在预处理与分块之间过滤与场景不相关内容。

    采用对话级一次性抽取判定相关性；仅对"不相关对话"的消息按比例删除，
    重要信息（时间、编号、金额、联系方式、地址等）优先保留。
    """

    def __init__(self, config: Optional[PruningConfig] = None, llm_client=None):
        cfg_dict = get_pruning_config() if config is None else config.model_dump()
        self.config = PruningConfig.model_validate(cfg_dict)
        self.llm_client = llm_client
        # Load Jinja2 template
        self.template = prompt_env.get_template("extracat_Pruning.jinja2")
        # 对话抽取缓存：避免同一对话重复调用 LLM / 重复渲染
        self._dialog_extract_cache: dict[str, DialogExtractionResponse] = {}
        # 运行日志：收集关键终端输出，便于写入 JSON
        self.run_logs: List[str] = []
        # 采用顺序处理，移除并发配置以简化与稳定执行

    def _is_important_message(self, message: ConversationMessage) -> bool:
        """基于启发式规则识别重要信息消息，优先保留。

        - 含日期/时间（如YYYY-MM-DD、HH:MM、2024年11月10日、上午/下午）。
        - 含编号/ID/订单号/申请号/账号/电话/金额等关键字段。
        - 关键词："时间"、"日期"、"编号"、"订单"、"流水"、"金额"、"￥"、"元"、"电话"、"手机号"、"邮箱"、"地址"。
        """
        import re
        text = message.msg.strip()
        if not text:
            return False
        patterns = [
            r"\b\d{4}-\d{1,2}-\d{1,2}\b",
            r"\b\d{1,2}:\d{2}\b",
            r"\d{4}年\d{1,2}月\d{1,2}日",
            r"上午|下午|AM|PM",
            r"订单号|工单|申请号|编号|ID|账号|账户",
            r"电话|手机号|微信|QQ|邮箱",
            r"地址|地点",
            r"金额|费用|价格|¥|￥|\d+元",
            r"时间|日期|有效期|截止",
        ]
        for p in patterns:
            if re.search(p, text, flags=re.IGNORECASE):
                return True
        return False
    def _importance_score(self, message: ConversationMessage) -> int:
        """为重要消息打分，用于在保留比例内优先保留更关键的内容。

        简单启发：匹配到的类别越多、越关键分值越高。
        """
        import re
        text = message.msg.strip()
        score = 0
        weights = [
            (r"\b\d{4}-\d{1,2}-\d{1,2}\b", 3),
            (r"\b\d{1,2}:\d{2}\b", 2),
            (r"\d{4}年\d{1,2}月\d{1,2}日", 3),
            (r"订单号|工单|申请号|编号|ID|账号|账户", 4),
            (r"电话|手机号|微信|QQ|邮箱", 3),
            (r"地址|地点", 2),
            (r"金额|费用|价格|¥|￥|\d+元", 4),
            (r"时间|日期|有效期|截止", 2),
        ]
        for p, w in weights:
            if re.search(p, text, flags=re.IGNORECASE):
                score += w
        return score

    def _is_filler_message(self, message: ConversationMessage) -> bool:
        """检测典型寒暄/口头禅/确认类短消息，用于跳过LLM分类以加速。

        满足以下之一视为填充消息：
        - 纯标点或长度很短（<= 4 个汉字或 <= 8 个字符）且不包含数字或关键实体；
        - 常见词：你好/您好/在吗/嗯/嗯嗯/哦/好的/好/行/可以/不可以/谢谢/拜拜/再见/哈哈/呵呵/哈哈哈/。。。/？？。
        """
        import re
        t = message.msg.strip()
        if not t:
            return True
        # 常见填充语
        fillers = [
            "你好", "您好", "在吗", "嗯", "嗯嗯", "哦", "好的", "好", "行", "可以", "不可以", "谢谢",
            "拜拜", "再见", "哈哈", "呵呵", "哈哈哈", "。。。", "??", "？？"
        ]
        if t in fillers:
            return True
        # 长度与字符类型判断
        if len(t) <= 8:
            # 非数字、无关键实体的短文本
            if not re.search(r"[0-9]", t) and not self._is_important_message(message):
                # 主要是标点或简单确认词
                if re.fullmatch(r"[。！？,.!?…·\s]+", t) or t in fillers:
                    return True
        return False

    async def _extract_dialog_important(self, dialog_text: str) -> DialogExtractionResponse:
        """对话级一次性抽取：从整段对话中提取重要信息并判定相关性。

        - 仅使用 LLM 结构化输出；
        """
        # 缓存命中则直接返回（场景+内容作为键）
        cache_key = f"{self.config.pruning_scene}:" + hashlib.sha1(dialog_text.encode("utf-8")).hexdigest()
        if cache_key in self._dialog_extract_cache:
            return self._dialog_extract_cache[cache_key]

        rendered = self.template.render(pruning_scene=self.config.pruning_scene, dialog_text=dialog_text)
        log_template_rendering("extracat_Pruning.jinja2", {"pruning_scene": self.config.pruning_scene})
        log_prompt_rendering("pruning-extract", rendered)

        # 强制使用 LLM；移除正则回退
        if not self.llm_client:
            raise RuntimeError("llm_client 未配置；请配置 LLM 以进行结构化抽取。")

        messages = [
            {"role": "system", "content": "你是一个严谨的场景抽取助手，只输出严格 JSON。"},
            {"role": "user", "content": rendered},
        ]
        try:
            ex = await self.llm_client.response_structured(messages, DialogExtractionResponse)
            self._dialog_extract_cache[cache_key] = ex
            return ex
        except Exception as e:
            raise RuntimeError("LLM 结构化抽取失败；请检查 LLM 配置或重试。") from e

    def _msg_matches_tokens(self, message: ConversationMessage, tokens: List[str]) -> bool:
        """判断消息是否包含任意抽取到的重要片段。"""
        if not tokens:
            return False
        t = message.msg
        return any(tok and (tok in t) for tok in tokens)

    async def prune_dialog(self, dialog: DialogData) -> DialogData:
        """单对话剪枝：使用一次性对话抽取，避免逐条消息 LLM 调用。

        流程：
        - 对整段对话进行抽取与相关性判定；若相关则不剪；
        - 若不相关：用抽取到的重要片段 + 简单启发识别重要消息，按比例删除不相关消息，优先删除不重要，再删除重要（但重要最多按比例）。
        - 删除策略：不重要消息按出现顺序删除（确定性、无随机）。
        """
        if not self.config.pruning_switch:
            return dialog

        proportion = float(self.config.pruning_threshold)
        extraction = await self._extract_dialog_important(dialog.content)
        if extraction.is_related:
            # 相关对话不剪枝
            return dialog

        # 在不相关对话中，识别重要/不重要消息
        tokens = extraction.times + extraction.ids + extraction.amounts + extraction.contacts + extraction.addresses + extraction.keywords
        msgs = dialog.context.msgs
        imp_unrel_msgs: List[ConversationMessage] = []
        unimp_unrel_msgs: List[ConversationMessage] = []
        for m in msgs:
            if self._msg_matches_tokens(m, tokens) or self._is_important_message(m):
                imp_unrel_msgs.append(m)
            else:
                unimp_unrel_msgs.append(m)
        # 计算总删除目标数量
        total_unrel = len(msgs)
        delete_target = int(total_unrel * proportion)
        if proportion > 0 and total_unrel > 0 and delete_target == 0:
            delete_target = 1
        imp_del_cap = min(int(len(imp_unrel_msgs) * proportion), len(imp_unrel_msgs))
        unimp_del_cap = len(unimp_unrel_msgs)
        max_capacity = max(0, len(msgs) - 1)
        max_deletable = min(imp_del_cap + unimp_del_cap, max_capacity)
        delete_target = min(delete_target, max_deletable)
        # 删除配额分配
        del_unimp = min(delete_target, unimp_del_cap)
        rem = delete_target - del_unimp
        del_imp = min(rem, imp_del_cap)

        # 选取删除集合
        unimp_delete_ids = []
        imp_delete_ids = []
        if del_unimp > 0:
            # 按出现顺序选取前 del_unimp 条不重要消息进行删除（确定性、可复现）
            unimp_delete_ids = [id(m) for m in unimp_unrel_msgs[:del_unimp]]
        if del_imp > 0:
            imp_sorted = sorted(imp_unrel_msgs, key=lambda m: self._importance_score(m))
            imp_delete_ids = [id(m) for m in imp_sorted[:del_imp]]

        # 统计实际删除数量（重要/不重要）
        actual_unimp_deleted = 0
        actual_imp_deleted = 0
        kept_msgs = []
        delete_targets = set(unimp_delete_ids) | set(imp_delete_ids)
        for m in msgs:
            mid = id(m)
            if mid in delete_targets:
                if mid in set(unimp_delete_ids) and actual_unimp_deleted < del_unimp:
                    actual_unimp_deleted += 1
                    continue
                if mid in set(imp_delete_ids) and actual_imp_deleted < del_imp:
                    actual_imp_deleted += 1
                    continue
            kept_msgs.append(m)
        if not kept_msgs and msgs:
            kept_msgs = [msgs[0]]

        deleted_total = actual_unimp_deleted + actual_imp_deleted
        self._log(
            f"[剪枝-对话] 对话ID={dialog.id} 总消息={len(msgs)} 删除目标={delete_target} 实删={deleted_total} 保留={len(kept_msgs)}"
        )

        dialog.context = ConversationContext(msgs=kept_msgs)
        return dialog

    async def prune_dataset(self, dialogs: List[DialogData]) -> List[DialogData]:
        """数据集层面：全局消息级剪枝，保留所有对话。

        - 仅在"不相关对话"的范围内执行消息剪枝；相关对话不动。
        - 只删除"不重要的不相关消息"，重要信息（时间、编号等）强制保留。
        - 删除总量 = 阈值 * 全部不相关可删消息数，按可删容量比例分配；顺序删除。
        - 保证每段对话至少保留1条消息，不会删除整段对话。
        """
        # 如果剪枝功能关闭，直接返回原始数据集。
        if not self.config.pruning_switch:
            return dialogs

        # 阈值保护：最高0.9
        proportion = float(self.config.pruning_threshold)
        if proportion > 0.9:
            print(f"[剪枝-数据集] 阈值{proportion}超过上限0.9，已自动调整为0.9")
            proportion = 0.9
        if proportion < 0.0:
            proportion = 0.0
        evaluated_dialogs = []  # list of dicts: {dialog, is_related}

        self._log(
            f"[剪枝-数据集] 对话总数={len(dialogs)} 场景={self.config.pruning_scene} 删除比例={proportion} 开关={self.config.pruning_switch}"
        )
        # 对话级相关性分类（一次性对整段对话文本进行判断，顺序执行并复用缓存）
        evaluated_dialogs = []
        for idx, dd in enumerate(dialogs):
            try:
                ex = await self._extract_dialog_important(dd.content)
                evaluated_dialogs.append({
                    "dialog": dd,
                    "is_related": bool(ex.is_related),
                    "index": idx,
                    "extraction": ex
                })
            except Exception:
                evaluated_dialogs.append({
                    "dialog": dd,
                    "is_related": True,
                    "index": idx,
                    "extraction": None
                })

        # 统计相关 / 不相关对话
        not_related_dialogs = [d for d in evaluated_dialogs if not d["is_related"]]
        related_dialogs = [d for d in evaluated_dialogs if d["is_related"]]
        self._log(
            f"[剪枝-数据集] 相关对话数={len(related_dialogs)} 不相关对话数={len(not_related_dialogs)}"
        )

        # 简洁打印第几段对话相关/不相关（索引基于1）
        def _fmt_indices(items, cap: int = 10):
            inds = [i["index"] + 1 for i in items]
            if len(inds) <= cap:
                return inds
            # 超过上限时只打印前cap个，并标注总数
            return inds[:cap] + ["...", f"共{len(inds)}个"]

        rel_inds = _fmt_indices(related_dialogs)
        nrel_inds = _fmt_indices(not_related_dialogs)
        self._log(f"[剪枝-数据集] 相关对话：第{rel_inds}段；不相关对话：第{nrel_inds}段")

        result: List[DialogData] = []
        if not_related_dialogs:
            # 为每个不相关对话进行一次性抽取，识别重要/不重要（避免逐条 LLM）
            per_dialog_info = {}
            total_unrelated = 0
            total_capacity = 0
            for d in not_related_dialogs:
                dd = d["dialog"]
                extraction = d.get("extraction")
                if extraction is None:
                    extraction = await self._extract_dialog_important(dd.content)
                # 合并所有重要标记
                tokens = extraction.times + extraction.ids + extraction.amounts + extraction.contacts + extraction.addresses + extraction.keywords
                msgs = dd.context.msgs
                # 分类消息
                imp_unrel_msgs = [m for m in msgs if self._msg_matches_tokens(m, tokens) or self._is_important_message(m)]
                unimp_unrel_msgs = [m for m in msgs if m not in imp_unrel_msgs]
                # 重要消息按重要性排序
                imp_sorted_ids = [id(m) for m in sorted(imp_unrel_msgs, key=lambda m: self._importance_score(m))]
                info = {
                    "dialog": dd,
                    "total_msgs": len(msgs),
                    "unrelated_count": len(msgs),
                    "imp_ids_sorted": imp_sorted_ids,
                    "unimp_ids": [id(m) for m in unimp_unrel_msgs],
                }
                per_dialog_info[d["index"]] = info
                total_unrelated += info["unrelated_count"]
            # 全局删除配额：比例作用于全部不相关消息（重要+不重要）
            global_delete = int(total_unrelated * proportion)
            if proportion > 0 and total_unrelated > 0 and global_delete == 0:
                global_delete = 1
            # 每段的最大可删容量：不重要全部 + 重要最多删除 floor(len(重要)*比例)，且至少保留1条消息
            capacities = []
            for d in not_related_dialogs:
                idx = d["index"]
                info = per_dialog_info[idx]
                # 统计重要数量
                imp_count = len(info["imp_ids_sorted"])
                unimp_count = len(info["unimp_ids"])
                imp_cap = int(imp_count * proportion)
                cap = min(unimp_count + imp_cap, max(0, info["total_msgs"] - 1))
                capacities.append(cap)
            total_capacity = sum(capacities)
            if global_delete > total_capacity:
                print(f"[剪枝-数据集] 不相关消息总数={total_unrelated}，目标删除={global_delete}，最大可删={total_capacity}（重要消息按比例保留）。将按最大可删执行。")
                global_delete = total_capacity

            # 配额分配：按不相关消息占比分配到各对话，但不超过各自容量
            alloc = []
            for i, d in enumerate(not_related_dialogs):
                idx = d["index"]
                info = per_dialog_info[idx]
                share = int(global_delete * (info["unrelated_count"] / total_unrelated)) if total_unrelated > 0 else 0
                alloc.append(min(share, capacities[i]))
            allocated = sum(alloc)
            rem = global_delete - allocated
            turn = 0
            while rem > 0 and turn < 100000:
                progressed = False
                for i in range(len(not_related_dialogs)):
                    if rem <= 0:
                        break
                    if alloc[i] < capacities[i]:
                        alloc[i] += 1
                        rem -= 1
                        progressed = True
                if not progressed:
                    break
                turn += 1

            # 应用删除：相关对话不动；不相关按分配先删不重要，再删重要（低分优先）
            total_deleted_confirm = 0
            for d in evaluated_dialogs:
                dd = d["dialog"]
                msgs = dd.context.msgs
                original = len(msgs)
                if d["is_related"]:
                    result.append(dd)
                    continue
                idx_in_unrel = next((k for k, x in enumerate(not_related_dialogs) if x["index"] == d["index"]), None)
                if idx_in_unrel is None:
                    result.append(dd)
                    continue
                quota = alloc[idx_in_unrel]
                info = per_dialog_info[d["index"]]
                # 计算本对话重要最多可删数量
                imp_count = len(info["imp_ids_sorted"])
                imp_del_cap = int(imp_count * proportion)
                # 先构造顺序删除的"不重要ID集合"（按出现顺序前 quota 条）
                unimp_delete_ids = set(info["unimp_ids"][:min(quota, len(info["unimp_ids"]))])
                del_unimp = min(quota, len(unimp_delete_ids))
                rem_quota = quota - del_unimp
                # 再从重要里选低分优先的删除ID（不超过 imp_del_cap）
                imp_delete_ids = set(info["imp_ids_sorted"][:min(rem_quota, imp_del_cap)])
                deleted_here = 0
                actual_unimp_deleted = 0
                actual_imp_deleted = 0
                kept = []
                for m in msgs:
                    mid = id(m)
                    if mid in unimp_delete_ids and actual_unimp_deleted < del_unimp:
                        actual_unimp_deleted += 1
                        deleted_here += 1
                        continue
                    if mid in imp_delete_ids and actual_imp_deleted < len(imp_delete_ids):
                        actual_imp_deleted += 1
                        deleted_here += 1
                        continue
                    kept.append(m)
                if not kept and msgs:
                    kept = [msgs[0]]
                dd.context.msgs = kept
                total_deleted_confirm += deleted_here
                self._log(
                    f"[剪枝-对话] 对话 {d['index']+1} 总消息={original} 分配删除={quota} 实删={deleted_here} 保留={len(kept)}"
                )
                result.append(dd)
            self._log(f"[剪枝-数据集] 全局消息级顺序剪枝完成，总删除 {total_deleted_confirm} 条（不相关消息，重要按比例保留）。")
        else:
            # 全部相关：不执行剪枝
            result = [d["dialog"] for d in evaluated_dialogs]
        self._log(f"[剪枝-数据集] 剩余对话数={len(result)}")

        # 将本次剪枝阶段的终端输出保存为 JSON 文件（仅在剪枝器内部完成）
        try:
            from app.core.config import settings
            settings.ensure_memory_output_dir()
            log_output_path = settings.get_memory_output_path("pruned_terminal.json")
            # 去除日志前缀标签（如 [剪枝-数据集]、[剪枝-对话]）后再解析为结构化字段保存
            sanitized_logs = [self._sanitize_log_line(l) for l in self.run_logs]
            payload = self._parse_logs_to_structured(sanitized_logs)
            with open(log_output_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self._log(f"[剪枝-数据集] 保存终端输出日志失败：{e}")

        # Safety: avoid empty dataset
        if not result:
            print("警告: 语义剪枝后数据集为空，已回退为未剪枝数据以避免流程中断")
            return dialogs
        return result

    def _log(self, msg: str) -> None:
        """记录日志并打印到终端。"""
        try:
            self.run_logs.append(msg)
        except Exception:
            # 任何异常都不影响打印
            pass
        print(msg)

    def _sanitize_log_line(self, line: str) -> str:
        """移除行首的方括号标签前缀，例如 [剪枝-数据集] 或 [剪枝-对话]。"""
        try:
            return re.sub(r"^\[[^\]]+\]\s*", "", line)
        except Exception:
            return line

    def _parse_logs_to_structured(self, logs: List[str]) -> dict:
        """将已去前缀的日志列表解析为结构化 JSON，便于数据对接。"""
        summary = {
            "scene": self.config.pruning_scene,
            "dialog_total": None,
            "deletion_ratio": None,
            "enabled": None,
            "related_count": None,
            "unrelated_count": None,
            "related_indices": [],
            "unrelated_indices": [],
            "total_deleted_messages": None,
            "remaining_dialogs": None,
        }
        dialogs = []

        # 解析函数
        def parse_int(value: str) -> Optional[int]:
            try:
                return int(value)
            except Exception:
                return None

        def parse_float(value: str) -> Optional[float]:
            try:
                return float(value)
            except Exception:
                return None

        def parse_indices(s: str) -> List[int]:
            s = s.strip()
            if not s:
                return []
            parts = [p.strip() for p in s.split(",") if p.strip()]
            out: List[int] = []
            for p in parts:
                try:
                    out.append(int(p))
                except Exception:
                    pass
            return out

        # 正则
        re_header = re.compile(r"对话总数=(\d+)\s+场景=([^\s]+)\s+删除比例=([0-9.]+)\s+开关=(True|False)")
        re_counts = re.compile(r"相关对话数=(\d+)\s+不相关对话数=(\d+)")
        re_indices = re.compile(r"相关对话：第\[(.*?)\]段；不相关对话：第\[(.*?)\]段")
        re_dialog = re.compile(r"对话\s+(\d+)\s+总消息=(\d+)\s+分配删除=(\d+)\s+实删=(\d+)\s+保留=(\d+)")
        re_total_del = re.compile(r"总删除\s+(\d+)\s+条")
        re_remaining = re.compile(r"剩余对话数=(\d+)")

        for line in logs:
            # 第一行：总览
            m = re_header.search(line)
            if m:
                summary["dialog_total"] = parse_int(m.group(1))
                # 顶层 scene 依配置，这里不覆盖，但也可校验 m.group(2)
                summary["deletion_ratio"] = parse_float(m.group(3))
                summary["enabled"] = True if m.group(4) == "True" else False
                continue

            # 第二行：相关/不相关数量
            m = re_counts.search(line)
            if m:
                summary["related_count"] = parse_int(m.group(1))
                summary["unrelated_count"] = parse_int(m.group(2))
                continue

            # 第三行：相关/不相关索引
            m = re_indices.search(line)
            if m:
                summary["related_indices"] = parse_indices(m.group(1))
                summary["unrelated_indices"] = parse_indices(m.group(2))
                continue

            # 对话级统计
            m = re_dialog.search(line)
            if m:
                dialogs.append({
                    "index": parse_int(m.group(1)),
                    "total_messages": parse_int(m.group(2)),
                    "quota_delete": parse_int(m.group(3)),
                    "actual_deleted": parse_int(m.group(4)),
                    "kept": parse_int(m.group(5)),
                })
                continue

            # 全局删除总数
            m = re_total_del.search(line)
            if m:
                summary["total_deleted_messages"] = parse_int(m.group(1))
                continue

            # 剩余对话数
            m = re_remaining.search(line)
            if m:
                summary["remaining_dialogs"] = parse_int(m.group(1))
                continue

        return {
            "scene": summary["scene"],
            "timestamp": datetime.now().isoformat(),
            "summary": {k: v for k, v in summary.items() if k != "scene"},
            "dialogs": dialogs,
        }
