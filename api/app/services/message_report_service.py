"""
消息举报服务（含选中反馈和审核）
"""
import uuid
from datetime import datetime
from typing import Dict, Any, Optional, List, Tuple

from sqlalchemy.orm import Session

from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.logging_config import get_business_logger
from app.models import MessageReport, Message

logger = get_business_logger()


class ReportService:
    """消息举报服务"""

    def __init__(self, db: Session):
        self.db = db

    def submit_report(
        self,
        message_id: uuid.UUID,
        conversation_id: uuid.UUID,
        workspace_id: uuid.UUID,
        reported_by: uuid.UUID,
        report_type: str,
        report_reason: Optional[str] = None,
        text_start_offset: Optional[int] = None,
        text_end_offset: Optional[int] = None,
        selected_text: Optional[str] = None,
    ) -> Dict[str, Any]:
        """提交举报/选中反馈

        Args:
            message_id: 消息ID
            conversation_id: 会话ID
            workspace_id: 工作空间ID
            reported_by: 举报人ID
            report_type: 举报类型
            report_reason: 举报原因
            text_start_offset: 选中文本起始位置
            text_end_offset: 选中文本结束位置
            selected_text: 选中的文本片段

        Returns:
            Dict: 包含举报ID和状态
        """
        report = MessageReport(
            message_id=message_id,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
            reported_by=reported_by,
            report_type=report_type,
            report_reason=report_reason,
            text_start_offset=text_start_offset,
            text_end_offset=text_end_offset,
            selected_text=selected_text,
        )
        self.db.add(report)

        # 更新消息举报计数
        message = self.db.get(Message, message_id)
        if message:
            message.report_count += 1

        self.db.commit()
        self.db.refresh(report)

        logger.info(
            "提交举报",
            extra={
                "report_id": str(report.id),
                "message_id": str(message_id),
                "report_type": report_type,
                "reported_by": str(reported_by),
            }
        )

        # 触发自动审核（异步）
        self._trigger_auto_audit(report.id)

        return {
            "report_id": str(report.id),
            "status": "pending",
        }

    def _trigger_auto_audit(self, report_id: uuid.UUID):
        """触发自动审核（调用内容安全网关）

        Args:
            report_id: 举报ID
        """
        # TODO: 接入阿里云内容安全、腾讯云天御等
        # 审核结果更新到 report.status 和 report.severity
        logger.debug(f"触发自动审核: {report_id}")

    def get_reports_for_review(
        self,
        workspace_id: uuid.UUID,
        status: Optional[str] = None,
        severity: Optional[str] = None,
        page: int = 1,
        pagesize: int = 20,
    ) -> Tuple[List[MessageReport], int]:
        """获取待审核举报列表（运营后台）

        Args:
            workspace_id: 工作空间ID
            status: 状态过滤
            severity: 严重程度过滤
            page: 页码
            pagesize: 每页数量

        Returns:
            Tuple[List[MessageReport], int]: 举报列表和总数
        """
        query = self.db.query(MessageReport).filter(
            MessageReport.workspace_id == workspace_id,
        )

        if status:
            query = query.filter(MessageReport.status == status)
        if severity:
            query = query.filter(MessageReport.severity == severity)

        total = query.count()
        reports = query.order_by(
            MessageReport.created_at.desc()
        ).offset((page - 1) * pagesize).limit(pagesize).all()

        return reports, total

    def review_report(
        self,
        report_id: uuid.UUID,
        reviewer_id: uuid.UUID,
        severity: str,
        action_taken: str,
        review_note: Optional[str] = None,
    ) -> Dict[str, Any]:
        """审核举报（运营后台）

        Args:
            report_id: 举报ID
            reviewer_id: 审核人ID
            severity: 严重程度
            action_taken: 处理措施
            review_note: 审核备注

        Returns:
            Dict: 审核结果
        """
        report = self.db.get(MessageReport, report_id)
        if not report:
            raise BusinessException("举报记录不存在", BizCode.NOT_FOUND)

        report.status = "reviewed"
        report.severity = severity
        report.action_taken = action_taken
        report.reviewer_id = reviewer_id
        report.review_note = review_note
        report.reviewed_at = datetime.now()

        # 根据处理措施执行相应操作
        if action_taken == "content_removed":
            # 标记消息为删除
            message = self.db.get(Message, report.message_id)
            if message:
                message.is_deleted = True
        elif action_taken == "user_banned":
            # TODO: 封禁用户逻辑
            pass

        self.db.commit()

        logger.info(
            "审核举报",
            extra={
                "report_id": str(report_id),
                "reviewer_id": str(reviewer_id),
                "severity": severity,
                "action_taken": action_taken,
            }
        )

        return {
            "report_id": str(report_id),
            "status": "reviewed",
            "action_taken": action_taken,
        }

    def get_reports_statistics(
        self,
        workspace_id: uuid.UUID,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """获取举报统计（平台侧）

        Args:
            workspace_id: 工作空间ID
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            Dict: 统计数据
        """
        query = self.db.query(MessageReport).filter(
            MessageReport.workspace_id == workspace_id,
        )

        if start_date:
            query = query.filter(MessageReport.created_at >= start_date)
        if end_date:
            query = query.filter(MessageReport.created_at <= end_date)

        reports = query.all()

        # 按类型分组统计
        type_stats = {}
        severity_stats = {}
        status_stats = {}
        for r in reports:
            type_stats[r.report_type] = type_stats.get(r.report_type, 0) + 1
            if r.severity:
                severity_stats[r.severity] = severity_stats.get(r.severity, 0) + 1
            status_stats[r.status] = status_stats.get(r.status, 0) + 1

        return {
            "total_count": len(reports),
            "by_type": type_stats,
            "by_severity": severity_stats,
            "by_status": status_stats,
            "pending_count": sum(1 for r in reports if r.status == "pending"),
        }
