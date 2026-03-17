"""
Fernet 对称加密工具：用于加密/解密敏感配置（如 Langfuse Secret Key）
"""

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


def _get_fernet() -> Fernet:
    """获取 Fernet 实例，加密密钥从 ENCRYPTION_KEY 环境变量获取"""
    key = get_settings().ENCRYPTION_KEY
    if not key:
        raise RuntimeError("ENCRYPTION_KEY 未配置，无法加解密敏感数据")
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_secret(plaintext: str) -> str:
    """加密明文，返回 base64 编码的密文"""
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    """解密密文，返回明文"""
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        raise ValueError("解密失败：ENCRYPTION_KEY 不匹配或密文已损坏")


def generate_encryption_key() -> str:
    """生成新的 Fernet 加密密钥（首次部署时使用）"""
    return Fernet.generate_key().decode()
