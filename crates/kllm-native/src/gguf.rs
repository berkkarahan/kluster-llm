use serde_json::{json, Map, Value};
use std::fs::File;
use std::io::{BufReader, Read};
use std::path::Path;

const STRING: u32 = 8;
const ARRAY: u32 = 9;
const MAX_STRING: u64 = 10_000_000;
const MAX_ITEMS: u64 = 10_000_000;

struct Reader {
    inner: BufReader<File>,
    len: u64,
    position: u64,
}

impl Reader {
    fn open(path: &Path) -> Result<Self, String> {
        let file = File::open(path).map_err(|e| e.to_string())?;
        let len = file.metadata().map_err(|e| e.to_string())?.len();
        Ok(Self {
            inner: BufReader::with_capacity(64 * 1024, file),
            len,
            position: 0,
        })
    }

    fn bytes<const N: usize>(&mut self) -> Result<[u8; N], String> {
        let mut out = [0; N];
        self.inner.read_exact(&mut out).map_err(|e| e.to_string())?;
        self.position += N as u64;
        Ok(out)
    }

    fn u32(&mut self) -> Result<u32, String> {
        Ok(u32::from_le_bytes(self.bytes()?))
    }

    fn u64(&mut self) -> Result<u64, String> {
        Ok(u64::from_le_bytes(self.bytes()?))
    }

    fn string(&mut self) -> Result<String, String> {
        let len = self.u64()?;
        if len > MAX_STRING {
            return Err("GGUF string length too large".into());
        }
        let mut bytes = vec![0; len as usize];
        self.inner
            .read_exact(&mut bytes)
            .map_err(|e| e.to_string())?;
        self.position += len;
        Ok(String::from_utf8_lossy(&bytes).into_owned())
    }

    fn skip(&mut self, bytes: u64) -> Result<(), String> {
        let target = self
            .position
            .checked_add(bytes)
            .filter(|target| *target <= self.len)
            .ok_or_else(|| "GGUF value exceeds file length".to_string())?;
        self.inner
            .seek_relative(bytes as i64)
            .map_err(|e| e.to_string())?;
        self.position = target;
        Ok(())
    }

    fn skip_string(&mut self) -> Result<(), String> {
        let len = self.u64()?;
        if len > MAX_STRING {
            return Err("GGUF string length too large".into());
        }
        self.skip(len)
    }

    fn skip_value(&mut self, value_type: u32) -> Result<(), String> {
        if value_type == STRING {
            return self.skip_string();
        }
        if value_type == ARRAY {
            let element_type = self.u32()?;
            let count = self.u64()?;
            if count > MAX_ITEMS {
                return Err("GGUF array length too large".into());
            }
            if element_type == STRING {
                for _ in 0..count {
                    self.skip_string()?;
                }
                return Ok(());
            }
            let size = scalar_size(element_type)
                .ok_or_else(|| "unsupported GGUF array element type".to_string())?;
            return self.skip(
                count
                    .checked_mul(size)
                    .ok_or_else(|| "GGUF array size overflow".to_string())?,
            );
        }
        self.skip(scalar_size(value_type).ok_or_else(|| "unsupported GGUF value type".to_string())?)
    }
}

fn scalar_size(value_type: u32) -> Option<u64> {
    match value_type {
        0 | 1 | 7 => Some(1),
        2 | 3 => Some(2),
        4 | 5 | 6 => Some(4),
        10 | 11 | 12 => Some(8),
        _ => None,
    }
}

enum Number {
    Unsigned(u64),
    Signed(i64),
    Float(f64),
}

impl Number {
    fn positive_u64(&self) -> Option<u64> {
        match self {
            Self::Unsigned(value) if *value > 0 => Some(*value),
            Self::Signed(value) if *value > 0 => Some(*value as u64),
            _ => None,
        }
    }

    fn value(self) -> Value {
        match self {
            Self::Unsigned(value) => json!(value),
            Self::Signed(value) => json!(value),
            Self::Float(value) => json!(value),
        }
    }
}

fn read_number(reader: &mut Reader, value_type: u32) -> Result<Number, String> {
    match value_type {
        4 => Ok(Number::Unsigned(reader.u32()? as u64)),
        5 => Ok(Number::Signed(i32::from_le_bytes(reader.bytes()?) as i64)),
        6 => Ok(Number::Float(f32::from_le_bytes(reader.bytes()?) as f64)),
        10 => Ok(Number::Unsigned(reader.u64()?)),
        11 => Ok(Number::Signed(i64::from_le_bytes(reader.bytes()?))),
        12 => Ok(Number::Float(f64::from_le_bytes(reader.bytes()?))),
        _ => Err("GGUF value is not numeric".into()),
    }
}

fn reasoning(template: &str) -> Value {
    let supports_enable = template.contains("enable_thinking");
    let supports_effort = template.contains("reasoning_effort");
    let supports_preserve =
        template.contains("preserve_thinking") || template.contains("preserve_reasoning");
    let always_on = !supports_enable
        && (template.contains("<think>")
            || template.contains("reasoning_content")
            || template.contains("</think>"));
    let default_enable = if supports_enable {
        let false_guard =
            template.contains("enable_thinking is defined and enable_thinking is false");
        let true_guard =
            template.contains("enable_thinking is defined and enable_thinking is true");
        Some(!true_guard || false_guard)
    } else {
        None
    };
    json!({
        "supports_enable_thinking": supports_enable,
        "supports_reasoning_effort": supports_effort,
        "supports_preserve_thinking": supports_preserve,
        "always_on_thinking": always_on,
        "default_enable_thinking": default_enable,
    })
}

pub fn inference_metadata_json(path: &str, include_template: bool) -> Option<String> {
    scan(path, include_template).ok()
}

fn scan(path: &str, include_template: bool) -> Result<String, String> {
    let mut reader = Reader::open(Path::new(path))?;
    if &reader.bytes::<4>()? != b"GGUF" {
        return Err("not a GGUF file".into());
    }
    let version = reader.u32()?;
    if !matches!(version, 2 | 3) {
        return Err("unsupported GGUF version".into());
    }
    let _tensor_count = reader.u64()?;
    let metadata_count = reader.u64()?;
    if metadata_count > 100_000 {
        return Err("too many GGUF metadata entries".into());
    }

    let mut architecture: Option<String> = None;
    let mut context_length: Option<u64> = None;
    let mut block_count: Option<u64> = None;
    let mut sampling = Map::new();
    let mut has_template = false;
    let mut template_text: Option<String> = None;
    let mut reasoning_flags = reasoning("");

    for _ in 0..metadata_count {
        let key = reader.string()?;
        let value_type = reader.u32()?;
        if key == "general.architecture" && value_type == STRING {
            architecture = Some(reader.string()?);
        } else if key.ends_with(".context_length") && matches!(value_type, 4 | 5 | 10 | 11) {
            let value = read_number(&mut reader, value_type)?.positive_u64();
            let exact = architecture
                .as_ref()
                .is_some_and(|arch| key == format!("{arch}.context_length"));
            if exact || context_length.is_none() {
                context_length = value;
            }
        } else if key.ends_with(".block_count") && matches!(value_type, 4 | 5 | 10 | 11) {
            let value = read_number(&mut reader, value_type)?.positive_u64();
            let exact = architecture
                .as_ref()
                .is_some_and(|arch| key == format!("{arch}.block_count"));
            if exact || block_count.is_none() {
                block_count = value;
            }
        } else if let Some(suffix) = key.strip_prefix("general.sampling.") {
            if matches!(value_type, 4 | 5 | 6 | 10 | 11 | 12) {
                let mapped = match suffix {
                    "temp" | "temperature" => Some("temperature"),
                    "top_k" => Some("top_k"),
                    "top_p" => Some("top_p"),
                    "min_p" => Some("min_p"),
                    "penalty_repeat" => Some("repeat_penalty"),
                    "penalty_present" => Some("presence_penalty"),
                    "penalty_freq" => Some("frequency_penalty"),
                    _ => None,
                };
                let value = read_number(&mut reader, value_type)?;
                if let Some(mapped) = mapped {
                    sampling.insert(mapped.into(), value.value());
                }
            } else {
                reader.skip_value(value_type)?;
            }
        } else if matches!(
            key.as_str(),
            "tokenizer.chat_template" | "tokenizer.ggml.chat_template"
        ) && value_type == STRING
        {
            let template = reader.string()?;
            has_template = !template.trim().is_empty();
            reasoning_flags = reasoning(&template);
            if include_template && template.len() <= 65_536 {
                template_text = Some(template);
            }
        } else {
            reader.skip_value(value_type)?;
        }
    }

    let mut result = Map::new();
    result.insert("architecture".into(), json!(architecture));
    result.insert("context_length".into(), json!(context_length));
    result.insert("block_count".into(), json!(block_count));
    result.insert("sampling".into(), Value::Object(sampling));
    result.insert("reasoning".into(), reasoning_flags);
    result.insert("has_chat_template".into(), json!(has_template));
    if include_template {
        result.insert("chat_template_text".into(), json!(template_text));
    }
    Ok(Value::Object(result).to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    fn string(bytes: &mut Vec<u8>, value: &str) {
        bytes.extend_from_slice(&(value.len() as u64).to_le_bytes());
        bytes.extend_from_slice(value.as_bytes());
    }

    #[test]
    fn scans_selected_metadata_and_skips_token_strings() {
        let mut bytes = b"GGUF".to_vec();
        bytes.extend_from_slice(&3_u32.to_le_bytes());
        bytes.extend_from_slice(&0_u64.to_le_bytes());
        bytes.extend_from_slice(&3_u64.to_le_bytes());
        string(&mut bytes, "general.architecture");
        bytes.extend_from_slice(&STRING.to_le_bytes());
        string(&mut bytes, "llama");
        string(&mut bytes, "llama.context_length");
        bytes.extend_from_slice(&4_u32.to_le_bytes());
        bytes.extend_from_slice(&8192_u32.to_le_bytes());
        string(&mut bytes, "tokenizer.ggml.tokens");
        bytes.extend_from_slice(&ARRAY.to_le_bytes());
        bytes.extend_from_slice(&STRING.to_le_bytes());
        bytes.extend_from_slice(&2_u64.to_le_bytes());
        string(&mut bytes, "one");
        string(&mut bytes, "two");

        let mut file = tempfile::NamedTempFile::new().unwrap();
        file.write_all(&bytes).unwrap();
        let encoded = inference_metadata_json(file.path().to_str().unwrap(), false).unwrap();
        let result: Value = serde_json::from_str(&encoded).unwrap();
        assert_eq!(result["architecture"], "llama");
        assert_eq!(result["context_length"], 8192);
        assert!(result.get("chat_template_text").is_none());
    }

    #[test]
    fn rejects_truncated_metadata() {
        let mut file = tempfile::NamedTempFile::new().unwrap();
        file.write_all(b"GGUF\x03\0\0\0").unwrap();
        assert!(inference_metadata_json(file.path().to_str().unwrap(), false).is_none());
    }
}
