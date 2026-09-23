//! `#[derive(Equivalence)]` for the pure-Rust `mpi` crate.
//!
//! Generates an `Equivalence` implementation for a plain-old-data struct so it
//! can be used directly as an MPI datatype. The struct must be `Copy` and have
//! a stable byte representation (use `#[repr(C)]`), exactly as required by
//! rsmpi's derive.
//!
//! This macro has **no external dependencies** (no `syn` / `quote`): it does
//! the minimal token inspection needed to find the type name and emits the impl
//! by parsing a formatted source string.

use proc_macro::{TokenStream, TokenTree};
use std::str::FromStr;

/// Derive `mpi::datatype::Equivalence` for a POD struct.
#[proc_macro_derive(Equivalence, attributes(mpi))]
pub fn derive_equivalence(input: TokenStream) -> TokenStream {
    let name = match find_type_name(input) {
        Some(n) => n,
        None => {
            return TokenStream::from_str(
                "compile_error!(\"#[derive(Equivalence)] could not determine the type name; \
                 only non-generic structs/enums are supported\");",
            )
            .unwrap()
        }
    };

    // A stable-per-name datatype id derived from an FNV-1a hash of the name,
    // offset into the user-datatype id space.
    let hash = fnv1a(&name);

    let generated = format!(
        "unsafe impl ::mpi::datatype::Equivalence for {name} {{\n\
         \x20   fn equivalent_datatype() -> ::mpi::datatype::DatatypeRef {{\n\
         \x20       ::mpi::datatype::DatatypeRef {{\n\
         \x20           id: ::mpi::datatype::ids::USER_BASE.wrapping_add({hash}),\n\
         \x20           size: ::core::mem::size_of::<{name}>(),\n\
         \x20       }}\n\
         \x20   }}\n\
         }}\n"
    );

    TokenStream::from_str(&generated).expect("generated Equivalence impl failed to parse")
}

/// Find the identifier following the `struct`/`enum`/`union` keyword.
fn find_type_name(input: TokenStream) -> Option<String> {
    let mut saw_keyword = false;
    for tt in input {
        if let TokenTree::Ident(ident) = &tt {
            let s = ident.to_string();
            if saw_keyword {
                return Some(s);
            }
            if s == "struct" || s == "enum" || s == "union" {
                saw_keyword = true;
            }
        }
    }
    None
}

/// A small FNV-1a hash of the type name, used to give each derived type a
/// distinct datatype id.
fn fnv1a(s: &str) -> u32 {
    let mut hash: u32 = 0x811c_9dc5;
    for b in s.bytes() {
        hash ^= b as u32;
        hash = hash.wrapping_mul(0x0100_0193);
    }
    // Keep it comfortably inside the u32 range once offset by USER_BASE.
    hash & 0x00FF_FFFF
}
