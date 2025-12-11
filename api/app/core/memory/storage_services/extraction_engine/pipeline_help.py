"""
提取流水线工具函数

该模块提供知识提取流水线的辅助工具函数，包括：
1. 解析和格式化提取结果
2. 生成提取结果汇总报告
3. 导出测试输入文档

这些函数主要用于：
- 解析三元组和实体信息
- 统计去重和消歧效果
- 生成可读的结果报告

作者：Memory Refactoring Team
原路径：app/core/memory/src/pipeline_help.py（已迁移）
迁移日期：2025-11-22
"""

import os
import re
import json
from datetime import datetime
from collections import defaultdict


def _parse_triplets_from_file(filepath):
    """解析三元组文件，返回三元组列表"""
    triplets = []
    if not os.path.exists(filepath):
        return triplets
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        
        lines = content.split('\n')
        current_triplet = {}
        
        for line in lines:
            line = line.strip()
            if line.startswith('Triplet '):
                if current_triplet:
                    triplets.append(current_triplet)
                current_triplet = {}
            elif line.startswith('Subject:'):
                subject = line.replace('Subject:', '').strip()
                subject = subject.split('(ID:')[0].strip()
                current_triplet['subject'] = subject
            elif line.startswith('Predicate:'):
                predicate = line.replace('Predicate:', '').strip()
                current_triplet['predicate'] = predicate
            elif line.startswith('Object:'):
                obj = line.replace('Object:', '').strip()
                obj = obj.split('(ID:')[0].strip()
                current_triplet['object'] = obj
        
        if current_triplet:
            triplets.append(current_triplet)
    except Exception as e:
        print(f"解析三元组文件失败: {e}")
    
    return triplets


def _parse_entities_from_triplets(filepath):
    """从三元组文件中解析实体信息，按类型分组"""
    entities_by_type = defaultdict(list)
    
    if not os.path.exists(filepath):
        return entities_by_type
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        
        if '=== EXTRACTED ENTITIES' in content:
            entity_section = content.split('=== EXTRACTED ENTITIES')[1]
            lines = entity_section.split('\n')
            
            current_entity = {}
            for line in lines:
                line = line.strip()
                if line.startswith('Entity '):
                    if current_entity and 'name' in current_entity and 'type' in current_entity:
                        entities_by_type[current_entity['type']].append(current_entity['name'])
                    current_entity = {}
                elif line.startswith('Name:'):
                    name = line.replace('Name:', '').strip()
                    current_entity['name'] = name
                elif line.startswith('Type:'):
                    entity_type = line.replace('Type:', '').strip()
                    current_entity['type'] = entity_type
            
            if current_entity and 'name' in current_entity and 'type' in current_entity:
                entities_by_type[current_entity['type']].append(current_entity['name'])
        
        # 去重
        for entity_type in entities_by_type:
            entities_by_type[entity_type] = list(set(entities_by_type[entity_type]))
    except Exception as e:
        print(f"解析实体信息失败: {e}")
    
    return entities_by_type


def _format_predicate(predicate):
    """格式化谓词为中文"""
    predicate_map = {
        'COLLABORATES_WITH': '同事',
        'MENTIONS': '提到',
        'DEVELOPED': '开发',
        'PART_OF': '参与',
        'LOCATED_IN': '位于',
        'WORKS_AT': '工作于',
        'PURCHASED': '购买',
        'INTERESTED_IN': '感兴趣'
    }
    return predicate_map.get(predicate, predicate.lower().replace('_', ' '))


def _write_extracted_result_summary(
    chunk_nodes,
    pipeline_output_dir: str,
):
    """
    汇总生成 logs/memory-output/extracted_result.json，包含：
    - 提取实体数（从 extracted_entities_edges.txt 的 ENTITY 行计数）
    - 去重后合并个数（统计 dedup_entity_output.txt 的精确/模糊/LLM合并记录）
    - 实体消歧次数（统计阻断与合并应用，并输出同名实体“消歧成功”）
    - 记忆片段数（chunk_nodes 的数量）
    - 关系三元组数（从 extracted_triplets.txt 标题获取总数）
    """
    os.makedirs(pipeline_output_dir, exist_ok=True)
    result_path = os.path.join(pipeline_output_dir, "extracted_result.json")
    entities_edges_path = os.path.join(pipeline_output_dir, "extracted_entities_edges.txt")
    dedup_report_path = os.path.join(pipeline_output_dir, "dedup_entity_output.txt")
    triplets_path = os.path.join(pipeline_output_dir, "extracted_triplets.txt")

    # 1) 提取实体数
    extracted_entity_count = 0
    # 初始提取的名称计数（用于“出现X次”的基础计数）
    initial_name_counts: dict[str, int] = {}
    try:
        with open(entities_edges_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith("ENTITY:"):
                    extracted_entity_count += 1
                    # 解析 name 字段
                    try:
                        m = re.search(r"\{\s*\"id\"\s*:\s*\"[^\"]*\"\s*,\s*\"name\"\s*:\s*\"([^\"]+)\"", line)
                        if m:
                            nm = m.group(1).strip()
                            if nm:
                                initial_name_counts[nm] = initial_name_counts.get(nm, 0) + 1
                    except Exception:
                        pass
    except Exception:
        pass

    # 2) 去重后合并个数 & 3) 实体消歧次数（含成功名称）
    exact_merge_total = 0
    fuzzy_merge_total = 0
    llm_merge_total = 0
    disamb_block_total = 0
    # 记录成功区分的消歧对（阻断的左右实体及类型）
    disamb_success_pairs: list[tuple[str, str, str, str]] = []
    # 在外部定义这些字典，确保后续代码可以访问
    dedup_impact: dict[tuple[str, str], int] = {}
    # 第二层精准合并新增：包含自合并（自合并视为"比较两个实体后合并为一"）
    second_layer_exact_additions: dict[tuple[str, str], int] = {}
    # LLM 同名类型相似：按名称计一次出现（代表两个实体合并为一）
    llm_same_name_additions: dict[str, int] = {}
    
    try:
        with open(dedup_report_path, "r", encoding="utf-8") as f:
            current_layer: str | None = None
            for raw in f:
                line = raw.strip()
                if line.startswith("=== 第一层去重消歧 ==="):
                    current_layer = "第一层去重消歧"
                    continue
                if line.startswith("=== 第二层去重消歧 ==="):
                    current_layer = "第二层去重消歧"
                    continue
                # 精确合并：统计“合并实体IDs”数量
                if line.startswith("[精确] ") and "合并实体IDs" in line:
                    try:
                        # 先提取规范ID（用于第二层去重统计）
                        canonical_id = ""
                        id_match = re.search(r"规范实体\s+([0-9a-f]{40})", line)
                        if id_match:
                            canonical_id = id_match.group(1).strip()
                        
                        # 提取名称、类型和合并实体IDs
                        m = re.search(r"名称\s+'([^']+)'\s+类型\s+(\S+)\s+<-\s+合并实体IDs\s+(.+)$", line)
                        if m:
                            name = m.group(1).strip()
                            ent_type = m.group(2).strip()
                            ids_part = m.group(3).strip()
                        else:
                            # 退化解析：如果上式失败，回退到简单切分
                            canonical_id = ""
                            name = ""
                            ent_type = ""
                            ids_part = line.split("合并实体IDs", 1)[1].lstrip("：:").strip()
                        id_list = [i.strip() for i in ids_part.split(",") if i.strip()]
                        exact_merge_total += len(id_list)
                        if name and ent_type:
                            key = (name, ent_type)
                            dedup_impact[key] = dedup_impact.get(key, 0) + len(id_list)
                            # 在第二层：统计新增出现次数（包含自合并，视为两实体比较后合并为一，至少+1）
                            if current_layer == "第二层去重消歧":
                                try:
                                    non_self = len([i for i in id_list if i != canonical_id]) if canonical_id else len(id_list)
                                except Exception:
                                    non_self = len(id_list)
                                add_cnt = non_self if non_self > 0 else 1
                                second_layer_exact_additions[key] = second_layer_exact_additions.get(key, 0) + add_cnt
                    except Exception:
                        pass
                # 模糊合并：每条记录算一次合并
                elif line.startswith("[模糊] ") and "<- 合并实体" in line:
                    fuzzy_merge_total += 1
                    # 解析括号中的三元组 (group|name|type)
                    try:
                        m = re.search(r"规范实体[^\(]*\(([^|]+)\|([^|]+)\|([^\)]+)\)", line)
                        if m:
                            name = m.group(2).strip()
                            ent_type = m.group(3).strip()
                            key = (name, ent_type)
                            dedup_impact[key] = dedup_impact.get(key, 0) + 1
                    except Exception:
                        pass
                # LLM 决策合并：每条记录算一次合并（包含 LLM融合/LLM合并 以及 “同名类型相似”的 LLM 去重）
                elif (line.startswith("[LLM融合]") or line.startswith("[LLM合并]")) and "<- 合并实体" in line:
                    llm_merge_total += 1
                    try:
                        m = re.search(r"规范实体[^\(]*\(([^|]+)\|([^|]+)\|([^\)]+)\)", line)
                        if m:
                            name = m.group(2).strip()
                            ent_type = m.group(3).strip()
                            key = (name, ent_type)
                            dedup_impact[key] = dedup_impact.get(key, 0) + 1
                    except Exception:
                        pass
                elif line.startswith("[LLM去重]"):
                    # 例如：[LLM去重] 同名类型相似 A（TypeA）|B（TypeB） | conf=... | reason=...
                    # 这类记录同样属于 LLM 决策的去重合并，计入 LLM 合并总数
                    llm_merge_total += 1
                    # 若同名类型相似（名称相同），按“名称”计一次出现（两实体合并为一）
                    try:
                        m = re.search(r"同名类型相似\s*([^（(]+)[（(][^）)]+[）)]\|([^（(]+)[（(][^）)]+[）)]", line)
                        if m:
                            left = m.group(1).strip()
                            right = m.group(2).strip()
                            if left and right and left == right:
                                llm_same_name_additions[left] = llm_same_name_additions.get(left, 0) + 1
                    except Exception:
                        pass
                    # 可选：解析名称与类型，当前不用于后续统计输出，保持简单
                    # 若未来需要统计影响，可以解析左右两侧名称/类型并分别+1
                # 消歧阻断计数：仅统计 [DISAMB阻断]，忽略异常阻断与合并应用
                elif line.startswith("[DISAMB阻断]"):
                    disamb_block_total += 1
                    # 解析形如：
                    # [DISAMB阻断] A（TypeA）|B（TypeB） | conf=... | reason=... || block_pair=True
                    try:
                        m = re.search(r"\[DISAMB阻断\]\s*([^（(]+)[（(]([^）)]+)[）)]\|([^（(]+)[（(]([^）)]+)[）)]", line)
                        if m:
                            left_name = m.group(1).strip()
                            left_type = m.group(2).strip()
                            right_name = m.group(3).strip()
                            right_type = m.group(4).strip()
                            disamb_success_pairs.append((left_name, left_type, right_name, right_type))
                    except Exception:
                        pass
    except Exception:
        pass

    total_merged_count = exact_merge_total + fuzzy_merge_total + llm_merge_total
    disamb_total = disamb_block_total

    # 4) 记忆片段数（分块器生成的 chunk 数量）
    memory_chunk_count = 0
    try:
        memory_chunk_count = len(chunk_nodes) if chunk_nodes is not None else 0
    except Exception:
        pass

    # 5) 关系三元组数（从文件头部“EXTRACTED TRIPLETS (N total)”解析）
    triplet_count = 0
    try:
        with open(triplets_path, "r", encoding="utf-8") as f:
            head = f.readline()
            m = re.search(r"EXTRACTED\s+TRIPLETS\s*\((\d+)\s+total\)", head)
            if m:
                triplet_count = int(m.group(1))
    except Exception:
        pass

    # 写入结果文件
    # 构建 JSON 结构（字段顺序按用户需求组织：先“实体去重的影响”，后“实体消歧的效果”）
    readable_path = os.path.join(pipeline_output_dir, "extracted_result_readable.txt")
    summary_json = {
        "generated_at": datetime.now().isoformat(),
        "entities": {
            "extracted_count": extracted_entity_count,
        },
        "dedup": {
            "total_merged_count": total_merged_count,
            "breakdown": {
                "exact": exact_merge_total,
                "fuzzy": fuzzy_merge_total,
                "llm": llm_merge_total,
            },
            "impact": [
                {
                    "name": nm,
                    "type": tp,
                    "appear_count": (initial_name_counts.get(nm, 0)
                                      + second_layer_exact_additions.get((nm, tp), 0)
                                      + llm_same_name_additions.get(nm, 0)) if (initial_name_counts.get(nm, 0)
                                      + second_layer_exact_additions.get((nm, tp), 0)
                                      + llm_same_name_additions.get(nm, 0)) > 0 else merge_cnt,
                    "merge_count": merge_cnt,
                }
                for (nm, tp), merge_cnt in (dedup_impact.items() if 'dedup_impact' in locals() else [])
            ],
        },
        "disambiguation": {
            "block_count": disamb_block_total,
            "effects": [
                {
                    "left": {"name": ln, "type": lt},
                    "right": {"name": rn, "type": rt},
                    "result": "成功区分"
                }
                for (ln, lt, rn, rt) in disamb_success_pairs
            ],
        },
        "memory": {"chunks": memory_chunk_count},
        "triplets": {"count": triplet_count},
        "core_entities": [],  # 将在下面填充
        "triplet_samples": [],  # 将在下面填充
    }

    # 解析实体和三元组数据（用于JSON和文本输出）
    entities_by_type = _parse_entities_from_triplets(triplets_path)
    triplets_list = _parse_triplets_from_file(triplets_path)
    
    # 类型翻译映射
    type_translation = {
        'Person': '人物',
        'Organization': '组织',
        'Location': '地点',
        'Product': '产品',
        'Event': '事件',
        'Technology': '技术',
        'Activity': '活动',
        'Exercise': '运动'
    }
    
    # 构建核心实体数据（按类型分组）
    core_entities_data = []
    for entity_type, entities in sorted(entities_by_type.items(), key=lambda x: -len(x[1])):
        type_name_cn = type_translation.get(entity_type, entity_type)
        core_entities_data.append({
            "type": entity_type,
            "type_cn": type_name_cn,
            "count": len(entities),
            "entities": entities[:5]  # 最多显示5个
        })
    summary_json["core_entities"] = core_entities_data
    
    # 构建三元组示例数据
    triplet_samples = []
    display_count = min(7, len(triplets_list))
    for i in range(display_count):
        triplet = triplets_list[i]
        predicate_cn = _format_predicate(triplet.get('predicate', ''))
        triplet_samples.append({
            "subject": triplet.get('subject', ''),
            "predicate": triplet.get('predicate', ''),
            "predicate_cn": predicate_cn,
            "object": triplet.get('object', '')
        })
    summary_json["triplet_samples"] = triplet_samples

    # 写 JSON 到 extracted_result.json（满足"以 json 格式输出并为 .json 文件"的要求）
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(summary_json, f, ensure_ascii=False, indent=2)

    # 额外生成可读版文本，模块顺序调整
    lines: list[str] = []
    lines.append(f"结果汇总 - {datetime.now().isoformat()}")
    lines.append("")
    # 提取实体数模块
    lines.append("提取实体数：")
    lines.append(f"总计 {extracted_entity_count} 个")
    lines.append(f"去重后合并个数：{total_merged_count} （精确={exact_merge_total}，模糊={fuzzy_merge_total}，LLM={llm_merge_total}）")
    lines.append("")
    # 实体消歧次数模块
    lines.append("实体消歧次数：")
    lines.append(f"总计 {disamb_total} 次（阻断={disamb_block_total}）")
    lines.append("")
    # 记忆片段数模块
    lines.append("记忆片段数：")
    lines.append(f"总计 {memory_chunk_count} 条")
    lines.append("")
    # 关系三元组数模块
    lines.append("关系三元组数：")
    lines.append(f"总计 {triplet_count} 条")
    lines.append("")

    # 新增模块1：提取的核心实体（去重后）
    lines.append("提取的核心实体（去重后）：")
    lines.append("")
    # 从 extracted_triplets.txt 解析去重后的实体并按类型分组
    entities_by_type = _parse_entities_from_triplets(triplets_path)
    type_translation = {
        'Person': '人物',
        'Organization': '组织',
        'Location': '地点',
        'Product': '产品',
        'Event': '事件',
        'Technology': '技术',
        'Activity': '活动',
        'Exercise': '运动'
    }
    for entity_type, entities in sorted(entities_by_type.items(), key=lambda x: -len(x[1])):
        type_name = type_translation.get(entity_type, entity_type)
        count = len(entities)
        lines.append(f"{type_name}({count}):")
        # 最多显示5个实体
        display_entities = entities[:5]
        for entity in display_entities:
            lines.append(f"  • {entity}")
        lines.append("")

    # 新增模块2：提取的关系三元组（部分）
    lines.append("提取的关系三元组（部分）：")
    lines.append("")
    # 从 extracted_triplets.txt 读取三元组
    triplets = _parse_triplets_from_file(triplets_path)
    display_count = min(7, len(triplets))
    for i in range(display_count):
        triplet = triplets[i]
        predicate_cn = _format_predicate(triplet['predicate'])
        lines.append(f"  • ({triplet['subject']}, {predicate_cn}, {triplet['object']})")
    lines.append("")
    lines.append(f"... 共{triplet_count}条关系三元组")
    lines.append("")

    # 实体去重的影响模块（先输出）
    if dedup_impact:
        lines.append("实体去重的影响：")
        # 出现次数 = 初始提取次数 + 第二层精准合并新增次数（包含自合并至少+1） + LLM同名类型相似按名称的新增次数
        # 若某名称初始未出现但发生了合并（少见），退化为使用合并次数
        for (nm, tp), merge_cnt in dedup_impact.items():
            init_cnt = initial_name_counts.get(nm, 0)
            add_cnt = second_layer_exact_additions.get((nm, tp), 0)
            llm_add = llm_same_name_additions.get(nm, 0)
            appear_cnt = init_cnt + add_cnt + llm_add
            if appear_cnt <= 0:
                appear_cnt = merge_cnt
            lines.append(f"[{nm}]出现{appear_cnt}次 → 合并为1个类型是[{tp}]的实体")
        lines.append("")

    # 新增模块：实体消歧的效果（后输出，来源于 dedup_entity_output.txt 的 DISAMB阻断 记录）
    if disamb_success_pairs:
        lines.append("实体消歧的效果：")
        for left_name, left_type, right_name, right_type in disamb_success_pairs:
            lines.append(f"{left_name}（{left_type}） vs {right_name}（{right_type}） → 成功区分。")
        lines.append("")

    with open(readable_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

def export_test_input_doc(
    entity_nodes,
    statement_entity_edges,
    entity_entity_edges,
):
    """将提取出的实体与两类边导出到 extracted_entities_edges.txt。

    保持与 extraction_pipeline.py 原本本地函数一致的行为与输出格式。
    """
    try:
        from app.core.config import settings
        settings.ensure_memory_output_dir()
        out_path = settings.get_memory_output_path("extracted_entities_edges.txt")

        def _to_dict(m):
            d = m.model_dump()
            for k, v in list(d.items()):
                if isinstance(v, datetime):
                    d[k] = v.isoformat()
            return d

        def _entity_to_dict(e):
            return {
                "id": getattr(e, "id"),
                "name": getattr(e, "name"),
                "entity_type": getattr(e, "entity_type"),
                "description": getattr(e, "description"),
            }

        with open(out_path, "w", encoding="utf-8") as f:
            header_time = entity_nodes[0].created_at.isoformat()
            f.write(
                f"=== TEST EXTRACTED ENTITIES === (created_at: {header_time})\n"
            )
            for e in entity_nodes:
                f.write(
                    "ENTITY: " + json.dumps(_entity_to_dict(e), ensure_ascii=False) + "\n"
                )

            f.write("\n=== TEST STATEMENT-ENTITY EDGES ===\n")
            for se in statement_entity_edges:
                f.write("SE_EDGE: " + json.dumps(_to_dict(se), ensure_ascii=False) + "\n")

            f.write("\n=== TEST ENTITY-ENTITY EDGES ===\n")
            for ee in entity_entity_edges:
                f.write("EE_EDGE: " + json.dumps(_to_dict(ee), ensure_ascii=False) + "\n")

        print(f"Exported extracted entities & edges to: {out_path}")
    except Exception as e:
        print(f"Failed to export test input doc: {e}")
