"""Code encryption utilities"""
import base64


def encrypt_code(code: bytes, key: bytes) -> str:
    """Encrypt code using XOR cipher with base64 encoding
    
    Args:
        code: Plain code string
        key: Encryption key bytes
        
    Returns:
        Base64 encoded encrypted code
    """
    key_length = len(key)
    encrypted_code = bytearray(len(code))
    for i in range(len(code)):
        encrypted_code[i] = code[i] ^ key[i % key_length]
    encoded_code = base64.b64encode(encrypted_code).decode("utf-8")
    return encoded_code


def generate_key(length: int = 64) -> bytes:
    """Generate random encryption key
    
    Args:
        length: Key length in bytes (default 64 for 512 bits)
        
    Returns:
        Random key bytes
    """
    import secrets
    return secrets.token_bytes(length)
