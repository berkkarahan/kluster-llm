use sha2::{Digest, Sha256};
use std::borrow::Cow;
use std::fs;
use std::io;
use std::path::{Path, PathBuf};

pub const PROVIDER_STAMP_INPUTS: &[&str] = &[
    "Cargo.toml",
    "Cargo.lock",
    "build.rs",
    "stamp.rs",
    ".gitattributes",
    "src",
    "mpi-derive",
    "THIRD_PARTY_NOTICES.md",
];

pub struct ProviderSourceStamp {
    pub digest: String,
    pub files: Vec<PathBuf>,
}

fn collect_files(path: &Path, files: &mut Vec<PathBuf>) -> io::Result<()> {
    if path.is_file() {
        files.push(path.to_path_buf());
        return Ok(());
    }

    let mut entries = fs::read_dir(path)?.collect::<Result<Vec<_>, _>>()?;
    entries.sort_by_key(|entry| entry.file_name());
    for entry in entries {
        let child = entry.path();
        let name = entry.file_name();
        if child.is_dir() && (name == "target" || name == ".git") {
            continue;
        }
        collect_files(&child, files)?;
    }
    Ok(())
}

pub fn provider_source_stamp(manifest_dir: &Path) -> io::Result<ProviderSourceStamp> {
    let mut files = Vec::new();
    for relative in PROVIDER_STAMP_INPUTS {
        let path = manifest_dir.join(relative);
        if path.exists() {
            collect_files(&path, &mut files)?;
        }
    }
    files.sort();

    let mut digest = Sha256::new();
    for path in &files {
        let relative = path
            .strip_prefix(manifest_dir)
            .expect("provider source must be below manifest directory");
        let name = relative.to_string_lossy().replace('\\', "/");
        let contents = fs::read(path)?;
        update_source_digest(&mut digest, &name, &contents);
    }

    Ok(ProviderSourceStamp {
        digest: format!("sha256:{:x}", digest.finalize()),
        files,
    })
}

/// Normalize CRLF to LF before hashing source text so a Git checkout on any
/// supported host produces the same provider source identity.
pub fn canonicalize_newlines(input: &[u8]) -> Cow<'_, [u8]> {
    if !input.windows(2).any(|pair| pair == b"\r\n") {
        return Cow::Borrowed(input);
    }

    let mut output = Vec::with_capacity(input.len());
    let mut index = 0;
    while index < input.len() {
        if input[index] == b'\r' && input.get(index + 1) == Some(&b'\n') {
            output.push(b'\n');
            index += 2;
        } else {
            output.push(input[index]);
            index += 1;
        }
    }
    Cow::Owned(output)
}

pub fn update_source_digest(hasher: &mut Sha256, path: &str, contents: &[u8]) {
    let contents = canonicalize_newlines(contents);
    hasher.update(path.as_bytes());
    hasher.update([0]);
    hasher.update(contents.as_ref());
    hasher.update([0xff]);
}

/// Hash a sorted source snapshot. This is used by integration tests to prove
/// that LF and CRLF checkouts carry the same provider patch identity.
#[cfg(test)]
pub fn source_digest(mut entries: Vec<(String, Vec<u8>)>) -> String {
    entries.sort_by(|left, right| left.0.cmp(&right.0));
    let mut digest = Sha256::new();
    for (path, contents) in entries {
        update_source_digest(&mut digest, &path, &contents);
    }
    format!("sha256:{:x}", digest.finalize())
}
