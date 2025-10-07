from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from typing import Any, Dict, Tuple

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from .config import EncryptionConfig


KDF_LENGTH = 32
NOTE_NONCE_LENGTH = 12
MASTER_KEY_LENGTH = 32


def _b64encode(data: bytes) -> str:
    return base64.b64encode(data).decode("utf8")


def _b64decode(data: str) -> bytes:
    return base64.b64decode(data.encode("utf8"))


def derive_password_key(password: str, salt: bytes, iterations: int) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=KDF_LENGTH,
        salt=salt,
        iterations=iterations,
    )
    return kdf.derive(password.encode("utf8"))


def generate_master_key() -> bytes:
    return os.urandom(MASTER_KEY_LENGTH)


@dataclass
class MasterKeyBundle:
    encrypted_key: str
    salt: str
    nonce: str
    iterations: int

    def to_encryption_config(self) -> EncryptionConfig:
        return EncryptionConfig(
            kdf_salt=self.salt,
            kdf_iterations=self.iterations,
            enc_master_key=self.encrypted_key,
            master_key_nonce=self.nonce,
        )


class CryptoManager:
    def __init__(self, config: EncryptionConfig):
        self._config = config

    @property
    def iterations(self) -> int:
        return self._config.kdf_iterations

    def decrypt_master_key(self, password: str) -> bytes:
        salt = _b64decode(self._config.kdf_salt)
        nonce = _b64decode(self._config.master_key_nonce)
        encrypted = _b64decode(self._config.enc_master_key)
        password_key = derive_password_key(password, salt, self._config.kdf_iterations)
        aesgcm = AESGCM(password_key)
        return aesgcm.decrypt(nonce, encrypted, None)

    @staticmethod
    def create_master_key_bundle(password: str, iterations: int = 400_000) -> Tuple[bytes, MasterKeyBundle]:
        salt = os.urandom(16)
        password_key = derive_password_key(password, salt, iterations)
        master_key = generate_master_key()
        nonce = os.urandom(NOTE_NONCE_LENGTH)
        aesgcm = AESGCM(password_key)
        encrypted_master = aesgcm.encrypt(nonce, master_key, None)
        bundle = MasterKeyBundle(
            encrypted_key=_b64encode(encrypted_master),
            salt=_b64encode(salt),
            nonce=_b64encode(nonce),
            iterations=iterations,
        )
        return master_key, bundle

    @staticmethod
    def rewrap_master_key(master_key: bytes, password: str, iterations: int = 400_000) -> MasterKeyBundle:
        salt = os.urandom(16)
        password_key = derive_password_key(password, salt, iterations)
        nonce = os.urandom(NOTE_NONCE_LENGTH)
        aesgcm = AESGCM(password_key)
        encrypted_master = aesgcm.encrypt(nonce, master_key, None)
        return MasterKeyBundle(
            encrypted_key=_b64encode(encrypted_master),
            salt=_b64encode(salt),
            nonce=_b64encode(nonce),
            iterations=iterations,
        )

    @staticmethod
    def encrypt_note(master_key: bytes, payload: Dict[str, Any]) -> Tuple[bytes, bytes]:
        nonce = os.urandom(NOTE_NONCE_LENGTH)
        aesgcm = AESGCM(master_key)
        plaintext = json_dumps(payload)
        ciphertext = aesgcm.encrypt(nonce, plaintext, None)
        return nonce, ciphertext

    @staticmethod
    def decrypt_note(master_key: bytes, nonce: bytes, ciphertext: bytes) -> Dict[str, Any]:
        aesgcm = AESGCM(master_key)
        plaintext = aesgcm.decrypt(nonce, ciphertext, None)
        return json_loads(plaintext)


def json_dumps(data: Dict[str, Any]) -> bytes:
    import json

    return json.dumps(data, ensure_ascii=False).encode("utf8")


def json_loads(data: bytes) -> Dict[str, Any]:
    import json

    return json.loads(data.decode("utf8"))
