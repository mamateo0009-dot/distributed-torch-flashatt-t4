use base64::prelude::*;
use flate2::Compression;
use std::cell::RefCell;

/// Ultra-fast, zero-allocation Gzip compressor for Pearl ZK-PoW Base64 proofs.
/// Matches Python's zlib.compressobj(level=4, zlib.DEFLATED, 31).
pub struct FastGzipCompressor;

struct CompressorScratch {
    raw_bytes: Vec<u8>,
    compressed_bytes: Vec<u8>,
    compressor: flate2::Compress,
}

impl CompressorScratch {
    fn new() -> Self {
        Self {
            raw_bytes: Vec::with_capacity(32768),
            compressed_bytes: Vec::with_capacity(2048),
            compressor: flate2::Compress::new(Compression::new(4), false),
        }
    }
}

thread_local! {
    static SCRATCH: RefCell<CompressorScratch> = RefCell::new(CompressorScratch::new());
}

// Fixed 10-byte standard Gzip header (RFC 1952)
// ID1=0x1F, ID2=0x8B, CM=8 (deflate), FLG=0, MTIME=0, XFL=0, OS=0xFF (unknown/default)
const GZIP_HEADER: [u8; 10] = [0x1F, 0x8B, 0x08, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0xFF];

impl FastGzipCompressor {
    /// Compresses a base64 plain proof into Kryptex Gzip v2 format.
    /// If the proof is already compressed (starts with gzip magic 0x1F, 0x8B), returns it as-is.
    pub fn compress_b64_proof(plain_proof_b64: &str) -> String {
        let trimmed = plain_proof_b64.trim();
        let trimmed_bytes = trimmed.as_bytes();

        // 1. Bitwise fast-path check for already-compressed proof without decoding full payload.
        // In RFC 4648 Base64, raw bytes [0x1F, 0x8B] map deterministically to ASCII prefix "H4".
        // If the prefix matches "H4", decode only the first 4 base64 chars (3 raw bytes) on stack.
        if trimmed_bytes.len() >= 4 && trimmed_bytes[0] == b'H' && trimmed_bytes[1] == b'4' {
            let mut head = [0u8; 3];
            if BASE64_STANDARD.decode_slice(&trimmed_bytes[..4], &mut head).is_ok()
                && head[0] == 0x1F
                && head[1] == 0x8B
            {
                return plain_proof_b64.to_string();
            }
        }

        SCRATCH.with(|cell| {
            let mut scratch = cell.borrow_mut();
            let CompressorScratch {
                ref mut raw_bytes,
                ref mut compressed_bytes,
                ref mut compressor,
            } = *scratch;

            // 2. Base64 decode into reusable scratch buffer (0 heap allocation after warmup)
            raw_bytes.clear();
            if BASE64_STANDARD
                .decode_vec(trimmed_bytes, raw_bytes)
                .is_err()
            {
                return plain_proof_b64.to_string();
            }

            // Secondary check if decoded bytes start with gzip magic
            if raw_bytes.len() >= 2
                && raw_bytes[0] == 0x1F
                && raw_bytes[1] == 0x8B
            {
                return plain_proof_b64.to_string();
            }

            // 3. Ultra-fast compression using thread-local Compress state and reusable buffer
            compressed_bytes.clear();
            compressed_bytes.extend_from_slice(&GZIP_HEADER);

            let mut crc = flate2::Crc::new();
            crc.update(raw_bytes);

            compressor.reset();

            // Compress loop ensuring complete stream flush without truncation
            let mut total_in = 0;
            loop {
                if compressed_bytes.len() == compressed_bytes.capacity() {
                    compressed_bytes.reserve(1024);
                }
                let status = match compressor.compress_vec(
                    &raw_bytes[total_in..],
                    compressed_bytes,
                    flate2::FlushCompress::Finish,
                ) {
                    Ok(st) => st,
                    Err(_) => return plain_proof_b64.to_string(),
                };

                total_in = compressor.total_in() as usize;
                if status == flate2::Status::StreamEnd {
                    break;
                }
            }

            let uncompressed_len = raw_bytes.len() as u32;

            // Append Gzip trailer: CRC32 (4 bytes LE) + ISIZE (4 bytes LE)
            compressed_bytes.extend_from_slice(&crc.sum().to_le_bytes());
            compressed_bytes.extend_from_slice(&uncompressed_len.to_le_bytes());

            // 4. Pre-allocated Base64 string encoding
            let b64_len = (compressed_bytes.len() + 2) / 3 * 4;
            let mut out_str = String::with_capacity(b64_len);
            BASE64_STANDARD.encode_string(&compressed_bytes[..], &mut out_str);

            // 5. Memory guard: prevent unbounded capacity retention from rare oversize payloads
            if raw_bytes.capacity() > 65536 {
                raw_bytes.shrink_to(32768);
            }
            if compressed_bytes.capacity() > 16384 {
                compressed_bytes.shrink_to(4096);
            }

            out_str
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Read;

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

        // Verify valid RFC 1952 decompress
        let mut decoder = flate2::read::GzDecoder::new(&decoded[..]);
        let mut decompressed = String::new();
        decoder.read_to_string(&mut decompressed).unwrap();
        assert_eq!(decompressed, plain);
    }

    #[test]
    fn test_compress_large_random_payload() {
        let mut data = vec![0u8; 16384];
        for (idx, b) in data.iter_mut().enumerate() {
            *b = (idx * 37 + 13) as u8;
        }
        let b64 = BASE64_STANDARD.encode(&data);
        let res = FastGzipCompressor::compress_b64_proof(&b64);
        let decoded = BASE64_STANDARD.decode(&res).unwrap();
        let mut decoder = flate2::read::GzDecoder::new(&decoded[..]);
        let mut decompressed = Vec::new();
        decoder.read_to_end(&mut decompressed).unwrap();
        assert_eq!(decompressed, data);
    }
}
