"""工具管理API控制器"""
import base64
from typing import List, Optional, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, Body
from langfuse.api.core import jsonable_encoder
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field, PositiveInt, field_validator
from cryptography.fernet import Fernet

from app.db import get_db
from app.dependencies import get_current_user
from app.models import User
from app.models.tool_model import ToolConfig, BuiltinToolConfig, ToolType, ToolStatus, CustomToolConfig, MCPToolConfig
from app.core.logging_config import get_business_logger
from app.core.config import settings
from app.core.tools.config_manager import ConfigManager

logger = get_business_logger()

router = APIRouter(prefix="/tools", tags=["工具管理"])


# ==================== 辅助函数 ====================


def _encrypt_sensitive_params(parameters: Dict[str, Any]) -> Dict[str, Any]:
    """加密敏感参数"""
    cipher_key = base64.urlsafe_b64encode(settings.SECRET_KEY[:32].ljust(32, '0').encode())
    cipher = Fernet(cipher_key)
    
    encrypted_params = {}
    sensitive_keys = ['api_key', 'token', 'api_secret', 'password']
    
    for key, value in parameters.items():
        if any(sensitive in key.lower() for sensitive in sensitive_keys) and value:
            encrypted_params[key] = cipher.encrypt(str(value).encode()).decode()
        else:
            encrypted_params[key] = value
    
    return encrypted_params


def _decrypt_sensitive_params(parameters: Dict[str, Any]) -> Dict[str, Any]:
    """解密敏感参数"""
    cipher_key = base64.urlsafe_b64encode(settings.SECRET_KEY[:32].ljust(32, '0').encode())
    cipher = Fernet(cipher_key)
    
    decrypted_params = {}
    sensitive_keys = ['api_key', 'token', 'secret', 'password']
    
    for key, value in parameters.items():
        if any(sensitive in key.lower() for sensitive in sensitive_keys) and value:
            try:
                decrypted_params[key] = cipher.decrypt(value.encode()).decode()
            except Exception as e:
                decrypted_params[key] = value
        else:
            decrypted_params[key] = value
    
    return decrypted_params


def _update_tool_status(tool_config: ToolConfig, builtin_config: BuiltinToolConfig = None, tool_info: Dict = None) -> str:
    """更新工具状态并返回新状态"""
    if tool_config.tool_type == ToolType.BUILTIN:
        if not tool_info or not tool_info.get('requires_config', False):
            new_status = ToolStatus.ACTIVE.value  # 不需要配置的内置工具
        elif not builtin_config or not builtin_config.parameters:
            new_status = ToolStatus.INACTIVE.value
        else:
            # 检查是否有必要的API密钥
            has_key = bool(builtin_config.parameters.get('api_key') or builtin_config.parameters.get('token'))
            new_status = ToolStatus.ACTIVE.value if has_key else ToolStatus.INACTIVE.value
    else:  # 自定义和MCP工具
        new_status = ToolStatus.ACTIVE.value if tool_config.config_data else ToolStatus.ERROR.value
    
    # 更新数据库中的状态
    if tool_config.status != new_status:
        tool_config.status = new_status
    
    return new_status


# ==================== 请求/响应模型 ====================

class ToolListResponse(BaseModel):
    """工具列表响应"""
    id: str
    name: str
    description: str
    tool_type: str
    category: str
    version: str = "1.0.0"
    status: str  # active inactive error loading
    requires_config: bool = False
    # is_configured: bool = False
    
    class Config:
        from_attributes = True

class BuiltinToolConfigRequest(BaseModel):
    """内置工具配置请求"""
    parameters: Dict[str, Any] = Field(default_factory=dict, description="工具参数")


class CustomToolCreateRequest(BaseModel):
    """自定义工具创建请求体模型，包含参数校验规则"""
    name: str = Field(..., min_length=1, max_length=100, description="工具名称，必填")
    description: str = Field(None, description="工具描述")
    base_url: str = Field(None, description="工具基础URL")
    schema_url: str = Field(None, description="工具Schema URL")
    schema_content: Optional[Dict[str, Any]] = Field(None, description="工具Schema内容，可选")
    auth_type: str = Field("none", pattern=r"^(none|api_key|bearer_token)$", description="认证类型")
    auth_config: Optional[Dict[str, Any]] = Field(None, description="认证配置，默认空字典")
    timeout: PositiveInt = Field(30, ge=1, le=300, description="超时时间，1-300秒，默认30")

    # 自定义校验：当auth_type为api_key时，auth_config必须包含api_key字段
    @field_validator("auth_config")
    def validate_auth_config(cls, v, values):
        auth_type = values.data.get("auth_type")
        if auth_type == "api_key" and (not v or "api_key" not in v):
            raise ValueError("认证类型为api_key时，auth_config必须包含api_key字段")
        if auth_type == "bearer_token" and (not v or "bearer_token" not in v):
            raise ValueError("认证类型为bearer_token时，auth_config必须包含bearer_token字段")
        return v

class MCPToolCreateRequest(BaseModel):
    """MCP工具创建请求体模型，适配MCP业务特性"""
    # 基础必填字段（带长度/格式校验）
    name: str = Field(..., min_length=1, max_length=100,description="MCP工具名称")
    description: str = Field(None, description="MCP工具描述")
    # MCP核心字段：服务端URL（强制HTTP/HTTPS格式）
    server_url: str = Field(..., description="MCP服务端URL，仅支持http/https协议")
    # 连接配置：默认空字典，可自定义校验规则（根据实际业务调整）
    connection_config: Dict[str, Any] = Field({},description="MCP连接配置（如认证信息、超时、重试等），默认空字典")

    @field_validator("connection_config")
    def validate_connection_config(cls, v):
        # 示例1：若包含timeout，必须是1-300的整数
        if "timeout" in v:
            timeout = v["timeout"]
            if not isinstance(timeout, int) or timeout < 1 or timeout > 300:
                raise ValueError("connection_config.timeout必须是1-300的整数")
        return v

    # @field_validator("server_url")
    # def validate_server_url_protocol(cls, v):
    #     if v.scheme != "https":
    #         raise ValueError("MCP服务端URL仅支持HTTPS协议（安全要求）")
    #     return v


# ==================== API端点 ====================
@router.get("", response_model=List[ToolListResponse])
async def list_tools(
    name: Optional[str] = None,
    tool_type: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取工具列表（包含内置工具、自定义工具和MCP工具）"""
    try:
        # 初始化内置工具（如果需要）
        config_manager = ConfigManager()
        config_manager.ensure_builtin_tools_initialized(
            current_user.tenant_id, db, ToolConfig, BuiltinToolConfig, ToolType, ToolStatus
        )

        response_tools = []

        query = db.query(ToolConfig).filter(
            ToolConfig.tenant_id == current_user.tenant_id
        )
        if tool_type:
            query = query.filter(ToolConfig.tool_type == tool_type)

        if name:
            query = query.filter(ToolConfig.name.ilike(f"%{name}%"))

        tools = query.all()
        builtin_tools = config_manager.load_builtin_tools_config()
        configured_tools = {tool_info["tool_class"]: tool_info for tool_key, tool_info in builtin_tools.items()}

        for tool_config in tools:
            if tool_config.tool_type == ToolType.BUILTIN.value:
                builtin_config = db.query(BuiltinToolConfig).filter(BuiltinToolConfig.id == tool_config.id).first()
                tool_info = configured_tools.get(builtin_config.tool_class)
                status = _update_tool_status(tool_config, builtin_config, tool_info)
            else:
                status = _update_tool_status(tool_config)

            response_tools.append(ToolListResponse(
                id=str(tool_config.id),
                name=tool_config.name,
                description=tool_config.description,
                tool_type=tool_config.tool_type,
                category=tool_info['category'] if tool_config.tool_type == ToolType.BUILTIN.value else tool_config.tool_type,
                version="1.0.0",
                status=status,
                requires_config=tool_info['requires_config'] if tool_config.tool_type == ToolType.BUILTIN.value else False,
            ))

        return response_tools
    except Exception as e:
        logger.error(f"获取工具列表失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/builtin/{tool_id}")
async def get_builtin_tool_detail(
    tool_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取内置工具详情"""
    try:
        config_manager = ConfigManager()
        builtin_tools = config_manager.load_builtin_tools_config()
        configured_tools = {tool_info["tool_class"]: tool_info for tool_key, tool_info in builtin_tools.items()}
        tool_config = db.query(ToolConfig).filter(
            ToolConfig.tenant_id == current_user.tenant_id,
            ToolConfig.id == tool_id
        ).first()
        builtin_config = db.query(BuiltinToolConfig).filter(BuiltinToolConfig.id == tool_config.id).first()
        tool_info = configured_tools.get(builtin_config.tool_class)

        is_configured = False
        config_parameters = {}
        
        if builtin_config and builtin_config.parameters:
            is_configured = bool(builtin_config.parameters.get('api_key') or builtin_config.parameters.get('token'))
            # 不返回敏感信息，只返回非敏感配置
            config_parameters = {k: v for k, v in builtin_config.parameters.items()
                               if not any(sensitive in k.lower() for sensitive in ['key', 'secret', 'token', 'password'])}
        
        return {
            "id": tool_config.id,
            "name": tool_config.name,
            "description": tool_config.description,
            "category": tool_info['category'],
            "status": tool_config.tool_type,
            "requires_config": tool_info['requires_config'],
            "is_configured": is_configured,
            "config_parameters": config_parameters
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取工具详情失败: {tool_id}, 错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/builtin/{tool_id}/configure")
async def configure_builtin_tool(
    tool_id: str,
    request: BuiltinToolConfigRequest = Body(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """配置内置工具参数（租户级别）"""
    try:
        # 查询工具配置
        tool_config = db.query(ToolConfig).filter(
            ToolConfig.tenant_id == current_user.tenant_id,
            ToolConfig.id == tool_id,
            ToolConfig.tool_type == ToolType.BUILTIN
        ).first()
        
        if not tool_config:
            raise HTTPException(status_code=404, detail="工具不存在")
        
        # 获取内置工具配置
        builtin_config = db.query(BuiltinToolConfig).filter(
            BuiltinToolConfig.id == tool_config.id
        ).first()
        
        if not builtin_config:
            raise HTTPException(status_code=404, detail="内置工具配置不存在")
        
        # 获取全局工具信息
        config_manager = ConfigManager()
        builtin_tools_config = config_manager.load_builtin_tools_config()
        tool_info = None
        for tool_key, info in builtin_tools_config.items():
            if info['tool_class'] == builtin_config.tool_class:
                tool_info = info
                break
        
        if not tool_info:
            raise HTTPException(status_code=404, detail="工具信息不存在")
        
        # 加密敏感参数
        encrypted_params = _encrypt_sensitive_params(request.parameters)
        
        # 更新配置
        builtin_config.parameters = encrypted_params
        
        # 更新状态
        _update_tool_status(tool_config, builtin_config, tool_info)
        
        db.commit()
        
        return {
            "success": True,
            "message": f"工具 {tool_config.name} 配置成功"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"配置内置工具失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/builtin/{tool_id}/config")
async def get_builtin_tool_config(
    tool_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取内置工具配置（用于使用）"""
    try:
        # 查询工具配置
        tool_config = db.query(ToolConfig).filter(
            ToolConfig.tenant_id == current_user.tenant_id,
            ToolConfig.id == tool_id,
            ToolConfig.tool_type == ToolType.BUILTIN
        ).first()
        
        if not tool_config:
            raise HTTPException(status_code=404, detail="工具不存在")
        
        # 获取内置工具配置
        builtin_config = db.query(BuiltinToolConfig).filter(
            BuiltinToolConfig.id == tool_config.id
        ).first()
        
        if not builtin_config:
            raise HTTPException(status_code=404, detail="内置工具配置不存在")
        
        # 解密参数
        decrypted_params = _decrypt_sensitive_params(builtin_config.parameters or {})
        
        return {
            "tool_id": tool_id,
            "tool_class": builtin_config.tool_class,
            "name": tool_config.name,
            "parameters": decrypted_params,
            "status": tool_config.status
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取工具配置失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/custom")
async def create_custom_tool(
    request: CustomToolCreateRequest = Body(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """创建自定义工具"""
    try:
        config_data = jsonable_encoder(request.model_dump())
        config_data["tool_type"] = "custom"

        config_manager = ConfigManager()
        is_valid, error_msg = config_manager.validate_config(config_data, "custom")
        if not is_valid:
            raise HTTPException(status_code=400, detail=error_msg)

        # 创建数据库记录
        tool_config = ToolConfig(
            name=request.name,
            description=request.description,
            tool_type=ToolType.CUSTOM,
            tenant_id=current_user.tenant_id,
            status=ToolStatus.ACTIVE.value,
            config_data=config_data
        )
        db.add(tool_config)
        db.flush()

        # 创建CustomToolConfig记录
        custom_config = CustomToolConfig(
            id=tool_config.id,
            base_url=request.base_url,
            schema_url=request.schema_url,
            schema_content=request.schema_content,
            auth_type=request.auth_type,
            auth_config=request.auth_config,
            timeout=request.timeout
        )
        db.add(custom_config)

        db.commit()

        return {
            "success": True,
            "message": f"自定义工具 {request.name} 创建成功",
            "tool_id": str(tool_config.id)
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"创建自定义工具失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/mcp")
async def create_mcp_tool(
    request: MCPToolCreateRequest = Body(..., description="MCP工具创建参数"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """创建MCP工具"""
    try:
        config_data = jsonable_encoder(request.model_dump())
        config_data["tool_type"] = "mcp"

        config_manager = ConfigManager()
        is_valid, error_msg = config_manager.validate_config(config_data, "mcp")
        if not is_valid:
            raise HTTPException(status_code=400, detail=error_msg)

        # 创建数据库记录
        try:
            tool_config = ToolConfig(
                name=request.name,
                description=request.description,
                tool_type=ToolType.MCP,
                tenant_id=current_user.tenant_id,
                status=ToolStatus.ACTIVE.value,
                config_data=config_data
            )
            db.add(tool_config)
            db.flush()

            # 创建MCPToolConfig记录
            mcp_config = MCPToolConfig(
                id=tool_config.id,
                server_url=request.server_url,
                connection_config=request.connection_config
            )
            db.add(mcp_config)

            db.commit()
        except SQLAlchemyError as db_e:
            db.rollback()
            logger.error(f"创建MCP工具数据库操作失败（租户ID：{current_user.tenant_id}，工具名：{request.name}）: {str(db_e)}",
                exc_info=True)
            raise HTTPException(status_code=500, detail=f"创建MCP工具数据库操作失败（租户ID：{current_user.tenant_id}，"
                                                        f"工具名：{request.name}）：{str(db_e)}")

        return {
            "success": True,
            "message": f"MCP工具 {request.name} 创建成功",
            "tool_id": str(tool_config.id)
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"创建MCP工具失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/{tool_id}")
async def delete_tool(
    tool_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """删除工具（仅限自定义和MCP工具）"""
    try:
        tool = db.query(ToolConfig).filter(
            ToolConfig.id == tool_id,
            ToolConfig.tenant_id == current_user.tenant_id
        ).first()
        
        if not tool:
            raise HTTPException(status_code=404, detail="工具不存在")
        
        if tool.tool_type == ToolType.BUILTIN:
            raise HTTPException(status_code=403, detail="内置工具不允许删除")
        
        db.delete(tool)
        db.commit()
        
        return {
            "success": True,
            "message": f"工具 {tool.name} 删除成功"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除工具失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{tool_id}")
async def update_tool(
    tool_id: str,
    config_data: Optional[Dict[str, Any]] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """更新工具（仅限自定义和MCP工具）"""
    try:
        tool = db.query(ToolConfig).filter(
            ToolConfig.id == tool_id,
            ToolConfig.tenant_id == current_user.tenant_id
        ).first()
        
        if not tool:
            raise HTTPException(status_code=404, detail="工具不存在")
        
        if tool.tool_type == ToolType.BUILTIN:
            raise HTTPException(status_code=403, detail="内置工具不允许修改")
        
        if config_data is not None:
            tool.config_data = config_data
            # 更新状态
            _update_tool_status(tool)
        
        db.commit()
        db.refresh(tool)
        
        return {
            "success": True,
            "message": f"工具 {tool.name} 更新成功",
            "status": tool.status
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新工具失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{tool_id}/toggle")
async def toggle_tool_status(
    tool_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """切换工具活跃/非活跃状态"""
    try:
        tool = db.query(ToolConfig).filter(
            ToolConfig.id == tool_id,
            ToolConfig.tenant_id == current_user.tenant_id
        ).first()
        
        if not tool:
            raise HTTPException(status_code=404, detail="工具不存在")
        
        # 在active和inactive之间切换
        if tool.status == ToolStatus.ACTIVE.value:
            tool.status = ToolStatus.INACTIVE.value
        elif tool.status == ToolStatus.INACTIVE.value:
            tool.status = ToolStatus.ACTIVE.value
        else:
            raise HTTPException(status_code=400, detail="只有可用或非活跃状态的工具可以切换")
        
        db.commit()
        db.refresh(tool)
        
        return {
            "success": True,
            "message": f"工具 {tool.name} 状态已更新为 {tool.status}",
            "status": tool.status
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"切换工具状态失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))