import smtplib
import re
import asyncio
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header
from email.utils import formataddr
from concurrent.futures import ThreadPoolExecutor

from app.core.config import settings
from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.logging_config import get_business_logger

business_logger = get_business_logger()


def _send_email_sync(to_email: str, subject: str, html_content: str, text_content: str = None):
    """同步发送邮件"""
    smtp_server = settings.SMTP_SERVER
    smtp_port = settings.SMTP_PORT
    smtp_user = settings.SMTP_USER
    smtp_password = settings.SMTP_PASSWORD
    
    if not smtp_server or not smtp_user or not smtp_password:
        raise BusinessException("邮件服务未配置", code=BizCode.SERVICE_UNAVAILABLE)
    
    msg = MIMEMultipart('alternative')
    msg['Subject'] = Header(subject, "utf-8")
    from_name = "MemoryBear系统"
    msg['From'] = formataddr((Header(from_name, 'utf-8').encode(), smtp_user))
    msg['To'] = Header(to_email, "utf-8")

    if not text_content:
        text_content = html_content.replace('<br>', '\n').replace('<p>', '\n').replace('</p>', '\n')
        text_content = re.sub(r'<.*?>', '', text_content)
    text_part = MIMEText(text_content, 'plain', 'utf-8')
    msg.attach(text_part)
    
    html_part = MIMEText(html_content, 'html', 'utf-8')
    msg.attach(html_part)
    
    if smtp_port == 465:
        with smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=10) as server:
            server.login(smtp_user, smtp_password)
            server.send_message(msg)
    else:
        with smtplib.SMTP(smtp_server, smtp_port, timeout=10) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.send_message(msg)


async def send_email(to_email: str, subject: str, html_content: str, text_content: str = None):
    """异步发送邮件"""
    to_email = to_email.strip()
    if not to_email or not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', to_email):
        err_msg = f"收件人邮箱格式无效: {to_email}"
        business_logger.error(err_msg)
        raise BusinessException(err_msg, code=BizCode.INVALID_PARAMETER)
    
    try:
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor() as executor:
            await loop.run_in_executor(
                executor,
                _send_email_sync,
                to_email,
                subject,
                html_content,
                text_content
            )
        business_logger.info(f"邮件发送成功: {to_email}")
    except smtplib.SMTPAuthenticationError:
        err_msg = "SMTP认证失败，请检查SMTP账号/密码是否正确"
        business_logger.error(f"邮件发送失败: {to_email} - {err_msg}")
        raise BusinessException(err_msg, code=BizCode.UNAUTHORIZED)
    except smtplib.SMTPConnectError:
        err_msg = "SMTP服务器连接失败，请检查服务器地址/端口是否正确"
        business_logger.error(f"邮件发送失败: {to_email} - {err_msg}")
        raise BusinessException(err_msg, code=BizCode.SERVICE_UNAVAILABLE)
    except TimeoutError:
        err_msg = "邮件发送超时，请检查SMTP服务器配置"
        business_logger.error(f"邮件发送失败: {to_email} - {err_msg}")
        raise BusinessException(err_msg, code=BizCode.BAD_REQUEST)
    except Exception as e:
        business_logger.error(f"邮件发送失败: {to_email} - {str(e)}")
        raise BusinessException(f"邮件发送失败: {str(e)}", code=BizCode.SERVICE_UNAVAILABLE)
