use std::env;

mod stamp;

const UPSTREAM_REVISION: &str = "99be8ac11c04f5419452428a486ae0eee1f5552e";
const PROTOCOL_VERSION: &str = "1";

fn main() {
    let manifest_dir = env::var("CARGO_MANIFEST_DIR").expect("manifest dir");
    let source_stamp = stamp::provider_source_stamp(std::path::Path::new(&manifest_dir))
        .expect("collect provider stamp inputs");
    // Cargo tracks a watched directory recursively, including added and removed
    // files. The source digest covers both trees, so file-level directives alone
    // would let a newly added source file retain a stale compiled-in identity.
    for directory in ["src", "mpi-derive"] {
        println!(
            "cargo:rerun-if-changed={}",
            std::path::Path::new(&manifest_dir)
                .join(directory)
                .display()
        );
    }
    for path in &source_stamp.files {
        println!("cargo:rerun-if-changed={}", path.display());
    }

    let patch_digest = source_stamp.digest;
    println!("cargo:rustc-env=KLLM_MPI_UPSTREAM_REVISION={UPSTREAM_REVISION}");
    println!("cargo:rustc-env=KLLM_MPI_PROTOCOL_VERSION={PROTOCOL_VERSION}");
    println!("cargo:rustc-env=KLLM_MPI_PATCH_DIGEST={patch_digest}");
    println!(
        "cargo:rustc-env=KLLM_MPI_TARGET={}",
        env::var("TARGET").expect("Cargo target")
    );
}
