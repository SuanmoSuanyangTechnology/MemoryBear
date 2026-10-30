"""沙箱命令/代码输出清洗：还原终端文本并遮盖高置信度敏感信息。

只处理有明确结构信号的秘密（鉴权头、Bearer、私钥块和敏感字段赋值），避免因
自然语言里出现 token/password 等单词而误删正常输出。
"""
from __future__ import annotations

import re

# CSI 序列（颜色、光标、擦除，如 ESC[2K、ESC[?25l）与 OSC 序列（标题等，以 BEL 或 ST 结束）
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]")
_REDACTED = "[REDACTED]"

# PEM 私钥内容跨行，标签必须成对一致；证书和公钥不在这里遮盖。
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN (?P<label>[A-Z0-9 ]*PRIVATE KEY)-----.*?"
    r"-----END (?P=label)-----",
    re.DOTALL,
)
# Bearer 本身就是明确的鉴权信号，不要求前面一定出现 Authorization。
_BEARER_RE = re.compile(r"(?i)(\bBearer[ \t]+)[A-Za-z0-9._~+/=-]+")
# Authorization 也可能使用 Basic 或自定义 scheme。分别覆盖 JSON/日志引号值和 HTTP 头行。
_AUTH_QUOTED_RE = re.compile(
    r"(?i)(?P<prefix>(?<![\w-])(?P<kq>[\"'])?authorization(?P=kq)\s*[:=]\s*)"
    r"(?P<vq>[\"'])(?P<value>.*?)(?P=vq)"
)
_AUTH_BARE_RE = re.compile(
    r"(?i)(?P<prefix>(?<![\w-])authorization[ \t]*[:=][ \t]*)"
    r"(?P<value>(?![\"'])(?:[A-Za-z][\w.-]*[ \t]+)?[^\s,;}}\]\"']+)"
)

# 敏感键允许供应商/业务前缀，例如 OPENAI_API_KEY、AWS_SECRET_ACCESS_KEY、
# DATABASE_PASSWORD 和 X-API-Key；必须紧跟赋值符，避免误伤自然语言。
_SECRET_KEY = (
    r"(?:[a-z0-9]+[_-])*(?:password|passwd|token|secret|api[_-]?key|access[_-]?key|client[_-]?secret)"
)
_SECRET_QUOTED_RE = re.compile(
    rf"(?i)(?P<prefix>(?<![\w-])(?P<kq>[\"'])?{_SECRET_KEY}(?P=kq)\s*[:=]\s*)"
    r"(?P<vq>[\"'])(?P<value>.*?)(?P=vq)"
)
_SECRET_BARE_RE = re.compile(
    rf"(?i)(?P<prefix>(?<![\w-]){_SECRET_KEY}\s*[:=]\s*)"
    r"(?P<value>(?![\"'])[^\s,;}}\]]+)"
)


def clean_terminal_output(text: str | None) -> str:
    """去 ANSI 控制序列，并让回车重绘的行只保留最终显示的那一段。"""
    if not text:
        return text or ""
    text = _ANSI_RE.sub("", text.replace("\r\n", "\n"))
    if "\r" not in text:
        return text
    lines: list[str] = []
    for line in text.split("\n"):
        if "\r" in line:
            # 终端里 \r 把光标移回行首，后写的内容覆盖先写的：取最后一个非空片段
            segments = [seg for seg in line.split("\r") if seg.strip()]
            line = segments[-1] if segments else ""
        lines.append(line)
    return "\n".join(lines)


def _replace_quoted(match: re.Match[str]) -> str:
    return f"{match.group('prefix')}{match.group('vq')}{_REDACTED}{match.group('vq')}"


def _replace_bare(match: re.Match[str]) -> str:
    return f"{match.group('prefix')}{_REDACTED}"


def sanitize_sandbox_output(text: str | None) -> str:
    """清理终端控制文本，并遮盖带明确结构信号的秘密。"""
    cleaned = clean_terminal_output(text)
    if not cleaned:
        return cleaned
    cleaned = _PRIVATE_KEY_RE.sub(_REDACTED, cleaned)
    cleaned = _AUTH_QUOTED_RE.sub(_replace_quoted, cleaned)
    cleaned = _AUTH_BARE_RE.sub(_replace_bare, cleaned)
    cleaned = _BEARER_RE.sub(rf"\1{_REDACTED}", cleaned)
    cleaned = _SECRET_QUOTED_RE.sub(_replace_quoted, cleaned)
    return _SECRET_BARE_RE.sub(_replace_bare, cleaned)
