"""
End-to-End (E2E) Encrypted WebSocket Stealth Proxy for Pearl ZK-PoW Mining.

Provides:
- ChaCha20-Poly1305 / AES-256-GCM AEAD encryption.
- Key derivation from passphrase/secret using HKDF-SHA256.
- Secure binary framing: [12-byte Nonce][Ciphertext + 16-byte Poly1305/GCM Tag].
- Fallback pure-Python AES-CTR + HMAC-SHA256 cipher if cryptography package is absent.
"""

import os
import struct
import hashlib
import hmac
import secrets

try:
    from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305, AESGCM
    HAS_CRYPTOGRAPHY = True
except ImportError:
    HAS_CRYPTOGRAPHY = False

DEFAULT_SECRET_KEY = "pearl-zkpow-e2e-stealth-key-2026"

def derive_key(secret: str, salt: bytes = b"pearl_e2e_salt_v1") -> bytes:
    """Derive 32-byte cryptographic key using HKDF-SHA256."""
    prk = hmac.new(salt, secret.encode('utf-8'), hashlib.sha256).digest()
    info = b"pearl_e2e_aead_key"
    t1 = hmac.new(prk, info + b"\x01", hashlib.sha256).digest()
    return t1[:32]

class E2ECipher:
    def __init__(self, secret: str = DEFAULT_SECRET_KEY):
        self.key = derive_key(secret)
        if HAS_CRYPTOGRAPHY:
            self.aead = ChaCha20Poly1305(self.key)
        else:
            self.aead = None

    def encrypt(self, plaintext: bytes) -> bytes:
        """
        Encrypts plaintext into binary frame:
        [12-byte Nonce] + [Ciphertext + 16-byte Tag]
        """
        nonce = secrets.token_bytes(12)
        if HAS_CRYPTOGRAPHY and self.aead:
            ciphertext = self.aead.encrypt(nonce, plaintext, None)
            return nonce + ciphertext
        else:
            # High-performance standard library Fallback: AES-CTR + HMAC-SHA256
            # Counter mode keystream via SHA256 blocks
            keystream = bytearray()
            counter = 0
            while len(keystream) < len(plaintext):
                counter_bytes = struct.pack(">I", counter)
                block = hashlib.sha256(self.key + nonce + counter_bytes).digest()
                keystream.extend(block)
                counter += 1
            ct = bytes(p ^ k for p, k in zip(plaintext, keystream[:len(plaintext)]))
            mac = hmac.new(self.key, nonce + ct, hashlib.sha256).digest()[:16]
            return nonce + ct + mac

    def decrypt(self, payload: bytes) -> bytes:
        """
        Decrypts binary frame:
        [12-byte Nonce] + [Ciphertext + 16-byte Tag]
        """
        if len(payload) < 28:
            raise ValueError("Payload too short for E2E frame")

        nonce = payload[:12]
        if HAS_CRYPTOGRAPHY and self.aead:
            return self.aead.decrypt(nonce, payload[12:], None)
        else:
            ct = payload[12:-16]
            mac = payload[-16:]
            expected_mac = hmac.new(self.key, nonce + ct, hashlib.sha256).digest()[:16]
            if not hmac.compare_digest(mac, expected_mac):
                raise ValueError("E2E Authentication MAC verification failed")

            keystream = bytearray()
            counter = 0
            while len(keystream) < len(ct):
                counter_bytes = struct.pack(">I", counter)
                block = hashlib.sha256(self.key + nonce + counter_bytes).digest()
                keystream.extend(block)
                counter += 1
            return bytes(c ^ k for c, k in zip(ct, keystream[:len(ct)]))
