import os
import re
from typing import Dict, Any, List, Tuple


def _parse_meta_block(md_text: str) -> Dict[str, Any]:
    sections: Dict[str, Any] = {}
    m = re.search(r"```javascript([\s\S]*?)```", md_text)
    if not m:
        return sections
    block = m.group(1)
    search_opts: List[Dict[str, Any]] = []
    status_codes: List[Dict[str, Any]] = []
    for line in block.splitlines():
        s = line.strip()
        if not s:
            continue
        msw = re.match(r"search_switch：?(\d+)\s*(?:（(.*?)）)?", s)
        if msw:
            val = msw.group(1)
            desc = msw.group(2) or ""
            search_opts.append({"value": val, "desc": desc})
            continue
        mcode = re.match(r"code:(\d+)\.\s*(.*)", s)
        if mcode:
            code = mcode.group(1)
            desc = mcode.group(2).strip()
            status_codes.append({"code": code, "desc": desc})
            continue
    if search_opts:
        sections["search_switch"] = search_opts
    if status_codes:
        sections["status_code"] = status_codes
    return sections


def _extract_code_block(md_lines: List[str], start_idx: int) -> Tuple[str, int]:
    content_lines: List[str] = []
    i = start_idx
    while i < len(md_lines) and md_lines[i].strip() == "":
        i += 1
    if i >= len(md_lines):
        return "", i
    start_line = md_lines[i].strip()
    if not re.match(r"^`{3,}.*", start_line):
        return "", i
    i += 1
    while i < len(md_lines):
        line = md_lines[i]
        if re.match(r"^`{3,}.*", line.strip()):
            i += 1
            break
        content_lines.append(line)
        i += 1
    return "\n".join(content_lines).strip(), i


def _parse_sections(md_text: str) -> List[Dict[str, Any]]:
    lines = md_text.splitlines()
    sections: List[Dict[str, Any]] = []
    i = 0
    current: Dict[str, Any] | None = None

    def _clean_inline(s: str) -> str:
        s = s.strip()
        if s.startswith("`") and s.endswith("`"):
            s = s[1:-1]
        return s.strip()

    while i < len(lines):
        line = lines[i]
        if line.startswith("# ") and "：" in line:
            name = line.split("：", 1)[1].strip()
            current = {"name": name}
            sections.append(current)
            i += 1
            continue
        if current is not None and line.strip().startswith("### "):
            s = line.strip()
            if "请求端口" in s:
                m = re.search(r"请求端口(.*)$", s)
                if m:
                    current["path"] = _clean_inline(m.group(1))
                i += 1
                continue
            if "请求方式" in s:
                m = re.search(r"请求方式[：:](.*)$", s)
                if m:
                    current["method"] = _clean_inline(m.group(1))
                i += 1
                continue
            if s.startswith("### 描述"):
                i += 1
                desc_lines: List[str] = []
                while i < len(lines):
                    nl = lines[i]
                    if nl.strip().startswith("### "):
                        break
                    if re.match(r"^`{3,}.*", nl.strip()):
                        break
                    desc_lines.append(nl.strip())
                    i += 1
                current["desc"] = "\n".join([x for x in desc_lines if x]).strip() or None
                continue
            if s.startswith("### 输入"):
                if "无" in s:
                    current["input"] = "无"
                    i += 1
                else:
                    i += 1
                    block, i = _extract_code_block(lines, i)
                    current["input"] = block or None
                continue
            if s.startswith("### 输出"):
                i += 1
                block, i = _extract_code_block(lines, i)
                current["output"] = block or None
                continue
            if s.startswith("### 请求体参数"):
                i += 1
                params, i = _parse_body_params_table(lines, i)
                if params:
                    current["body_params"] = params
                continue
        i += 1
    return sections


def parse_api_docs(file_path: str) -> Dict[str, Any]:
    if not os.path.isfile(file_path):
        raise FileNotFoundError(file_path)
    with open(file_path, "r", encoding="utf-8") as f:
        md_text = f.read()
    m = re.search(r"^#\s*(.+)$", md_text, re.M)
    title = m.group(1).strip() if m else ""
    meta = _parse_meta_block(md_text)
    sections = _parse_sections(md_text)
    return {"title": title, "meta": meta, "sections": sections}


def get_default_docs_path() -> str:
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    return os.path.join(project_root, "src", "analytics", "API接口.md")


def parse_api_docs_default() -> Dict[str, Any]:
    return parse_api_docs(get_default_docs_path())


def get_section_descriptions(file_path: str) -> Dict[str, str]:
    data = parse_api_docs(file_path)
    out: Dict[str, str] = {}
    for s in data.get("sections", []):
        name = s.get("name")
        if not name:
            continue
        out[name] = s.get("desc") or ""
    return out


def get_section_descriptions_default() -> Dict[str, str]:
    return get_section_descriptions(get_default_docs_path())


def _parse_body_params_table(md_lines: List[str], start_idx: int) -> Tuple[List[Dict[str, Any]], int]:
    rows: List[str] = []
    i = start_idx
    while i < len(md_lines) and md_lines[i].strip() == "":
        i += 1
    if i >= len(md_lines) or not md_lines[i].strip().startswith("|"):
        return [], i
    header = md_lines[i].strip()
    i += 1
    if i < len(md_lines) and md_lines[i].strip().startswith("|"):
        i += 1
    while i < len(md_lines) and md_lines[i].strip().startswith("|"):
        rows.append(md_lines[i].strip())
        i += 1
    headers = [h.strip() for h in header.strip('|').split('|')]
    out: List[Dict[str, Any]] = []
    for r in rows:
        cols = [c.strip() for c in r.strip('|').split('|')]
        if len(cols) != len(headers):
            continue
        item: Dict[str, Any] = {}
        for k, v in zip(headers, cols, strict=False):
            if k == "参数名":
                item["name"] = v
            elif k == "类型":
                item["type"] = v
            elif k == "是否必填":
                item["required"] = v
            elif k == "描述":
                item["desc"] = v
            else:
                item[k] = v
        out.append(item)
    return out, i
