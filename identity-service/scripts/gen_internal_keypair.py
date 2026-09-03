"""预生成内部 token RS256 密钥对（决策 #15：注入模式，K8s Secret 管理、不轮换）。

identity 为内部 token 密钥唯一权威，但密钥对由运维经 K8s Secret 管理（首次生成
可用本脚本），PEM 内容 + kid 经 env/Secret 同时注入 identity 与 gateway（两侧值
必须一致）。密钥变更 = K8s Secret 替换 + 部署重启，旧 token 随 TTL 自然过期，
不设代码侧轮换窗口。

用法（identity-service/ 下）：
    uv run python scripts/gen_internal_keypair.py [--output-dir DIR]

输出：私钥 PEM 文件 + kid + 可直接粘贴的 env 配置片段。
"""
import argparse
import sys
import uuid
from pathlib import Path

import rsa as pyrsa
from jose import jwt
from jose.jwk import RSAKey


def main() -> int:
    parser = argparse.ArgumentParser(description="生成内部 token RS256 密钥对（决策 #15）")
    parser.add_argument("--output-dir", type=Path, default=Path("."),
                        help="私钥 PEM 输出目录（默认当前目录）")
    args = parser.parse_args()

    _, priv = pyrsa.newkeys(2048)
    rsa = RSAKey(priv, jwt.ALGORITHMS.RS256)
    kid = str(uuid.uuid4())
    pem = rsa.to_pem().decode()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    out = args.output_dir / f"internal_issuer_{kid}.pem"
    out.write_text(pem)
    out.chmod(0o600)

    print(f"kid = {kid}")
    print(f"私钥文件 = {out}（权限 600，妥善保管）")
    print()
    print("# 以下配置同时注入 identity 与 gateway 的 env/Secret（两侧值必须一致）：")
    print(f'INTERNAL_ISSUER_PRIVATE_KEY="{pem}"')
    print(f"INTERNAL_ISSUER_KID={kid}")
    print()
    print("# 密钥变更：K8s Secret 替换后重启（不轮换、无叠加窗口，决策 #15）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
