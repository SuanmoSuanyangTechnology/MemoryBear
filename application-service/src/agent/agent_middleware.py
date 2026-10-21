"""Agent Middleware - dynamic skill filtering"""
import uuid
from typing import List, Dict, Any, Optional
from langchain_core.runnables import RunnablePassthrough

from ..services.skill_service import SkillService
from ..repositories.skill_repository import SkillRepository


class AgentMiddleware:
    """Agent middleware - dynamically filters and loads skills"""
    
    def __init__(self, skills: Optional[dict] = None):
        """
        Initialize the middleware
        
        Args:
            skills: Skill config dict {"enabled": bool, "all_skills": bool, "skill_ids": [...]}
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
        Dynamically filter tools based on the message content and skill configs
        
        Args:
            tools: List of all available tools
            message: User message (usable for smart filtering)
            skill_configs: Skill config dict {skill_id: {"keywords": [...], "enabled": True, "prompt": "..."}}
            tool_to_skill_map: Tool-to-skill map {tool_name: skill_id}
        
        Returns:
            (Filtered tool list, activated skill ID list)
        """
        if not tools:
            return [], []
        
        # No skill configs: return all tools
        if not skill_configs:
            return tools, []
        
        # Activate skills via keyword matching
        activated_skill_ids = []
        message_lower = message.lower()
        
        for skill_id, config in skill_configs.items():
            if not config.get('enabled', True):
                continue
            
            keywords = config.get('keywords', [])
            # Activate the skill when it has no keyword restriction or the message contains a keyword
            if not keywords or any(kw.lower() in message_lower for kw in keywords):
                activated_skill_ids.append(skill_id)
        
        # No tool mapping: return all tools
        if not tool_to_skill_map:
            return tools, activated_skill_ids
        
        # Filter tools by the activated skills
        filtered_tools = []
        for tool in tools:
            tool_name = getattr(tool, 'name', str(id(tool)))
            # Keep the tool if it belongs to no skill (base_tools) or its skill was activated
            if tool_name not in tool_to_skill_map or tool_to_skill_map[tool_name] in activated_skill_ids:
                filtered_tools.append(tool)
        
        return filtered_tools, activated_skill_ids
    
    def load_skill_tools(
        self,
        db,
        tenant_id: uuid.UUID,
        base_tools: List = None,
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> tuple[List, Dict[str, Any], Dict[str, str]]:
        """
        Load tools associated with skills
        
        Args:
            db: Database session
            tenant_id: Tenant id
            base_tools: Base tool list
            runtime_context: Runtime context carrying non-business parameters
        
        Returns:
            (Tool list, skill config dict, tool-to-skill map {tool_name: skill_id})
        """

        tools_dict = {}
        tool_to_skill_map = {}  # Tool name -> skill ID map
        
        if base_tools:
            for tool in base_tools:
                tool_name = getattr(tool, 'name', str(id(tool)))
                tools_dict[tool_name] = tool
                # base_tools belong to no skill, so they are not added to the map

        skill_configs = {}
        skill_ids_to_load = []
        
        # When skills are enabled and all_skills is True, load every active skill of the tenant
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
                        # Save the skill config (including the prompt)
                        config = skill.config or {}
                        config['prompt'] = skill.prompt
                        config['name'] = skill.name
                        skill_configs[skill_id] = config
                except Exception:
                    continue
            
            # Load the skill tools and obtain the mapping
            skill_tools, skill_tool_map = SkillService.load_skill_tools(
                db,
                skill_ids_to_load,
                tenant_id,
                runtime_context=runtime_context,
            )
            
            # Only add non-conflicting skill_tools
            for tool in skill_tools:
                tool_name = getattr(tool, 'name', str(id(tool)))
                if tool_name not in tools_dict:
                    tools_dict[tool_name] = tool
                    # Copy the mapping
                    if tool_name in skill_tool_map:
                        tool_to_skill_map[tool_name] = skill_tool_map[tool_name]
        
        return list(tools_dict.values()), skill_configs, tool_to_skill_map

    async def load_skill_tools_async(
        self,
        db,
        tenant_id: uuid.UUID,
        base_tools: List = None,
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> tuple[List, Dict[str, Any], Dict[str, str]]:
        """Asynchronously load tools associated with skills"""
        tools_dict = {}
        tool_to_skill_map = {}

        if base_tools:
            for tool in base_tools:
                tool_name = getattr(tool, 'name', str(id(tool)))
                tools_dict[tool_name] = tool

        skill_configs = {}
        skill_ids_to_load = []

        if self.enabled and self.all_skills:
            skills, _ = await SkillService.list_skills_async(
                db,
                tenant_id,
                is_active=True,
                page=1,
                pagesize=1000,
            )
            skill_ids_to_load = [str(skill.id) for skill in skills]
        elif self.enabled and self.skill_ids:
            skill_ids_to_load = self.skill_ids

        if skill_ids_to_load:
            for skill_id in skill_ids_to_load:
                try:
                    skill = await SkillRepository.get_by_id_async(db, uuid.UUID(skill_id), tenant_id)
                    if skill and skill.is_active:
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

        return list(tools_dict.values()), skill_configs, tool_to_skill_map

    @staticmethod
    def get_active_prompts(activated_skill_ids: List[str], skill_configs: Dict[str, Any]) -> str:
        """
        Get the prompts for the activated skill IDs

        Args:
            activated_skill_ids: Activated skill ID list
            skill_configs: Skill config dict

        Returns:
            Merged prompts
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
        """Create a runnable middleware"""
        return RunnablePassthrough()
