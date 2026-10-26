"""Prompt blocks for the sandbox toolset (code interpreter, terminal, file tools).

Rule of thumb: how ONE tool is used belongs in that tool's ``description``; only
cross-tool procedure (which tools to combine, in which order, where files go) lives here,
and each block is injected only when the tool combination it talks about is present.

Not injected on purpose: ``sandbox_import_file`` / ``sandbox_read_file`` usage. The
import tool's description and the per-turn attachment manifest (appended to the user
message) already tell the model when and how to use it.
"""

from __future__ import annotations

from .registry import (
    ORDER_CAPABILITY,
    ORDER_DELIVERY,
    ORDER_PLAYBOOK,
    PromptContext,
    prompt_block,
)

CODE_INTERPRETER_TOOL = "code_interpreter_tool"
TERMINAL_TOOL = "terminal_tool"
PUBLISH_TOOL = "sandbox_publish_file"
WRITE_TOOL = "sandbox_write_file"
READ_TOOL = "sandbox_read_file"


def _can_run_code(ctx: PromptContext) -> bool:
    return ctx.has_any(CODE_INTERPRETER_TOOL, TERMINAL_TOOL)


def _can_create_files(ctx: PromptContext) -> bool:
    return ctx.has_any(CODE_INTERPRETER_TOOL, TERMINAL_TOOL, WRITE_TOOL)


# ----------------------------------------------------------------- capability


@prompt_block(
    "sandbox.code_interpreter",
    order=ORDER_CAPABILITY,
    when=lambda ctx: ctx.has(CODE_INTERPRETER_TOOL),
)
def _code_interpreter(ctx: PromptContext) -> str:
    return (
        f"你可以通过 {CODE_INTERPRETER_TOOL} 在隔离沙箱中执行 Python3 代码（Node.js 脚本请用终端工具运行）。"
        "复杂计算、数据处理、文件生成与格式转换应交给它完成，并以其实际输出为准。"
    )


@prompt_block(
    "sandbox.terminal",
    order=ORDER_CAPABILITY + 10,
    when=lambda ctx: ctx.has(TERMINAL_TOOL),
)
def _terminal(ctx: PromptContext) -> str:
    return (
        f"你可以通过 {TERMINAL_TOOL} 在隔离沙箱中执行 shell 命令（运行脚本、操作文件、调用命令行工具），"
        "并以其返回的退出码和输出为准。"
    )


@prompt_block(
    "sandbox.network",
    order=ORDER_CAPABILITY + 20,
    when=_can_run_code,
)
def _network(ctx: PromptContext) -> str:
    return (
        "沙箱出站网络时通时断，缺库时可以尝试联网安装一次并设较短超时；安装失败（超时、网络不可达、找不到版本）"
        "说明此刻网络不可用，不要反复重试，改用已有库完成，或直接告诉用户缺少什么。"
    )


@prompt_block(
    "sandbox.code_executed_upstream",
    order=ORDER_CAPABILITY + 5,
    when=lambda ctx: not ctx.has(CODE_INTERPRETER_TOOL) and ctx.was_executed(CODE_INTERPRETER_TOOL),
)
def _code_executed_upstream(ctx: PromptContext) -> str:
    return (
        "代码执行已通过代码解释器在上游完成，以上文中的实际输出为准；"
        "上文没有对应执行记录的内容，不要声称已运行代码，也不要虚构执行结果。"
    )


@prompt_block(
    "sandbox.no_code_execution",
    order=ORDER_CAPABILITY + 30,
    when=lambda ctx: not _can_run_code(ctx) and not ctx.was_executed(CODE_INTERPRETER_TOOL),
)
def _no_code_execution(ctx: PromptContext) -> str:
    return (
        "你当前不具备代码或命令执行能力：不要声称已运行代码或命令，也不要虚构执行结果；"
        "涉及需要执行代码才能得到的结果时，直接说明无法执行，必要时只给出代码供用户自行运行。"
    )


# ------------------------------------------------------------------- playbook


@prompt_block("sandbox.script_workflow", order=ORDER_PLAYBOOK, when=_can_run_code)
def _script_workflow(ctx: PromptContext) -> str:
    if ctx.has(WRITE_TOOL):
        write_hint = f"用 {WRITE_TOOL} 的 path + content 参数把脚本写成文件（如 path=gen_ppt.py），"
        if ctx.has(READ_TOOL):
            view_hint = f"查看文件内容用 {READ_TOOL}（按行号翻页），按关键字查找用终端的 grep -n。"
        else:
            view_hint = "查看内容或查找用终端的 cat / grep -n / sed -n。"
        fix_hint = (
            f"报错后只改出错的几行：用 {WRITE_TOOL} 的 path + old_string + new_string 做精确替换，"
            f"不要把整份脚本重发；{view_hint}"
        )
    else:
        write_hint = "在代码解释器里用 open('gen_ppt.py','w').write(...) 写入，或在终端用 cat <<'EOF' > gen_ppt.py，"
        fix_hint = ""
    return (
        "生成 PPT、Word、Excel、PDF 等文档，或编写超过几行的程序时，不要把整段代码塞进一条命令或一次内联执行："
        f"先{write_hint}再执行该文件。"
        "终端和代码解释器的默认目录就是当前会话目录，脚本和中间文件一律用相对路径写在这里，"
        "不要写到 /workspace 根目录或其他会话的目录（/workspace 根由该用户的所有会话共享，会互相覆盖）；"
        "要交付给用户的成品放进 out/ 子目录（如 out/report.pptx）。"
        "报错时读懂错误信息，修改脚本文件后重新执行，直到退出码为 0 且产物文件确实存在，"
        f"{fix_hint}"
        "并重新打开产物确认内容（如页数、工作表）无误，再交付。"
    )


@prompt_block("sandbox.research_first", order=ORDER_PLAYBOOK + 10, when=_can_run_code)
def _research_first(ctx: PromptContext) -> str:
    return (
        "需要围绕某个主题产出报告、PPT 等内容型文档时，先用检索/搜索类工具调研，拿到资料和数据后再动手，"
        "不要凭空编造内容；没有可用的检索工具时，如实告知内容仅基于已有知识。"
    )


# ------------------------------------------------------------------- delivery


@prompt_block("sandbox.deliver", order=ORDER_DELIVERY, when=lambda ctx: ctx.has(PUBLISH_TOOL))
def _deliver(ctx: PromptContext) -> str:
    if _can_create_files(ctx):
        return (
            f"在沙箱里生成了要交给用户的成品文件（PPT、Word、Excel、PDF、图片等）后，调用 {PUBLISH_TOOL} "
            "取得下载链接，并把链接写进回复；不要把沙箱内的文件路径当作用户可访问的地址。"
        )
    # Publishing only hands over files that already exist in the sandbox. Without any
    # tool that can create a file, the model must not promise or invent a deliverable.
    return (
        f"{PUBLISH_TOOL} 只能交付沙箱里已经存在的文件，而你当前没有任何能在沙箱里生成文件的工具，"
        "所以不能自己制作 PPT、Word、Excel、PDF 等成品，也不要编造文件路径去交付；"
        "只有路径来自用户明确给出或其他工具的返回结果时才调用它。"
        "用户要你生成文件时，如实说明当前没有生成文件的能力。"
    )
