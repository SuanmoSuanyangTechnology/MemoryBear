"""凭据加密信封：v{ver}:{iv_b64}:{tag_b64}:{ct_b64}，单列存储、自描述、AAD 防错置。

ver = 主密钥轮换版本（信封自带，不落渠道列）；轮换 = 以新 ver 用新 key 重写存量行密文。
AAD 调用方约定 provider:tenant_id，防止跨租户/跨供应商复制密文行被误解密。
"""
import base64
import hashlib
import os
from typing import Protocol

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class CredentialCipher(Protocol):
    def encrypt(self, plaintext: str, *, ver: int = 1, aad: str) -> str: ...
    def decrypt(self, envelope: str, *, aad: str) -> str: ...


class AESGCMEnvCipher:
    """AES-256-GCM 信封实现（可被 KMS 信封实现同协议替换）。"""

    _IV_LEN = 12
    _TAG_LEN = 16

    def __init__(self, secret: bytes):
        if len(secret) not in (16, 24, 32):
            raise ValueError("secret must be 16/24/32 bytes (AES-128/192/256)")
        self._cipher = AESGCM(secret)

    def encrypt(self, plaintext: str, *, ver: int = 1, aad: str) -> str:
        if not aad:
            raise ValueError("aad is required (provider:tenant_id)")
        iv = os.urandom(self._IV_LEN)
        ct = self._cipher.encrypt(iv, plaintext.encode("utf-8"), aad.encode("utf-8"))
        return f"v{ver}:{base64.b64encode(iv).decode()}:{base64.b64encode(ct[-self._TAG_LEN:]).decode()}:{base64.b64encode(ct[:-self._TAG_LEN]).decode()}"

    def decrypt(self, envelope: str, *, aad: str) -> str:
        ver, iv_b64, tag_b64, ct_b64 = envelope.split(":")
        if not ver.startswith("v"):
            raise ValueError("malformed envelope")
        iv = base64.b64decode(iv_b64)
        tag = base64.b64decode(tag_b64)
        ct = base64.b64decode(ct_b64)
        # AESGCM 输出 ct||tag（tag 尾随），信封按 tag:ct 分开存储，恢复须拼回原序
        plaintext = self._cipher.decrypt(iv, ct + tag, aad.encode("utf-8"))
        return plaintext.decode("utf-8")


def credential_sha256(credential: str) -> str:
    """凭据指纹：幂等合并判定 + 迁移去重共用。"""
    return hashlib.sha256(credential.encode("utf-8")).hexdigest()


__all__ = ["AESGCMEnvCipher", "CredentialCipher", "credential_sha256"]
