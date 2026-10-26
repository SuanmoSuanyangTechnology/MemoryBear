"""沙箱命令/代码输出的清洗：去掉终端控制序列，折叠进度条。

pip、npm 等工具会往 stdout/stderr 写 ANSI 颜色码和用回车 (\\r) 反复重绘的进度条。
原样交给模型会把真正有用的错误信息（例如 ``Network is unreachable``）淹没在几十 KB
的重绘文本里，还白白消耗上下文。这里按终端的实际显示效果还原文本。
"""
from __future__ import annotations

import re

# CSI 序列（颜色、光标、擦除，如 ESC[2K、ESC[?25l）与 OSC 序列（标题等，以 BEL 或 ST 结束）
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]")


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
