"""Agent Middleware - 动态技能过滤"""
import uuid
from typing import List, Dict, Any, Optional
from langchain_core.runnables import RunnablePassthrough

from app.services.skill_service import SkillService
from app.repositories.skill_repository import SkillRepository


class AgentMiddleware:
    """Agent 中间件 - 用于动态过滤和加载技能"""
    
    def __init__(self, skills: Optional[dict] = None):
        """
        初始化中间件
        
        Args:
            skills: 技能配置字典 {"enabled": bool, "all_skills": bool, "skill_ids": [...]}
        """
        self.skills = skills or {}
        self.enabled = self.skills.get('enabled', False)
        self.all_skills = self.skills.get('all_skills', False)
        self.skill_ids = self.skills.get('skill_ids', [])

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
    
    def load_skill_tools(self, db, tenant_id: uuid.UUID, base_tools: List = None) -> tuple[List, Dict[str, Any], Dict[str, str]]:
        """
        加载技能关联的工具
        
        Args:
            db: 数据库会话
            tenant_id: 租户id
            base_tools: 基础工具列表
        
        Returns:
            (工具列表, 技能配置字典, 工具到技能的映射 {tool_name: skill_id})
        """

        tools_dict = {}
        tool_to_skill_map = {}  # 工具名称到技能ID的映射
        
        if base_tools:
            for tool in base_tools:
                tool_name = getattr(tool, 'name', str(id(tool)))
                tools_dict[tool_name] = tool
                # base_tools 不属于任何 skill，不加入映射

        skill_configs = {}
        skill_ids_to_load = []
        
        # 如果启用技能且 all_skills 为 True，加载租户下所有激活的技能
        if self.enabled and self.all_skills:
            skills, _ = SkillRepository.list_skills(db, tenant_id, is_active=True, page=1, pagesize=1000)
            skill_ids_to_load = [str(skill.id) for skill in skills]
        elif self.enabled and self.skill_ids:
            skill_ids_to_load = self.skill_ids
        
        if skill_ids_to_load:
            for skill_id in skill_ids_to_load:
                try:
                    skill = SkillRepository.get_by_id(db, uuid.UUID(skill_id), tenant_id)
                    if skill and skill.is_active:
                        # 保存技能配置（包含prompt）
                        config = skill.config or {}
                        config['prompt'] = skill.prompt
                        config['name'] = skill.name
                        skill_configs[skill_id] = config
                except Exception:
                    continue
            
            # 加载技能工具并获取映射关系
            skill_tools, skill_tool_map = SkillService.load_skill_tools(db, skill_ids_to_load, tenant_id)
            
            # 只添加不冲突的 skill_tools
            for tool in skill_tools:
                tool_name = getattr(tool, 'name', str(id(tool)))
                if tool_name not in tools_dict:
                    tools_dict[tool_name] = tool
                    # 复制映射关系
                    if tool_name in skill_tool_map:
                        tool_to_skill_map[tool_name] = skill_tool_map[tool_name]
        
        return list(tools_dict.values()), skill_configs, tool_to_skill_map

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
