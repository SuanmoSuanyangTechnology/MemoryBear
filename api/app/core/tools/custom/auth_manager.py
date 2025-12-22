"""认证管理器 - 处理自定义工具的认证配置"""
import base64
import hashlib
import hmac
import time
from typing import Dict, Any, Tuple
from urllib.parse import quote
import aiohttp

from app.models.tool_model import AuthType
from app.core.logging_config import get_business_logger

logger = get_business_logger()


class AuthManager:
    """认证管理器 - 支持多种认证方式"""
    
    def __init__(self):
        """初始化认证管理器"""
        self.supported_auth_types = [
            AuthType.NONE,
            AuthType.API_KEY,
            AuthType.BEARER_TOKEN
        ]
    
    def validate_auth_config(self, auth_type: AuthType, auth_config: Dict[str, Any]) -> Tuple[bool, str]:
        """验证认证配置
        
        Args:
            auth_type: 认证类型
            auth_config: 认证配置
            
        Returns:
            (是否有效, 错误信息)
        """
        try:
            if auth_type not in self.supported_auth_types:
                return False, f"不支持的认证类型: {auth_type}"
            
            if auth_type == AuthType.NONE:
                return True, ""
            
            elif auth_type == AuthType.API_KEY:
                return self._validate_api_key_config(auth_config)
            
            elif auth_type == AuthType.BEARER_TOKEN:
                return self._validate_bearer_token_config(auth_config)
            
            return False, "未知的认证类型"
            
        except Exception as e:
            return False, f"验证认证配置时出错: {e}"
    
    def _validate_api_key_config(self, auth_config: Dict[str, Any]) -> Tuple[bool, str]:
        """验证API Key认证配置
        
        Args:
            auth_config: 认证配置
            
        Returns:
            (是否有效, 错误信息)
        """
        api_key = auth_config.get("api_key")
        if not api_key:
            return False, "API Key不能为空"
        
        if not isinstance(api_key, str):
            return False, "API Key必须是字符串"
        
        # 验证key名称
        key_name = auth_config.get("key_name", "X-API-Key")
        if not isinstance(key_name, str):
            return False, "API Key名称必须是字符串"
        
        # 验证位置
        key_location = auth_config.get("location", "header")
        if key_location not in ["header", "query", "cookie"]:
            return False, "API Key位置必须是 header、query 或 cookie"
        
        return True, ""
    
    def _validate_bearer_token_config(self, auth_config: Dict[str, Any]) -> Tuple[bool, str]:
        """验证Bearer Token认证配置
        
        Args:
            auth_config: 认证配置
            
        Returns:
            (是否有效, 错误信息)
        """
        token = auth_config.get("token")
        if not token:
            return False, "Bearer Token不能为空"
        
        if not isinstance(token, str):
            return False, "Bearer Token必须是字符串"
        
        return True, ""
    
    def apply_authentication(
        self,
        auth_type: AuthType,
        auth_config: Dict[str, Any],
        url: str,
        headers: Dict[str, str],
        params: Dict[str, Any]
    ) -> Tuple[str, Dict[str, str], Dict[str, Any]]:
        """应用认证到请求
        
        Args:
            auth_type: 认证类型
            auth_config: 认证配置
            url: 请求URL
            headers: 请求头
            params: 请求参数
            
        Returns:
            (修改后的URL, 修改后的headers, 修改后的params)
        """
        try:
            if auth_type == AuthType.NONE:
                return url, headers, params
            
            elif auth_type == AuthType.API_KEY:
                return self._apply_api_key_auth(auth_config, url, headers, params)
            
            elif auth_type == AuthType.BEARER_TOKEN:
                return self._apply_bearer_token_auth(auth_config, url, headers, params)
            
            else:
                logger.warning(f"不支持的认证类型: {auth_type}")
                return url, headers, params
                
        except Exception as e:
            logger.error(f"应用认证时出错: {e}")
            return url, headers, params
    
    def _apply_api_key_auth(
        self,
        auth_config: Dict[str, Any],
        url: str,
        headers: Dict[str, str],
        params: Dict[str, Any]
    ) -> Tuple[str, Dict[str, str], Dict[str, Any]]:
        """应用API Key认证
        
        Args:
            auth_config: 认证配置
            url: 请求URL
            headers: 请求头
            params: 请求参数
            
        Returns:
            (修改后的URL, 修改后的headers, 修改后的params)
        """
        api_key = auth_config.get("api_key")
        key_name = auth_config.get("key_name", "X-API-Key")
        location = auth_config.get("location", "header")
        
        if location == "header":
            headers[key_name] = api_key
        
        elif location == "query":
            # 添加到URL查询参数
            separator = "&" if "?" in url else "?"
            encoded_key = quote(str(api_key))
            url += f"{separator}{key_name}={encoded_key}"
        
        elif location == "cookie":
            # 添加到Cookie头
            cookie_value = f"{key_name}={api_key}"
            if "Cookie" in headers:
                headers["Cookie"] += f"; {cookie_value}"
            else:
                headers["Cookie"] = cookie_value
        
        return url, headers, params
    
    def _apply_bearer_token_auth(
        self,
        auth_config: Dict[str, Any],
        url: str,
        headers: Dict[str, str],
        params: Dict[str, Any]
    ) -> Tuple[str, Dict[str, str], Dict[str, Any]]:
        """应用Bearer Token认证
        
        Args:
            auth_config: 认证配置
            url: 请求URL
            headers: 请求头
            params: 请求参数
            
        Returns:
            (修改后的URL, 修改后的headers, 修改后的params)
        """
        token = auth_config.get("token")
        headers["Authorization"] = f"Bearer {token}"
        
        return url, headers, params
    
    def encrypt_auth_config(self, auth_config: Dict[str, Any], encryption_key: str) -> Dict[str, Any]:
        """加密认证配置中的敏感信息
        
        Args:
            auth_config: 认证配置
            encryption_key: 加密密钥
            
        Returns:
            加密后的认证配置
        """
        try:
            encrypted_config = auth_config.copy()
            
            # 需要加密的字段
            sensitive_fields = ["api_key", "token", "secret", "password"]
            
            for field in sensitive_fields:
                if field in encrypted_config:
                    value = encrypted_config[field]
                    if isinstance(value, str) and value:
                        encrypted_value = self._encrypt_string(value, encryption_key)
                        encrypted_config[field] = encrypted_value
                        encrypted_config[f"{field}_encrypted"] = True
            
            return encrypted_config
            
        except Exception as e:
            logger.error(f"加密认证配置失败: {e}")
            return auth_config
    
    def decrypt_auth_config(self, encrypted_config: Dict[str, Any], encryption_key: str) -> Dict[str, Any]:
        """解密认证配置中的敏感信息
        
        Args:
            encrypted_config: 加密的认证配置
            encryption_key: 解密密钥
            
        Returns:
            解密后的认证配置
        """
        try:
            decrypted_config = encrypted_config.copy()
            
            # 需要解密的字段
            sensitive_fields = ["api_key", "token", "secret", "password"]
            
            for field in sensitive_fields:
                if field in decrypted_config and decrypted_config.get(f"{field}_encrypted"):
                    encrypted_value = decrypted_config[field]
                    if isinstance(encrypted_value, str) and encrypted_value:
                        decrypted_value = self._decrypt_string(encrypted_value, encryption_key)
                        decrypted_config[field] = decrypted_value
                        # 移除加密标记
                        decrypted_config.pop(f"{field}_encrypted", None)
            
            return decrypted_config
            
        except Exception as e:
            logger.error(f"解密认证配置失败: {e}")
            return encrypted_config
    
    def _encrypt_string(self, value: str, key: str) -> str:
        """加密字符串
        
        Args:
            value: 要加密的字符串
            key: 加密密钥
            
        Returns:
            加密后的字符串（Base64编码）
        """
        try:
            # 使用HMAC-SHA256进行简单加密
            key_bytes = key.encode('utf-8')
            value_bytes = value.encode('utf-8')
            
            # 生成HMAC
            hmac_obj = hmac.new(key_bytes, value_bytes, hashlib.sha256)
            signature = hmac_obj.hexdigest()
            
            # 组合原始值和签名，然后Base64编码
            combined = f"{value}:{signature}"
            encrypted = base64.b64encode(combined.encode('utf-8')).decode('utf-8')
            
            return encrypted
            
        except Exception as e:
            logger.error(f"加密字符串失败: {e}")
            return value
    
    def _decrypt_string(self, encrypted_value: str, key: str) -> str:
        """解密字符串
        
        Args:
            encrypted_value: 加密的字符串
            key: 解密密钥
            
        Returns:
            解密后的字符串
        """
        try:
            # Base64解码
            decoded = base64.b64decode(encrypted_value.encode('utf-8')).decode('utf-8')
            
            # 分离原始值和签名
            if ':' not in decoded:
                return encrypted_value  # 可能不是加密的值
            
            value, signature = decoded.rsplit(':', 1)
            
            # 验证签名
            key_bytes = key.encode('utf-8')
            value_bytes = value.encode('utf-8')
            
            hmac_obj = hmac.new(key_bytes, value_bytes, hashlib.sha256)
            expected_signature = hmac_obj.hexdigest()
            
            if signature == expected_signature:
                return value
            else:
                logger.warning("解密时签名验证失败")
                return encrypted_value
                
        except Exception as e:
            logger.error(f"解密字符串失败: {e}")
            return encrypted_value
    
    def test_authentication(
        self,
        auth_type: AuthType,
        auth_config: Dict[str, Any],
        test_url: str = None
    ) -> Dict[str, Any]:
        """测试认证配置
        
        Args:
            auth_type: 认证类型
            auth_config: 认证配置
            test_url: 测试URL（可选）
            
        Returns:
            测试结果
        """
        try:
            # 验证配置
            is_valid, error_msg = self.validate_auth_config(auth_type, auth_config)
            if not is_valid:
                return {
                    "success": False,
                    "error": error_msg,
                    "auth_type": auth_type.value
                }
            
            # 如果没有测试URL，只验证配置
            if not test_url:
                return {
                    "success": True,
                    "message": "认证配置有效",
                    "auth_type": auth_type.value
                }
            
            # 构建测试请求
            headers = {"User-Agent": "AuthManager-Test/1.0"}
            params = {}
            
            # 应用认证
            test_url, headers, params = self.apply_authentication(
                auth_type, auth_config, test_url, headers, params
            )
            
            return {
                "success": True,
                "message": "认证配置测试成功",
                "auth_type": auth_type.value,
                "test_url": test_url,
                "headers": {k: v for k, v in headers.items() if k != "Authorization"},  # 不返回敏感信息
                "has_auth_header": "Authorization" in headers
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "auth_type": auth_type.value if auth_type else "unknown"
            }
    
    async def test_authentication_with_request(
        self,
        auth_type: AuthType,
        auth_config: Dict[str, Any],
        test_url: str,
        timeout: int = 10
    ) -> Dict[str, Any]:
        """通过实际HTTP请求测试认证
        
        Args:
            auth_type: 认证类型
            auth_config: 认证配置
            test_url: 测试URL
            timeout: 超时时间（秒）
            
        Returns:
            测试结果
        """
        try:
            # 验证配置
            is_valid, error_msg = self.validate_auth_config(auth_type, auth_config)
            if not is_valid:
                return {
                    "success": False,
                    "error": error_msg,
                    "auth_type": auth_type.value
                }
            
            # 构建请求
            headers = {"User-Agent": "AuthManager-Test/1.0"}
            params = {}
            
            # 应用认证
            test_url, headers, params = self.apply_authentication(
                auth_type, auth_config, test_url, headers, params
            )
            
            # 发送测试请求
            client_timeout = aiohttp.ClientTimeout(total=timeout)
            async with aiohttp.ClientSession(timeout=client_timeout) as session:
                async with session.get(test_url, headers=headers) as response:
                    status_code = response.status
                    
                    # 根据状态码判断认证是否成功
                    if status_code == 200:
                        return {
                            "success": True,
                            "message": "认证测试成功",
                            "status_code": status_code,
                            "auth_type": auth_type.value
                        }
                    elif status_code == 401:
                        return {
                            "success": False,
                            "error": "认证失败 - 401 Unauthorized",
                            "status_code": status_code,
                            "auth_type": auth_type.value
                        }
                    elif status_code == 403:
                        return {
                            "success": False,
                            "error": "认证失败 - 403 Forbidden",
                            "status_code": status_code,
                            "auth_type": auth_type.value
                        }
                    else:
                        return {
                            "success": True,
                            "message": f"请求成功，状态码: {status_code}",
                            "status_code": status_code,
                            "auth_type": auth_type.value
                        }
            
        except aiohttp.ClientError as e:
            return {
                "success": False,
                "error": f"网络请求失败: {e}",
                "auth_type": auth_type.value
            }
        except Exception as e:
            return {
                "success": False,
                "error": f"测试认证时出错: {e}",
                "auth_type": auth_type.value
            }
    
    def get_auth_config_template(self, auth_type: AuthType) -> Dict[str, Any]:
        """获取认证配置模板
        
        Args:
            auth_type: 认证类型
            
        Returns:
            配置模板
        """
        templates = {
            AuthType.NONE: {},
            
            AuthType.API_KEY: {
                "api_key": "",
                "key_name": "X-API-Key",
                "location": "header",  # header, query, cookie
                "description": "API Key认证配置"
            },
            
            AuthType.BEARER_TOKEN: {
                "token": "",
                "description": "Bearer Token认证配置"
            }
        }
        
        return templates.get(auth_type, {})
    
    def mask_sensitive_config(self, auth_config: Dict[str, Any]) -> Dict[str, Any]:
        """遮蔽认证配置中的敏感信息
        
        Args:
            auth_config: 认证配置
            
        Returns:
            遮蔽敏感信息后的配置
        """
        masked_config = auth_config.copy()
        
        # 需要遮蔽的字段
        sensitive_fields = ["api_key", "token", "secret", "password"]
        
        for field in sensitive_fields:
            if field in masked_config:
                value = masked_config[field]
                if isinstance(value, str) and len(value) > 4:
                    # 只显示前2位和后2位
                    masked_config[field] = f"{value[:2]}***{value[-2:]}"
                elif isinstance(value, str) and value:
                    masked_config[field] = "***"
        
        return masked_config