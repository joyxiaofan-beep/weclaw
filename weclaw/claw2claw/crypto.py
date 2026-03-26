"""
E2E 加密模块 — 端到端加密/解密 (v1.4)

Relay 只看到密文 — 零知识消息传输。

加密方案：AES-256-GCM（认证加密）
密钥派生：HKDF-SHA256（从 shared_secret 派生 256-bit 加密密钥）
编码方式：Base64（密文 + nonce + tag 均用 base64 传输）

使用流程：
  发送方:
    key = derive_key(shared_secret)
    encrypted_content, nonce, tag = e2e_encrypt(content, key)
    → 将 encrypted_content, nonce, tag 放入消息 → 通过 Relay/HTTP 发送

  接收方:
    key = derive_key(shared_secret)
    content = e2e_decrypt(encrypted_content, key, nonce, tag)
    → 得到明文

安全保证：
  - 每条消息使用独立的随机 nonce（12 bytes），同一密钥下不会重复
  - GCM 模式同时提供加密和认证（防篡改）
  - HKDF 将任意长度 shared_secret 派生为固定 256-bit 密钥
  - Relay Server 全程只看到 Base64 密文，无法获知消息内容
"""

import base64
import json
import os
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes

from loguru import logger

# ──────────────────────────────────────────
# 常量
# ──────────────────────────────────────────

# HKDF 信息标签（区分用途，避免密钥混用）
_HKDF_INFO = b"weclaw-e2e-v1"

# AES-GCM nonce 长度（12 bytes = 96 bits，GCM 推荐值）
_NONCE_LENGTH = 12

# AES 密钥长度（32 bytes = 256 bits）
_KEY_LENGTH = 32


# ──────────────────────────────────────────
# 密钥派生
# ──────────────────────────────────────────

def derive_key(shared_secret: str, salt: Optional[bytes] = None) -> bytes:
    """
    从 shared_secret 派生 AES-256 加密密钥。

    使用 HKDF-SHA256，将任意长度的 shared_secret 转换为
    固定 256-bit 的加密密钥。

    Args:
        shared_secret: 两只龙虾间的共享密钥（字符串）
        salt: 可选盐值（默认 None，HKDF 规范中 salt=None
              等价于使用全零盐，因为 shared_secret 已有足够熵）

    Returns:
        32 bytes 的 AES-256 密钥
    """
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=_KEY_LENGTH,
        salt=salt,
        info=_HKDF_INFO,
    )
    return hkdf.derive(shared_secret.encode("utf-8"))


# ──────────────────────────────────────────
# 加密 / 解密
# ──────────────────────────────────────────

def e2e_encrypt(plaintext: str, key: bytes) -> tuple[str, str, str]:
    """
    AES-256-GCM 加密。

    Args:
        plaintext: 明文字符串
        key: 32 bytes AES-256 密钥（由 derive_key 生成）

    Returns:
        (ciphertext_b64, nonce_b64, tag_b64)
        - ciphertext_b64: Base64 编码的密文（含 GCM tag 在末尾）
        - nonce_b64: Base64 编码的 nonce
        注意：AESGCM.encrypt() 返回的是 ciphertext + tag 拼接体，
        所以 tag 已包含在 ciphertext_b64 中，tag_b64 返回空字符串。

    Raises:
        ValueError: 加密失败
    """
    try:
        aesgcm = AESGCM(key)
        nonce = os.urandom(_NONCE_LENGTH)

        # AESGCM.encrypt 返回: ciphertext || tag (16 bytes)
        ct_with_tag = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)

        return (
            base64.b64encode(ct_with_tag).decode("ascii"),
            base64.b64encode(nonce).decode("ascii"),
            "",  # tag 已嵌入 ct_with_tag 末尾
        )
    except Exception as e:
        raise ValueError(f"E2E 加密失败: {e}") from e


def e2e_decrypt(ciphertext_b64: str, key: bytes, nonce_b64: str, tag_b64: str = "") -> str:
    """
    AES-256-GCM 解密。

    Args:
        ciphertext_b64: Base64 编码的密文（含 GCM tag）
        key: 32 bytes AES-256 密钥（由 derive_key 生成）
        nonce_b64: Base64 编码的 nonce
        tag_b64: 预留参数（当前未使用，tag 包含在 ciphertext 中）

    Returns:
        解密后的明文字符串

    Raises:
        ValueError: 解密失败（密钥错误、消息被篡改、nonce 不匹配等）
    """
    try:
        aesgcm = AESGCM(key)
        ct_with_tag = base64.b64decode(ciphertext_b64)
        nonce = base64.b64decode(nonce_b64)

        plaintext_bytes = aesgcm.decrypt(nonce, ct_with_tag, None)
        return plaintext_bytes.decode("utf-8")
    except Exception as e:
        raise ValueError(f"E2E 解密失败: {e}") from e


# ──────────────────────────────────────────
# 高层封装 — 消息级加密/解密
# ──────────────────────────────────────────

def encrypt_message_fields(
    content: str,
    payload: dict,
    shared_secret: str,
) -> dict:
    """
    加密消息的 content 和 payload 字段。

    Args:
        content: 消息明文内容
        payload: 消息结构化数据
        shared_secret: 共享密钥

    Returns:
        {
            "encrypted_content": str,  # Base64 密文
            "encrypted_payload": str,  # Base64 密文（payload JSON 序列化后加密）
            "e2e_nonce": str,          # Base64 nonce（content 和 payload 共用同一密钥但各自独立 nonce）
            "e2e_payload_nonce": str,  # Base64 nonce（payload 专用）
        }
    """
    key = derive_key(shared_secret)

    # 加密 content
    enc_content, nonce_content, _ = e2e_encrypt(content, key)

    # 加密 payload（JSON 序列化后加密）
    payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    enc_payload, nonce_payload, _ = e2e_encrypt(payload_json, key)

    return {
        "encrypted_content": enc_content,
        "encrypted_payload": enc_payload,
        "e2e_nonce": nonce_content,
        "e2e_payload_nonce": nonce_payload,
    }


def decrypt_message_fields(
    encrypted_content: str,
    encrypted_payload: str,
    e2e_nonce: str,
    e2e_payload_nonce: str,
    shared_secret: str,
) -> tuple[str, dict]:
    """
    解密消息的 content 和 payload 字段。

    Args:
        encrypted_content: Base64 加密的 content
        encrypted_payload: Base64 加密的 payload
        e2e_nonce: content 的 nonce
        e2e_payload_nonce: payload 的 nonce
        shared_secret: 共享密钥

    Returns:
        (content, payload) — 解密后的明文内容和结构化数据

    Raises:
        ValueError: 解密失败
    """
    key = derive_key(shared_secret)

    # 解密 content
    content = e2e_decrypt(encrypted_content, key, e2e_nonce)

    # 解密 payload
    payload_json = e2e_decrypt(encrypted_payload, key, e2e_payload_nonce)
    payload = json.loads(payload_json)

    return content, payload


# ──────────────────────────────────────────
# 本地密钥加密 — 保护 shared_secret 在磁盘上的安全
# ──────────────────────────────────────────

# 本地加密使用 Fernet（AES-128-CBC + HMAC-SHA256），
# 密钥从 machine_id + 固定 salt 派生，绑定到当前机器。
# 即使 SQLite 文件被拷贝到其他机器，也无法解密 shared_secret。

_LOCAL_KEY_INFO = b"weclaw-local-storage-v1"
_LOCAL_KEY_SALT = b"weclaw-local-salt"


def _get_machine_id() -> str:
    """
    获取机器唯一标识，用于派生本地加密密钥。

    优先级：
    1. 环境变量 WECLAW_MACHINE_SECRET（用户可自定义）
    2. /etc/machine-id（Linux）
    3. macOS IOPlatformUUID
    4. 回退到 hostname + username

    Returns:
        机器唯一标识字符串
    """
    # 1. 用户自定义 secret（最优先）
    custom = os.environ.get("WECLAW_MACHINE_SECRET", "")
    if custom:
        return custom

    # 2. Linux /etc/machine-id
    try:
        with open("/etc/machine-id", "r") as f:
            machine_id = f.read().strip()
            if machine_id:
                return machine_id
    except (FileNotFoundError, PermissionError):
        pass

    # 3. macOS IOPlatformUUID
    try:
        import subprocess
        result = subprocess.run(
            ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
            capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.split("\n"):
            if "IOPlatformUUID" in line:
                uuid_str = line.split('"')[-2]
                if uuid_str:
                    return uuid_str
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
        pass

    # 4. 回退：hostname + username（安全性较低，但总比明文好）
    import getpass
    import socket
    fallback = f"{socket.gethostname()}-{getpass.getuser()}-weclaw-fallback"
    logger.warning(
        "⚠️  无法获取机器唯一标识，使用 hostname+username 作为回退。"
        "建议设置 WECLAW_MACHINE_SECRET 环境变量以提高安全性。"
    )
    return fallback


_cached_local_key: Optional[bytes] = None


def _derive_local_key() -> bytes:
    """
    从机器标识派生 Fernet 密钥（32 bytes → Base64 URL-safe）。
    结果会被缓存，避免每次加解密都执行文件读取和子进程。

    Returns:
        Fernet 兼容的 32-byte Base64-encoded 密钥
    """
    global _cached_local_key
    if _cached_local_key is not None:
        return _cached_local_key

    machine_id = _get_machine_id()
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_LOCAL_KEY_SALT,
        info=_LOCAL_KEY_INFO,
    )
    raw_key = hkdf.derive(machine_id.encode("utf-8"))
    _cached_local_key = base64.urlsafe_b64encode(raw_key)
    return _cached_local_key


def local_encrypt(plaintext: str) -> str:
    """
    使用机器绑定密钥加密字符串（用于安全存储 shared_secret）。

    Args:
        plaintext: 待加密的明文字符串

    Returns:
        加密后的密文字符串（Fernet token，Base64 编码）

    Raises:
        ValueError: 加密失败
    """
    if not plaintext:
        return ""
    try:
        from cryptography.fernet import Fernet
        f = Fernet(_derive_local_key())
        return f.encrypt(plaintext.encode("utf-8")).decode("ascii")
    except Exception as e:
        raise ValueError(f"本地加密失败: {e}") from e


def local_decrypt(ciphertext: str) -> str:
    """
    使用机器绑定密钥解密字符串（用于读取存储的 shared_secret）。

    Args:
        ciphertext: 加密后的密文字符串（Fernet token）

    Returns:
        解密后的明文字符串

    Raises:
        ValueError: 解密失败（密钥不匹配、数据损坏等）
    """
    if not ciphertext:
        return ""
    try:
        from cryptography.fernet import Fernet
        f = Fernet(_derive_local_key())
        return f.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except Exception as e:
        raise ValueError(f"本地解密失败: {e}") from e
