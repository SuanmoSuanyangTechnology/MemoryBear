# -*- coding: utf-8 -*-
"""情绪分析服务模块

本模块提供情绪数据的分析和统计功能，包括情绪标签、词云、健康指数计算等。

Classes:
    EmotionAnalyticsService: 情绪分析服务，提供各种情绪分析功能
"""

import json
import statistics
from typing import Any, Dict, List, Optional

from app.core.logging_config import get_business_logger
from app.repositories.neo4j.emotion_repository import EmotionRepository
from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

logger = get_business_logger()


class EmotionSuggestion(BaseModel):
    """情绪建议模型"""
    type: str = Field(..., description="建议类型：emotion_balance/activity_recommendation/social_connection/stress_management")
    title: str = Field(..., description="建议标题")
    content: str = Field(..., description="建议内容")
    priority: str = Field(..., description="优先级：high/medium/low")
    actionable_steps: List[str] = Field(..., description="可执行步骤列表（3个）")


class EmotionSuggestionsResponse(BaseModel):
    """情绪建议响应模型"""
    health_summary: str = Field(..., description="健康状态摘要（不超过50字）")
    suggestions: List[EmotionSuggestion] = Field(..., description="建议列表（3-5条）")


class EmotionAnalyticsService:
    """情绪分析服务
    
    提供情绪数据的分析和统计功能，包括：
    - 情绪标签统计
    - 情绪词云数据
    - 情绪健康指数计算
    - 个性化情绪建议生成
    
    Attributes:
        emotion_repo: 情绪数据仓储实例
    """
    
    def __init__(self):
        """初始化情绪分析服务"""
        connector = Neo4jConnector()
        self.emotion_repo = EmotionRepository(connector)
        logger.info("情绪分析服务初始化完成")
    
    async def get_emotion_tags(
        self,
        end_user_id: str,
        emotion_type: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 10
    ) -> Dict[str, Any]:
        """获取情绪标签统计
        
        查询指定用户的情绪类型分布，包括计数、百分比和平均强度。
        确保返回所有6个情绪维度（joy、sadness、anger、fear、surprise、neutral），
        即使某些维度没有数据也会返回count=0的记录。

        """
        try:
            logger.info(f"获取情绪标签统计: user={end_user_id}, type={emotion_type}, "
                       f"start={start_date}, end={end_date}, limit={limit}")
            
            # 调用仓储层查询
            tags = await self.emotion_repo.get_emotion_tags(
                group_id=end_user_id,
                emotion_type=emotion_type,
                start_date=start_date,
                end_date=end_date,
                limit=limit
            )
            
            # 定义所有6个情绪维度
            all_emotion_types = ['joy', 'sadness', 'anger', 'fear', 'surprise', 'neutral']
            
            # 将查询结果转换为字典，方便查找
            tags_dict = {tag["emotion_type"]: tag for tag in tags}
            
            # 补全缺失的情绪维度
            complete_tags = []
            for emotion in all_emotion_types:
                if emotion in tags_dict:
                    complete_tags.append(tags_dict[emotion])
                else:
                    # 如果该情绪类型不存在，添加默认值
                    complete_tags.append({
                        "emotion_type": emotion,
                        "count": 0,
                        "percentage": 0.0,
                        "avg_intensity": 0.0
                    })
            
            # 计算总数
            total_count = sum(tag["count"] for tag in complete_tags)
            
            # 如果有数据，重新计算百分比（因为补全了0值项）
            if total_count > 0:
                for tag in complete_tags:
                    if tag["count"] > 0:
                        tag["percentage"] = round((tag["count"] / total_count) * 100, 2)
            
            # 构建时间范围信息
            time_range = {}
            if start_date:
                time_range["start_date"] = start_date
            if end_date:
                time_range["end_date"] = end_date
            
            # 格式化响应
            response = {
                "tags": complete_tags,
                "total_count": total_count,
                "time_range": time_range if time_range else None
            }
            
            logger.info(f"情绪标签统计完成: total_count={total_count}, tags_count={len(complete_tags)}")
            return response
            
        except Exception as e:
            logger.error(f"获取情绪标签统计失败: {str(e)}", exc_info=True)
            raise
    
    async def get_emotion_wordcloud(
        self,
        end_user_id: str,
        emotion_type: Optional[str] = None,
        limit: int = 50
    ) -> Dict[str, Any]:
        """获取情绪词云数据
        
        查询情绪关键词及其频率，用于生成词云可视化。
        
        Args:
            end_user_id: 宿主ID（用户组ID）
            emotion_type: 可选的情绪类型过滤
            limit: 返回关键词的最大数量
            
        Returns:
            Dict: 包含情绪词云数据的响应：
                - keywords: 关键词列表
                - total_keywords: 总关键词数量
        """
        try:
            logger.info(f"获取情绪词云数据: user={end_user_id}, type={emotion_type}, limit={limit}")
            
            # 调用仓储层查询
            keywords = await self.emotion_repo.get_emotion_wordcloud(
                group_id=end_user_id,
                emotion_type=emotion_type,
                limit=limit
            )
            
            # 计算总关键词数量
            total_keywords = len(keywords)
            
            # 格式化响应
            response = {
                "keywords": keywords,
                "total_keywords": total_keywords
            }
            
            logger.info(f"情绪词云数据获取完成: total_keywords={total_keywords}")
            return response
            
        except Exception as e:
            logger.error(f"获取情绪词云数据失败: {str(e)}", exc_info=True)
            raise
    
    def _calculate_positivity_rate(self, emotions: List[Dict[str, Any]]) -> Dict[str, Any]:
        """计算积极率
        
        根据情绪类型分类正面、负面和中性情绪，计算积极率。
        公式：(正面数 / (正面数 + 负面数)) * 100
        
        Args:
            emotions: 情绪数据列表，每个包含 emotion_type 字段
            
        Returns:
            Dict: 包含积极率计算结果：
                - score: 积极率分数（0-100）
                - positive_count: 正面情绪数量
                - negative_count: 负面情绪数量
                - neutral_count: 中性情绪数量
        """
        # 定义情绪分类
        positive_emotions = {'joy', 'surprise'}
        negative_emotions = {'sadness', 'anger', 'fear'}
        
        # 统计各类情绪数量
        positive_count = sum(1 for e in emotions if e.get('emotion_type') in positive_emotions)
        negative_count = sum(1 for e in emotions if e.get('emotion_type') in negative_emotions)
        neutral_count = sum(1 for e in emotions if e.get('emotion_type') == 'neutral')
        
        # 计算积极率
        total_non_neutral = positive_count + negative_count
        if total_non_neutral > 0:
            score = (positive_count / total_non_neutral) * 100
        else:
            score = 50.0  # 如果没有非中性情绪，默认为50
        
        logger.debug(f"积极率计算: positive={positive_count}, negative={negative_count}, "
                    f"neutral={neutral_count}, score={score:.2f}")
        
        return {
            "score": round(score, 2),
            "positive_count": positive_count,
            "negative_count": negative_count,
            "neutral_count": neutral_count
        }
    
    def _calculate_stability(self, emotions: List[Dict[str, Any]]) -> Dict[str, Any]:
        """计算稳定性
        
        基于情绪强度的标准差计算情绪稳定性。
        公式：(1 - min(std_deviation, 1.0)) * 100
        
        Args:
            emotions: 情绪数据列表，每个包含 emotion_intensity 字段
            
        Returns:
            Dict: 包含稳定性计算结果：
                - score: 稳定性分数（0-100）
                - std_deviation: 标准差
        """
        # 提取所有情绪强度
        intensities = [e.get('emotion_intensity', 0.0) for e in emotions if e.get('emotion_intensity') is not None]
        
        # 计算标准差
        if len(intensities) >= 2:
            std_deviation = statistics.stdev(intensities)
        elif len(intensities) == 1:
            std_deviation = 0.0  # 只有一个数据点，标准差为0
        else:
            std_deviation = 0.0  # 没有数据，标准差为0
        
        # 计算稳定性分数
        # 标准差越小，稳定性越高
        score = (1 - min(std_deviation, 1.0)) * 100
        
        logger.debug(f"稳定性计算: intensities_count={len(intensities)}, "
                    f"std_deviation={std_deviation:.3f}, score={score:.2f}")
        
        return {
            "score": round(score, 2),
            "std_deviation": round(std_deviation, 3)
        }
    
    def _calculate_resilience(self, emotions: List[Dict[str, Any]]) -> Dict[str, Any]:
        """计算恢复力
        
        分析情绪转换模式，统计从负面情绪恢复到正面情绪的能力。
        公式：(负面到正面转换次数 / 总负面情绪数) * 100
        
        Args:
            emotions: 情绪数据列表，每个包含 emotion_type 和 created_at 字段
                     应该按时间顺序排列
            
        Returns:
            Dict: 包含恢复力计算结果：
                - score: 恢复力分数（0-100）
                - recovery_rate: 恢复率（转换次数/负面情绪数）
        """
        # 定义情绪分类
        positive_emotions = {'joy', 'surprise'}
        negative_emotions = {'sadness', 'anger', 'fear'}
        
        # 统计负面到正面的转换次数
        recovery_count = 0
        negative_count = 0
        
        for i in range(len(emotions)):
            current_emotion = emotions[i].get('emotion_type')
            
            # 统计负面情绪总数
            if current_emotion in negative_emotions:
                negative_count += 1
                
                # 检查下一个情绪是否为正面
                if i + 1 < len(emotions):
                    next_emotion = emotions[i + 1].get('emotion_type')
                    if next_emotion in positive_emotions:
                        recovery_count += 1
        
        # 计算恢复力分数
        if negative_count > 0:
            recovery_rate = recovery_count / negative_count
            score = recovery_rate * 100
        else:
            # 如果没有负面情绪，恢复力设为100（最佳状态）
            recovery_rate = 1.0
            score = 100.0
        
        logger.debug(f"恢复力计算: negative_count={negative_count}, "
                    f"recovery_count={recovery_count}, score={score:.2f}")
        
        return {
            "score": round(score, 2),
            "recovery_rate": round(recovery_rate, 3)
        }
    
    async def calculate_emotion_health_index(
        self,
        end_user_id: str,
        time_range: str = "30d"
    ) -> Dict[str, Any]:
        """计算情绪健康指数
        
        综合积极率、稳定性和恢复力计算情绪健康指数。
        
        Args:
            end_user_id: 宿主ID（用户组ID）
            time_range: 时间范围（7d/30d/90d）
            
        Returns:
            Dict: 包含情绪健康指数的完整响应：
                - health_score: 综合健康分数（0-100）
                - level: 健康等级（优秀/良好/一般/较差）
                - dimensions: 各维度详细数据
                    - positivity_rate: 积极率
                    - stability: 稳定性
                    - resilience: 恢复力
                - emotion_distribution: 情绪分布统计
                - time_range: 时间范围
        """
        try:
            logger.info(f"计算情绪健康指数: user={end_user_id}, time_range={time_range}")
            
            # 获取时间范围内的情绪数据
            emotions = await self.emotion_repo.get_emotions_in_range(
                group_id=end_user_id,
                time_range=time_range
            )
            
            # 如果没有数据，返回默认值
            if not emotions:
                logger.warning(f"用户 {end_user_id} 在时间范围 {time_range} 内没有情绪数据")
                return {
                    "health_score": 0.0,
                    "level": "无数据",
                    "dimensions": {
                        "positivity_rate": {"score": 0.0, "positive_count": 0, "negative_count": 0, "neutral_count": 0},
                        "stability": {"score": 0.0, "std_deviation": 0.0},
                        "resilience": {"score": 0.0, "recovery_rate": 0.0}
                    },
                    "emotion_distribution": {},
                    "time_range": time_range
                }
            
            # 计算各维度指标
            positivity_rate = self._calculate_positivity_rate(emotions)
            stability = self._calculate_stability(emotions)
            resilience = self._calculate_resilience(emotions)
            
            # 计算综合健康分数
            # 公式：positivity_rate * 0.4 + stability * 0.3 + resilience * 0.3
            health_score = (
                positivity_rate["score"] * 0.4 +
                stability["score"] * 0.3 +
                resilience["score"] * 0.3
            )
            
            # 确定健康等级
            if health_score >= 80:
                level = "优秀"
            elif health_score >= 60:
                level = "良好"
            elif health_score >= 40:
                level = "一般"
            else:
                level = "较差"
            
            # 统计情绪分布
            emotion_distribution = {}
            for emotion_type in ['joy', 'sadness', 'anger', 'fear', 'surprise', 'neutral']:
                count = sum(1 for e in emotions if e.get('emotion_type') == emotion_type)
                emotion_distribution[emotion_type] = count
            
            # 格式化响应
            response = {
                "health_score": round(health_score, 2),
                "level": level,
                "dimensions": {
                    "positivity_rate": positivity_rate,
                    "stability": stability,
                    "resilience": resilience
                },
                "emotion_distribution": emotion_distribution,
                "time_range": time_range
            }
            
            logger.info(f"情绪健康指数计算完成: score={health_score:.2f}, level={level}")
            return response
            
        except Exception as e:
            logger.error(f"计算情绪健康指数失败: {str(e)}", exc_info=True)
            raise
    
    def _analyze_emotion_patterns(self, emotions: List[Dict[str, Any]]) -> Dict[str, Any]:
        """分析情绪模式
        
        识别主要负面情绪、情绪触发因素和波动时段。
        
        Args:
            emotions: 情绪数据列表，每个包含 emotion_type、emotion_intensity、created_at 字段
            
        Returns:
            Dict: 包含情绪模式分析结果：
                - dominant_negative_emotion: 主要负面情绪类型
                - high_intensity_emotions: 高强度情绪列表
                - emotion_volatility: 情绪波动性（高/中/低）
        """
        negative_emotions = {'sadness', 'anger', 'fear'}
        
        # 统计负面情绪分布
        negative_emotion_counts = {}
        for emotion in emotions:
            emotion_type = emotion.get('emotion_type')
            if emotion_type in negative_emotions:
                negative_emotion_counts[emotion_type] = negative_emotion_counts.get(emotion_type, 0) + 1
        
        # 识别主要负面情绪
        dominant_negative_emotion = None
        if negative_emotion_counts:
            dominant_negative_emotion = max(negative_emotion_counts, key=negative_emotion_counts.get)
        
        # 识别高强度情绪（强度 >= 0.7）
        high_intensity_emotions = [
            {
                "type": e.get('emotion_type'),
                "intensity": e.get('emotion_intensity'),
                "created_at": e.get('created_at')
            }
            for e in emotions
            if e.get('emotion_intensity', 0) >= 0.7
        ]
        
        # 评估情绪波动性
        intensities = [e.get('emotion_intensity', 0.0) for e in emotions if e.get('emotion_intensity') is not None]
        if len(intensities) >= 2:
            std_dev = statistics.stdev(intensities)
            if std_dev > 0.3:
                volatility = "高"
            elif std_dev > 0.15:
                volatility = "中"
            else:
                volatility = "低"
        else:
            volatility = "未知"
        
        logger.debug(f"情绪模式分析: dominant_negative={dominant_negative_emotion}, "
                    f"high_intensity_count={len(high_intensity_emotions)}, volatility={volatility}")
        
        return {
            "dominant_negative_emotion": dominant_negative_emotion,
            "high_intensity_emotions": high_intensity_emotions[:5],  # 最多返回5个
            "emotion_volatility": volatility
        }
    
    async def generate_emotion_suggestions(
        self,
        end_user_id: str,
        db: Session,
    ) -> Dict[str, Any]:
        """生成个性化情绪建议
        
        基于情绪健康数据和用户画像生成个性化建议。
        
        Args:
            end_user_id: 宿主ID（用户组ID）
            db: 数据库会话
            
        Returns:
            Dict: 包含个性化建议的响应：
                - health_summary: 健康状态摘要
                - suggestions: 建议列表（3-5条）
        """
        try:
            logger.info(f"生成个性化情绪建议: user={end_user_id}")
            
            # 1. 从 end_user_id 获取关联的 memory_config_id
            llm_client = None
            try:
                from app.services.memory_agent_service import (
                    get_end_user_connected_config,
                )
                
                connected_config = get_end_user_connected_config(end_user_id, db)
                config_id = connected_config.get("memory_config_id")
                
                if config_id is not None:
                    from app.services.memory_config_service import (
                        MemoryConfigService,
                    )
                    config_service = MemoryConfigService(db)
                    memory_config = config_service.load_memory_config(
                        config_id=int(config_id),
                        service_name="EmotionAnalyticsService.generate_emotion_suggestions"
                    )
                    from app.core.memory.utils.llm.llm_utils import MemoryClientFactory
                    factory = MemoryClientFactory(db)
                    llm_client = factory.get_llm_client(str(memory_config.llm_model_id))
            except Exception as e:
                logger.warning(f"无法获取 end_user {end_user_id} 的配置，将使用默认配置: {e}")
            
            # 2. 获取情绪健康数据
            health_data = await self.calculate_emotion_health_index(end_user_id, time_range="30d")
            
            # 3. 获取情绪数据用于模式分析
            emotions = await self.emotion_repo.get_emotions_in_range(
                group_id=end_user_id,
                time_range="30d"
            )
            
            # 4. 分析情绪模式
            patterns = self._analyze_emotion_patterns(emotions)
            
            # 5. 获取用户画像数据（简化版，直接从Neo4j获取）
            user_profile = await self._get_simple_user_profile(end_user_id)
            
            # 6. 构建LLM prompt
            prompt = await self._build_suggestion_prompt(health_data, patterns, user_profile)
            
            # 7. 调用LLM生成建议（使用配置中的LLM）
            if llm_client is None:
                # 无法获取配置时，抛出错误而不是使用默认配置
                raise ValueError("无法获取LLM配置，请确保end_user关联了有效的memory_config")
            
            # 将 prompt 转换为 messages 格式
            messages = [
                {"role": "user", "content": prompt}
            ]
            
            # 8. 使用结构化输出直接获取 Pydantic 模型
            try:
                suggestions_response = await llm_client.response_structured(
                    messages=messages,
                    response_model=EmotionSuggestionsResponse
                )
            except Exception as e:
                logger.error(f"LLM 结构化输出失败: {str(e)}")
                # 返回默认建议
                suggestions_response = self._get_default_suggestions(health_data)
            
            # 8. 验证建议数量（3-5条）
            if len(suggestions_response.suggestions) < 3:
                logger.warning(f"建议数量不足: {len(suggestions_response.suggestions)}")
                suggestions_response = self._get_default_suggestions(health_data)
            elif len(suggestions_response.suggestions) > 5:
                logger.warning(f"建议数量过多: {len(suggestions_response.suggestions)}")
                suggestions_response.suggestions = suggestions_response.suggestions[:5]
            
            # 9. 格式化响应
            response = {
                "health_summary": suggestions_response.health_summary,
                "suggestions": [
                    {
                        "type": s.type,
                        "title": s.title,
                        "content": s.content,
                        "priority": s.priority,
                        "actionable_steps": s.actionable_steps
                    }
                    for s in suggestions_response.suggestions
                ]
            }
            
            logger.info(f"个性化建议生成完成: suggestions_count={len(response['suggestions'])}")
            return response
            
        except Exception as e:
            logger.error(f"生成个性化建议失败: {str(e)}", exc_info=True)
            raise
    
    async def _get_simple_user_profile(self, end_user_id: str) -> Dict[str, Any]:
        """获取简化的用户画像数据
        
        Args:
            end_user_id: 用户ID
            
        Returns:
            Dict: 用户画像数据
        """
        try:
            connector = Neo4jConnector()
            
            # 查询用户的实体和标签
            query = """
            MATCH (e:Entity)
            WHERE e.group_id = $group_id
            RETURN e.name as name, e.type as type
            ORDER BY e.created_at DESC
            LIMIT 20
            """
            
            entities = await connector.execute_query(query, group_id=end_user_id)
            
            # 提取兴趣标签
            interests = [e["name"] for e in entities if e.get("type") in ["INTEREST", "HOBBY"]][:5]
            # 后期会引入用户的习惯。。
            return {
                "interests": interests if interests else ["未知"]
            }
            
        except Exception as e:
            logger.error(f"获取用户画像失败: {str(e)}")
            return {"interests": ["未知"]}
    
    async def _build_suggestion_prompt(
        self,
        health_data: Dict[str, Any],
        patterns: Dict[str, Any],
        user_profile: Dict[str, Any]
    ) -> str:
        """构建情绪建议生成的prompt
        
        Args:
            health_data: 情绪健康数据
            patterns: 情绪模式分析结果
            user_profile: 用户画像数据
            
        Returns:
            str: LLM prompt
        """
        from app.core.memory.utils.prompt.prompt_utils import (
            render_emotion_suggestions_prompt,
        )
        
        prompt = await render_emotion_suggestions_prompt(
            health_data=health_data,
            patterns=patterns,
            user_profile=user_profile
        )
        
        return prompt
    
    def _get_default_suggestions(self, health_data: Dict[str, Any]) -> EmotionSuggestionsResponse:
        """获取默认建议（当LLM调用失败时使用）
        
        Args:
            health_data: 情绪健康数据
            
        Returns:
            EmotionSuggestionsResponse: 默认建议
        """
        health_score = health_data.get('health_score', 0)
        
        if health_score >= 80:
            summary = "您的情绪健康状况优秀，请继续保持积极的生活态度。"
        elif health_score >= 60:
            summary = "您的情绪健康状况良好，可以通过一些调整进一步提升。"
        elif health_score >= 40:
            summary = "您的情绪健康需要关注，建议采取一些改善措施。"
        else:
            summary = "您的情绪健康需要重点关注，建议寻求专业帮助。"
        
        suggestions = [
            EmotionSuggestion(
                type="emotion_balance",
                title="保持情绪平衡",
                content="通过正念冥想和深呼吸练习，帮助您更好地管理情绪波动，提升情绪稳定性。",
                priority="high",
                actionable_steps=[
                    "每天早晨进行5-10分钟的正念冥想",
                    "感到情绪波动时，进行3次深呼吸",
                    "记录每天的情绪变化，识别触发因素"
                ]
            ),
            EmotionSuggestion(
                type="activity_recommendation",
                title="增加户外活动",
                content="适度的户外运动可以有效改善情绪，增强身心健康。建议每周进行3-4次户外活动。",
                priority="medium",
                actionable_steps=[
                    "每周安排2-3次30分钟的散步",
                    "周末尝试户外运动如骑行或爬山",
                    "在户外活动时关注周围环境，放松心情"
                ]
            ),
            EmotionSuggestion(
                type="social_connection",
                title="加强社交联系",
                content="与朋友和家人保持良好的社交联系，可以提供情感支持，改善情绪健康。",
                priority="medium",
                actionable_steps=[
                    "每周至少与一位朋友或家人深入交流",
                    "参加感兴趣的社交活动或兴趣小组",
                    "主动分享自己的感受和想法"
                ]
            )
        ]
        
        return EmotionSuggestionsResponse(
            health_summary=summary,
            suggestions=suggestions
        )
