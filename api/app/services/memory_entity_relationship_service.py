
from app.repositories.neo4j.cypher_queries import (
Memory_Timeline_ExtractedEntity,
Memory_Timeline_MemorySummary,
Memory_Timeline_Statement,
Memory_Space_Emotion_Statement,
Memory_Space_Emotion_MemorySummary,
Memory_Space_Emotion_ExtractedEntity,
Memory_Space_Associative,Memory_Space_User,Memory_Space_Entity
)
from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from typing import Dict, List, Any, Optional
import logging
from neo4j.time import DateTime as Neo4jDateTime
import json
from datetime import datetime

logger = logging.getLogger(__name__)

class MemoryEntityService:
    def __init__(self, id: str, table: str):
        self.id = id
        self.table = table
        self.connector = Neo4jConnector()
    async def get_timeline_memories_server(self):
        """
        获取时间线记忆数据

        Args:
            id: 节点ID
            table: 节点类型/标签

        Returns:
            Dict包含：
            - success: 是否成功
            - data: 时间线数据列表
            - total: 数据总数
            - error: 错误信息（如果有）

        根据不同标签返回相应字段：
        - MemorySummary: content字段
        - Statement: statement字段
        - ExtractedEntity: name字段
        """
        try:
            logger.info(f"获取时间线记忆数据 - ID: {self.id}, Table: {self.table}")

            # 根据表类型选择查询
            if self.table == 'Statement':
                # Statement只需要输入ID，使用简化查询
                results = await self.connector.execute_query(Memory_Timeline_Statement, id=self.id)
            elif self.table == 'ExtractedEntity':
                # ExtractedEntity类型查询
                results = await self.connector.execute_query(Memory_Timeline_ExtractedEntity, id=self.id)
            else:
                # MemorySummary类型查询
                results = await self.connector.execute_query(Memory_Timeline_MemorySummary, id=self.id)
            
            # 记录查询结果的类型和内容用于调试
            logger.info(f"时间线查询结果类型: {type(results)}, 长度: {len(results) if isinstance(results, list) else 'N/A'}")
            
            # 处理查询结果
            timeline_data = self._process_timeline_results(results)

            logger.info(f"成功获取时间线记忆数据: 总计 {len(timeline_data.get('timelines_memory', []))} 条")

            return timeline_data
            
        except Exception as e:
            logger.error(f"获取时间线记忆数据失败: {str(e)}", exc_info=True)
            return  str(e)
    def _process_timeline_results(self, results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        处理时间线查询结果
        
        Args:
            results: Neo4j查询结果
            
        Returns:
            处理后的时间线数据字典
        """
        # 检查results是否为空或不是列表
        if not results or not isinstance(results, list):
            logger.warning(f"时间线查询结果为空或格式不正确: {type(results)}")
            return {
                "MemorySummary": [],
                "Statement": [],
                "ExtractedEntity": [],
                "timelines_memory": []
            }
        
        memory_summary_list = []
        statement_list = []
        extracted_entity_list = []
        
        for data in results:
            # 检查data是否为字典类型
            if not isinstance(data, dict):
                logger.warning(f"跳过非字典类型的记录: {type(data)} - {data}")
                continue
            
            # 处理MemorySummary
            summary = data.get('MemorySummary')
            if summary is not None:
                processed_summary = self._process_field_value(summary, "MemorySummary")
                memory_summary_list.extend(processed_summary)
            
            # 处理Statement
            statement = data.get('statement')
            if statement is not None:
                processed_statement = self._process_field_value(statement, "Statement")
                statement_list.extend(processed_statement)
            
            # 处理ExtractedEntity
            extracted_entity = data.get('ExtractedEntity')
            if extracted_entity is not None:
                processed_entity = self._process_field_value(extracted_entity, "ExtractedEntity")
                extracted_entity_list.extend(processed_entity)
        
        # 去重 - 现在处理的是字典列表，需要更智能的去重
        memory_summary_list = self._deduplicate_dict_list(memory_summary_list)
        statement_list = self._deduplicate_dict_list(statement_list)
        extracted_entity_list = self._deduplicate_dict_list(extracted_entity_list)
        
        # 合并所有数据并处理相同text的合并
        all_timeline_data = memory_summary_list + statement_list + extracted_entity_list
        all_timeline_data = self._merge_same_text_items(all_timeline_data)
        
        result = {
            "MemorySummary": memory_summary_list,
            "Statement": statement_list,
            "ExtractedEntity": extracted_entity_list,
            "timelines_memory": all_timeline_data
        }
        
        logger.info(f"时间线数据处理完成: MemorySummary={len(memory_summary_list)}, Statement={len(statement_list)}, ExtractedEntity={len(extracted_entity_list)}")
        
        return result

    def _deduplicate_dict_list(self, dict_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        对字典列表进行去重
        
        Args:
            dict_list: 字典列表
            
        Returns:
            去重后的字典列表
        """
        seen = set()
        result = []
        
        for item in dict_list:
            # 使用text作为去重的键
            text = item.get('text', '')
            if text and text not in seen:
                seen.add(text)
                result.append(item)
        
        return result

    def _merge_same_text_items(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        合并具有相同text的项目，合并type字段，保留一个时间
        
        Args:
            items: 项目列表
            
        Returns:
            合并后的项目列表
        """
        text_groups = {}
        
        # 按text分组
        for item in items:
            text = item.get('text', '')
            if not text:
                continue
                
            if text not in text_groups:
                text_groups[text] = {
                    'text': text,
                    'types': set(),
                    'created_at': item.get('created_at'),
                    'latest_time': item.get('created_at')
                }
            
            # 添加type到集合中
            item_type = item.get('type')
            if item_type:
                text_groups[text]['types'].add(item_type)
            
            # 保留最新的时间（如果有的话）
            current_time = item.get('created_at')
            if current_time and (not text_groups[text]['latest_time'] or 
                               self._is_later_time(current_time, text_groups[text]['latest_time'])):
                text_groups[text]['latest_time'] = current_time
        
        # 转换为最终格式
        result = []
        for text, group_data in text_groups.items():
            merged_item = {
                'text': text,
                'type': ', '.join(sorted(group_data['types'])),  # 合并多个type
                'created_at': group_data['latest_time']
            }
            result.append(merged_item)
        
        # 按时间排序（最新的在前）
        result.sort(key=lambda x: x.get('created_at', ''), reverse=True)
        
        return result

    def _is_later_time(self, time1: str, time2: str) -> bool:
        """
        比较两个时间字符串，判断time1是否晚于time2
        
        Args:
            time1: 时间字符串1
            time2: 时间字符串2
            
        Returns:
            time1是否晚于time2
        """
        try:
            if not time1 or not time2:
                return bool(time1)  # 如果time2为空，time1存在就算更晚
            
            # 简单的字符串比较（适用于ISO格式的时间）
            return time1 > time2
        except Exception:
            return False

    def _process_field_value(self, value: Any, field_name: str) -> List[Dict[str, Any]]:
        """
        处理字段值，支持字符串、列表等类型
        
        Args:
            value: 字段值
            field_name: 字段名称（用于日志）
            
        Returns:
            处理后的字典列表
        """
        processed_values = []

        try:
            if isinstance(value, list):
                # 如果是列表，处理每个元素
                for item in value:
                    if self._is_valid_item(item):
                        processed_item = self._process_single_item(item)
                        if processed_item:
                            processed_values.append(processed_item)
            elif isinstance(value, dict):
                # 如果是字典，直接处理
                if self._is_valid_item(value):
                    processed_item = self._process_single_item(value)
                    if processed_item:
                        processed_values.append(processed_item)
            elif isinstance(value, str):
                # 如果是字符串，转换为字典格式
                if value.strip() != '' and "MemorySummaryChunk" not in value:
                    processed_values.append({
                        'text': value,
                        'type': field_name,
                        'created_at': None
                    })
            elif value is not None:
                # 其他类型转换为字符串
                str_value = str(value)
                if str_value.strip() != '' and "MemorySummaryChunk" not in str_value:
                    processed_values.append({
                        'text': str_value,
                        'type': field_name,
                        'created_at': None
                    })
        except Exception as e:
            logger.warning(f"处理字段 {field_name} 的值时出错: {e}, 值类型: {type(value)}, 值: {value}")
        
        return processed_values

    def _is_valid_item(self, item: Any) -> bool:
        """
        检查项目是否有效
        
        Args:
            item: 要检查的项目
            
        Returns:
            是否有效
        """
        if item is None:
            return False
            
        if isinstance(item, dict):
            text = item.get('text')
            return (text is not None and 
                   str(text).strip() != '' and 
                   "MemorySummaryChunk" not in str(text))
        
        return (str(item).strip() != '' and 
               "MemorySummaryChunk" not in str(item))

    def _process_single_item(self, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        处理单个项目
        
        Args:
            item: 要处理的项目字典
            
        Returns:
            处理后的项目字典
        """
        try:
            text = item.get('text')
            created_at = item.get('created_at')
            item_type = item.get('type', '未知类型')
            
            # 转换Neo4j时间格式
            formatted_time = self._convert_neo4j_datetime(created_at)
            
            return {
                'text': text,
                'type': item_type,
                'created_at': formatted_time
            }
        except Exception as e:
            logger.warning(f"处理单个项目时出错: {e}, 项目: {item}")
            return None

    def _convert_neo4j_datetime(self, dt: Any) -> str:
        """
        转换Neo4j时间格式为标准时间字符串
        
        Args:
            dt: Neo4j时间对象或其他时间格式
            
        Returns:
            格式化的时间字符串
        """
        if dt is None:
            return None
            
        try:
            # 处理Neo4j DateTime对象
            if isinstance(dt, Neo4jDateTime):
                return dt.iso_format().replace('T', ' ').split('.')[0]
            
            # 处理其他neo4j时间类型
            if hasattr(dt, 'iso_format'):
                return dt.iso_format().replace('T', ' ').split('.')[0]
            
            # 处理字符串格式的时间
            if isinstance(dt, str):
                # 尝试解析ISO格式
                try:
                    parsed_dt = datetime.fromisoformat(dt.replace('Z', '+00:00'))
                    return parsed_dt.strftime("%Y-%m-%d %H:%M:%S")
                except ValueError:
                    return dt
            
            # 其他情况直接转换为字符串
            return str(dt)
            
        except Exception as e:
            logger.warning(f"转换时间格式失败: {e}, 原始值: {dt}")
            return str(dt) if dt is not None else None




    async def close(self):
        """关闭数据库连接"""
        await self.connector.close()



class MemoryEmotion:
    def __init__(self, id: str, table: str):
        self.id = id
        self.table = table
        self.connector = Neo4jConnector()

    def _convert_neo4j_types(self, obj: Any) -> Any:
        """
        递归转换Neo4j特殊类型为可序列化的Python类型
        """
        if isinstance(obj, Neo4jDateTime):
            # 转换为用户友好的日期格式
            return self._format_datetime(obj.iso_format())
        elif hasattr(obj, '__class__') and 'neo4j' in str(obj.__class__):
            if hasattr(obj, 'iso_format'):
                return self._format_datetime(obj.iso_format())
            elif hasattr(obj, '__str__'):
                return str(obj)
            else:
                return repr(obj)
        elif isinstance(obj, dict):
            return {k: self._convert_neo4j_types(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._convert_neo4j_types(item) for item in obj]
        elif isinstance(obj, tuple):
            return tuple(self._convert_neo4j_types(item) for item in obj)
        else:
            return obj

    def _format_datetime(self, iso_string: str) -> str:
        """
        将ISO格式的日期时间字符串转换为用户友好的格式
        
        Args:
            iso_string: ISO格式的日期时间字符串，如 "2026-01-07T13:40:33.679530"
            
        Returns:
            格式化后的日期时间字符串，如 "2026-01-07 13:40:33"
        """
        try:
            # 解析ISO格式的日期时间
            dt = datetime.fromisoformat(iso_string.replace('Z', '+00:00'))
            # 返回用户友好的格式：YYYY-MM-DD HH:MM:SS
            return dt.strftime("%Y.%m")
        except (ValueError, AttributeError):
            # 如果解析失败，返回原始字符串
            return iso_string

    async def get_emotion(self) -> Dict[str, Any]:
        """
        获取情绪随时间变化数据
        
        Returns:
            包含情绪数据的字典
        """
        try:
            logger.info(f"获取情绪数据 - ID: {self.id}, Table: {self.table}")

            if self.table == 'Statement':
                results = await self.connector.execute_query(Memory_Space_Emotion_Statement, id=self.id)
            elif self.table == 'ExtractedEntity':
                results = await self.connector.execute_query(Memory_Space_Emotion_ExtractedEntity, id=self.id)
            else:
                # MemorySummary/Chunk类型查询
                results = await self.connector.execute_query(Memory_Space_Emotion_MemorySummary, id=self.id)

            # 处理查询结果
            emotion_data = self._process_emotion_results(results)
            
            # 转换Neo4j类型
            final_data = self._convert_neo4j_types(emotion_data)
            
            logger.info(f"成功获取 {len(final_data)} 条情绪数据")
            
            return final_data
            
        except Exception as e:
            logger.error(f"获取情绪数据失败: {str(e)}")
            return e

    def _process_emotion_results(self, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        处理情绪查询结果，按emotion_type和created_at分组并累加emotion_intensity
        
        Args:
            results: Neo4j查询结果
            
        Returns:
            处理后的情绪数据列表，相同emotion_type和created_at的记录会合并并累加intensity
        """
        length_data=[]
        from collections import defaultdict
        
        # 用于按(emotion_type, created_at)分组累加intensity
        emotion_groups = defaultdict(float)
        
        # 检查results是否为空或不是列表
        if not results or not isinstance(results, list):
            logger.warning(f"情绪查询结果为空或格式不正确: {type(results)}")
            return []
        
        for record in results:
            # 检查record是否为字典类型
            if not isinstance(record, dict):
                logger.warning(f"跳过非字典类型的记录: {type(record)} - {record}")
                continue
                
            # 获取创建时间并格式化
            created_at = record.get('created_at')
            formatted_created_at = created_at
            
            # 如果created_at是字符串格式，尝试格式化
            if isinstance(created_at, str):
                formatted_created_at = self._format_datetime(created_at)
                
            emotion_type = record.get('emotion_type')
            emotion_intensity = record.get('emotion_intensity')
            if emotion_type !=None:
                length_data.append(emotion_intensity)


            if emotion_type is not None and emotion_intensity is not None and formatted_created_at is not None:
                # 使用(emotion_type, created_at)作为分组键
                if emotion_type=='joy' or emotion_type=='surprise':
                    emotion_type='positive'
                elif emotion_type=='sadness' or emotion_type=='fear'  or emotion_type=='anger':
                    emotion_type='negative'
                elif emotion_type=='neutral':
                    emotion_type='neutral'
                group_key = (emotion_type, formatted_created_at)
                
                # 累加emotion_intensity
                try:
                    emotion_groups[group_key] += float(emotion_intensity)
                except (ValueError, TypeError):
                    logger.warning(f"无法转换emotion_intensity为数字: {emotion_intensity}")
                    continue
        # 转换为最终格式
        emotion_data = [
            {
                'emotion_intensity':  round(intensity / len(length_data) * 100, 2),
                'emotion_type': emotion_type,
                'created_at': created_at
            }
            for (emotion_type, created_at), intensity in emotion_groups.items()
        ]
        
        # 按时间排序（最新的在前）
        emotion_data.sort(key=lambda x: x.get('created_at', ''), reverse=True)

        
        return emotion_data

    async def close(self):
        """关闭数据库连接"""
        await self.connector.close()


class MemoryInteraction:
    def __init__(self, id: str, table: str):
        self.id = id
        self.table = table
        self.connector = Neo4jConnector()

    def _convert_neo4j_types(self, obj: Any) -> Any:
        """
        递归转换Neo4j特殊类型为可序列化的Python类型
        """
        if isinstance(obj, Neo4jDateTime):
            # 转换为用户友好的日期格式
            return self._format_datetime(obj.iso_format())
        elif hasattr(obj, '__class__') and 'neo4j' in str(obj.__class__):
            if hasattr(obj, 'iso_format'):
                return self._format_datetime(obj.iso_format())
            elif hasattr(obj, '__str__'):
                return str(obj)
            else:
                return repr(obj)
        elif isinstance(obj, dict):
            return {k: self._convert_neo4j_types(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._convert_neo4j_types(item) for item in obj]
        elif isinstance(obj, tuple):
            return tuple(self._convert_neo4j_types(item) for item in obj)
        else:
            return obj

    def _format_datetime(self, iso_string: str) -> str:
        """
        将ISO格式的日期时间字符串转换为用户友好的格式
        
        Args:
            iso_string: ISO格式的日期时间字符串，如 "2026-01-07T13:40:33.679530"
            
        Returns:
            格式化后的日期时间字符串，如 "2026-01-07 13:40:33"
        """
        try:
            # 解析ISO格式的日期时间
            dt = datetime.fromisoformat(iso_string.replace('Z', '+00:00'))
            # 返回用户友好的格式：YYYY-MM-DD HH:MM:SS
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, AttributeError):
            # 如果解析失败，返回原始字符串
            return iso_string


    async def get_interaction_frequency(self) -> Dict[str, Any]:
        """
        获取交互频率数据
        
        Returns:
            包含交互数据的字典
        """
        try:
            logger.info(f"获取交互数据 - ID: {self.id}, Table: {self.table}")

            ori_data= await self.connector.execute_query(Memory_Space_Entity, id=self.id)
            if ori_data!=[]:
                # name = ori_data[0]['name']
                group_id = ori_data[0]['group_id']
                Space_User = await self.connector.execute_query(Memory_Space_User, group_id=group_id)
                if not Space_User:
                    return '不存在用户'
                user_id=Space_User[0]['id']

                results = await self.connector.execute_query(Memory_Space_Associative, id=self.id,user_id=user_id)



                # 处理查询结果
                interaction_data = self._process_interaction_results(results)

                # 转换Neo4j类型
                final_data = self._convert_neo4j_types(interaction_data)

                logger.info(f"成功获取 {len(final_data)} 条交互数据")

                return final_data
            return []
            
        except Exception as e:
            logger.error(f"获取交互数据失败: {str(e)}")
            return e

    def _process_interaction_results(self, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        处理交互查询结果，按季度统计交互频率
        
        Args:
            results: Neo4j查询结果
            
        Returns:
            按季度统计的交互数据列表，格式: [{"created_at": "2026Q1", "count": 3}]
        """
        from collections import defaultdict
        from datetime import datetime
        
        # 用于按季度分组计数
        quarterly_counts = defaultdict(int)
        
        for record in results:
            # 过滤掉statement为None的记录
            if not isinstance(record, dict) or record.get('statement') is None:
                continue
                
            created_at = record.get('created_at')
            if not created_at:
                continue
                
            try:
                # 处理不同类型的时间格式
                if isinstance(created_at, str):
                    # 解析ISO格式时间字符串
                    dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                elif hasattr(created_at, 'year') and hasattr(created_at, 'month'):
                    # 处理Neo4j DateTime对象
                    dt = datetime(created_at.year, created_at.month, created_at.day)
                else:
                    continue
                # 计算季度
                quarter = (dt.month - 1) // 3 + 1
                quarter_key = f"{dt.year}.Q{quarter}"
                # 增加该季度的计数
                quarterly_counts[quarter_key] += 1
                
            except (ValueError, AttributeError) as e:
                logger.warning(f"解析时间失败: {e}, 原始值: {created_at}")
                continue
        
        # 转换为所需格式并按时间排序
        interaction_data = [
            {"created_at": quarter, "count": count}
            for quarter, count in quarterly_counts.items()
        ]
        
        # 按季度排序（最新的在前）
        interaction_data.sort(key=lambda x: x["created_at"], reverse=True)
        
        return interaction_data

    async def close(self):
        """关闭数据库连接"""
        await self.connector.close()
