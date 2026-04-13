import json
import os

branch = os.environ.get("BRANCH", "")
author = os.environ.get("AUTHOR", "")
pr_title = os.environ.get("PR_TITLE", "")
pr_url = os.environ.get("PR_URL", "")
ai_summary = os.environ.get("AI_SUMMARY", "")

content = (
    "## 🚀 Release 发布通知\n"
    f"> 📦 **分支**: {branch}\n"
    f"> 👤 **提交人**: {author}\n"
    f"> 📝 **标题**: {pr_title}\n\n"
    "### 🧠 AI变更摘要\n"
    f"{ai_summary}\n\n"
    "---\n"
    f"🔗 [查看PR详情]({pr_url})"
)

payload = {"msgtype": "markdown", "markdown": {"content": content}}

with open("wechat_payload.json", "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False)
