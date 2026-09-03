"""内部 token RS256 密钥加载（决策 #15：注入模式，K8s Secret 管理、不轮换）。

identity 是内部 token 密钥唯一权威，但密钥对由运维经 K8s Secret 生成管理（首次
可用本服务 scripts/gen_internal_keypair.py），PEM 内容 + kid 经 env/Secret 同时
注入 identity 与 gateway（两侧值必须一致）；本模块只把注入的 PEM 解析为 JWK 供
/jwks 发布。不提供轮换叠加窗口——轮换由 K8s Secret 变更 + 部署重启承载，旧 token
随 TTL 自然过期。
"""
from dataclasses import dataclass
from datetime import datetime

from jose.jwk import RSAKey


@dataclass
class SigningKey:
    kid: str
    private_pem: str
    public_jwk: dict
    created_at: datetime


class KeyLoader:
    def __init__(self, primary_pem: str, primary_kid: str):
        if not primary_pem or not primary_kid:
            raise ValueError(
                "内部 token 主密钥缺失：PEM + kid 须经 env/Secret 注入（决策 #15）")
        self._keys: list[SigningKey] = []
        self._add(primary_pem, primary_kid)

    def _add(self, pem: str, kid: str) -> None:
        rsa = RSAKey(pem, "RS256")
        self._keys.append(SigningKey(
            kid=kid, private_pem=pem,
            # to_dict() 在私钥对象上会序列化出 d/p/q 等私钥字段；JWKS 只发布公钥
            public_jwk=rsa.public_key().to_dict() | {"kid": kid}, created_at=datetime.now()))

    def current_keys(self) -> list[SigningKey]:
        return self._keys
