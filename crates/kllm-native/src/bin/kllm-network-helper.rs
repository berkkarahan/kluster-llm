#[path = "../network/address_events.rs"]
mod address_events;
#[path = "../network/arp_conflict.rs"]
mod arp_conflict;
mod relay {
    pub fn private(ip: std::net::Ipv4Addr) -> bool {
        ip.is_private() && !ip.is_unspecified() && !ip.is_loopback()
    }
}
// Installed privileged process. Only scoped network operations cross local IPC.
#[path = "network_helper/mod.rs"]
mod network_helper;
fn main() {
    if let Err(error) = network_helper::main() {
        eprintln!("kllm-network-helper: {error}");
        std::process::exit(1);
    }
}
