use base64::Engine;
use rand::Rng;

/// XOR-encrypt code bytes with a repeating key, return base64-encoded ciphertext.
pub fn encrypt_code(code: &[u8], key: &[u8]) -> String {
    let encrypted: Vec<u8> = code
        .iter()
        .enumerate()
        .map(|(i, &b)| b ^ key[i % key.len()])
        .collect();
    base64::engine::general_purpose::STANDARD.encode(encrypted)
}

/// Generate a random encryption key of `length` bytes.
pub fn generate_key(length: usize) -> Vec<u8> {
    let mut rng = rand::rng();
    let mut key = vec![0u8; length];
    rng.fill_bytes(&mut key);
    key
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn roundtrip() {
        let plain = b"hello world";
        let key = generate_key(64);
        let enc = encrypt_code(plain, &key);
        let dec = base64::engine::general_purpose::STANDARD
            .decode(&enc)
            .unwrap();
        let recovered: Vec<u8> = dec
            .iter()
            .enumerate()
            .map(|(i, &b)| b ^ key[i % key.len()])
            .collect();
        assert_eq!(recovered, plain);
    }
}
