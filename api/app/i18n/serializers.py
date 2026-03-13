"""
国际化响应序列化器

提供基础的 I18nResponseMixin 类，用于为 API 响应添加国际化字段。
"""

from typing import Any, Dict, List, Union
from pydantic import BaseModel


class I18nResponseMixin:
    """国际化响应混入类
    
    为响应数据添加国际化字段，特别是为枚举值添加 _display 后缀的翻译字段。
    
    使用方法：
        1. 继承此类
        2. 实现 _get_enum_fields() 方法定义需要翻译的枚举字段
        3. 调用 serialize_with_i18n() 方法序列化数据
    
    示例：
        class WorkspaceSerializer(I18nResponseMixin):
            def _get_enum_fields(self) -> Dict[str, str]:
                return {
                    "role": "workspace_role",
                    "status": "workspace_status"
                }
            
            def serialize(self, workspace: Workspace, locale: str = "zh") -> Dict:
                data = {
                    "id": str(workspace.id),
                    "name": workspace.name,
                    "role": workspace.role,
                    "status": workspace.status
                }
                return self.serialize_with_i18n(data, locale)
    """
    
    def serialize_with_i18n(
        self, 
        data: Any, 
        locale: str = "zh"
    ) -> Union[Dict, List[Dict], Any]:
        """序列化数据并添加国际化字段
        
        Args:
            data: 要序列化的数据（字典、列表或 Pydantic 模型）
            locale: 语言代码
            
        Returns:
            序列化后的数据，包含国际化字段
        """
        # 如果是 Pydantic 模型，转换为字典
        if isinstance(data, BaseModel):
            data = data.model_dump()
        
        # 处理不同类型的数据
        if isinstance(data, dict):
            return self._serialize_dict(data, locale)
        elif isinstance(data, list):
            return [self._serialize_dict(item, locale) if isinstance(item, dict) else item for item in data]
        else:
            return data
    
    def _serialize_dict(self, data: Dict, locale: str) -> Dict:
        """序列化字典并添加 _display 字段
        
        Args:
            data: 字典数据
            locale: 语言代码
            
        Returns:
            添加了 _display 字段的字典
        """
        from app.i18n.service import translation_service
        
        result = data.copy()
        
        # 获取需要翻译的枚举字段
        enum_fields = self._get_enum_fields()
        
        # 为每个枚举字段添加 _display 字段
        for field, enum_type in enum_fields.items():
            if field in result and result[field] is not None:
                value = result[field]
                # 翻译枚举值
                display_value = translation_service.translate_enum(
                    enum_type=enum_type,
                    value=str(value),
                    locale=locale
                )
                # 添加 _display 字段
                result[f"{field}_display"] = display_value
        
        return result
    
    def _get_enum_fields(self) -> Dict[str, str]:
        """获取需要翻译的枚举字段
        
        子类必须实现此方法，返回字段名到枚举类型的映射。
        
        Returns:
            字段名到枚举类型的映射
            例如: {"role": "workspace_role", "status": "workspace_status"}
        """
        return {}


class WorkspaceSerializer(I18nResponseMixin):
    """工作空间序列化器
    
    为工作空间响应添加国际化字段。
    """
    
    def _get_enum_fields(self) -> Dict[str, str]:
        """定义工作空间的枚举字段"""
        return {
            "role": "workspace_role",
            "status": "workspace_status"
        }
    
    def serialize(self, workspace_data: Union[Dict, BaseModel], locale: str = "zh") -> Dict:
        """序列化工作空间数据
        
        Args:
            workspace_data: 工作空间数据（字典或 Pydantic 模型）
            locale: 语言代码
            
        Returns:
            序列化后的工作空间数据，包含国际化字段
        """
        return self.serialize_with_i18n(workspace_data, locale)
    
    def serialize_list(self, workspaces: List[Union[Dict, BaseModel]], locale: str = "zh") -> List[Dict]:
        """序列化工作空间列表
        
        Args:
            workspaces: 工作空间列表
            locale: 语言代码
            
        Returns:
            序列化后的工作空间列表
        """
        return [self.serialize(ws, locale) for ws in workspaces]


class WorkspaceMemberSerializer(I18nResponseMixin):
    """工作空间成员序列化器
    
    为工作空间成员响应添加国际化字段。
    """
    
    def _get_enum_fields(self) -> Dict[str, str]:
        """定义工作空间成员的枚举字段"""
        return {
            "role": "workspace_role"
        }
    
    def serialize(self, member_data: Union[Dict, BaseModel], locale: str = "zh") -> Dict:
        """序列化工作空间成员数据
        
        Args:
            member_data: 成员数据（字典或 Pydantic 模型）
            locale: 语言代码
            
        Returns:
            序列化后的成员数据，包含国际化字段
        """
        return self.serialize_with_i18n(member_data, locale)
    
    def serialize_list(self, members: List[Union[Dict, BaseModel]], locale: str = "zh") -> List[Dict]:
        """序列化工作空间成员列表
        
        Args:
            members: 成员列表
            locale: 语言代码
            
        Returns:
            序列化后的成员列表
        """
        return [self.serialize(member, locale) for member in members]


class WorkspaceInviteSerializer(I18nResponseMixin):
    """工作空间邀请序列化器
    
    为工作空间邀请响应添加国际化字段。
    """
    
    def _get_enum_fields(self) -> Dict[str, str]:
        """定义工作空间邀请的枚举字段"""
        return {
            "status": "invite_status",
            "role": "workspace_role"
        }
    
    def serialize(self, invite_data: Union[Dict, BaseModel], locale: str = "zh") -> Dict:
        """序列化工作空间邀请数据
        
        Args:
            invite_data: 邀请数据（字典或 Pydantic 模型）
            locale: 语言代码
            
        Returns:
            序列化后的邀请数据，包含国际化字段
        """
        return self.serialize_with_i18n(invite_data, locale)
    
    def serialize_list(self, invites: List[Union[Dict, BaseModel]], locale: str = "zh") -> List[Dict]:
        """序列化工作空间邀请列表
        
        Args:
            invites: 邀请列表
            locale: 语言代码
            
        Returns:
            序列化后的邀请列表
        """
        return [self.serialize(invite, locale) for invite in invites]
