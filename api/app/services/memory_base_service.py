"""
Memory Base Service

提供记忆服务的基础功能和共享辅助方法。
"""
import asyncio
import re
from datetime import datetime
from typing import Optional
from pydantic import BaseModel
from app.core.logging_config import get_logger
from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.services.emotion_analytics_service import EmotionAnalyticsService
from app.core.memory.llm_tools.openai_client import OpenAIClient
from app.core.models.base import RedBearModelConfig
from app.services.memory_config_service import MemoryConfigService
from app.db import get_db_context
logger = get_logger(__name__)
class TranslationResponse(BaseModel):
    """翻译响应模型"""
    data: str

class MemoryTransService:
    """记忆翻译服务，提供中英文翻译功能"""
    
    def __init__(self, llm_client=None, model_id: Optional[str] = None):
        """
        初始化翻译服务
        
        Args:
            llm_client: LLM客户端实例或模型ID字符串（可选）
            model_id: 模型ID，用于初始化LLM客户端（可选）
        
        Note:
            - 如果llm_client是字符串，会被当作model_id使用
            - 如果同时提供llm_client和model_id，优先使用llm_client
        """
        # 处理llm_client参数：如果是字符串，当作model_id
        if isinstance(llm_client, str):
            self.model_id = llm_client
            self.llm_client = None
        else:
            self.llm_client = llm_client
            self.model_id = model_id
        
        self._initialized = False
    
    def _ensure_llm_client(self):
        """确保LLM客户端已初始化"""
        if self._initialized:
            return
        
        if self.llm_client is None:
            if self.model_id:
                with get_db_context() as db:
                    config_service = MemoryConfigService(db)
                    model_config = config_service.get_model_config(self.model_id)
                
                extra_params = {
                    "temperature": 0.2,
                    "max_tokens": 400,
                    "top_p": 0.8,
                    "stream": False,
                }
                
                self.llm_client = OpenAIClient(
                    RedBearModelConfig(
                        model_name=model_config.get("model_name"),
                        provider=model_config.get("provider"),
                        api_key=model_config.get("api_key"),
                        base_url=model_config.get("base_url"),
                        timeout=model_config.get("timeout", 30),
                        max_retries=model_config.get("max_retries", 3),
                        extra_params=extra_params
                    ),
                    type_=model_config.get("type")
                )
            else:
                raise ValueError("必须提供 llm_client 或 model_id 之一")
        
        self._initialized = True
    
    async def translate_to_english(self, text: str) -> str:
        """
        将中文翻译为英文
        
        Args:
            text: 要翻译的中文文本
            
        Returns:
            翻译后的英文文本
        """
        self._ensure_llm_client()

        translation_messages = [
            {
                "role": "user",
                "content": f"{text}\n\n中文翻译为英文，输出格式为{{\"data\":\"翻译后的内容\"}}"
            }
        ]

        try:
            response = await self.llm_client.response_structured(
                messages=translation_messages,
                response_model=TranslationResponse
            )
            return response.data
        except Exception as e:
            logger.error(f"翻译失败: {str(e)}")
            return text  # 翻译失败时返回原文

    async def is_english(self,text: str) -> bool:
        return bool(re.fullmatch(r"[A-Za-z\s]+", text))
    async def Translate(self, text: str, target_language: str = "en") -> str:
        """
        通用翻译方法（保持向后兼容）
        
        Args:
            text: 要翻译的文本
            target_language: 目标语言，"en"表示英文，"zh"表示中文
            
        Returns:
            翻译后的文本
        """
        if target_language == "en":
            return await self.translate_to_english(text)
        else:
            logger.warning(f"不支持的目标语言: {target_language}，返回原文")
            return text
    


 # 测试翻译服务
async def Translation_English(modid, text, fields=None):
    """
    将数据翻译为英文（支持字段级翻译）

    Args:
        modid: 模型ID
        text: 要翻译的数据（可以是字符串、字典或列表）
        fields: 需要翻译的字段列表（可选）
                如果为None，默认翻译: ['content', 'summary', 'statement', 'description',
                                      'name', 'aliases', 'caption', 'emotion_keywords']

    Returns:
        翻译后的数据，保持原有结构
    """
    trans_service = MemoryTransService(modid)
    # 执行翻译
    if isinstance(text, list):
        english_result=[]
        for i in text:
            is_eng=await trans_service.is_english(i)
            if not is_eng:
                english = await trans_service.Translate(i)
                english_result.append(english)
        return english_result
    if isinstance(text, str):
        is_eng = await trans_service.is_english(text)
        if not is_eng:
            english_result = await trans_service.Translate(text)
            return english_result
    return text
class MemoryBaseService:
    """记忆服务基类，提供共享的辅助方法"""
    
    def __init__(self):
        self.neo4j_connector = Neo4jConnector()
    
    @staticmethod
    def parse_timestamp(timestamp_value) -> Optional[int]:
        """
        将时间戳转换为毫秒级时间戳
        
        支持多种输入格式：
        - Neo4j DateTime 对象
        - ISO格式的时间戳字符串
        - Python datetime 对象
        
        Args:
            timestamp_value: 时间戳值（可以是多种类型）
            
        Returns:
            毫秒级时间戳，如果解析失败则返回None
        """
        if not timestamp_value:
            return None
        
        try:
            # 处理 Neo4j DateTime 对象
            if hasattr(timestamp_value, 'to_native'):
                dt_object = timestamp_value.to_native()
                return int(dt_object.timestamp() * 1000)
            
            # 处理 Python datetime 对象
            if isinstance(timestamp_value, datetime):
                return int(timestamp_value.timestamp() * 1000)
            
            # 处理字符串格式
            if isinstance(timestamp_value, str):
                dt_object = datetime.fromisoformat(timestamp_value.replace("Z", "+00:00"))
                return int(dt_object.timestamp() * 1000)
            
            # 其他情况尝试转换为字符串再解析
            dt_object = datetime.fromisoformat(str(timestamp_value).replace("Z", "+00:00"))
            return int(dt_object.timestamp() * 1000)
            
        except (ValueError, TypeError, AttributeError) as e:
            logger.warning(f"无法解析时间戳: {timestamp_value}, error={str(e)}")
            return None
    
    async def extract_episodic_emotion(
        self,
        summary_id: str,
        end_user_id: str
    ) -> Optional[str]:
        """
        提取情景记忆的主要情绪
        
        查询MemorySummary节点关联的Statement节点，
        返回emotion_intensity最大的emotion_type。
        
        Args:
            summary_id: Summary节点的ID
            end_user_id: 终端用户ID (group_id)
            
        Returns:
            最大emotion_intensity对应的emotion_type，如果没有则返回None
        """
        try:
            query = """
            MATCH (s:MemorySummary)
            WHERE elementId(s) = $summary_id AND s.group_id = $group_id
            MATCH (s)-[:DERIVED_FROM_STATEMENT]->(stmt:Statement)
            WHERE stmt.emotion_type IS NOT NULL 
              AND stmt.emotion_intensity IS NOT NULL
            RETURN stmt.emotion_type AS emotion_type, 
                   stmt.emotion_intensity AS emotion_intensity
            ORDER BY emotion_intensity DESC
            LIMIT 1
            """
            
            result = await self.neo4j_connector.execute_query(
                query,
                summary_id=summary_id,
                group_id=end_user_id
            )
            
            if result and len(result) > 0:
                emotion_type = result[0].get("emotion_type")
                logger.info(f"成功提取 summary_id={summary_id} 的情绪: {emotion_type}")
                return emotion_type
            else:
                logger.info(f"summary_id={summary_id} 没有情绪信息")
                return None
            
        except Exception as e:
            logger.error(f"提取情景记忆情绪时出错: {str(e)}", exc_info=True)
            return None
    
    async def get_episodic_memory_count(
        self,
        end_user_id: Optional[str] = None
    ) -> int:
        """
        获取情景记忆数量
        
        查询 MemorySummary 节点的数量。
        
        Args:
            end_user_id: 可选的终端用户ID，用于过滤特定用户的节点
            
        Returns:
            情景记忆的数量
        """
        try:
            if end_user_id:
                query = """
                MATCH (n:MemorySummary)
                WHERE n.group_id = $group_id
                RETURN count(n) as count
                """
                result = await self.neo4j_connector.execute_query(query, group_id=end_user_id)
            else:
                query = """
                MATCH (n:MemorySummary)
                RETURN count(n) as count
                """
                result = await self.neo4j_connector.execute_query(query)
            
            count = result[0]["count"] if result and len(result) > 0 else 0
            logger.debug(f"情景记忆数量: {count} (end_user_id={end_user_id})")
            return count
            
        except Exception as e:
            logger.error(f"获取情景记忆数量时出错: {str(e)}", exc_info=True)
            return 0
    
    async def get_explicit_memory_count(
        self,
        end_user_id: Optional[str] = None
    ) -> int:
        """
        获取显性记忆数量
        
        显性记忆 = 情景记忆（MemorySummary）+ 语义记忆（ExtractedEntity with is_explicit_memory=true）
        
        Args:
            end_user_id: 可选的终端用户ID，用于过滤特定用户的节点
            
        Returns:
            显性记忆的数量
        """
        try:
            # 1. 获取情景记忆数量
            episodic_count = await self.get_episodic_memory_count(end_user_id)
            
            # 2. 获取语义记忆数量（ExtractedEntity 且 is_explicit_memory = true）
            if end_user_id:
                semantic_query = """
                MATCH (e:ExtractedEntity)
                WHERE e.group_id = $group_id AND e.is_explicit_memory = true
                RETURN count(e) as count
                """
                semantic_result = await self.neo4j_connector.execute_query(
                    semantic_query, 
                    group_id=end_user_id
                )
            else:
                semantic_query = """
                MATCH (e:ExtractedEntity)
                WHERE e.is_explicit_memory = true
                RETURN count(e) as count
                """
                semantic_result = await self.neo4j_connector.execute_query(semantic_query)
            
            semantic_count = semantic_result[0]["count"] if semantic_result and len(semantic_result) > 0 else 0
            
            # 3. 计算总数
            explicit_count = episodic_count + semantic_count
            logger.debug(
                f"显性记忆数量: {explicit_count} "
                f"(情景={episodic_count}, 语义={semantic_count}, end_user_id={end_user_id})"
            )
            return explicit_count
            
        except Exception as e:
            logger.error(f"获取显性记忆数量时出错: {str(e)}", exc_info=True)
            return 0
    
    async def get_emotional_memory_count(
        self,
        end_user_id: Optional[str] = None,
        statement_count_fallback: int = 0
    ) -> int:
        """
        获取情绪记忆数量
        
        通过 EmotionAnalyticsService 获取情绪标签统计总数。
        如果获取失败或没有指定 end_user_id，使用 statement_count_fallback 作为后备。
        
        Args:
            end_user_id: 可选的终端用户ID
            statement_count_fallback: 后备方案的数量（通常是 statement 节点数量）
            
        Returns:
            情绪记忆的数量
        """
        try:
            if end_user_id:
                emotion_service = EmotionAnalyticsService()
                
                emotion_data = await emotion_service.get_emotion_tags(
                    end_user_id=end_user_id,
                    emotion_type=None,
                    start_date=None,
                    end_date=None,
                    limit=10
                )
                emotion_count = emotion_data.get("total_count", 0)
                logger.debug(f"情绪记忆数量: {emotion_count} (end_user_id={end_user_id})")
                return emotion_count
            else:
                # 如果没有指定 end_user_id，使用后备方案
                logger.debug(f"情绪记忆数量: {statement_count_fallback} (使用后备方案)")
                return statement_count_fallback
                
        except Exception as e:
            logger.warning(f"获取情绪记忆数量失败，使用后备方案: {str(e)}")
            return statement_count_fallback
    
    async def get_forget_memory_count(
        self,
        end_user_id: Optional[str] = None,
        forgetting_threshold: float = 0.3
    ) -> int:
        """
        获取遗忘记忆数量
        
        统计激活值低于遗忘阈值的节点数量（low_activation_nodes）。
        查询范围包括：Statement、ExtractedEntity、MemorySummary、Chunk 节点。
        
        Args:
            end_user_id: 可选的终端用户ID，用于过滤特定用户的节点
            forgetting_threshold: 遗忘阈值，默认 0.3
            
        Returns:
            遗忘记忆的数量（激活值低于阈值的节点数）
        """
        try:
            # 构建查询语句
            query = """
            MATCH (n)
            WHERE (n:Statement OR n:ExtractedEntity OR n:MemorySummary OR n:Chunk)
            """
            
            if end_user_id:
                query += " AND n.group_id = $group_id"
            
            query += """
            RETURN sum(CASE WHEN n.activation_value IS NOT NULL AND n.activation_value < $threshold THEN 1 ELSE 0 END) as low_activation_nodes
            """
            
            # 设置查询参数
            params = {'threshold': forgetting_threshold}
            if end_user_id:
                params['group_id'] = end_user_id
            
            # 执行查询
            result = await self.neo4j_connector.execute_query(query, **params)
            
            # 提取结果
            forget_count = result[0]['low_activation_nodes'] if result and len(result) > 0 else 0
            forget_count = forget_count or 0  # 处理 None 值
            
            logger.debug(
                f"遗忘记忆数量: {forget_count} "
                f"(threshold={forgetting_threshold}, end_user_id={end_user_id})"
            )
            return forget_count
            
        except Exception as e:
            logger.error(f"获取遗忘记忆数量时出错: {str(e)}", exc_info=True)
            return 0

if __name__ == '__main__':
    import asyncio
    a=[{"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:33925", "label": "Chunk", "properties": {"content": "\u7528\u6237: \u4f60\u597d", "created_at": "2026-01-06T14:50:08.381230", "associative_memory": 0}, "caption": "Chunk"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:33926", "label": "MemorySummary", "properties": {"content": "\u7528\u6237\u53d1\u8d77\u4e86\u5bf9\u8bdd\uff0c\u53d1\u9001\u4e86\u95ee\u5019\u8bed\"\u4f60\u597d\"\u3002", "created_at": "2026-01-06T14:50:11.363879", "associative_memory": 0}, "caption": "MemorySummary"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:76903", "label": "ExtractedEntity", "properties": {"description": "\u5728\u673a\u5668\u5b66\u4e60\u4e2d\u901a\u8fc7\u4e0d\u540c\u6570\u636e\u6837\u672c\u6765\u8861\u91cf\u6a21\u578b\u9884\u6d4b\u8bef\u5dee\u7684\u65b9\u6cd5", "name": "\u5f88\u591a\u91cd\u8981\u7684\u4eba\u751f\u9009\u62e9", "entity_type": "\u6982\u5ff5\u5b9e\u4f53\u8282\u70b9", "created_at": "2026-01-06T19:24:55.805367", "aliases": ["\u505a\u7279\u5f81\u63d0\u53d6", "\u56de\u6eaf\u5386\u53f2\u6570\u636e", "\u5728\u4e0d\u540c\u65f6\u95f4\u7a97\u53e3\u4e0b\u505a\u957f\u671f\u4e0e\u77ed\u671f\u6536\u76ca\u7684\u6743\u8861", "\u5728\u4e0d\u540c\u6837\u672c\u4e0a\u8bc4\u4f30\u635f\u5931\u51fd\u6570", "\u5bf9\u6bd4\u90a3\u4e9b\u8ba9\u4eba\u8e0f\u5b9e\u6216\u540e\u6094\u7684\u51b3\u5b9a", "\u628a\u65f6\u95f4\u62c9\u957f\u53bb\u60f3\u4e00\u5e74\u3001\u4e09\u5e74\u3001\u4e94\u5e74\u540e\u7684\u7ed3\u679c", "\u6a21\u578b\u53d1\u73b0\u4e86\u5f02\u5e38\u4fe1\u53f7", "\u6a21\u578b\u5728\u591a\u6b21\u62c6\u89e3\u4e0e\u8fed\u4ee3\u4e2d\u9010\u6e10\u6536\u655b\u5230\u7684\u7ed3\u679c", "\u7b54\u6848", "\u4eba\u6027", "\u6545\u4e8b\u4e2d\u7684\u4eba\u7269\u5173\u7cfb"], "connect_strength": "strong", "associative_memory": 11}, "caption": "ExtractedEntity"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:76904", "label": "ExtractedEntity", "properties": {"description": "\u4e00\u79cd\u6d89\u53ca\u591a\u56e0\u7d20\u5206\u6790\u548c\u672a\u6765\u63a8\u65ad\u7684\u590d\u6742\u4efb\u52a1\u7c7b\u578b", "name": "\u4e00\u6b21\u590d\u6742\u7684\u9884\u6d4b\u4efb\u52a1", "entity_type": "", "created_at": "2026-01-06T19:24:55.805367", "connect_strength": "Strong", "associative_memory": 2}, "caption": "ExtractedEntity"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:76905", "label": "ExtractedEntity", "properties": {"description": "\u4e00\u79cd\u5728\u91cd\u8981\u4eba\u751f\u9009\u62e9\u521d\u671f\u51fa\u73b0\u7684\u8f7b\u5fae\u4e0d\u5b89\u60c5\u7eea", "name": "\u6700\u5f00\u59cb\u51fa\u73b0\u7684\u90a3\u70b9\u4e0d\u5b89", "entity_type": "", "created_at": "2026-01-06T19:24:55.805367", "connect_strength": "Strong", "associative_memory": 2}, "caption": "ExtractedEntity"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:76906", "label": "ExtractedEntity", "properties": {"description": "\u5bf9\u4ee5\u5f80\u7ecf\u9a8c\u8fdb\u884c\u53cd\u601d\u548c\u5206\u6790\u7684\u884c\u4e3a", "name": "\u56de\u987e\u8fc7\u53bb\u7684\u7ecf\u5386", "entity_type": "\u6982\u5ff5\u5b9e\u4f53\u8282\u70b9", "created_at": "2026-01-06T19:24:55.805367", "connect_strength": "Strong", "associative_memory": 2}, "caption": "ExtractedEntity"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:76907", "label": "MemorySummary", "properties": {"content": "\u7528\u6237\u5c06\u91cd\u8981\u4eba\u751f\u9009\u62e9\u7c7b\u6bd4\u4e3a\u590d\u6742\u7684\u9884\u6d4b\u4efb\u52a1\uff1a\u521d\u59cb\u4e0d\u5b89\u5982\u540c\u6a21\u578b\u68c0\u6d4b\u5230\u5f02\u5e38\u4fe1\u53f7\uff1b\u56de\u987e\u8fc7\u5f80\u7ecf\u5386\u662f\u8fdb\u884c\u5386\u53f2\u6570\u636e\u56de\u6eaf\u4e0e\u7279\u5f81\u63d0\u53d6\uff1b\u5bf9\u6bd4\u4ee4\u4eba\u5b89\u5fc3\u6216\u540e\u6094\u7684\u51b3\u7b56\u76f8\u5f53\u4e8e\u5728\u4e0d\u540c\u6837\u672c\u4e0a\u8bc4\u4f30\u635f\u5931\u51fd\u6570\uff1b\u957f\u671f\u601d\u8003\u4e00\u5e74\u3001\u4e09\u5e74\u3001\u4e94\u5e74\u540e\u7684\u7ed3\u679c\uff0c\u662f\u5728\u6743\u8861\u4e0d\u540c\u65f6\u95f4\u7a97\u53e3\u4e0b\u7684\u77ed\u671f\u4e0e\u957f\u671f\u6536\u76ca\u3002\u6700\u7ec8\u7684\u201c\u7b54\u6848\u201d\u5e76\u975e\u76f4\u63a5\u8ba1\u7b97\u5f97\u51fa\uff0c\u800c\u662f\u5728\u591a\u6b21\u62c6\u89e3\u4e0e\u8fed\u4ee3\u8fc7\u7a0b\u4e2d\uff0c\u7531\u6a21\u578b\u9010\u6b65\u6536\u655b\u5f62\u6210\u3002", "created_at": "2026-01-06T19:25:18.822414", "associative_memory": 6}, "caption": "MemorySummary"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:76908", "label": "Dialogue", "properties": {"content": "\u7528\u6237: 1778 \u97f3\u4e50 ## \u4e8b\u4ef6 - 1 \u6708 1 \u65e5 \u2013 \u5a01\u5ec9\u00b7\u535a\u4f0a\u65af\u7684\u201c\u5f53\u654c\u5bf9\u56fd\u5bb6\u6b66\u88c5\u8d77\u6765\u65f6\u201d\u5728\u4f26\u6566\u5723\u8a79\u59c6\u65af\u5bab\u9996\u6620\u3002[1] - 1 \u6708 14 \u65e5 \u2013 \u6c83\u5c14\u592b\u5188\u00b7\u963f\u9a6c\u5fb7\u4e4c\u65af\u00b7\u83ab\u624e\u7279\u5728\u8bbf\u95ee\u66fc\u6d77\u59c6\u65f6\u4f1a\u89c1\u4e86\u5f53\u5730\u4f5c\u66f2\u5bb6\u683c\u5965\u5c14\u683c\u00b7\u7ea6\u745f\u592b\u00b7\u6c83\u683c\u52d2\u3002[1] - 1 \u6708 27 \u65e5 \u2013 \u5c3c\u79d1\u6d1b\u00b7\u76ae\u94a6\u5c3c\u7684\u7b2c\u4e00\u90e8\u6cd5\u56fd\u6b4c\u5267\u201c\u7f57\u5170\u201d\u5728\u5df4\u9ece\u6b4c\u5267\u9662\u9996\u6f14\u3002[1] - 2 \u6708 14 \u65e5 \u2013 \u6c83\u5c14\u592b\u5188\u00b7\u963f\u9a6c\u5fb7\u4e4c\u65af\u00b7\u83ab\u624e\u7279\u5199\u4fe1\u7ed9\u4ed6\u7684\u7236\u4eb2\u5229\u5965\u6ce2\u5fb7\u00b7\u83ab\u624e\u7279\uff0c\u544a\u8bc9\u4ed6\u4ed6\u591a\u4e48\u8ba8\u538c\u4e3a\u957f\u7b1b\u4f5c\u66f2\u3002[1] - 2 \u6708 17 \u65e5 \u2013 \u4f0a\u683c\u7eb3\u5179\u00b7\u4e4c\u59c6\u52b3\u592b\u7684\u201cDie Bergknappen\u201d\u6210\u4e3a\u7b2c\u4e00\u90e8\u5728\u7ef4\u4e5f\u7eb3\u4e0a\u6f14\u7684\u5f53\u5730\u4f5c\u66f2\u5bb6\u521b\u4f5c\u7684\u6b4c\u5531\u5267\u3002[1] - 3 \u6708 1 \u65e5 \u2013 \u514b\u91cc\u65af\u6258\u592b\u00b7\u5a01\u5229\u5df4\u5c14\u5fb7\u00b7\u683c\u9c81\u514b\u5728\u5df4\u9ece\u5c45\u4f4f\u5341\u5e74\u540e\u8fd4\u56de\u7ef4\u4e5f\u7eb3\u3002[1] - 3 \u6708 2 \u65e5 \u2013 \u7ef4\u4e5f\u7eb3\u56fd\u5bb6\u5267\u9662\u559c\u6b4c\u5267\u516c\u53f8\u4e3e\u884c\u6700\u540e\u4e00\u6b21\u6f14\u51fa", "created_at": "2026-01-06T19:31:26.129718", "associative_memory": 0}, "caption": "Dialogue"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:76909", "label": "Chunk", "properties": {"content": "\u7528\u6237: 1778 \u97f3\u4e50 ## \u4e8b\u4ef6 - 1 \u6708 1 \u65e5 \u2013 \u5a01\u5ec9\u00b7\u535a\u4f0a\u65af\u7684\u201c\u5f53\u654c\u5bf9\u56fd\u5bb6\u6b66\u88c5\u8d77\u6765\u65f6\u201d\u5728\u4f26\u6566\u5723\u8a79\u59c6\u65af\u5bab\u9996\u6620\u3002[1] - 1 \u6708 14 \u65e5 \u2013 \u6c83\u5c14\u592b\u5188\u00b7\u963f\u9a6c\u5fb7\u4e4c\u65af\u00b7\u83ab\u624e\u7279\u5728\u8bbf\u95ee\u66fc\u6d77\u59c6\u65f6\u4f1a\u89c1\u4e86\u5f53\u5730\u4f5c\u66f2\u5bb6\u683c\u5965\u5c14\u683c\u00b7\u7ea6\u745f\u592b\u00b7\u6c83\u683c\u52d2\u3002[1] - 1 \u6708 27 \u65e5 \u2013 \u5c3c\u79d1\u6d1b\u00b7\u76ae\u94a6\u5c3c\u7684\u7b2c\u4e00\u90e8\u6cd5\u56fd\u6b4c\u5267\u201c\u7f57\u5170\u201d\u5728\u5df4\u9ece\u6b4c\u5267\u9662\u9996\u6f14\u3002[1] - 2 \u6708 14 \u65e5 \u2013 \u6c83\u5c14\u592b\u5188\u00b7\u963f\u9a6c\u5fb7\u4e4c\u65af\u00b7\u83ab\u624e\u7279\u5199\u4fe1\u7ed9\u4ed6\u7684\u7236\u4eb2\u5229\u5965\u6ce2\u5fb7\u00b7\u83ab\u624e\u7279\uff0c\u544a\u8bc9\u4ed6\u4ed6\u591a\u4e48\u8ba8\u538c\u4e3a\u957f\u7b1b\u4f5c\u66f2\u3002[1] - 2 \u6708 17 \u65e5 \u2013 \u4f0a\u683c\u7eb3\u5179\u00b7\u4e4c\u59c6\u52b3\u592b\u7684\u201cDie Bergknappen\u201d\u6210\u4e3a\u7b2c\u4e00\u90e8\u5728\u7ef4\u4e5f\u7eb3\u4e0a\u6f14\u7684\u5f53\u5730\u4f5c\u66f2\u5bb6\u521b\u4f5c\u7684\u6b4c\u5531\u5267\u3002[1] - 3 \u6708 1 \u65e5 \u2013 \u514b\u91cc\u65af\u6258\u592b\u00b7\u5a01\u5229\u5df4\u5c14\u5fb7\u00b7\u683c\u9c81\u514b\u5728\u5df4\u9ece\u5c45\u4f4f\u5341\u5e74\u540e\u8fd4\u56de\u7ef4\u4e5f\u7eb3\u3002[1] - 3 \u6708 2 \u65e5 \u2013 \u7ef4\u4e5f\u7eb3\u56fd\u5bb6\u5267\u9662\u559c\u6b4c\u5267\u516c\u53f8\u4e3e\u884c\u6700\u540e\u4e00\u6b21\u6f14\u51fa", "created_at": "2026-01-06T19:31:26.129718", "associative_memory": 7}, "caption": "Chunk"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:76910", "label": "Statement", "properties": {"temporal_info": "STATIC", "stmt_type": "FACT", "statement": "1778\u5e741\u67081\u65e5\uff0c\u5a01\u5ec9\u00b7\u535a\u4f0a\u65af\u7684\u201c\u5f53\u654c\u5bf9\u56fd\u5bb6\u6b66\u88c5\u8d77\u6765\u65f6\u201d\u5728\u4f26\u6566\u5723\u8a79\u59c6\u65af\u5bab\u9996\u6620\u3002", "valid_at": "1778-01-01T00:00:00+00:00", "created_at": "2026-01-06T19:31:26.129718", "emotion_keywords": [], "associative_memory": 5}, "caption": "Statement"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:76911", "label": "Statement", "properties": {"temporal_info": "STATIC", "stmt_type": "FACT", "statement": "1778\u5e741\u670814\u65e5\uff0c\u6c83\u5c14\u592b\u5188\u00b7\u963f\u9a6c\u5fb7\u4e4c\u65af\u00b7\u83ab\u624e\u7279\u5728\u8bbf\u95ee\u66fc\u6d77\u59c6\u65f6\u4f1a\u89c1\u4e86\u5f53\u5730\u4f5c\u66f2\u5bb6\u683c\u5965\u5c14\u683c\u00b7\u7ea6\u745f\u592b\u00b7\u6c83\u683c\u52d2\u3002", "valid_at": "1778-01-14T00:00:00+00:00", "created_at": "2026-01-06T19:31:26.129718", "emotion_keywords": [], "associative_memory": 3}, "caption": "Statement"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:76912", "label": "Statement", "properties": {"temporal_info": "STATIC", "stmt_type": "FACT", "statement": "1778\u5e741\u670827\u65e5\uff0c\u5c3c\u79d1\u6d1b\u00b7\u76ae\u94a6\u5c3c\u7684\u7b2c\u4e00\u90e8\u6cd5\u56fd\u6b4c\u5267\u201c\u7f57\u5170\u201d\u5728\u5df4\u9ece\u6b4c\u5267\u9662\u9996\u6f14\u3002", "valid_at": "1778-01-27T00:00:00+00:00", "created_at": "2026-01-06T19:31:26.129718", "emotion_keywords": [], "associative_memory": 5}, "caption": "Statement"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:76913", "label": "Statement", "properties": {"temporal_info": "STATIC", "stmt_type": "FACT", "statement": "1778\u5e742\u670814\u65e5\uff0c\u6c83\u5c14\u592b\u5188\u00b7\u963f\u9a6c\u5fb7\u4e4c\u65af\u00b7\u83ab\u624e\u7279\u5199\u4fe1\u7ed9\u4ed6\u7684\u7236\u4eb2\u5229\u5965\u6ce2\u5fb7\u00b7\u83ab\u624e\u7279\uff0c\u544a\u8bc9\u4ed6\u4ed6\u591a\u4e48\u8ba8\u538c\u4e3a\u957f\u7b1b\u4f5c\u66f2\u3002", "valid_at": "1778-02-14T00:00:00+00:00", "created_at": "2026-01-06T19:31:26.129718", "emotion_keywords": [], "associative_memory": 4}, "caption": "Statement"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:76914", "label": "Statement", "properties": {"temporal_info": "STATIC", "stmt_type": "FACT", "statement": "1778\u5e742\u670817\u65e5\uff0c\u4f0a\u683c\u7eb3\u5179\u00b7\u4e4c\u59c6\u52b3\u592b\u7684\u201cDie Bergknappen\u201d\u6210\u4e3a\u7b2c\u4e00\u90e8\u5728\u7ef4\u4e5f\u7eb3\u4e0a\u6f14\u7684\u5f53\u5730\u4f5c\u66f2\u5bb6\u521b\u4f5c\u7684\u6b4c\u5531\u5267\u3002", "valid_at": "1778-02-17T00:00:00+00:00", "created_at": "2026-01-06T19:31:26.129718", "emotion_keywords": [], "associative_memory": 6}, "caption": "Statement"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:76915", "label": "Statement", "properties": {"temporal_info": "STATIC", "stmt_type": "FACT", "statement": "1778\u5e743\u67081\u65e5\uff0c\u514b\u91cc\u65af\u6258\u592b\u00b7\u5a01\u5229\u5df4\u5c14\u5fb7\u00b7\u683c\u9c81\u514b\u5728\u5df4\u9ece\u5c45\u4f4f\u5341\u5e74\u540e\u8fd4\u56de\u7ef4\u4e5f\u7eb3\u3002", "valid_at": "1778-03-01T00:00:00+00:00", "created_at": "2026-01-06T19:31:26.129718", "emotion_keywords": [], "associative_memory": 4}, "caption": "Statement"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:76916", "label": "Statement", "properties": {"temporal_info": "STATIC", "stmt_type": "FACT", "statement": "1778\u5e743\u67082\u65e5\uff0c\u7ef4\u4e5f\u7eb3\u56fd\u5bb6\u5267\u9662\u559c\u6b4c\u5267\u516c\u53f8\u4e3e\u884c\u6700\u540e\u4e00\u6b21\u6f14\u51fa\u3002", "valid_at": "1778-03-02T00:00:00+00:00", "created_at": "2026-01-06T19:31:26.129718", "emotion_keywords": [], "associative_memory": 4}, "caption": "Statement"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:76917", "label": "ExtractedEntity", "properties": {"description": "\u6c83\u5c14\u592b\u5188\u00b7\u963f\u9a6c\u5fb7\u4e4c\u65af\u00b7\u83ab\u624e\u7279\u7684\u7236\u4eb2\uff0c\u97f3\u4e50\u5bb6\u548c\u4f5c\u66f2\u5bb6", "name": "\u5a01\u5ec9\u00b7\u535a\u4f0a\u65af", "entity_type": "\u4eba\u7269\u5b9e\u4f53\u8282\u70b9", "created_at": "2026-01-06T19:31:26.129718", "aliases": ["\u4f0a\u683c\u7eb3\u5179\u00b7\u4e4c\u59c6\u52b3\u592b", "\u514b\u91cc\u65af\u6258\u592b\u00b7\u5a01\u5229\u5df4\u5c14\u5fb7\u00b7\u683c\u9c81\u514b", "\u5229\u5965\u6ce2\u5fb7\u00b7\u83ab\u624e\u7279", "\u5c3c\u79d1\u6d1b\u00b7\u76ae\u94a6\u5c3c", "\u683c\u5965\u5c14\u683c\u00b7\u7ea6\u745f\u592b\u00b7\u6c83\u683c\u52d2", "\u6c83\u5c14\u592b\u5188\u00b7\u963f\u9a6c\u5fb7\u4e4c\u65af\u00b7\u83ab\u624e\u7279", "\u83ab\u624e\u7279"], "connect_strength": "strong", "associative_memory": 11}, "caption": "ExtractedEntity"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:76918", "label": "ExtractedEntity", "properties": {"description": "\u4e00\u90e8\u7531\u4f0a\u683c\u7eb3\u5179\u00b7\u4e4c\u59c6\u52b3\u592b\u521b\u4f5c\u7684\u6b4c\u5531\u5267", "name": "\u5f53\u654c\u5bf9\u56fd\u5bb6\u6b66\u88c5\u8d77\u6765\u65f6", "entity_type": "", "created_at": "2026-01-06T19:31:26.129718", "aliases": ["Die Bergknappen"], "connect_strength": "strong", "associative_memory": 6}, "caption": "ExtractedEntity"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:76919", "label": "ExtractedEntity", "properties": {"description": "\u4f4d\u4e8e\u4f26\u6566\u7684\u7687\u5bb6\u5bab\u6bbf\uff0c\u66fe\u7528\u4e8e\u4e3e\u529e\u97f3\u4e50\u9996\u6f14", "name": "\u4f26\u6566\u5723\u8a79\u59c6\u65af\u5bab", "entity_type": "\u5730\u70b9\u5b9e\u4f53\u8282\u70b9", "created_at": "2026-01-06T19:31:26.129718", "aliases": ["\u5723\u8a79\u59c6\u65af\u5bab", "\u7f57\u5170"], "connect_strength": "strong", "associative_memory": 5}, "caption": "ExtractedEntity"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:76920", "label": "ExtractedEntity", "properties": {"description": "\u4f4d\u4e8e\u5df4\u9ece\u7684\u8457\u540d\u6b4c\u5267\u9662\uff0c\u9996\u6f14\u591a\u90e8\u91cd\u8981\u6b4c\u5267\u4f5c\u54c1", "name": "\u5df4\u9ece\u6b4c\u5267\u9662", "entity_type": "\u5730\u70b9\u5b9e\u4f53\u8282\u70b9", "created_at": "2026-01-06T19:31:26.129718", "aliases": ["\u5df4\u9ece\u56fd\u5bb6\u6b4c\u5267\u9662", "\u7ef4\u4e5f\u7eb3", "\u7ef4\u4e5f\u7eb3\u56fd\u5bb6\u5267\u9662\u559c\u6b4c\u5267\u516c\u53f8", "\u7ef4\u4e5f\u7eb3\u5e02"], "connect_strength": "strong", "associative_memory": 8}, "caption": "ExtractedEntity"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:76921", "label": "ExtractedEntity", "properties": {"description": "\u7ec4\u7ec7\u4e3e\u529e\u7684\u6700\u7ec8\u573a\u6b21\u7684\u8868\u6f14\u6d3b\u52a8", "name": "\u4e3a\u957f\u7b1b\u4f5c\u66f2", "entity_type": "", "created_at": "2026-01-06T19:31:26.129718", "aliases": ["\u6700\u540e\u4e00\u6b21\u6f14\u51fa"], "connect_strength": "strong", "associative_memory": 4}, "caption": "ExtractedEntity"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:76922", "label": "ExtractedEntity", "properties": {"description": "\u4e00\u79cd\u7ed3\u5408\u97f3\u4e50\u4e0e\u620f\u5267\u7684\u821e\u53f0\u827a\u672f\u5f62\u5f0f", "name": "\u6b4c\u5531\u5267", "entity_type": "", "created_at": "2026-01-06T19:31:26.129718", "aliases": ["Singspiel", "\u5fb7\u8bed\u6b4c\u5531\u5267"], "connect_strength": "Strong", "associative_memory": 2}, "caption": "ExtractedEntity"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:76923", "label": "MemorySummary", "properties": {"content": "1778\u5e74\u97f3\u4e50\u4e8b\u4ef6\uff1a1\u67081\u65e5\uff0c\u5a01\u5ec9\u00b7\u535a\u4f0a\u65af\u7684\u300a\u5f53\u654c\u5bf9\u56fd\u5bb6\u6b66\u88c5\u8d77\u6765\u65f6\u300b\u5728\u4f26\u6566\u5723\u8a79\u59c6\u65af\u5bab\u9996\u6f14\uff1b1\u670814\u65e5\uff0c\u83ab\u624e\u7279\u5728\u66fc\u6d77\u59c6\u4f1a\u89c1\u4f5c\u66f2\u5bb6\u683c\u5965\u5c14\u683c\u00b7\u7ea6\u745f\u592b\u00b7\u6c83\u683c\u52d2\uff1b1\u670827\u65e5\uff0c\u5c3c\u79d1\u6d1b\u00b7\u76ae\u94a6\u5c3c\u7684\u6b4c\u5267\u300a\u7f57\u5170\u300b\u5728\u5df4\u9ece\u6b4c\u5267\u9662\u9996\u6f14\uff1b2\u670814\u65e5\uff0c\u83ab\u624e\u7279\u5199\u4fe1\u7ed9\u7236\u4eb2\uff0c\u8868\u8fbe\u5bf9\u4e3a\u957f\u7b1b\u4f5c\u66f2\u7684\u538c\u6076\uff1b2\u670817\u65e5\uff0c\u4f0a\u683c\u7eb3\u5179\u00b7\u4e4c\u59c6\u52b3\u592b\u7684\u300aDie Bergknappen\u300b\u6210\u4e3a\u9996\u90e8\u5728\u7ef4\u4e5f\u7eb3\u4e0a\u6f14\u7684\u672c\u5730\u521b\u4f5c\u6b4c\u5531\u5267\uff1b3\u67081\u65e5\uff0c\u683c\u9c81\u514b\u5728\u5df4\u9ece\u5c45\u4f4f\u5341\u5e74\u540e\u8fd4\u56de\u7ef4\u4e5f\u7eb3\uff1b3\u67082\u65e5\uff0c\u7ef4\u4e5f\u7eb3\u56fd\u5bb6\u5267\u9662\u559c\u6b4c\u5267\u516c\u53f8\u4e3e\u884c\u6700\u540e\u4e00\u573a\u6f14\u51fa\u3002", "created_at": "2026-01-06T19:32:01.901346", "associative_memory": 7}, "caption": "MemorySummary"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:76998", "label": "Dialogue", "properties": {"content": "\u7528\u6237: \u5f20\u66fc\u7389\u51fa\u6f14\u4e86\u5f90\u514b\u5bfc\u6f14\u7684\u9752\u86c7\u3002\u4ee5\u4eba\u6027\uff0c\u795e\u6027\u7b49\u65b9\u9762\u6765\u63cf\u8ff0\u4e86\u6545\u4e8b\u4e2d\u7684\u4eba\u7269\u5173\u7cfb", "created_at": "2026-01-07T13:40:33.679530", "associative_memory": 0}, "caption": "Dialogue"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77001", "label": "Chunk", "properties": {"content": "\u7528\u6237: \u5f20\u66fc\u7389\u51fa\u6f14\u4e86\u5f90\u514b\u5bfc\u6f14\u7684\u9752\u86c7\u3002\u4ee5\u4eba\u6027\uff0c\u795e\u6027\u7b49\u65b9\u9762\u6765\u63cf\u8ff0\u4e86\u6545\u4e8b\u4e2d\u7684\u4eba\u7269\u5173\u7cfb", "created_at": "2026-01-07T13:40:33.679530", "associative_memory": 2}, "caption": "Chunk"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77002", "label": "Statement", "properties": {"temporal_info": "STATIC", "stmt_type": "FACT", "statement": "\u5f20\u66fc\u7389\u51fa\u6f14\u4e86\u5f90\u514b\u5bfc\u6f14\u7684\u9752\u86c7\u3002", "created_at": "2026-01-07T13:40:33.679530", "emotion_keywords": [], "emotion_type": "\u4e2d\u6027", "emotion_subject": "\u81ea\u5df1", "associative_memory": 4}, "caption": "Statement"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77003", "label": "Statement", "properties": {"temporal_info": "ATEMPORAL", "stmt_type": "FACT", "statement": "\u6545\u4e8b\u4e2d\u7684\u4eba\u7269\u5173\u7cfb\u4ece\u4eba\u6027\u3001\u795e\u6027\u7b49\u65b9\u9762\u88ab\u63cf\u8ff0\u3002", "created_at": "2026-01-07T13:40:33.679530", "emotion_keywords": [], "emotion_type": "\u4e2d\u6027", "emotion_subject": "\u4e8b\u7269\u5bf9\u8c61", "associative_memory": 4}, "caption": "Statement"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77015", "label": "Dialogue", "properties": {"content": "\u7528\u6237: \u5c0f\u7eff\u4e0d\u559c\u6b22\u770b\u6050\u6016\u7247", "created_at": "2026-01-12T10:38:44.913286", "associative_memory": 0}, "caption": "Dialogue"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77016", "label": "ExtractedEntity", "properties": {"description": "\u4e2d\u56fd\u8457\u540d\u5973\u6f14\u5458", "name": "\u5f20\u66fc\u7389", "entity_type": "\u4eba\u7269\u5b9e\u4f53\u8282\u70b9", "created_at": "2026-01-07T13:40:33.679530", "aliases": ["\u5f90\u514b"], "connect_strength": "strong", "associative_memory": 2}, "caption": "ExtractedEntity"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77017", "label": "ExtractedEntity", "properties": {"description": "\u7531\u5f90\u514b\u5bfc\u6f14\u7684\u7535\u5f71\u4f5c\u54c1", "name": "\u9752\u86c7", "entity_type": "", "created_at": "2026-01-07T13:40:33.679530", "aliases": ["\u9752\u86c7\u7535\u5f71"], "connect_strength": "Strong", "associative_memory": 2}, "caption": "ExtractedEntity"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77018", "label": "ExtractedEntity", "properties": {"description": "\u795e\u7684\u7279\u8d28\uff0c\u5982\u8d85\u51e1\u80fd\u529b\u3001\u4e0d\u673d\u3001\u795e\u5723\u6027", "name": "\u795e\u6027", "entity_type": "\u6982\u5ff5\u5b9e\u4f53\u8282\u70b9", "created_at": "2026-01-07T13:40:33.679530", "connect_strength": "Strong", "associative_memory": 1}, "caption": "ExtractedEntity"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77019", "label": "MemorySummary", "properties": {"content": "\u5f20\u66fc\u7389\u51fa\u6f14\u4e86\u5f90\u514b\u5bfc\u6f14\u7684\u7535\u5f71\u300a\u9752\u86c7\u300b\uff0c\u5f71\u7247\u901a\u8fc7\u4eba\u6027\u4e0e\u795e\u6027\u7684\u5bf9\u6bd4\u63a2\u8ba8\u4e86\u4eba\u7269\u5173\u7cfb\uff0c\u5c55\u73b0\u4e86\u89d2\u8272\u4e4b\u95f4\u590d\u6742\u7684\u60c5\u611f\u4e0e\u8eab\u4efd\u51b2\u7a81\u3002", "created_at": "2026-01-07T13:40:50.650486", "associative_memory": 2}, "caption": "MemorySummary"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77020", "label": "Dialogue", "properties": {"content": "\u7528\u6237: \u4eca\u5929\u5468\u4e00\uff0c\u6211\u60f3\u53bb\u722c\u5c71", "created_at": "2026-01-07T16:44:09.602315", "associative_memory": 0}, "caption": "Dialogue"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77021", "label": "Chunk", "properties": {"content": "\u7528\u6237: \u4eca\u5929\u5468\u4e00\uff0c\u6211\u60f3\u53bb\u722c\u5c71", "created_at": "2026-01-07T16:44:09.602315", "associative_memory": 2}, "caption": "Chunk"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77022", "label": "Statement", "properties": {"temporal_info": "STATIC", "stmt_type": "FACT", "statement": "\u4eca\u5929\u662f\u5468\u4e00\u3002", "valid_at": "2026-01-05T00:00:00+00:00", "created_at": "2026-01-07T16:44:09.602315", "emotion_keywords": [], "emotion_type": "\u4e2d\u6027", "emotion_subject": "\u81ea\u5df1", "associative_memory": 3}, "caption": "Statement"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77023", "label": "Statement", "properties": {"temporal_info": "DYNAMIC", "stmt_type": "PREDICTION", "statement": "\u7528\u6237\u60f3\u53bb\u722c\u5c71\u3002", "valid_at": "2026-01-07T00:00:00+00:00", "created_at": "2026-01-07T16:44:09.602315", "emotion_keywords": [], "associative_memory": 4}, "caption": "Statement"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77024", "label": "ExtractedEntity", "properties": {"description": "\u5f53\u524d\u65e5\u671f\uff0c\u6307\u8bf4\u8bdd\u65f6\u7684\u5f53\u5929", "name": "\u4eca\u5929", "entity_type": "\u65f6\u95f4\u5b9e\u4f53\u8282\u70b9", "created_at": "2026-01-07T16:44:09.602315", "aliases": ["\u5468\u4e00", "\u661f\u671f\u4e00"], "connect_strength": "strong", "associative_memory": 2}, "caption": "ExtractedEntity"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77025", "label": "ExtractedEntity", "properties": {"description": "\u8bf4\u8bdd\u7684\u672c\u4eba\uff0c\u8ba1\u5212\u53c2\u4e0e\u722c\u5c71\u6d3b\u52a8", "name": "\u7528\u6237", "entity_type": "\u4eba\u7269\u5b9e\u4f53\u8282\u70b9", "created_at": "2026-01-07T16:44:09.602315", "aliases": ["\u5c0f\u660e"], "connect_strength": "Strong", "associative_memory": 6}, "caption": "ExtractedEntity"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77026", "label": "ExtractedEntity", "properties": {"description": "\u4e00\u79cd\u6237\u5916\u767b\u5c71\u8fd0\u52a8", "name": "\u722c\u5c71", "entity_type": "", "created_at": "2026-01-07T16:44:09.602315", "aliases": ["\u767b\u5c71"], "connect_strength": "Strong", "associative_memory": 1}, "caption": "ExtractedEntity"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77027", "label": "MemorySummary", "properties": {"content": "\u7528\u6237\u5728\u5468\u4e00\u8868\u793a\u60f3\u53bb\u722c\u5c71\u3002", "created_at": "2026-01-07T16:44:54.146672", "associative_memory": 2}, "caption": "MemorySummary"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77028", "label": "Chunk", "properties": {"content": "\u7528\u6237: \u5c0f\u7eff\u4e0d\u559c\u6b22\u770b\u6050\u6016\u7247", "created_at": "2026-01-12T10:38:44.913286", "associative_memory": 1}, "caption": "Chunk"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77029", "label": "Statement", "properties": {"temporal_info": "DYNAMIC", "stmt_type": "OPINION", "statement": "\u5c0f\u7eff\u4e0d\u559c\u6b22\u770b\u6050\u6016\u7247\u3002", "created_at": "2026-01-12T10:38:44.913286", "emotion_keywords": [], "associative_memory": 4}, "caption": "Statement"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77030", "label": "ExtractedEntity", "properties": {"description": "\u4e00\u4e2a\u7528\u6237\u63d0\u5230\u7684\u4eba\u7269", "name": "\u5c0f\u7eff", "entity_type": "\u4eba\u7269\u5b9e\u4f53\u8282\u70b9", "created_at": "2026-01-12T10:38:44.913286", "connect_strength": "Strong", "associative_memory": 3}, "caption": "ExtractedEntity"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77031", "label": "ExtractedEntity", "properties": {"description": "\u89c2\u770b\u6050\u6016\u7535\u5f71\u7684\u6d3b\u52a8", "name": "\u770b\u6050\u6016\u7247", "entity_type": "", "created_at": "2026-01-12T10:38:44.913286", "aliases": ["\u770b\u6050\u6016\u7535\u5f71", "\u89c2\u770b\u6050\u6016\u7247"], "connect_strength": "Strong", "associative_memory": 1}, "caption": "ExtractedEntity"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77032", "label": "MemorySummary", "properties": {"content": "\u5c0f\u7eff\u4e0d\u559c\u6b22\u770b\u6050\u6016\u7247", "created_at": "2026-01-12T10:38:54.849079", "associative_memory": 1}, "caption": "MemorySummary"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77033", "label": "Dialogue", "properties": {"content": "\u7528\u6237: \u5c0f\u7eff\u6253\u7b97\u548c\u5c0f\u660e\u53bb\u722c\u5c71", "created_at": "2026-01-12T10:40:16.459309", "associative_memory": 0}, "caption": "Dialogue"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77034", "label": "Chunk", "properties": {"content": "\u7528\u6237: \u5c0f\u7eff\u6253\u7b97\u548c\u5c0f\u660e\u53bb\u722c\u5c71", "created_at": "2026-01-12T10:40:16.459309", "associative_memory": 1}, "caption": "Chunk"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77035", "label": "Statement", "properties": {"temporal_info": "DYNAMIC", "stmt_type": "PREDICTION", "statement": "\u5c0f\u7eff\u6253\u7b97\u548c\u5c0f\u660e\u53bb\u722c\u5c71\u3002", "valid_at": "2026-01-12T00:00:00+00:00", "created_at": "2026-01-12T10:40:16.459309", "emotion_keywords": [], "associative_memory": 5}, "caption": "Statement"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77036", "label": "ExtractedEntity", "properties": {"description": "\u6237\u5916\u81ea\u7136\u5730\u5f62\uff0c\u7528\u4e8e\u722c\u5c71\u6d3b\u52a8", "name": "\u5c71", "entity_type": "\u5730\u70b9\u5b9e\u4f53\u8282\u70b9", "created_at": "2026-01-12T10:40:16.459309", "aliases": ["\u5c71\u8109", "\u9ad8\u5c71"], "connect_strength": "Strong", "associative_memory": 2}, "caption": "ExtractedEntity"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77037", "label": "MemorySummary", "properties": {"content": "\u5c0f\u7eff\u6253\u7b97\u548c\u5c0f\u660e\u53bb\u722c\u5c71\u3002", "created_at": "2026-01-12T10:40:36.429460", "associative_memory": 1}, "caption": "MemorySummary"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77038", "label": "Dialogue", "properties": {"content": "\u7528\u6237: \u6211\u6253\u7b97\u548c\u5c0f\u660e\u4ee5\u53ca\u5c0f\u7ea2\u53bb\u722c\u5c71", "created_at": "2026-01-12T11:21:39.219607", "associative_memory": 0}, "caption": "Dialogue"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77039", "label": "Chunk", "properties": {"content": "\u7528\u6237: \u6211\u6253\u7b97\u548c\u5c0f\u660e\u4ee5\u53ca\u5c0f\u7ea2\u53bb\u722c\u5c71", "created_at": "2026-01-12T11:21:39.219607", "associative_memory": 1}, "caption": "Chunk"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77040", "label": "Statement", "properties": {"temporal_info": "DYNAMIC", "stmt_type": "PREDICTION", "statement": "\u7528\u6237\u6253\u7b97\u548c\u5c0f\u660e\u4ee5\u53ca\u5c0f\u7ea2\u53bb\u722c\u5c71\u3002", "valid_at": "2026-01-12T00:00:00+00:00", "created_at": "2026-08-12T11:21:39.219607+00:00", "emotion_keywords": [], "associative_memory": 5}, "caption": "Statement"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77041", "label": "ExtractedEntity", "properties": {"description": "\u88ab\u63d0\u53ca\u7684\u53e6\u4e00\u4e2a\u4eba\u7269\uff0c\u53ef\u80fd\u662f\u670b\u53cb\u6216\u540c\u4f34", "name": "\u5c0f\u7ea2", "entity_type": "\u4eba\u7269\u5b9e\u4f53\u8282\u70b9", "created_at": "2026-01-12T11:21:39.219607", "connect_strength": "Strong", "associative_memory": 4}, "caption": "ExtractedEntity"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77042", "label": "MemorySummary", "properties": {"content": "\u7528\u6237\u8ba1\u5212\u4e0e\u5c0f\u660e\u548c\u5c0f\u7ea2\u4e00\u8d77\u53bb\u722c\u5c71\u3002", "created_at": "2026-01-12T11:21:56.346023", "associative_memory": 1}, "caption": "MemorySummary"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77043", "label": "Dialogue", "properties": {"content": "\u7528\u6237: \u6211\u8fd8\u60f3\u548c\u5c0f\u7ea2\u53bb\u770b\u7535\u5f71", "created_at": "2026-01-12T11:23:35.894508", "associative_memory": 0}, "caption": "Dialogue"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77044", "label": "Chunk", "properties": {"content": "\u7528\u6237: \u6211\u8fd8\u60f3\u548c\u5c0f\u7ea2\u53bb\u770b\u7535\u5f71", "created_at": "2026-01-12T11:23:35.894508", "associative_memory": 1}, "caption": "Chunk"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77045", "label": "Statement", "properties": {"temporal_info": "DYNAMIC", "stmt_type": "PREDICTION", "statement": "\u7528\u6237\u8fd8\u60f3\u548c\u5c0f\u7ea2\u53bb\u770b\u7535\u5f71\u3002", "valid_at": "2026-01-12T00:00:00+00:00", "created_at": "2026-01-12T11:23:35.894508", "emotion_keywords": ["\u60f3"], "emotion_type": "\u6109\u5feb", "emotion_subject": "\u81ea\u5df1", "associative_memory": 5}, "caption": "Statement"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77046", "label": "ExtractedEntity", "properties": {"description": "\u4e00\u79cd\u5a31\u4e50\u6d3b\u52a8\uff0c\u6307\u89c2\u770b\u7535\u5f71\u7684\u884c\u4e3a", "name": "\u7535\u5f71", "entity_type": "\u4e8b\u4ef6\u5b9e\u4f53\u8282\u70b9", "created_at": "2026-01-12T11:23:35.894508", "aliases": ["\u5f71\u7247", "\u7535\u5f71\u653e\u6620"], "connect_strength": "Strong", "associative_memory": 1}, "caption": "ExtractedEntity"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77047", "label": "MemorySummary", "properties": {"content": "\u7528\u6237\u60f3\u548c\u5c0f\u7ea2\u4e00\u8d77\u53bb\u770b\u7535\u5f71\u3002", "created_at": "2026-01-12T11:23:47.907049", "associative_memory": 1}, "caption": "MemorySummary"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77048", "label": "Dialogue", "properties": {"content": "\u7528\u6237: \u6211\u8fd8\u60f3\u548c\u5c0f\u7ea2\u53bb\u6e38\u6e56", "created_at": "2026-01-12T11:43:22.323255", "associative_memory": 0}, "caption": "Dialogue"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77049", "label": "Chunk", "properties": {"content": "\u7528\u6237: \u6211\u8fd8\u60f3\u548c\u5c0f\u7ea2\u53bb\u6e38\u6e56", "created_at": "2026-01-12T11:43:22.323255", "associative_memory": 1}, "caption": "Chunk"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77050", "label": "Statement", "properties": {"temporal_info": "DYNAMIC", "stmt_type": "PREDICTION", "statement": "\u7528\u6237\u8fd8\u60f3\u548c\u5c0f\u7ea2\u53bb\u6e38\u6e56\u3002", "valid_at": "2026-01-12T00:00:00+00:00", "created_at": "2026-01-12T11:43:22.323255", "emotion_keywords": ["\u60f3"], "emotion_type": "\u6109\u5feb", "emotion_subject": "\u81ea\u5df1", "associative_memory": 5}, "caption": "Statement"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77051", "label": "ExtractedEntity", "properties": {"description": "\u81ea\u7136\u6c34\u4f53\uff0c\u7528\u4e8e\u6e38\u61a9\u6d3b\u52a8\u7684\u6e56\u6cca", "name": "\u6e56", "entity_type": "\u5730\u70b9\u5b9e\u4f53\u8282\u70b9", "created_at": "2026-01-12T11:43:22.323255", "aliases": ["\u6e56\u6cca"], "connect_strength": "Strong", "associative_memory": 1}, "caption": "ExtractedEntity"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77052", "label": "MemorySummary", "properties": {"content": "\u7528\u6237\u60f3\u548c\u5c0f\u7ea2\u4e00\u8d77\u53bb\u6e38\u6e56\u3002", "created_at": "2026-01-12T11:43:37.367749", "associative_memory": 1}, "caption": "MemorySummary"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77054", "label": "Dialogue", "properties": {"content": "\u7528\u6237: \u60a8\u597d\u6211\u53eb\u5c0f\u84dd\uff0c\u5c0f\u660e\u4eca\u5929\u7ea6\u6211\u51fa\u53bb\u91ce\u9910\uff0c\u4f46\u662f\u5c0f\u7eff\u7ea6\u6211\u51fa\u53bb\u770b\u7535\u5f71\uff0c\u6211\u5f88\u72b9\u8c6b\uff0c\u6240\u4ee5\u6211\u548c\u6211\u59d0\u59d0\u5c0f\u7ea2\u51fa\u53bb\u770b\u620f", "created_at": "2026-01-07T19:14:34.489524", "associative_memory": 0}, "caption": "Dialogue"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77055", "label": "Chunk", "properties": {"content": "\u7528\u6237: \u60a8\u597d\u6211\u53eb\u5c0f\u84dd\uff0c\u5c0f\u660e\u4eca\u5929\u7ea6\u6211\u51fa\u53bb\u91ce\u9910\uff0c\u4f46\u662f\u5c0f\u7eff\u7ea6\u6211\u51fa\u53bb\u770b\u7535\u5f71\uff0c\u6211\u5f88\u72b9\u8c6b\uff0c\u6240\u4ee5\u6211\u548c\u6211\u59d0\u59d0\u5c0f\u7ea2\u51fa\u53bb\u770b\u620f", "created_at": "2026-01-07T19:14:34.489524", "associative_memory": 5}, "caption": "Chunk"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77056", "label": "Statement", "properties": {"temporal_info": "ATEMPORAL", "stmt_type": "FACT", "statement": "\u5c0f\u84dd\u53eb\u5c0f\u84dd\u3002", "created_at": "2026-01-07T19:14:34.489524", "emotion_keywords": [], "emotion_type": "\u4e2d\u6027", "emotion_subject": "\u81ea\u5df1", "associative_memory": 3}, "caption": "Statement"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77057", "label": "Statement", "properties": {"temporal_info": "STATIC", "stmt_type": "FACT", "statement": "\u5c0f\u660e\u4eca\u5929\u7ea6\u5c0f\u84dd\u51fa\u53bb\u91ce\u9910\u3002", "valid_at": "2026-01-07T00:00:00+00:00", "created_at": "2026-01-07T19:14:34.489524", "emotion_keywords": [], "emotion_type": "\u4e2d\u6027", "emotion_subject": "\u522b\u4eba", "associative_memory": 5}, "caption": "Statement"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77058", "label": "Statement", "properties": {"temporal_info": "STATIC", "stmt_type": "FACT", "statement": "\u5c0f\u7eff\u7ea6\u5c0f\u84dd\u51fa\u53bb\u770b\u7535\u5f71\u3002", "created_at": "2026-01-07T19:14:34.489524", "emotion_keywords": [], "emotion_type": "\u4e2d\u6027", "emotion_subject": "\u522b\u4eba", "associative_memory": 4}, "caption": "Statement"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77059", "label": "Statement", "properties": {"temporal_info": "DYNAMIC", "stmt_type": "OPINION", "statement": "\u5c0f\u84dd\u5bf9\u662f\u5426\u53bb\u91ce\u9910\u6216\u770b\u7535\u5f71\u611f\u5230\u72b9\u8c6b\u3002", "valid_at": "2026-01-07T00:00:00+00:00", "created_at": "2026-01-07T19:14:34.489524", "emotion_keywords": [], "emotion_type": "\u4e2d\u6027", "emotion_subject": "\u522b\u4eba", "associative_memory": 5}, "caption": "Statement"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77060", "label": "Statement", "properties": {"temporal_info": "STATIC", "stmt_type": "FACT", "statement": "\u5c0f\u84dd\u548c\u5979\u59d0\u59d0\u5c0f\u7ea2\u51fa\u53bb\u770b\u620f\u3002", "created_at": "2026-01-07T19:14:34.489524", "emotion_keywords": [], "emotion_type": "\u4e2d\u6027", "emotion_subject": "\u522b\u4eba", "associative_memory": 5}, "caption": "Statement"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77061", "label": "ExtractedEntity", "properties": {"description": "\u5bf9\u8bdd\u4e2d\u7684\u7528\u6237\uff0c\u6536\u5230\u91ce\u9910\u548c\u770b\u7535\u5f71\u9080\u7ea6\u7684\u4eba", "name": "\u5c0f\u84dd", "entity_type": "\u4eba\u7269\u5b9e\u4f53\u8282\u70b9", "created_at": "2026-01-07T19:14:34.489524", "aliases": ["\u5c0f\u7eff"], "connect_strength": "strong", "associative_memory": 9}, "caption": "ExtractedEntity"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77062", "label": "ExtractedEntity", "properties": {"description": "\u5728\u5f71\u9662\u6216\u5bb6\u4e2d\u89c2\u770b\u7535\u5f71\u7684\u5a31\u4e50\u6d3b\u52a8", "name": "\u91ce\u9910", "entity_type": "\u4e8b\u4ef6\u5b9e\u4f53\u8282\u70b9", "created_at": "2026-01-07T19:14:34.489524", "aliases": ["\u53bb\u91ce\u9910", "\u770b\u620f", "\u770b\u7535\u5f71"], "connect_strength": "strong", "associative_memory": 4}, "caption": "ExtractedEntity"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77063", "label": "ExtractedEntity", "properties": {"description": "\u7528\u4e8e\u89c2\u770b\u7535\u5f71\u7684\u573a\u6240", "name": "\u7535\u5f71\u9662", "entity_type": "\u5730\u70b9\u5b9e\u4f53\u8282\u70b9", "created_at": "2026-01-07T19:14:34.489524", "aliases": ["\u5f71\u9662"], "connect_strength": "Strong", "associative_memory": 1}, "caption": "ExtractedEntity"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77064", "label": "MemorySummary", "properties": {"content": "\u5c0f\u84dd\u4eca\u5929\u539f\u8ba1\u5212\u4e0e\u5c0f\u660e\u91ce\u9910\u3001\u4e0e\u5c0f\u7eff\u770b\u7535\u5f71\uff0c\u4f46\u6700\u7ec8\u9009\u62e9\u4e0e\u59d0\u59d0\u5c0f\u7ea2\u4e00\u8d77\u770b\u620f\u3002", "created_at": "2026-01-07T19:14:58.086704", "associative_memory": 5}, "caption": "MemorySummary"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77133", "label": "Dialogue", "properties": {"content": "\u7528\u6237: \u5f88\u591a\u91cd\u8981\u7684\u4eba\u751f\u9009\u62e9\uff0c\u5176\u5b9e\u5c31\u50cf\u4e00\u6b21\u590d\u6742\u7684\u9884\u6d4b\u4efb\u52a1\u3002\u6700\u5f00\u59cb\u51fa\u73b0\u7684\u90a3\u70b9\u4e0d\u5b89\uff0c\u7c7b\u4f3c\u4e8e\u6a21\u578b\u53d1\u73b0\u4e86\u5f02\u5e38\u4fe1\u53f7\uff1b\u56de\u987e\u8fc7\u53bb\u7684\u7ecf\u5386\uff0c\u662f\u5728\u56de\u6eaf\u5386\u53f2\u6570\u636e\u3001\u505a\u7279\u5f81\u63d0\u53d6\uff1b\u5bf9\u6bd4\u90a3\u4e9b\u8ba9\u4eba\u8e0f\u5b9e\u6216\u540e\u6094\u7684\u51b3\u5b9a\uff0c\u76f8\u5f53\u4e8e\u5728\u4e0d\u540c\u6837\u672c\u4e0a\u8bc4\u4f30\u635f\u5931\u51fd\u6570\uff1b\u800c\u628a\u65f6\u95f4\u62c9\u957f\u53bb\u60f3\u4e00\u5e74\u3001\u4e09\u5e74\u3001\u4e94\u5e74\u540e\u7684\u7ed3\u679c\uff0c\u5219\u662f\u5728\u4e0d\u540c\u65f6\u95f4\u7a97\u53e3\u4e0b\u505a\u957f\u671f\u4e0e\u77ed\u671f\u6536\u76ca\u7684\u6743\u8861\u3002\u7b49\u8fd9\u4e9b\u6b65\u9aa4\u90fd\u8d70\u8fc7\u4e4b\u540e\uff0c\u6240\u8c13\u201c\u7b54\u6848\u201d\uff0c\u5e76\u4e0d\u662f\u88ab\u76f4\u63a5\u7b97\u51fa\u6765\u7684\uff0c\u800c\u662f\u6a21\u578b\u5728\u591a\u6b21\u62c6\u89e3\u4e0e\u8fed\u4ee3\u4e2d\u9010\u6e10\u6536\u655b\u5230\u7684\u7ed3\u679c\u3002", "created_at": "2026-01-06T19:24:55.805367", "associative_memory": 0}, "caption": "Dialogue"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77134", "label": "Chunk", "properties": {"content": "\u7528\u6237: \u5f88\u591a\u91cd\u8981\u7684\u4eba\u751f\u9009\u62e9\uff0c\u5176\u5b9e\u5c31\u50cf\u4e00\u6b21\u590d\u6742\u7684\u9884\u6d4b\u4efb\u52a1\u3002\u6700\u5f00\u59cb\u51fa\u73b0\u7684\u90a3\u70b9\u4e0d\u5b89\uff0c\u7c7b\u4f3c\u4e8e\u6a21\u578b\u53d1\u73b0\u4e86\u5f02\u5e38\u4fe1\u53f7\uff1b\u56de\u987e\u8fc7\u53bb\u7684\u7ecf\u5386\uff0c\u662f\u5728\u56de\u6eaf\u5386\u53f2\u6570\u636e\u3001\u505a\u7279\u5f81\u63d0\u53d6\uff1b\u5bf9\u6bd4\u90a3\u4e9b\u8ba9\u4eba\u8e0f\u5b9e\u6216\u540e\u6094\u7684\u51b3\u5b9a\uff0c\u76f8\u5f53\u4e8e\u5728\u4e0d\u540c\u6837\u672c\u4e0a\u8bc4\u4f30\u635f\u5931\u51fd\u6570\uff1b\u800c\u628a\u65f6\u95f4\u62c9\u957f\u53bb\u60f3\u4e00\u5e74\u3001\u4e09\u5e74\u3001\u4e94\u5e74\u540e\u7684\u7ed3\u679c\uff0c\u5219\u662f\u5728\u4e0d\u540c\u65f6\u95f4\u7a97\u53e3\u4e0b\u505a\u957f\u671f\u4e0e\u77ed\u671f\u6536\u76ca\u7684\u6743\u8861\u3002\u7b49\u8fd9\u4e9b\u6b65\u9aa4\u90fd\u8d70\u8fc7\u4e4b\u540e\uff0c\u6240\u8c13\u201c\u7b54\u6848\u201d\uff0c\u5e76\u4e0d\u662f\u88ab\u76f4\u63a5\u7b97\u51fa\u6765\u7684\uff0c\u800c\u662f\u6a21\u578b\u5728\u591a\u6b21\u62c6\u89e3\u4e0e\u8fed\u4ee3\u4e2d\u9010\u6e10\u6536\u655b\u5230\u7684\u7ed3\u679c\u3002", "created_at": "2026-01-06T19:24:55.805367", "associative_memory": 6}, "caption": "Chunk"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77135", "label": "Statement", "properties": {"temporal_info": "ATEMPORAL", "stmt_type": "OPINION", "statement": "\u5f88\u591a\u91cd\u8981\u7684\u4eba\u751f\u9009\u62e9\u5c31\u50cf\u4e00\u6b21\u590d\u6742\u7684\u9884\u6d4b\u4efb\u52a1\u3002", "created_at": "2026-01-06T19:24:55.805367", "emotion_keywords": [], "associative_memory": 4}, "caption": "Statement"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77136", "label": "Statement", "properties": {"temporal_info": "ATEMPORAL", "stmt_type": "OPINION", "statement": "\u6700\u5f00\u59cb\u51fa\u73b0\u7684\u90a3\u70b9\u4e0d\u5b89\u7c7b\u4f3c\u4e8e\u6a21\u578b\u53d1\u73b0\u4e86\u5f02\u5e38\u4fe1\u53f7\u3002", "created_at": "2026-01-06T19:24:55.805367", "emotion_keywords": [], "associative_memory": 4}, "caption": "Statement"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77137", "label": "Statement", "properties": {"temporal_info": "ATEMPORAL", "stmt_type": "OPINION", "statement": "\u56de\u987e\u8fc7\u53bb\u7684\u7ecf\u5386\u662f\u5728\u56de\u6eaf\u5386\u53f2\u6570\u636e\u5e76\u505a\u7279\u5f81\u63d0\u53d6\u3002", "created_at": "2026-01-06T19:24:55.805367", "emotion_keywords": [], "associative_memory": 4}, "caption": "Statement"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77138", "label": "Statement", "properties": {"temporal_info": "ATEMPORAL", "stmt_type": "OPINION", "statement": "\u5bf9\u6bd4\u90a3\u4e9b\u8ba9\u4eba\u8e0f\u5b9e\u6216\u540e\u6094\u7684\u51b3\u5b9a\u76f8\u5f53\u4e8e\u5728\u4e0d\u540c\u6837\u672c\u4e0a\u8bc4\u4f30\u635f\u5931\u51fd\u6570\u3002", "created_at": "2026-01-06T19:24:55.805367", "emotion_keywords": [], "associative_memory": 3}, "caption": "Statement"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77139", "label": "Statement", "properties": {"temporal_info": "ATEMPORAL", "stmt_type": "OPINION", "statement": "\u628a\u65f6\u95f4\u62c9\u957f\u53bb\u60f3\u4e00\u5e74\u3001\u4e09\u5e74\u3001\u4e94\u5e74\u540e\u7684\u7ed3\u679c\u662f\u5728\u4e0d\u540c\u65f6\u95f4\u7a97\u53e3\u4e0b\u505a\u957f\u671f\u4e0e\u77ed\u671f\u6536\u76ca\u7684\u6743\u8861\u3002", "created_at": "2026-01-06T19:24:55.805367", "emotion_keywords": [], "associative_memory": 3}, "caption": "Statement"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77140", "label": "Statement", "properties": {"temporal_info": "ATEMPORAL", "stmt_type": "OPINION", "statement": "\u6240\u8c13\u201c\u7b54\u6848\u201d\u5e76\u4e0d\u662f\u88ab\u76f4\u63a5\u7b97\u51fa\u6765\u7684\uff0c\u800c\u662f\u6a21\u578b\u5728\u591a\u6b21\u62c6\u89e3\u4e0e\u8fed\u4ee3\u4e2d\u9010\u6e10\u6536\u655b\u5230\u7684\u7ed3\u679c\u3002", "created_at": "2026-01-06T19:24:55.805367", "emotion_keywords": [], "associative_memory": 3}, "caption": "Statement"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77141", "label": "Dialogue", "properties": {"content": "\u7528\u6237: \u7528\u6237\u5c0f\u660e\u559c\u6b22\u559d\u5496\u5561\uff0c\u6bcf\u5929\u90fd\u8981\u559d\u62ff\u94c1\u3002", "created_at": "2026-01-06T14:41:05.181477", "associative_memory": 0}, "caption": "Dialogue"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77142", "label": "Chunk", "properties": {"content": "\u7528\u6237: \u7528\u6237\u5c0f\u660e\u559c\u6b22\u559d\u5496\u5561\uff0c\u6bcf\u5929\u90fd\u8981\u559d\u62ff\u94c1\u3002", "created_at": "2026-01-06T14:41:05.181477", "associative_memory": 2}, "caption": "Chunk"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77143", "label": "Statement", "properties": {"temporal_info": "DYNAMIC", "stmt_type": "FACT", "statement": "\u5c0f\u660e\u559c\u6b22\u559d\u5496\u5561\u3002", "created_at": "2026-01-06T14:41:05.181477", "emotion_keywords": [], "associative_memory": 3}, "caption": "Statement"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77144", "label": "Statement", "properties": {"temporal_info": "DYNAMIC", "stmt_type": "FACT", "statement": "\u5c0f\u660e\u6bcf\u5929\u90fd\u8981\u559d\u62ff\u94c1\u3002", "valid_at": "2026-01-06T00:00:00+00:00", "created_at": "2026-01-06T14:41:05.181477", "emotion_keywords": [], "associative_memory": 3}, "caption": "Statement"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77145", "label": "ExtractedEntity", "properties": {"description": "\u53c2\u4e0e\u5bf9\u8bdd\u7684\u4e2a\u4eba\uff0c\u53d1\u51fa\u91ce\u9910\u9080\u7ea6\u7684\u4e00\u65b9", "name": "\u5c0f\u660e", "entity_type": "\u4eba\u7269\u5b9e\u4f53\u8282\u70b9", "created_at": "2026-01-06T14:41:05.181477", "aliases": ["\u5c0f\u7ea2"], "connect_strength": "strong", "associative_memory": 13}, "caption": "ExtractedEntity"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77146", "label": "ExtractedEntity", "properties": {"description": "\u4e00\u79cd\u5e38\u89c1\u7684\u542b\u5496\u5561\u56e0\u996e\u54c1", "name": "\u5496\u5561", "entity_type": "", "created_at": "2026-01-06T14:41:05.181477", "aliases": ["\u5496\u5561\u996e\u6599"], "connect_strength": "Strong", "associative_memory": 3}, "caption": "ExtractedEntity"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77147", "label": "ExtractedEntity", "properties": {"description": "\u4e00\u79cd\u5496\u5561\u996e\u54c1\uff0c\u7531\u6d53\u7f29\u5496\u5561\u548c\u725b\u5976\u5236\u6210", "name": "\u62ff\u94c1", "entity_type": "", "created_at": "2026-01-06T14:41:05.181477", "aliases": ["\u62ff\u94c1\u5496\u5561", "Latte"], "connect_strength": "Strong", "associative_memory": 3}, "caption": "ExtractedEntity"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77148", "label": "Dialogue", "properties": {"content": "\u7528\u6237: \u7528\u6237\u5c0f\u660e\u559c\u6b22\u559d\u5496\u5561\uff0c\u6bcf\u5929\u90fd\u8981\u559d\u62ff\u94c1\u3002", "created_at": "2026-01-06T14:44:22.921668", "associative_memory": 0}, "caption": "Dialogue"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77149", "label": "Chunk", "properties": {"content": "\u7528\u6237: \u7528\u6237\u5c0f\u660e\u559c\u6b22\u559d\u5496\u5561\uff0c\u6bcf\u5929\u90fd\u8981\u559d\u62ff\u94c1\u3002", "created_at": "2026-01-06T14:44:22.921668", "associative_memory": 2}, "caption": "Chunk"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77150", "label": "Statement", "properties": {"temporal_info": "DYNAMIC", "stmt_type": "FACT", "statement": "\u5c0f\u660e\u559c\u6b22\u559d\u5496\u5561\u3002", "valid_at": "2026-01-06T00:00:00+00:00", "created_at": "2026-01-06T14:44:22.921668", "emotion_keywords": [], "associative_memory": 3}, "caption": "Statement"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77151", "label": "Statement", "properties": {"temporal_info": "DYNAMIC", "stmt_type": "FACT", "statement": "\u5c0f\u660e\u6bcf\u5929\u90fd\u8981\u559d\u62ff\u94c1\u3002", "valid_at": "2026-01-06T00:00:00+00:00", "created_at": "2026-01-06T14:44:22.921668", "emotion_keywords": [], "associative_memory": 3}, "caption": "Statement"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77152", "label": "Dialogue", "properties": {"content": "\u7528\u6237: \u7528\u6237\u5c0f\u660e\u559c\u6b22\u559d\u5496\u5561\uff0c\u6bcf\u5929\u90fd\u8981\u559d\u62ff\u94c1\u3002", "created_at": "2026-01-06T14:46:02.387455", "associative_memory": 0}, "caption": "Dialogue"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77153", "label": "Chunk", "properties": {"content": "\u7528\u6237: \u7528\u6237\u5c0f\u660e\u559c\u6b22\u559d\u5496\u5561\uff0c\u6bcf\u5929\u90fd\u8981\u559d\u62ff\u94c1\u3002", "created_at": "2026-01-06T14:46:02.387455", "associative_memory": 2}, "caption": "Chunk"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77154", "label": "Statement", "properties": {"temporal_info": "DYNAMIC", "stmt_type": "FACT", "statement": "\u5c0f\u660e\u559c\u6b22\u559d\u5496\u5561\u3002", "valid_at": "2026-01-06T00:00:00+00:00", "created_at": "2026-01-06T14:46:02.387455", "emotion_keywords": [], "associative_memory": 4}, "caption": "Statement"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77155", "label": "Statement", "properties": {"temporal_info": "DYNAMIC", "stmt_type": "FACT", "statement": "\u5c0f\u660e\u6bcf\u5929\u90fd\u8981\u559d\u62ff\u94c1\u3002", "created_at": "2026-01-06T14:46:02.387455", "emotion_keywords": [], "associative_memory": 4}, "caption": "Statement"}, {"id": "4:f6039a9b-d553-4ba2-9b1c-d9a18917801f:77156", "label": "MemorySummary", "properties": {"content": "\u7528\u6237\u5c0f\u660e\u559c\u6b22\u559d\u5496\u5561\uff0c\u6bcf\u5929\u90fd\u8981\u559d\u62ff\u94c1\u3002", "created_at": "2026-01-06T14:46:16.548556", "associative_memory": 2},
  "caption": "MemorySummary"}]

    result=asyncio.run(Translation_English("2699984d-23be-4817-b81c-c38682a08306",a))
    print(result)