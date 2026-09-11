"""记忆统计（Dashboard）相关请求/响应 schema。

集中放置本模块新增接口的请求体模型，避免在 controller 内联定义。
"""

from typing import List

from pydantic import BaseModel, Field


class EndUserMemoryCountsRequest(BaseModel):
    """终端用户记忆量批量查询请求体。

    - ``end_user_ids``：终端用户 ID 数组，单次上限 200；合法性（UUID/数量）在
      controller 层校验并以业务错误码返回，避免直接抛 422。

    空间不作为入参：管理端与对外接口均以「调用方自身绑定空间」为准（管理端为当前
    会话空间，对外为 API Key 绑定空间），不接受外部指定 workspace_id，避免跨空间越权。
    """

    end_user_ids: List[str] = Field(..., description="终端用户 ID 数组，单次上限 200")
