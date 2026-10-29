"""Agent Middleware - 动态技能过滤"""
import uuid
from typing import List, Dict, Any, Optional
from langchain_core.runnables import RunnablePassthrough

from ..repositories.skill_package_repository import SkillPackageRepository
from ..services.skill_service import SkillService
from ..repositories.skill_repository import SkillRepository
from .skill_runtime_service import (
    build_skill_index,
    create_read_skill_tool,
    skill_index_triple,
)


def _inject_script_tool(
    tools_dict: Dict[str, Any],
    tenant_id: uuid.UUID,
    runtime_context: Optional[Dict[str, Any]] = None,
) -> None:
    """条件注入 ``run_skill_script``（§8.6）。

    与 ``read_skill`` 同一注入条件：只有会话加载集合里存在 standard 技能时才给，
    这样普通对话不会因为多带一个沙箱工具而误导模型去分配实例。运行上下文
    （user_id / workspace_id / conversation_id）先注入，缺 conversation_id 时
    后续消费端（app_chat / draft_run）还会再补一次。
    """

    from ..tools.builtin.skill_script_tool import create_run_skill_script_tool

    if 'run_skill_script' in tools_dict:
        return
    context = runtime_context or {}
    tool = create_run_skill_script_tool(
        tenant_id,
        user_id=context.get("user_id"),
        workspace_id=context.get("workspace_id"),
        conversation_id=context.get("conversation_id"),
    )
    if tool is not None:
        tools_dict['run_skill_script'] = tool


class AgentMiddleware:
    """Agent 中间件 - 用于动态过滤和加载技能"""
    
    def __init__(self, skills: Optional[dict] = None):
        """
        初始化中间件

        Args:
            skills: 技能配置字典。只认 ``skill_ids``：非空即启用这些技能，空列表/缺省
                    表示不挂任何技能。历史字段 ``enabled`` / ``all_skills`` 已废弃并被
                    完全忽略（旧数据无需迁移）。
        """
        self.skills = skills or {}
        self.skill_ids = self.skills.get('skill_ids', []) or []
        self._standard_skill_ids: List[str] = []  # standard 格式技能（Agent Skills 协议）

    @staticmethod
    def filter_tools(
        tools: List, 
        message: str = "", 
        skill_configs: Dict[str, Any] = None,
        tool_to_skill_map: Dict[str, str] = None
    ) -> tuple[List, List[str]]:
        """
        根据消息内容和技能配置动态过滤工具
        
        Args:
            tools: 所有可用工具列表
            message: 用户消息（可用于智能过滤）
            skill_configs: 技能配置字典 {skill_id: {"keywords": [...], "enabled": True, "prompt": "..."}}
            tool_to_skill_map: 工具到技能的映射 {tool_name: skill_id}
        
        Returns:
            (过滤后的工具列表, 激活的技能ID列表)
        """
        if not tools:
            return [], []
        
        # 如果没有技能配置，返回所有工具
        if not skill_configs:
            return tools, []
        
        # 基于关键词匹配激活技能
        activated_skill_ids = []
        message_lower = message.lower()
        
        for skill_id, config in skill_configs.items():
            if not config.get('enabled', True):
                continue
            
            keywords = config.get('keywords', [])
            # 如果没有关键词限制，或消息包含关键词，则激活该技能
            if not keywords or any(kw.lower() in message_lower for kw in keywords):
                activated_skill_ids.append(skill_id)
        
        # 如果没有工具映射关系，返回所有工具
        if not tool_to_skill_map:
            return tools, activated_skill_ids
        
        # 根据激活的技能过滤工具
        filtered_tools = []
        for tool in tools:
            tool_name = getattr(tool, 'name', str(id(tool)))
            # 如果工具不属于任何skill（base_tools），或者工具所属的skill被激活，则保留
            if tool_name not in tool_to_skill_map or tool_to_skill_map[tool_name] in activated_skill_ids:
                filtered_tools.append(tool)
        
        return filtered_tools, activated_skill_ids
    
    def load_skill_tools(
        self,
        db,
        tenant_id: uuid.UUID,
        base_tools: List = None,
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> tuple[List, Dict[str, Any], Dict[str, str], str]:
        """
        加载技能关联的工具

        Args:
            db: 数据库会话
            tenant_id: 租户id
            base_tools: 基础工具列表
            runtime_context: 运行时上下文, 含非业务参数

        Returns:
            (工具列表, 技能配置字典, 工具到技能的映射 {tool_name: skill_id}, standard 技能 discovery 索引段)
        """

        tools_dict = {}
        tool_to_skill_map = {}  # 工具名称到技能ID的映射
        
        if base_tools:
            for tool in base_tools:
                tool_name = getattr(tool, 'name', str(id(tool)))
                tools_dict[tool_name] = tool
                # base_tools 不属于任何 skill，不加入映射

        skill_configs = {}
        skill_ids_to_load = list(self.skill_ids)

        if skill_ids_to_load:
            for skill_id in skill_ids_to_load:
                try:
                    skill = SkillRepository(db).get_by_id(uuid.UUID(skill_id), tenant_id)
                    if skill and skill.is_active:
                        # standard 技能：不拼 prompt/keywords，记录进 standard 索引（discovery 层）
                        if (skill.skill_format or "legacy") == "standard" and skill.package_id:
                            if skill_id not in self._standard_skill_ids:
                                self._standard_skill_ids.append(skill_id)
                            continue
                        # 保存技能配置（包含prompt）
                        config = skill.config or {}
                        config['prompt'] = skill.prompt
                        config['name'] = skill.name
                        skill_configs[skill_id] = config
                except Exception:
                    continue
            
            # 加载技能工具并获取映射关系
            skill_tools, skill_tool_map = SkillService.load_skill_tools(
                db,
                skill_ids_to_load,
                tenant_id,
                runtime_context=runtime_context,
            )
            
            # 只添加不冲突的 skill_tools
            for tool in skill_tools:
                tool_name = getattr(tool, 'name', str(id(tool)))
                if tool_name not in tools_dict:
                    tools_dict[tool_name] = tool
                    # 复制映射关系
                    if tool_name in skill_tool_map:
                        tool_to_skill_map[tool_name] = skill_tool_map[tool_name]

        # standard 技能（Agent Skills 协议）：注入 read_skill 工具 + discovery 索引
        standard_index = ""
        if self._standard_skill_ids:
            package_repo = SkillPackageRepository(db)
            standard_skills = []
            for sid in self._standard_skill_ids:
                try:
                    skill = SkillRepository(db).get_by_id(uuid.UUID(sid), tenant_id)
                    if skill and skill.package_id:
                        pkg = package_repo.get_by_id(skill.package_id, tenant_id)
                        if pkg:
                            standard_skills.append(skill_index_triple(skill, pkg))
                except Exception:
                    continue
            if standard_skills:
                standard_index = build_skill_index(standard_skills)
                if 'read_skill' not in tools_dict:
                    tools_dict['read_skill'] = create_read_skill_tool(tenant_id)
                _inject_script_tool(tools_dict, tenant_id, runtime_context)

        return list(tools_dict.values()), skill_configs, tool_to_skill_map, standard_index

    async def load_skill_tools_async(
        self,
        db,
        tenant_id: uuid.UUID,
        base_tools: List = None,
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> tuple[List, Dict[str, Any], Dict[str, str], str]:
        """异步加载技能关联的工具（返回末位多一个 standard_index：标准技能 discovery 索引段）"""
        tools_dict = {}
        tool_to_skill_map = {}

        if base_tools:
            for tool in base_tools:
                tool_name = getattr(tool, 'name', str(id(tool)))
                tools_dict[tool_name] = tool

        skill_configs = {}
        skill_ids_to_load = list(self.skill_ids)

        if skill_ids_to_load:
            for skill_id in skill_ids_to_load:
                try:
                    skill = await SkillRepository.get_by_id_async(db, uuid.UUID(skill_id), tenant_id)
                    if skill and skill.is_active:
                        # standard 技能：不拼 prompt/keywords，记录进 standard 索引（discovery 层）
                        if (skill.skill_format or "legacy") == "standard" and skill.package_id:
                            if skill_id not in self._standard_skill_ids:
                                self._standard_skill_ids.append(skill_id)
                            continue
                        config = skill.config or {}
                        config['prompt'] = skill.prompt
                        config['name'] = skill.name
                        skill_configs[skill_id] = config
                except Exception:
                    continue

            skill_tools, skill_tool_map = await SkillService.load_skill_tools_async(
                db,
                skill_ids_to_load,
                tenant_id,
                runtime_context=runtime_context,
            )

            for tool in skill_tools:
                tool_name = getattr(tool, 'name', str(id(tool)))
                if tool_name not in tools_dict:
                    tools_dict[tool_name] = tool
                    if tool_name in skill_tool_map:
                        tool_to_skill_map[tool_name] = skill_tool_map[tool_name]

        # standard 技能（Agent Skills 协议）：注入 read_skill 工具 + discovery 索引
        standard_index = ""
        if self._standard_skill_ids:
            from sqlalchemy import select
            from ..models.skill_model import Skill as SkillModel
            from ..models.skill_package_model import SkillPackage
            result = await db.execute(
                select(SkillModel, SkillPackage)
                .join(SkillPackage, SkillModel.package_id == SkillPackage.id)
                .where(
                    SkillModel.tenant_id == tenant_id,
                    SkillModel.is_active.is_(True),
                    SkillModel.skill_format == "standard"),
            )
            allowed_ids = set(self._standard_skill_ids)
            standard_skills = [
                skill_index_triple(s, pkg)
                for s, pkg in result.all()
                if str(s.id) in allowed_ids
            ]
            if standard_skills:
                standard_index = build_skill_index(standard_skills)
                if 'read_skill' not in tools_dict:
                    tools_dict['read_skill'] = create_read_skill_tool(tenant_id)
                _inject_script_tool(tools_dict, tenant_id, runtime_context)

        return list(tools_dict.values()), skill_configs, tool_to_skill_map, standard_index

    @staticmethod
    def get_active_prompts(activated_skill_ids: List[str], skill_configs: Dict[str, Any]) -> str:
        """
        根据激活的技能ID获取对应的提示词
        
        Args:
            activated_skill_ids: 被激活的技能ID列表
            skill_configs: 技能配置字典
        
        Returns:
            合并后的提示词
        """
        prompts = []
        for skill_id in activated_skill_ids:
            config = skill_configs.get(skill_id, {})
            prompt = config.get('prompt')
            name = config.get('name', 'Skill')
            if prompt:
                prompts.append(f"# {name}\n{prompt}")
        
        return "\n\n".join(prompts) if prompts else ""

    @staticmethod
    def create_runnable():
        """创建可运行的中间件"""
        return RunnablePassthrough()