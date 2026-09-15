use base64::prelude::*;
use flate2::write::GzEncoder;
use flate2::Compression;
use std::io::Write;

/// Ultra-fast, zero-lock Gzip compressor for Pearl ZK-PoW Base64 proofs.
/// Equivalent to Python's zlib.compressobj(level=4, zlib.DEFLATED, 31).
pub struct FastGzipCompressor;

impl FastGzipCompressor {
    /// Compresses a base64 plain proof into Kryptex Gzip v2 format.
    /// If the proof is already compressed (starts with gzip magic 0x1F, 0x8B), returns it as-is.
    pub fn compress_b64_proof(plain_proof_b64: &str) -> String {
        let raw_bytes = match BASE64_STANDARD.decode(plain_proof_b64.trim()) {
            Ok(b) => b,
            Err(_) => return plain_proof_b64.to_string(),
        };

        // Fast check: already compressed with gzip magic header 0x1F, 0x8B
        if raw_bytes.len() >= 2 && raw_bytes[0] == 0x1F && raw_bytes[1] == 0x8B {
            return plain_proof_b64.to_string();
        }

        // Fast compression level 4 (optimal balance between CPU cycles and size reduction ~100-200 bytes)
        let mut encoder = GzEncoder::new(Vec::with_capacity(512), Compression::new(4));
        if encoder.write_all(&raw_bytes).is_err() {
            return plain_proof_b64.to_string();
        }

        match encoder.finish() {
            Ok(compressed_bytes) => BASE64_STANDARD.encode(&compressed_bytes),
            Err(_) => plain_proof_b64.to_string(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_compress_already_gzipped() {
        let fake_gzip = vec![0x1F, 0x8B, 0x08, 0x00];
        let b64 = BASE64_STANDARD.encode(&fake_gzip);
        let res = FastGzipCompressor::compress_b64_proof(&b64);
        assert_eq!(res, b64);
    }

    #[test]
    fn test_compress_plain_data() {
        let plain = "Hello world! This is a test string for plain proof compression in rust proxy.".repeat(20);
        let b64 = BASE64_STANDARD.encode(plain.as_bytes());
        let res = FastGzipCompressor::compress_b64_proof(&b64);
        assert_ne!(res, b64);
        let decoded = BASE64_STANDARD.decode(&res).unwrap();
        assert_eq!(decoded[0], 0x1F);
        assert_eq!(decoded[1], 0x8B);
    }
}
