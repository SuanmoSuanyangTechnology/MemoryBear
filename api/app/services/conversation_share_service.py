"""
会话分享服务
"""
import uuid
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List

from sqlalchemy.orm import Session

from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.logging_config import get_business_logger
from app.core.config import settings
from app.models import ConversationShare, Message

logger = get_business_logger()


class ConversationShareService:
    """会话分享服务"""

    def __init__(self, db: Session):
        self.db = db

    def create_share(
        self,
        conversation_id: uuid.UUID,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
        password: Optional[str] = None,
        expire_hours: Optional[int] = None,
        allow_copy: bool = True,
    ) -> Dict[str, Any]:
        """创建分享链接

        Args:
            conversation_id: 会话ID
            workspace_id: 工作空间ID
            user_id: 创建人ID
            password: 访问密码（可选）
            expire_hours: 过期时长（小时）
            allow_copy: 是否允许复制内容

        Returns:
            Dict: 包含分享链接等信息
        """
        from app.models import Conversation
        # 验证会话存在
        conv = self.db.get(Conversation, conversation_id)
        if not conv:
            raise BusinessException("会话不存在", BizCode.NOT_FOUND)

        # 生成唯一分享标识（8位短链接）
        share_uuid = str(uuid.uuid4())[:8]

        expire_at = None
        if expire_hours:
            expire_at = datetime.now() + timedelta(hours=expire_hours)

        share = ConversationShare(
            conversation_id=conversation_id,
            share_uuid=share_uuid,
            workspace_id=workspace_id,
            created_by=user_id,
            password=password,
            expire_at=expire_at,
            allow_copy=allow_copy,
        )
        self.db.add(share)
        self.db.commit()
        self.db.refresh(share)

        share_url = f"{settings.FILE_LOCAL_SERVER_URL}/share/{share_uuid}"

        logger.info(
            "创建分享链接",
            extra={
                "share_uuid": share_uuid,
                "conversation_id": str(conversation_id),
                "user_id": str(user_id),
            }
        )

        return {
            "share_id": str(share.id),
            "share_uuid": share_uuid,
            "share_url": share_url,
            "expire_at": int(expire_at.timestamp() * 1000) if expire_at else None,
            "has_password": bool(password),
        }

    def get_shared_conversation(
        self,
        share_uuid: str,
        password: Optional[str] = None,
    ) -> Dict[str, Any]:
        """获取分享的会话（只读模式）

        Args:
            share_uuid: 分享标识
            password: 访问密码

        Returns:
            Dict: 会话消息列表和元数据
        """
        share = self.db.query(ConversationShare).filter(
            ConversationShare.share_uuid == share_uuid,
            ConversationShare.is_active == True,
        ).first()

        if not share:
            raise BusinessException("分享链接不存在或已失效", BizCode.NOT_FOUND)

        # 检查过期
        if share.expire_at and share.expire_at < datetime.now():
            raise BusinessException("分享链接已过期", BizCode.FORBIDDEN)

        # 检查密码
        if share.password and share.password != password:
            raise BusinessException("访问密码错误", BizCode.FORBIDDEN)

        # 更新访问计数
        share.view_count += 1
        self.db.commit()

        # 获取会话消息（排除已删除的）
        messages = self.db.query(Message).filter(
            Message.conversation_id == share.conversation_id,
            Message.is_deleted == False,
        ).order_by(Message.created_at).all()

        logger.info(
            "访问分享链接",
            extra={
                "share_uuid": share_uuid,
                "view_count": share.view_count,
            }
        )

        return {
            "conversation_id": str(share.conversation_id),
            "messages": [
                {
                    "id": str(msg.id),
                    "role": msg.role,
                    "content": msg.content,
                    "created_at": int(msg.created_at.timestamp() * 1000),
                }
                for msg in messages
            ],
            "allow_copy": share.allow_copy,
            "is_readonly": True,  # 只读标记
        }

    def revoke_share(
        self,
        share_uuid: str,
        workspace_id: uuid.UUID,
    ) -> None:
        """撤销分享链接

        Args:
            share_uuid: 分享标识
            workspace_id: 工作空间ID
        """
        share = self.db.query(ConversationShare).filter(
            ConversationShare.share_uuid == share_uuid,
            ConversationShare.workspace_id == workspace_id,
        ).first()

        if not share:
            raise BusinessException("分享链接不存在", BizCode.NOT_FOUND)

        share.is_active = False
        self.db.commit()

        logger.info(
            "撤销分享链接",
            extra={
                "share_uuid": share_uuid,
                "workspace_id": str(workspace_id),
            }
        )

    def list_shares(
        self,
        conversation_id: uuid.UUID,
        workspace_id: uuid.UUID,
    ) -> List[Dict[str, Any]]:
        """列出会话的所有分享链接

        Args:
            conversation_id: 会话ID
            workspace_id: 工作空间ID

        Returns:
            List[Dict]: 分享链接列表
        """
        shares = self.db.query(ConversationShare).filter(
            ConversationShare.conversation_id == conversation_id,
            ConversationShare.workspace_id == workspace_id,
            ConversationShare.is_active == True,
        ).order_by(ConversationShare.created_at.desc()).all()

        return [
            {
                "share_id": str(s.id),
                "share_uuid": s.share_uuid,
                "share_url": f"{settings.FILE_LOCAL_SERVER_URL}/share/{s.share_uuid}",
                "view_count": s.view_count,
                "expire_at": int(s.expire_at.timestamp() * 1000) if s.expire_at else None,
                "has_password": bool(s.password),
                "allow_copy": s.allow_copy,
                "created_at": int(s.created_at.timestamp() * 1000),
            }
            for s in shares
        ]
