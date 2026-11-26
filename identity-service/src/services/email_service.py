"""SMTP delivery for the management plane (sync).

Wire-compatible port of ``core/api/app/services/email_service.py``: identical
Chinese error copy and code mapping, including the host's double-wrap where
``_send_email_sync``'s ``BusinessException`` falls through the generic handler.
Delivery is synchronous because the management plane runs on the sync engine.
"""

from __future__ import annotations

import logging
import re
import smtplib
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid

from error_core.codes import BizCode

from src.config import settings
from src.core.exceptions import BusinessException

logger = logging.getLogger(__name__)

_EMAIL_PATTERN = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")


def _send_email_sync(
    to_email: str,
    subject: str,
    html_content: str,
    text_content: str | None = None,
) -> None:
    smtp_server = settings.SMTP_SERVER
    smtp_port = settings.SMTP_PORT
    smtp_user = settings.SMTP_USER
    smtp_password = settings.SMTP_PASSWORD

    if not smtp_server or not smtp_user or not smtp_password:
        raise BusinessException("邮件服务未配置", code=BizCode.SERVICE_UNAVAILABLE)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = Header(subject, "utf-8")
    from_name = "MemoryBear系统"
    msg["From"] = formataddr((Header(from_name, "utf-8").encode(), smtp_user))
    msg["To"] = to_email
    msg["Date"] = formatdate(localtime=True)
    if "@" in smtp_user:
        msg["Message-ID"] = make_msgid(domain=smtp_user.rsplit("@", 1)[1])
    else:
        msg["Message-ID"] = make_msgid()

    if not text_content:
        text_content = (
            html_content.replace("<br>", "\n")
            .replace("<p>", "\n")
            .replace("</p>", "\n")
        )
        text_content = re.sub(r"<.*?>", "", text_content)
    msg.attach(MIMEText(text_content, "plain", "utf-8"))
    msg.attach(MIMEText(html_content, "html", "utf-8"))

    if smtp_port == 465:
        with smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=10) as server:
            server.login(smtp_user, smtp_password)
            server.send_message(msg)
    else:
        with smtplib.SMTP(smtp_server, smtp_port, timeout=10) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.send_message(msg)


def send_email(
    to_email: str,
    subject: str,
    html_content: str,
    text_content: str | None = None,
) -> None:
    """Send one HTML mail; failures map to the host's business codes."""

    to_email = to_email.strip()
    if not to_email or not _EMAIL_PATTERN.match(to_email):
        err_msg = f"收件人邮箱格式无效: {to_email}"
        logger.error(err_msg)
        raise BusinessException(err_msg, code=BizCode.INVALID_PARAMETER)

    try:
        _send_email_sync(to_email, subject, html_content, text_content)
        logger.info("邮件发送成功: %s", to_email)
    except smtplib.SMTPAuthenticationError as exc:
        err_msg = "SMTP认证失败，请检查SMTP账号/密码是否正确"
        logger.error("邮件发送失败: %s - %s", to_email, err_msg)
        raise BusinessException(err_msg, code=BizCode.UNAUTHORIZED) from exc
    except smtplib.SMTPConnectError as exc:
        err_msg = "SMTP服务器连接失败，请检查服务器地址/端口是否正确"
        logger.error("邮件发送失败: %s - %s", to_email, err_msg)
        raise BusinessException(err_msg, code=BizCode.SERVICE_UNAVAILABLE) from exc
    except TimeoutError as exc:
        err_msg = "邮件发送超时，请检查SMTP服务器配置"
        logger.error("邮件发送失败: %s - %s", to_email, err_msg)
        raise BusinessException(err_msg, code=BizCode.BAD_REQUEST) from exc
    except Exception as exc:
        logger.error("邮件发送失败: %s - %s", to_email, exc)
        raise BusinessException(
            f"邮件发送失败: {exc}", code=BizCode.SERVICE_UNAVAILABLE
        ) from exc
