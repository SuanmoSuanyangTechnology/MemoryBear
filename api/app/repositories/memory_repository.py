"""
记忆仓储模块 - 短期记忆和长期记忆的数据访问层
"""
from sqlalchemy.orm import Session
from typing import List, Optional, Dict, Any
import uuid
import datetime

from app.models.memory_model import ShortTermMemory, LongTermMemory
from app.core.logging_config import get_db_logger

# 获取数据库专用日志器
db_logger = get_db_logger()


class ShortTermMemoryRepository:
    """短期记忆仓储类"""
    
    def __init__(self, db: Session):
        self.db = db

    def create(self, end_user_id: str, messages: str, aimessages: str = None, search_switch: str = None, retrieved_content: List[Dict] = None) -> ShortTermMemory:
        """创建短期记忆记录
        
        Args:
            end_user_id: 终端用户ID
            messages: 用户消息内容
            aimessages: AI回复消息内容
            search_switch: 搜索开关状态
            retrieved_content: 检索到的相关内容，格式为[{}, {}]
            
        Returns:
            ShortTermMemory: 创建的短期记忆对象
        """
        try:
            memory = ShortTermMemory(
                end_user_id=end_user_id,
                messages=messages,
                aimessages=aimessages,
                search_switch=search_switch,
                retrieved_content=retrieved_content or []
            )
            
            self.db.add(memory)
            self.db.commit()
            self.db.refresh(memory)
            
            db_logger.info(f"成功创建短期记忆记录: {memory.id} for user {end_user_id}")
            return memory
            
        except Exception as e:
            self.db.rollback()
            db_logger.error(f"创建短期记忆记录时出错: {str(e)}")
            raise

    def count_by_user_id(self,end_user_id: str) -> int:
        """根据ID获取短期记忆记录
        
        Args:
            memory_id: 记忆ID
            
        Returns:
            Optional[ShortTermMemory]: 记忆对象，如果不存在则返回None
        """
        try:
            memory = (
                self.db.query(ShortTermMemory)
                .filter(ShortTermMemory.end_user_id == end_user_id)
                .count()
            )
            
            if memory:
                db_logger.debug(f"成功查询到短期记忆记录 {memory}")
            else:
                db_logger.debug(f"未找到短期记忆记录 {memory}")
                
            return memory
            
        except Exception as e:
            self.db.rollback()
            db_logger.error(f"查询短期记忆记录 {memory} 时出错: {str(e)}")
            raise


    def get_latest_by_user_id(self, end_user_id: str, limit: int = 5) -> List[ShortTermMemory]:
        """获取用户最新的短期记忆记录
        
        Args:
            end_user_id: 终端用户ID
            limit: 返回记录数限制，默认5条
            
        Returns:
            List[ShortTermMemory]: 最新的记忆记录列表，按创建时间倒序
        """
        try:
            # 使用复合索引 ix_memory_short_term_user_time 优化查询
            memories = (
                self.db.query(ShortTermMemory)
                .filter(ShortTermMemory.end_user_id == end_user_id)
                .order_by(ShortTermMemory.created_at.desc())
                .limit(limit)
                .all()
            )
            
            db_logger.info(f"成功查询用户 {end_user_id} 的最新 {len(memories)} 条短期记忆记录")
            return memories
            
        except Exception as e:
            self.db.rollback()
            db_logger.error(f"查询用户 {end_user_id} 的最新短期记忆记录时出错: {str(e)}")
            raise

    def get_recent_by_user_id(self, end_user_id: str, hours: int = 24) -> List[ShortTermMemory]:
        """获取用户最近指定小时内的短期记忆记录
        
        Args:
            end_user_id: 终端用户ID
            hours: 时间范围（小时），默认24小时
            
        Returns:
            List[ShortTermMemory]: 记忆记录列表，按创建时间倒序
        """
        try:
            cutoff_time = datetime.datetime.now() - datetime.timedelta(hours=hours)
            
            # 使用复合索引 ix_memory_short_term_user_time 优化查询
            memories = (
                self.db.query(ShortTermMemory)
                .filter(
                    ShortTermMemory.end_user_id == end_user_id,
                    ShortTermMemory.created_at >= cutoff_time
                )
                .order_by(ShortTermMemory.created_at.desc())
                .all()
            )
            
            db_logger.info(f"成功查询用户 {end_user_id} 最近 {hours} 小时的 {len(memories)} 条短期记忆记录")
            return memories
            
        except Exception as e:
            self.db.rollback()
            db_logger.error(f"查询用户 {end_user_id} 最近 {hours} 小时的短期记忆记录时出错: {str(e)}")
            raise

    def delete_by_id(self, memory_id: uuid.UUID) -> bool:
        """删除指定ID的短期记忆记录
        
        Args:
            memory_id: 记忆ID
            
        Returns:
            bool: 删除成功返回True，否则返回False
        """
        try:
            deleted_count = (
                self.db.query(ShortTermMemory)
                .filter(ShortTermMemory.id == memory_id)
                .delete(synchronize_session=False)
            )
            
            self.db.commit()
            
            if deleted_count > 0:
                db_logger.info(f"成功删除短期记忆记录 {memory_id}")
                return True
            else:
                db_logger.warning(f"未找到短期记忆记录 {memory_id}，无法删除")
                return False
                
        except Exception as e:
            self.db.rollback()
            db_logger.error(f"删除短期记忆记录 {memory_id} 时出错: {str(e)}")
            raise

    def delete_old_memories(self, days: int = 7) -> int:
        """删除指定天数之前的短期记忆记录
        
        Args:
            days: 保留天数，默认7天
            
        Returns:
            int: 删除的记录数
        """
        try:
            cutoff_time = datetime.datetime.now() - datetime.timedelta(days=days)
            
            deleted_count = (
                self.db.query(ShortTermMemory)
                .filter(ShortTermMemory.created_at < cutoff_time)
                .delete(synchronize_session=False)
            )
            
            self.db.commit()
            
            db_logger.info(f"成功删除 {days} 天前的 {deleted_count} 条短期记忆记录")
            return deleted_count
            
        except Exception as e:
            self.db.rollback()
            db_logger.error(f"删除 {days} 天前的短期记忆记录时出错: {str(e)}")
            raise

    def upsert(self, end_user_id: str, messages: str, aimessages: str = None, search_switch: str = None, retrieved_content: List[Dict] = None) -> ShortTermMemory:
        """创建或更新短期记忆记录
        
        根据 end_user_id、messages 和 aimessages 查找现有记录：
        - 如果找到匹配的记录，则更新 messages、aimessages、search_switch 和 retrieved_content
        - 如果没有找到匹配的记录，则创建新记录
        
        Args:
            end_user_id: 终端用户ID
            messages: 用户消息内容
            aimessages: AI回复消息内容
            search_switch: 搜索开关状态
            retrieved_content: 检索到的相关内容，格式为[{}, {}]
            
        Returns:
            ShortTermMemory: 创建或更新的短期记忆对象
        """
        try:
            # 构建查询条件，使用复合索引 ix_memory_short_term_user_messages 优化查询
            query_filters = [
                ShortTermMemory.end_user_id == end_user_id,
                ShortTermMemory.messages == messages
            ]
            
            # 如果 aimessages 不为空，则加入查询条件
            if aimessages is not None:
                query_filters.append(ShortTermMemory.aimessages == aimessages)
            else:
                # 如果 aimessages 为 None，则查找 aimessages 为 NULL 的记录
                query_filters.append(ShortTermMemory.aimessages.is_(None))
            
            # 查找现有记录
            existing_memory = (
                self.db.query(ShortTermMemory)
                .filter(*query_filters)
                .first()
            )
            
            if existing_memory:
                # 更新现有记录
                existing_memory.messages = messages
                existing_memory.aimessages = aimessages
                existing_memory.search_switch = search_switch
                existing_memory.retrieved_content = retrieved_content or []
                
                self.db.commit()
                self.db.refresh(existing_memory)
                
                db_logger.info(f"成功更新短期记忆记录: {existing_memory.id} for user {end_user_id}")
                return existing_memory
            else:
                # 创建新记录
                new_memory = ShortTermMemory(
                    end_user_id=end_user_id,
                    messages=messages,
                    aimessages=aimessages,
                    search_switch=search_switch,
                    retrieved_content=retrieved_content or []
                )
                
                self.db.add(new_memory)
                self.db.commit()
                self.db.refresh(new_memory)
                
                db_logger.info(f"成功创建新的短期记忆记录: {new_memory.id} for user {end_user_id}")
                return new_memory
                
        except Exception as e:
            self.db.rollback()
            db_logger.error(f"创建或更新短期记忆记录时出错: {str(e)}")
            raise


class LongTermMemoryRepository:
    """长期记忆仓储类"""
    
    def __init__(self, db: Session):
        self.db = db

    def create(self, end_user_id: str, retrieved_content: List[Dict] = None) -> LongTermMemory:
        """创建长期记忆记录
        
        Args:
            end_user_id: 终端用户ID
            retrieved_content: 检索到的相关内容，格式为[{}, {}]
            
        Returns:
            LongTermMemory: 创建的长期记忆对象
        """
        try:
            memory = LongTermMemory(
                end_user_id=end_user_id,
                retrieved_content=retrieved_content or []
            )
            
            self.db.add(memory)
            self.db.commit()
            self.db.refresh(memory)
            
            db_logger.info(f"成功创建长期记忆记录: {memory.id} for user {end_user_id}")
            return memory
            
        except Exception as e:
            self.db.rollback()
            db_logger.error(f"创建长期记忆记录时出错: {str(e)}")
            raise

    def get_by_id(self, memory_id: uuid.UUID) -> Optional[LongTermMemory]:
        """根据ID获取长期记忆记录
        
        Args:
            memory_id: 记忆ID
            
        Returns:
            Optional[LongTermMemory]: 记忆对象，如果不存在则返回None
        """
        try:
            memory = (
                self.db.query(LongTermMemory)
                .filter(LongTermMemory.id == memory_id)
                .first()
            )
            
            if memory:
                db_logger.debug(f"成功查询到长期记忆记录 {memory_id}")
            else:
                db_logger.debug(f"未找到长期记忆记录 {memory_id}")
                
            return memory
            
        except Exception as e:
            self.db.rollback()
            db_logger.error(f"查询长期记忆记录 {memory_id} 时出错: {str(e)}")
            raise

    def get_by_user_id(self, end_user_id: str, limit: int = 100, offset: int = 0) -> List[LongTermMemory]:
        """根据用户ID获取长期记忆记录列表
        
        Args:
            end_user_id: 终端用户ID
            limit: 返回记录数限制，默认100
            offset: 偏移量，默认0
            
        Returns:
            List[LongTermMemory]: 记忆记录列表，按创建时间倒序
        """
        try:
            # 使用复合索引 ix_memory_long_term_user_time 优化查询
            memories = (
                self.db.query(LongTermMemory)
                .filter(LongTermMemory.end_user_id == end_user_id)
                .order_by(LongTermMemory.created_at.desc())
                .limit(limit)
                .offset(offset)
                .all()
            )
            
            db_logger.info(f"成功查询用户 {end_user_id} 的 {len(memories)} 条长期记忆记录")
            return memories
            
        except Exception as e:
            self.db.rollback()
            db_logger.error(f"查询用户 {end_user_id} 的长期记忆记录时出错: {str(e)}")
            raise

    def search_by_content(self, end_user_id: str, keyword: str, limit: int = 50) -> List[LongTermMemory]:
        """根据内容关键词搜索长期记忆记录
        
        Args:
            end_user_id: 终端用户ID
            keyword: 搜索关键词
            limit: 返回记录数限制，默认50
            
        Returns:
            List[LongTermMemory]: 匹配的记忆记录列表，按创建时间倒序
        """
        try:
            # 使用 GIN 索引 ix_memory_long_term_retrieved_content_gin 优化 JSON 搜索
            # 同时使用复合索引 ix_memory_long_term_user_time 优化用户过滤
            memories = (
                self.db.query(LongTermMemory)
                .filter(
                    LongTermMemory.end_user_id == end_user_id,
                    LongTermMemory.retrieved_content.astext.contains(keyword)
                )
                .order_by(LongTermMemory.created_at.desc())
                .limit(limit)
                .all()
            )
            
            db_logger.info(f"成功搜索用户 {end_user_id} 包含关键词 '{keyword}' 的 {len(memories)} 条长期记忆记录")
            return memories
            
        except Exception as e:
            self.db.rollback()
            db_logger.error(f"搜索用户 {end_user_id} 包含关键词 '{keyword}' 的长期记忆记录时出错: {str(e)}")
            raise

    def delete_by_id(self, memory_id: uuid.UUID) -> bool:
        """删除指定ID的长期记忆记录
        
        Args:
            memory_id: 记忆ID
            
        Returns:
            bool: 删除成功返回True，否则返回False
        """
        try:
            deleted_count = (
                self.db.query(LongTermMemory)
                .filter(LongTermMemory.id == memory_id)
                .delete(synchronize_session=False)
            )
            
            self.db.commit()
            
            if deleted_count > 0:
                db_logger.info(f"成功删除长期记忆记录 {memory_id}")
                return True
            else:
                db_logger.warning(f"未找到长期记忆记录 {memory_id}，无法删除")
                return False
                
        except Exception as e:
            self.db.rollback()
            db_logger.error(f"删除长期记忆记录 {memory_id} 时出错: {str(e)}")
            raise

    def count_by_user_id(self, end_user_id: str) -> int:
        """统计用户的长期记忆记录数量
        
        Args:
            end_user_id: 终端用户ID
            
        Returns:
            int: 记录数量
        """
        try:
            count = (
                self.db.query(LongTermMemory)
                .filter(LongTermMemory.end_user_id == end_user_id)
                .count()
            )
            
            db_logger.debug(f"用户 {end_user_id} 共有 {count} 条长期记忆记录")
            return count
            
        except Exception as e:
            self.db.rollback()
            db_logger.error(f"统计用户 {end_user_id} 的长期记忆记录数量时出错: {str(e)}")
            raise

    def upsert(self, end_user_id: str, retrieved_content: List[Dict] = None) -> Optional[LongTermMemory]:
        """创建或更新长期记忆记录
        
        根据 end_user_id 和 retrieved_content 判断是否需要写入：
        - 如果找到相同的 end_user_id 和 retrieved_content，则不写入，返回 None
        - 如果没有找到相同的记录，则创建新记录
        
        Args:
            end_user_id: 终端用户ID
            retrieved_content: 检索到的相关内容，格式为[{}, {}]
            
        Returns:
            Optional[LongTermMemory]: 创建的长期记忆对象，如果不需要写入则返回 None
        """
        try:
            retrieved_content = retrieved_content or []
            
            # 优化查询：使用复合索引 ix_memory_long_term_user_time 先过滤用户
            # 然后在应用层比较 JSON 内容，避免复杂的数据库 JSON 比较
            existing_memories = (
                self.db.query(LongTermMemory)
                .filter(LongTermMemory.end_user_id == end_user_id)
                .order_by(LongTermMemory.created_at.desc())
                .limit(100)  # 限制查询数量，避免加载过多数据
                .all()
            )
            
            # 在 Python 中比较 retrieved_content
            for memory in existing_memories:
                if memory.retrieved_content == retrieved_content:
                    # 如果找到相同的记录，不写入
                    db_logger.info(f"长期记忆记录已存在，跳过写入: user {end_user_id}")
                    return None
            
            # 如果没有找到相同的记录，创建新记录
            new_memory = LongTermMemory(
                end_user_id=end_user_id,
                retrieved_content=retrieved_content
            )
            
            self.db.add(new_memory)
            self.db.commit()
            self.db.refresh(new_memory)
            
            db_logger.info(f"成功创建新的长期记忆记录: {new_memory.id} for user {end_user_id}")
            return new_memory
                
        except Exception as e:
            self.db.rollback()
            db_logger.error(f"创建或更新长期记忆记录时出错: {str(e)}")
            raise


