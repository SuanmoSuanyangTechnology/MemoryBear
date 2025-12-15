import sys
import os
import asyncio
from neo4j import GraphDatabase
from typing import List, Tuple
from pydantic import BaseModel, Field

# ------------------- 自包含路径解析 -------------------
# 这个代码块确保脚本可以从任何地方运行，并且仍然可以在项目结构中找到它需要的模块。
try:
    # 假设脚本在 /path/to/project/src/analytics/
    # 上升3个级别以到达项目根目录。
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
    src_path = os.path.join(project_root, 'src')

    # 将 'src' 和 'project_root' 都添加到路径中。
    # 'src' 目录对于像 'from utils.config_utils import ...' 这样的导入是必需的。
    # 'project_root' 目录对于像 'from variate_config import ...' 这样的导入是必需的。
    if src_path not in sys.path:
        sys.path.insert(0, src_path)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
except NameError:
    # 为 __file__ 未定义的环境（例如某些交互式解释器）提供回退方案
    project_root = os.path.abspath(os.path.join(os.getcwd()))
    src_path = os.path.join(project_root, 'src')
    if src_path not in sys.path:
        sys.path.insert(0, src_path)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
# ---------------------------------------------------------------------

# 现在路径已经配置好，我们可以使用绝对导入
from app.core.config import settings
from app.core.memory.utils.config.definitions import SELECTED_GROUP_ID, SELECTED_LLM_ID
from app.core.memory.utils.llm.llm_utils import get_llm_client
import json

# 定义用于LLM结构化输出的Pydantic模型
class FilteredTags(BaseModel):
    """用于接收LLM筛选后的核心标签列表的模型。"""
    meaningful_tags: List[str] = Field(..., description="从原始列表中筛选出的具有核心代表意义的名词列表。")

async def filter_tags_with_llm(tags: List[str], llm_client) -> List[str]:
    """
    使用LLM筛选标签列表，仅保留具有代表性的核心名词。
    """
    try:

        # 3. 构建Prompt
        tag_list_str = ", ".join(tags)
        messages = [
            {
                "role": "system",
                "content": "你是一位顶级的文本分析专家，任务是提炼、筛选并合并最具体、最核心的名词。你的目标是识别具体的事件、地点、物体或作品，并严格执行以下步骤：\n\n1. **筛选**: 严格过滤掉以下类型的词语：\n    *   **抽象概念或训练活动**: 任何描述抽象品质、训练项目或研究过程的词语（例如：'核心力量', '实际的历史研究', '团队合作'）。\n    *   **动作或过程词**: 任何描述具体动作或过程的词语（例如：'打篮球', '快攻', '远投'）。\n    *   **描述性短语**: 任何描述状态、关系或感受的短语（例如：'配合越来越默契'）。\n    *   **过于宽泛的类别**: 过于笼统的分类（例如：'历史剧'）。\n\n2. **合并**: 在筛选后，对语义相近或存在包含关系的词语进行合并，只保留最核心、最具代表性的一个。\n    *   例如，在“篮球赛”和“篮球场”中，“篮球赛”是更核心的事件，应保留“篮球赛”。\n\n你的最终输出应该是一个精炼的、无重复概念的列表，只包含最具体、最具有代表性的名词。\n\n**示例**:\n输入: ['篮球赛', '篮球场', '核心力量', '实际的历史研究', '《二战全史》', '攀岩']\n筛选后: ['篮球赛', '篮球场', '《二战全史》', '攀岩']\n合并后最终输出: ['篮球赛', '《二战全史》', '攀岩']"
            },
            {
                "role": "user",
                "content": f"请从以下标签列表中筛选出核心名词: {tag_list_str}"
            }
        ]

        # 调用LLM进行结构化输出
        structured_response = await llm_client.response_structured(
            messages=messages,
            response_model=FilteredTags
        )

        return structured_response.meaningful_tags

    except Exception as e:
        print(f"LLM筛选过程中发生错误: {e}")
        # 在LLM失败时返回原始标签，确保流程继续
        return tags

def get_db_connection():
    """
    使用项目的标准配置方法建立与Neo4j数据库的连接。
    """
    # 从全局配置获取 Neo4j 连接信息
    uri = settings.NEO4J_URI
    user = settings.NEO4J_USERNAME

    # 密码必须为了安全从环境变量加载
    password = os.getenv("NEO4J_PASSWORD")

    if not uri or not user:
        raise ValueError("在 config.json 中未找到 Neo4j 的 'uri' 或 'username'。")
    if not password:
        raise ValueError("NEO4J_PASSWORD 环境变量未设置。")

    # 为此脚本使用同步驱动
    return GraphDatabase.driver(uri, auth=(user, password))

def get_raw_tags_from_db(group_id: str, limit: int, by_user: bool = False) -> List[Tuple[str, int]]:
    """
    从数据库查询原始的、未经过滤的实体标签及其频率。

    Args:
        group_id: 如果by_user=False，则为group_id；如果by_user=True，则为user_id
        limit: 返回的标签数量限制
        by_user: 是否按user_id查询（默认False，按group_id查询）
    """
    names_to_exclude = ['AI', 'Caroline', 'Melanie', 'Jon', 'Gina', '用户', 'AI助手', 'John', 'Maria']

    if by_user:
        query = (
            "MATCH (e:ExtractedEntity) "
            "WHERE e.user_id = $id AND e.entity_type <> '人物' AND e.name IS NOT NULL AND NOT e.name IN $names_to_exclude "
            "RETURN e.name AS name, count(e) AS frequency "
            "ORDER BY frequency DESC "
            "LIMIT $limit"
        )
    else:
        query = (
            "MATCH (e:ExtractedEntity) "
            "WHERE e.group_id = $id AND e.entity_type <> '人物' AND e.name IS NOT NULL AND NOT e.name IN $names_to_exclude "
            "RETURN e.name AS name, count(e) AS frequency "
            "ORDER BY frequency DESC "
            "LIMIT $limit"
        )

    driver = None
    try:
        driver = get_db_connection()
        with driver.session() as session:
            result = session.run(query, id=group_id, limit=limit, names_to_exclude=names_to_exclude)
            return [(record["name"], record["frequency"]) for record in result]
    finally:
        if driver:
            driver.close()

async def get_hot_memory_tags(group_id: str | None = None, limit: int = 40, by_user: bool = False) -> List[Tuple[str, int]]:
    """
    获取原始标签，然后使用LLM进行筛选，返回最终的热门标签列表。
    查询更多的标签(limit=40)给LLM提供更丰富的上下文进行筛选。

    Args:
        group_id: 如果by_user=False，则为group_id；如果by_user=True，则为user_id
        limit: 返回的标签数量限制
        by_user: 是否按user_id查询（默认False，按group_id查询）
    """
    # 默认从 runtime.json selections.group_id 读取
    group_id = group_id or SELECTED_GROUP_ID
    # 1. 从数据库获取原始排名靠前的标签
    raw_tags_with_freq = get_raw_tags_from_db(group_id, limit, by_user=by_user)
    if not raw_tags_with_freq:
        return []

    raw_tag_names = [tag for tag, freq in raw_tags_with_freq]

    # 2. 初始化LLM客户端并使用LLM筛选出有意义的标签
    from app.core.memory.utils.config import definitions as config_defs
    llm_client = get_llm_client(config_defs.SELECTED_LLM_ID)
    meaningful_tag_names = await filter_tags_with_llm(raw_tag_names, llm_client)

    # 3. 根据LLM的筛选结果，构建最终的标签列表（保留原始频率和顺序）
    final_tags = []
    for tag, freq in raw_tags_with_freq:
        if tag in meaningful_tag_names:
            final_tags.append((tag, freq))

    return final_tags

if __name__ == "__main__":
    print("开始获取热门记忆标签...")
    try:
        # 直接使用 runtime.json 中的 group_id
        group_id_to_query = SELECTED_GROUP_ID
        # 使用 asyncio.run 来执行异步主函数
        top_tags = asyncio.run(get_hot_memory_tags(group_id=group_id_to_query))

        if top_tags:
            print(f"热门记忆标签 (Group ID: {group_id_to_query}, 经LLM筛选):")
            for tag, frequency in top_tags:
                print(f"- {tag} (数量: {frequency})")

            # --- 将结果写入统一的 Signboard.json 到 logs/memory-output ---
            from app.core.config import settings
            settings.ensure_memory_output_dir()
            signboard_path = settings.get_memory_output_path("Signboard.json")
            payload = {
                "group_id": group_id_to_query,
                "hot_tags": [{"name": t, "frequency": f} for t, f in top_tags]
            }
            try:
                existing = {}
                if os.path.exists(signboard_path):
                    with open(signboard_path, "r", encoding="utf-8") as rf:
                        existing = json.load(rf)
                existing["hot_memory_tags"] = payload
                with open(signboard_path, "w", encoding="utf-8") as wf:
                    json.dump(existing, wf, ensure_ascii=False, indent=2)
                print(f"已写入 {signboard_path} -> hot_memory_tags")
            except Exception as e:
                print(f"写入 Signboard.json 失败: {e}")
        else:
            print(f"在 Group ID '{group_id_to_query}' 中没有找到符合条件的实体标签。")
    except Exception as e:
        print(f"执行过程中发生严重错误: {e}")
        print("请检查：")
        print("1. Neo4j数据库服务是否正在运行。")
        print("2. 'config.json'中的配置是否正确。")
        print("3. 相关的环境变量 (如 NEO4J_PASSWORD, DASHSCOPE_API_KEY) 是否已正确设置。")
