import secrets
import string
import hashlib


def generate_share_token(length: int = 16) -> str:
    """生成唯一的分享 token
    
    Args:
        length: token 长度，默认 16
        
    Returns:
        随机字符串，包含大小写字母和数字
    """
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def hash_password(password: str) -> str:
    """加密密码
    
    Args:
        password: 明文密码
        
    Returns:
        密码哈希（使用 SHA-256）
    """
    # 使用 SHA-256 + salt
    salt = secrets.token_hex(16)
    pwd_hash = hashlib.sha256((password + salt).encode()).hexdigest()
    return f"{salt}${pwd_hash}"


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证密码
    
    Args:
        plain_password: 明文密码
        hashed_password: 密码哈希
        
    Returns:
        是否匹配
    """
    try:
        salt, pwd_hash = hashed_password.split('$')
        computed_hash = hashlib.sha256((plain_password + salt).encode()).hexdigest()
        return computed_hash == pwd_hash
    except (ValueError, AttributeError):
        return False


def build_share_url(share_token: str, base_url: str = None) -> str:
    """构建分享 URL
    
    Args:
        share_token: 分享 token
        base_url: 基础 URL，如果为 None 则使用相对路径
        
    Returns:
        完整的分享 URL
    """
    if base_url:
        return f"{base_url.rstrip('/')}/public/share/{share_token}"
    return f"/public/share/{share_token}"


def generate_embed_code(share_token: str, width: str = "100%", height: str = "600px", base_url: str = None) -> dict:
    """生成嵌入代码
    
    Args:
        share_token: 分享 token
        width: iframe 宽度
        height: iframe 高度
        base_url: 基础 URL
        
    Returns:
        包含 iframe_code 和 preview_url 的字典
    """
    preview_url = build_share_url(share_token, base_url)
    
    iframe_code = f'''<iframe 
    src="{preview_url}" 
    width="{width}" 
    height="{height}" 
    frameborder="0" 
    allowfullscreen>
</iframe>'''
    
    return {
        "iframe_code": iframe_code,
        "preview_url": preview_url,
        "width": width,
        "height": height
    }
