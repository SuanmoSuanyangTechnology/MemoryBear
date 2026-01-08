
from app.repositories.neo4j.cypher_queries import (
Memory_Timeline_ExtractedEntity,
Memory_Timeline_MemorySummary,
Memory_Timeline_Statement,
Memory_Space_Emotion_Statement,
Memory_Space_Emotion_MemorySummary,
Memory_Space_Emotion_ExtractedEntity,
Memory_Space_Interaction_Statement,
Memory_Space_Interaction_ExtractedEntity,
Memory_Space_Interaction_Summary
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

            return {
                'success': True,
                'data': timeline_data,
            }
            
        except Exception as e:
            logger.error(f"获取时间线记忆数据失败: {str(e)}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'data': {
                    "MemorySummary": [],
                    "Statement": [],
                    "ExtractedEntity": [],
                    "timelines_memory": []
                },
                'total': 0
            }
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
        
        # 去重
        memory_summary_list = list(set(memory_summary_list))
        statement_list = list(set(statement_list))
        extracted_entity_list = list(set(extracted_entity_list))
        
        # 合并所有数据
        all_timeline_data = memory_summary_list + statement_list + extracted_entity_list
        
        result = {
            "MemorySummary": memory_summary_list,
            "Statement": statement_list,
            "ExtractedEntity": extracted_entity_list,
            "timelines_memory": all_timeline_data
        }
        
        logger.info(f"时间线数据处理完成: MemorySummary={len(memory_summary_list)}, Statement={len(statement_list)}, ExtractedEntity={len(extracted_entity_list)}")
        
        return result

    def _process_field_value(self, value: Any, field_name: str) -> List[str]:
        """
        处理字段值，支持字符串、列表等类型
        
        Args:
            value: 字段值
            field_name: 字段名称（用于日志）
            
        Returns:
            处理后的字符串列表
        """
        processed_values = []
        
        try:
            if isinstance(value, list):
                # 如果是列表，处理每个元素
                for item in value:
                    if item is not None and str(item).strip() != '' and "MemorySummaryChunk" not in str(item):
                        processed_values.append(str(item))
            elif isinstance(value, str):
                # 如果是字符串，直接处理
                if value.strip() != '' and "MemorySummaryChunk" not in value:
                    processed_values.append(value)
            elif value is not None:
                # 其他类型转换为字符串
                str_value = str(value)
                if str_value.strip() != '' and "MemorySummaryChunk" not in str_value:
                    processed_values.append(str_value)
        except Exception as e:
            logger.warning(f"处理字段 {field_name} 的值时出错: {e}, 值类型: {type(value)}, 值: {value}")
        
        return processed_values




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
            return dt.strftime("%Y-%m-%d %H:%M:%S")
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
            
            return {
                'success': True,
                'data': final_data,
                'total': len(final_data)
            }
            
        except Exception as e:
            logger.error(f"获取情绪数据失败: {str(e)}")
            return {
                'success': False,
                'error': str(e),
                'data': [],
                'total': 0
            }

    def _process_emotion_results(self, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        处理情绪查询结果
        
        Args:
            results: Neo4j查询结果
            
        Returns:
            处理后的情绪数据列表
        """
        emotion_data = []
        
        # 检查results是否为空或不是列表
        if not results or not isinstance(results, list):
            logger.warning(f"情绪查询结果为空或格式不正确: {type(results)}")
            return emotion_data
        
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
            
            if emotion_type is not None and emotion_intensity is not None:
                # 只保留情绪相关的字段
                emotion_record = {
                    'emotion_intensity': emotion_intensity,
                    'emotion_type': emotion_type,
                    'created_at': formatted_created_at
                }
                emotion_data.append(emotion_record)
        
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

            if self.table == 'Statement':
                results = await self.connector.execute_query(Memory_Space_Interaction_Statement, id=self.id)
            elif self.table == 'ExtractedEntity':
                results = await self.connector.execute_query(Memory_Space_Interaction_ExtractedEntity, id=self.id)
            else:
                # MemorySummary/Chunk类型查询
                results = await self.connector.execute_query(Memory_Space_Interaction_Summary, id=self.id)

            # 处理查询结果
            interaction_data = self._process_interaction_results(results)
            
            # 转换Neo4j类型
            final_data = self._convert_neo4j_types(interaction_data)
            
            logger.info(f"成功获取 {len(final_data)} 条交互数据")
            
            return {
                'success': True,
                'data': final_data,
                'total': len(final_data)
            }
            
        except Exception as e:
            logger.error(f"获取交互数据失败: {str(e)}")
            return {
                'success': False,
                'error': str(e),
                'data': [],
                'total': 0
            }

    def _process_interaction_results(self, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        处理交互查询结果
        
        Args:
            results: Neo4j查询结果
            
        Returns:
            处理后的交互数据列表
        """
        interaction_data = []
        
        # 检查results是否为空或不是列表
        if not results or not isinstance(results, list):
            logger.warning(f"交互查询结果为空或格式不正确: {type(results)}")
            return interaction_data
        
        for record in results:
            # 检查record是否为字典类型
            if not isinstance(record, dict):
                logger.warning(f"跳过非字典类型的记录: {type(record)} - {record}")
                continue
                
            # 只保留交互相关的字段
            name = record.get('name')
            if name is not None:
                interaction_record = {
                    'name': name,
                    'importance_score': record.get('importance_score', 0.0),
                    'interaction_count': record.get('interaction_count', 1)  # 默认交互次数为1
                }
                interaction_data.append(interaction_record)
        
        return interaction_data

    async def close(self):
        """关闭数据库连接"""
        await self.connector.close()
