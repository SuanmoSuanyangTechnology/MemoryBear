"""Deterministic production cases for the SceneCommunity configuration preview."""

PREVIEW_CASES = (
    {
        "preview_case": "EXISTING_COMMUNITY_UPDATE",
        "display_name": "同一次装修：新增厨房细节",
        "sort_order": 1,
        "is_default": True,
    },
    {
        "preview_case": "NEW_SINGLE_MEMBER_COMMUNITY",
        "display_name": "同一房屋：启动第二次整体翻修",
        "sort_order": 2,
        "is_default": False,
    },
    {
        "preview_case": "SECOND_MEMBER_BOUNDARY",
        "display_name": "第二条记忆归入：首次生成社区边界",
        "sort_order": 3,
        "is_default": False,
    },
    {
        "preview_case": "NOT_ELIGIBLE",
        "display_name": "无社区价值：你好，早上好",
        "sort_order": 4,
        "is_default": False,
    },
)


CANDIDATE_COMMUNITIES = (
    {
        "rank": 1,
        "title": "杭州新房首次整体装修",
        "subtitle": "同一房屋 · 首次整装",
        "score": 0.94,
        "child_names": ["厨房 L 型布局", "柜体采用白色", "卫生间防水待确认"],
    },
    {
        "rank": 2,
        "title": "杭州新房入住后漏水维修",
        "subtitle": "同一房屋 · 独立维修",
        "score": 0.79,
        "child_names": ["厨房下水管漏水", "更换管道接头", "通水检查已完成"],
    },
    {
        "rank": 3,
        "title": "父母旧房整体翻修",
        "subtitle": "不同房屋 · 不同事项",
        "score": 0.69,
        "child_names": ["父母旧房翻修", "保留原有家具", "卧室墙面重新粉刷"],
    },
    {
        "rank": 4,
        "title": "杭州新房交房验收",
        "subtitle": "同一房屋 · 验收事项",
        "score": 0.61,
        "child_names": ["交房验收预约", "检查门窗密封", "水电验收已通过"],
    },
    {
        "rank": 5,
        "title": "杭州临时租房安排",
        "subtitle": "不同住所 · 独立事项",
        "score": 0.54,
        "child_names": ["短租三个月", "靠近工作地点", "需要独立厨房"],
    },
    {
        "rank": 6,
        "title": "旧家具搬运与安置",
        "subtitle": "相关物品 · 独立安置",
        "score": 0.46,
        "child_names": ["旧书搬入新住所", "沙发暂存仓库", "书架保留"],
    },
    {
        "rank": 7,
        "title": "搬家公司预约",
        "subtitle": "相关安排 · 独立服务",
        "score": 0.38,
        "child_names": ["预约周末搬家", "易碎物单独包装", "提前预约电梯"],
    },
    {
        "rank": 8,
        "title": "杭州住房证件办理",
        "subtitle": "同一房屋 · 证件办理",
        "score": 0.30,
        "child_names": ["整理住房资料", "预约办理时间", "领取办理回执"],
    },
)


PREVIEW_CASE_DATA = {
    "EXISTING_COMMUNITY_UPDATE": {
        "display_name": "同一次装修：新增厨房细节",
        "input_scene": {
            "content": "用户继续讨论杭州新房首次整体装修，决定厨房台面从岩板改为石英石，并确认预算不变。",
            "display_label": "居住与日常事务 · 文本",
        },
        "pipeline": [
            {"stage_name": "价值判断", "status": "COMPLETED", "result_name": "有社区价值"},
            {"stage_name": "记忆分类", "status": "COMPLETED", "result_name": "居住与日常事务"},
            {"stage_name": "归属判断", "status": "COMPLETED", "result_name": "归入已有"},
            {"stage_name": "摘要更新", "status": "COMPLETED", "result_name": "更新摘要"},
        ],
        "classification_result": {
            "result": "ASSIGN_EXISTING",
            "title": "归入已有社区",
            "reason_label": "同一次事项，范围未变化",
            "reason": "对象、装修轮次和目标一致，归入同一次整装社区。候选数量变化不会强制改变归类结论。",
        },
        "community_change": {
            "change_label": "仅更新摘要",
            "before": "已确定厨房 L 型布局；卫生间防水方案待确认。",
            "after": "已确定厨房 L 型布局与石英石台面；卫生间防水方案待确认。稳定名称、范围和边界保持不变。",
        },
        "new_content": {
            "node_name": "改用石英石台面",
            "description": "用户继续讨论杭州新房首次整体装修，决定厨房台面从岩板改为石英石，并确认预算不变。对象、装修轮次和目标一致，归入同一次整装社区。候选数量变化不会强制改变归类结论。",
            "joined_community_name": "杭州新房首次整体装修",
            "created_community_name": None,
        },
    },
    "NEW_SINGLE_MEMBER_COMMUNITY": {
        "display_name": "同一房屋：启动第二次整体翻修",
        "input_scene": {
            "content": "用户计划在入住五年后重新翻修杭州这套房，准备拆除旧柜体并调整全屋动线。",
            "display_label": "居住与日常事务 · 文本",
        },
        "pipeline": [
            {"stage_name": "价值判断", "status": "COMPLETED", "result_name": "有社区价值"},
            {"stage_name": "记忆分类", "status": "COMPLETED", "result_name": "居住与日常事务"},
            {"stage_name": "归属判断", "status": "COMPLETED", "result_name": "新建社区"},
            {"stage_name": "摘要更新", "status": "SKIPPED", "result_name": "跳过：单成员"},
        ],
        "classification_result": {
            "result": "CREATE_NEW",
            "title": "新建独立社区",
            "reason_label": "同一对象，不同事项轮次",
            "reason": "不能因为房屋相同就并入首次装修，需要新建杭州新房第二次整体翻修社区。",
        },
        "community_change": {
            "change_label": "新建单成员社区",
            "before": "首次装修社区保持不变。",
            "after": "创建新的单成员社区，暂展示当前记忆摘要；跳过 P3，待第二个成员归入时首次生成稳定边界。",
        },
        "new_content": {
            "node_name": "启动第二次翻修",
            "description": "用户计划在入住五年后重新翻修杭州这套房，准备拆除旧柜体并调整全屋动线。不能因为房屋相同就并入首次装修，需要新建杭州新房第二次整体翻修社区。",
            "joined_community_name": None,
            "created_community_name": "杭州新房第二次整体翻修",
        },
    },
    "SECOND_MEMBER_BOUNDARY": {
        "display_name": "第二条记忆归入：首次生成社区边界",
        "input_scene": {
            "content": "杭州新房首次装修社区当前只有厨房布局这一条记忆。用户补充同一次装修的卫生间防水方案已确认。",
            "display_label": "居住与日常事务 · 文本",
        },
        "pipeline": [
            {"stage_name": "价值判断", "status": "COMPLETED", "result_name": "有社区价值"},
            {"stage_name": "记忆分类", "status": "COMPLETED", "result_name": "居住与日常事务"},
            {"stage_name": "归属判断", "status": "COMPLETED", "result_name": "归入已有"},
            {"stage_name": "摘要更新", "status": "COMPLETED", "result_name": "首次生成边界"},
        ],
        "classification_result": {
            "result": "ASSIGN_EXISTING",
            "title": "归入已有社区",
            "reason_label": "第二个有效成员归入",
            "reason": "新记忆与单成员社区属于同一次装修，归入后首次生成事项名称、范围、稳定边界和摘要。",
        },
        "community_change": {
            "change_label": "首次生成边界与摘要",
            "before": "单成员社区，仅展示厨房布局的记忆摘要。",
            "after": "首次生成名称“杭州新房首次整体装修”、本次整装范围及排除其他房屋和后续轮次的稳定边界；摘要包含厨房布局和已确认的防水方案。",
        },
        "new_content": {
            "node_name": "防水方案已确认",
            "description": "杭州新房首次装修社区当前只有厨房布局这一条记忆。用户补充同一次装修的卫生间防水方案已确认。新记忆与单成员社区属于同一次装修，归入后首次生成事项名称、范围、稳定边界和摘要。",
            "joined_community_name": "杭州新房首次整体装修",
            "created_community_name": None,
        },
    },
    "NOT_ELIGIBLE": {
        "display_name": "无社区价值：你好，早上好",
        "input_scene": {
            "content": "用户说：你好，早上好。",
            "display_label": "未进入分类",
        },
        "pipeline": [
            {"stage_name": "价值判断", "status": "STOPPED", "result_name": "无社区价值"},
            {"stage_name": "记忆分类", "status": "SKIPPED", "result_name": "跳过"},
            {"stage_name": "归属判断", "status": "SKIPPED", "result_name": "跳过"},
            {"stage_name": "摘要更新", "status": "SKIPPED", "result_name": "跳过"},
        ],
        "classification_result": {
            "result": "NOT_ELIGIBLE",
            "title": "不进入社区",
            "reason_label": "价值判断阶段：日常招呼",
            "reason": "没有可持续跟踪的具体事项或可复用经验。",
        },
        "community_change": {
            "change_label": "无社区价值",
            "before": "现有社区保持不变。",
            "after": "保存 SceneSummary，但不设置社区处理状态，不创建或归入社区。",
        },
        "new_content": {
            "node_name": "日常问候",
            "description": "用户说：你好，早上好。没有可持续跟踪的具体事项或可复用经验，因此不进入社区图谱。",
            "joined_community_name": None,
            "created_community_name": None,
        },
    },
}


PREVIEW_CASES_EN = (
    {
        "preview_case": "EXISTING_COMMUNITY_UPDATE",
        "display_name": "Same renovation: add kitchen details",
        "sort_order": 1,
        "is_default": True,
    },
    {
        "preview_case": "NEW_SINGLE_MEMBER_COMMUNITY",
        "display_name": "Same home: start a second full renovation",
        "sort_order": 2,
        "is_default": False,
    },
    {
        "preview_case": "SECOND_MEMBER_BOUNDARY",
        "display_name": "Second memory joins: establish the community boundary",
        "sort_order": 3,
        "is_default": False,
    },
    {
        "preview_case": "NOT_ELIGIBLE",
        "display_name": "No community value: hello, good morning",
        "sort_order": 4,
        "is_default": False,
    },
)


CANDIDATE_COMMUNITIES_EN = (
    {
        "rank": 1,
        "title": "First Full Renovation of the Hangzhou New Home",
        "subtitle": "Same home · First full renovation",
        "score": 0.94,
        "child_names": [
            "L-shaped kitchen layout",
            "White cabinetry",
            "Bathroom waterproofing pending confirmation",
        ],
    },
    {
        "rank": 2,
        "title": "Leak Repair After Moving into the Hangzhou New Home",
        "subtitle": "Same home · Separate repair",
        "score": 0.79,
        "child_names": [
            "Kitchen drain leak",
            "Replace the pipe connector",
            "Water-flow inspection completed",
        ],
    },
    {
        "rank": 3,
        "title": "Full Renovation of the Parents' Old Home",
        "subtitle": "Different home · Different matter",
        "score": 0.69,
        "child_names": [
            "Renovate the parents' old home",
            "Keep the existing furniture",
            "Repaint the bedroom walls",
        ],
    },
    {
        "rank": 4,
        "title": "Handover Inspection for the Hangzhou New Home",
        "subtitle": "Same home · Handover inspection",
        "score": 0.61,
        "child_names": [
            "Schedule the handover inspection",
            "Check door and window seals",
            "Plumbing and electrical inspection passed",
        ],
    },
    {
        "rank": 5,
        "title": "Temporary Rental Arrangement in Hangzhou",
        "subtitle": "Different residence · Separate matter",
        "score": 0.54,
        "child_names": [
            "Three-month short-term rental",
            "Near the workplace",
            "Independent kitchen required",
        ],
    },
    {
        "rank": 6,
        "title": "Moving and Placement of Old Furniture",
        "subtitle": "Related items · Separate placement",
        "score": 0.46,
        "child_names": [
            "Move old books to the new home",
            "Store the sofa in a warehouse",
            "Keep the bookshelf",
        ],
    },
    {
        "rank": 7,
        "title": "Moving Company Booking",
        "subtitle": "Related arrangement · Separate service",
        "score": 0.38,
        "child_names": [
            "Book a weekend move",
            "Pack fragile items separately",
            "Reserve the elevator in advance",
        ],
    },
    {
        "rank": 8,
        "title": "Housing Document Processing in Hangzhou",
        "subtitle": "Same home · Document processing",
        "score": 0.30,
        "child_names": [
            "Prepare housing documents",
            "Schedule an appointment",
            "Collect the processing receipt",
        ],
    },
)


PREVIEW_CASE_DATA_EN = {
    "EXISTING_COMMUNITY_UPDATE": {
        "display_name": "Same renovation: add kitchen details",
        "input_scene": {
            "content": (
                "The user continues discussing the first full renovation of the "
                "Hangzhou new home, switches the kitchen countertop from sintered "
                "stone to quartz, and confirms that the budget is unchanged."
            ),
            "display_label": "Living and daily affairs · Text",
        },
        "pipeline": [
            {"stage_name": "Value assessment", "status": "COMPLETED", "result_name": "Community-worthy"},
            {"stage_name": "Memory classification", "status": "COMPLETED", "result_name": "Living and daily affairs"},
            {"stage_name": "Assignment decision", "status": "COMPLETED", "result_name": "Assign to existing"},
            {"stage_name": "Summary update", "status": "COMPLETED", "result_name": "Update summary"},
        ],
        "classification_result": {
            "result": "ASSIGN_EXISTING",
            "title": "Assign to an existing community",
            "reason_label": "Same matter, unchanged scope",
            "reason": (
                "The property, renovation round, and primary goal are the same, so "
                "this memory belongs to the existing full-renovation community. "
                "Changing the candidate limit does not force a different decision."
            ),
        },
        "community_change": {
            "change_label": "Update summary only",
            "before": "The L-shaped kitchen layout is confirmed; the bathroom waterproofing plan is pending confirmation.",
            "after": (
                "The L-shaped kitchen layout and quartz countertop are confirmed; "
                "the bathroom waterproofing plan is pending confirmation. The stable "
                "name, scope, and boundary remain unchanged."
            ),
        },
        "new_content": {
            "node_name": "Switch to a quartz countertop",
            "description": (
                "The user continues discussing the first full renovation of the "
                "Hangzhou new home, switches the kitchen countertop from sintered "
                "stone to quartz, and confirms that the budget is unchanged. The "
                "property, renovation round, and goal are the same, so this memory "
                "joins the existing full-renovation community. Changing the candidate "
                "limit does not force a different decision."
            ),
            "joined_community_name": "First Full Renovation of the Hangzhou New Home",
            "created_community_name": None,
        },
    },
    "NEW_SINGLE_MEMBER_COMMUNITY": {
        "display_name": "Same home: start a second full renovation",
        "input_scene": {
            "content": (
                "The user plans to renovate the Hangzhou home again five years after "
                "moving in, removing the old cabinetry and redesigning the circulation."
            ),
            "display_label": "Living and daily affairs · Text",
        },
        "pipeline": [
            {"stage_name": "Value assessment", "status": "COMPLETED", "result_name": "Community-worthy"},
            {"stage_name": "Memory classification", "status": "COMPLETED", "result_name": "Living and daily affairs"},
            {"stage_name": "Assignment decision", "status": "COMPLETED", "result_name": "Create new community"},
            {"stage_name": "Summary update", "status": "SKIPPED", "result_name": "Skipped: single member"},
        ],
        "classification_result": {
            "result": "CREATE_NEW",
            "title": "Create an independent community",
            "reason_label": "Same object, different matter round",
            "reason": (
                "Sharing the same home is not enough to merge this memory into the "
                "first renovation. A second-renovation community must be created."
            ),
        },
        "community_change": {
            "change_label": "Create a single-member community",
            "before": "The first-renovation community remains unchanged.",
            "after": (
                "A new single-member community is created and temporarily displays "
                "the current memory summary. P3 is skipped until a second member "
                "joins and establishes the stable boundary."
            ),
        },
        "new_content": {
            "node_name": "Start the second renovation",
            "description": (
                "The user plans to renovate the Hangzhou home again five years after "
                "moving in, removing the old cabinetry and redesigning the circulation. "
                "Sharing the same home is not enough to merge this memory into the first "
                "renovation, so a second-renovation community is created."
            ),
            "joined_community_name": None,
            "created_community_name": "Second Full Renovation of the Hangzhou New Home",
        },
    },
    "SECOND_MEMBER_BOUNDARY": {
        "display_name": "Second memory joins: establish the community boundary",
        "input_scene": {
            "content": (
                "The first-renovation community for the Hangzhou new home currently "
                "contains only the kitchen-layout memory. The user adds that the "
                "bathroom waterproofing plan for the same renovation is confirmed."
            ),
            "display_label": "Living and daily affairs · Text",
        },
        "pipeline": [
            {"stage_name": "Value assessment", "status": "COMPLETED", "result_name": "Community-worthy"},
            {"stage_name": "Memory classification", "status": "COMPLETED", "result_name": "Living and daily affairs"},
            {"stage_name": "Assignment decision", "status": "COMPLETED", "result_name": "Assign to existing"},
            {"stage_name": "Summary update", "status": "COMPLETED", "result_name": "Establish boundary"},
        ],
        "classification_result": {
            "result": "ASSIGN_EXISTING",
            "title": "Assign to an existing community",
            "reason_label": "Second valid member joins",
            "reason": (
                "The new memory and the single-member community belong to the same "
                "renovation. After it joins, the matter name, scope, stable boundary, "
                "and summary are generated for the first time."
            ),
        },
        "community_change": {
            "change_label": "Establish boundary and summary",
            "before": "The single-member community displays only the kitchen-layout memory summary.",
            "after": (
                "The name 'First Full Renovation of the Hangzhou New Home', its scope, "
                "and a stable boundary excluding other homes and later renovation rounds "
                "are generated. The summary includes the kitchen layout and confirmed "
                "waterproofing plan."
            ),
        },
        "new_content": {
            "node_name": "Waterproofing plan confirmed",
            "description": (
                "The first-renovation community currently contains only the kitchen-layout "
                "memory. The user adds the confirmed bathroom waterproofing plan from the "
                "same renovation. After this second valid member joins, the matter name, "
                "scope, stable boundary, and summary are generated for the first time."
            ),
            "joined_community_name": "First Full Renovation of the Hangzhou New Home",
            "created_community_name": None,
        },
    },
    "NOT_ELIGIBLE": {
        "display_name": "No community value: hello, good morning",
        "input_scene": {
            "content": "The user says: Hello, good morning.",
            "display_label": "Unclassified",
        },
        "pipeline": [
            {"stage_name": "Value assessment", "status": "STOPPED", "result_name": "No community value"},
            {"stage_name": "Memory classification", "status": "SKIPPED", "result_name": "Skipped"},
            {"stage_name": "Assignment decision", "status": "SKIPPED", "result_name": "Skipped"},
            {"stage_name": "Summary update", "status": "SKIPPED", "result_name": "Skipped"},
        ],
        "classification_result": {
            "result": "NOT_ELIGIBLE",
            "title": "Do not enter a community",
            "reason_label": "Value assessment: casual greeting",
            "reason": "There is no concrete matter to track or reusable experience.",
        },
        "community_change": {
            "change_label": "No community value",
            "before": "Existing communities remain unchanged.",
            "after": (
                "The SceneSummary is saved without a community-processing status and "
                "does not create or join a community."
            ),
        },
        "new_content": {
            "node_name": "Casual greeting",
            "description": (
                "The user says: Hello, good morning. There is no concrete matter to "
                "track or reusable experience, so this memory does not enter the "
                "community graph."
            ),
            "joined_community_name": None,
            "created_community_name": None,
        },
    },
}


PREVIEW_CASES_BY_LOCALE = {
    "zh": PREVIEW_CASES,
    "en": PREVIEW_CASES_EN,
}

CANDIDATE_COMMUNITIES_BY_LOCALE = {
    "zh": CANDIDATE_COMMUNITIES,
    "en": CANDIDATE_COMMUNITIES_EN,
}

PREVIEW_CASE_DATA_BY_LOCALE = {
    "zh": PREVIEW_CASE_DATA,
    "en": PREVIEW_CASE_DATA_EN,
}

PREVIEW_CANDIDATE_TEXT_BY_LOCALE = {
    "zh": {
        "with_candidates": (
            "候选分数仅为模拟排序。数量调整会改变比较范围；"
            "同一次事项的判断仍以对象、轮次与目标为准。"
        ),
        "not_eligible": "调整候选数量不会绕过价值判断。",
        "count_template": "实际展示 {actual} / 上限 {limit} 个",
        "not_queried": "未执行候选查询",
    },
    "en": {
        "with_candidates": (
            "Candidate scores are for simulated ranking only. Adjusting the "
            "candidate count changes the comparison scope; whether a memory belongs "
            "to the same matter is still determined by the object, round, and goal."
        ),
        "not_eligible": (
            "Adjusting the candidate count does not bypass the value assessment."
        ),
        "count_template": "Showing {actual} / limit {limit}",
        "not_queried": "Candidate query was not executed",
    },
}

PREVIEW_GRAPH_TEXT_BY_LOCALE = {
    "zh": {
        "caption_template": "居住与日常事务 · {count} 个社区 · 圈内为局部模拟记忆；箭头表示“包含记忆”。",
        "empty_text": "当前示例在价值判断阶段结束，后续步骤均跳过。",
        "empty_caption": "未进入分类 · 0 个社区 · 当前记忆未进入社区图谱。",
        "second_member_child_names": ["厨房 L 型布局", "防水方案已确认"],
    },
    "en": {
        "caption_template": (
            "Living and daily affairs · {count} communities · Circles show local "
            "simulated memories; arrows mean ‘contains memory’."
        ),
        "empty_text": "This example ends at value assessment; all later stages are skipped.",
        "empty_caption": "Unclassified · 0 communities · This memory is not included in the community graph.",
        "second_member_child_names": [
            "L-shaped kitchen layout",
            "Waterproofing plan confirmed",
        ],
    },
}
