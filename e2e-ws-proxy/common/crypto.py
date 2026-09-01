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

def derive_keys(secret: str, salt: bytes = b"pearl_e2e_salt_v1"):
    """Derive independent encryption and MAC keys using HKDF-SHA256."""
    prk = hmac.new(salt, secret.encode('utf-8'), hashlib.sha256).digest()
    # T1 for encryption / AEAD
    t1 = hmac.new(prk, b"pearl_e2e_enc_key\x01", hashlib.sha256).digest()
    # T2 for MAC in fallback mode
    t2 = hmac.new(prk, t1 + b"pearl_e2e_mac_key\x02", hashlib.sha256).digest()
    return t1[:32], t2[:32]

class E2ECipher:
    CIPHER_CHACHA20 = 0x01
    CIPHER_FALLBACK_CTR = 0x02

    def __init__(self, secret: str = DEFAULT_SECRET_KEY):
        self.enc_key, self.mac_key = derive_keys(secret)
        if HAS_CRYPTOGRAPHY:
            self.aead = ChaCha20Poly1305(self.enc_key)
        else:
            self.aead = None

    def encrypt(self, plaintext: bytes) -> bytes:
        """
        Encrypts plaintext into binary frame with 1-byte cipher ID prefix:
        - Mode 0x01: [0x01][12-byte Nonce][Ciphertext + 16-byte Poly1305 Tag]
        - Mode 0x02: [0x02][12-byte Nonce][Ciphertext + 16-byte HMAC Tag]
        """
        nonce = secrets.token_bytes(12)
        if HAS_CRYPTOGRAPHY and self.aead:
            ciphertext = self.aead.encrypt(nonce, plaintext, None)
            return bytes([self.CIPHER_CHACHA20]) + nonce + ciphertext
        else:
            # Fallback: Counter mode keystream via SHA256 blocks + HMAC-SHA256
            keystream = bytearray()
            counter = 0
            while len(keystream) < len(plaintext):
                counter_bytes = struct.pack(">I", counter)
                block = hashlib.sha256(self.enc_key + nonce + counter_bytes).digest()
                keystream.extend(block)
                counter += 1
            ct = bytes(p ^ k for p, k in zip(plaintext, keystream[:len(plaintext)]))
            mac = hmac.new(self.mac_key, nonce + ct, hashlib.sha256).digest()[:16]
            return bytes([self.CIPHER_FALLBACK_CTR]) + nonce + ct + mac

    def decrypt(self, payload: bytes) -> bytes:
        """
        Decrypts binary frame with cipher algorithm auto-detection.
        """
        if len(payload) < 28:
            raise ValueError("Payload too short for E2E frame")

        # Check if first byte is a cipher suite identifier
        if payload[0] in (self.CIPHER_CHACHA20, self.CIPHER_FALLBACK_CTR):
            cipher_type = payload[0]
            nonce = payload[1:13]
            body = payload[13:]
        else:
            # Backward compatibility without header prefix
            cipher_type = self.CIPHER_CHACHA20 if HAS_CRYPTOGRAPHY else self.CIPHER_FALLBACK_CTR
            nonce = payload[:12]
            body = payload[12:]

        if cipher_type == self.CIPHER_CHACHA20:
            if not HAS_CRYPTOGRAPHY:
                raise ValueError("Payload encrypted with ChaCha20-Poly1305 but 'cryptography' library is not installed on this node")
            return self.aead.decrypt(nonce, body, None)
        else:
            if len(body) < 16:
                raise ValueError("Payload body too short for MAC verification")
            ct = body[:-16]
            mac = body[-16:]
            expected_mac = hmac.new(self.mac_key, nonce + ct, hashlib.sha256).digest()[:16]
            if not hmac.compare_digest(mac, expected_mac):
                raise ValueError("E2E Authentication MAC verification failed")

            keystream = bytearray()
            counter = 0
            while len(keystream) < len(ct):
                counter_bytes = struct.pack(">I", counter)
                block = hashlib.sha256(self.enc_key + nonce + counter_bytes).digest()
                keystream.extend(block)
                counter += 1
            return bytes(c ^ k for c, k in zip(ct, keystream[:len(ct)]))
