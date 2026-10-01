mod host;
mod install;
mod ipc;
use crate::address_events::{self, AddressObserver};
#[cfg(unix)]
use crate::arp_conflict::{probe_native, start_native, IPv4ConflictObserver};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use ed25519_dalek::{Signature, VerifyingKey};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::HashMap,
    fs, io,
    path::{Path, PathBuf},
    time::{Duration, Instant},
};

pub type Result<T> = std::result::Result<T, Box<dyn std::error::Error + Send + Sync>>;
pub fn refuse(message: &str) -> Box<dyn std::error::Error + Send + Sync> {
    io::Error::other(message).into()
}
#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Grant {
    pub account: String,
    pub node_id: String,
    pub master_public_key: String,
    pub cluster_binding_id: String,
}
impl Grant {
    fn validate(&self) -> Result<()> {
        if !self
            .account
            .chars()
            .all(|c| c.is_ascii_digit() || c == '-' || c == 'S')
            || self.account.is_empty()
            || self.account.len() > 96
            || self.node_id.is_empty()
            || self.node_id.len() > 128
            || !self
                .node_id
                .chars()
                .all(|c| c.is_ascii_alphanumeric() || "_.:-".contains(c))
            || self.cluster_binding_id.len() != 36
        {
            return Err(refuse("Invalid authorization"));
        }
        let key: [u8; 32] = URL_SAFE_NO_PAD
            .decode(&self.master_public_key)?
            .try_into()
            .map_err(|_| refuse("Invalid signing key"))?;
        VerifyingKey::from_bytes(&key)?;
        if !valid_uuid(&self.cluster_binding_id) {
            return Err(refuse("Invalid cluster binding"));
        }
        #[cfg(unix)]
        self.account.parse::<u32>()?;
        #[cfg(windows)]
        if !regex::Regex::new(r"^S-1-5-(?:[0-9]+-)*[0-9]+$")?.is_match(&self.account) {
            return Err(refuse("Invalid account SID"));
        }
        Ok(())
    }
}
#[derive(Clone, Deserialize, Serialize)]
pub struct Scope {
    pub network: String,
    pub adapter: String,
    pub mac: String,
    pub index: u32,
    pub address: String,
    pub prefix: u8,
    pub management: Vec<String>,
    pub peers: Vec<String>,
    pub ports: Vec<u16>,
    pub original: Value,
    pub configured: bool,
    pub added: bool,
    pub uncertain: bool,
    pub term: u64,
    pub session: String,
    pub operation: String,
    pub revision: u64,
    pub phase: String,
}
#[derive(Default, Deserialize, Serialize)]
struct Journal {
    term: u64,
    session: String,
    scopes: HashMap<String, Scope>,
    #[serde(default)]
    operations: Vec<String>,
    #[serde(default)]
    revoked: bool,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Request {
    action: String,
    #[serde(default)]
    network: String,
    #[serde(default)]
    wire: String,
    #[serde(default)]
    snapshot: Value,
    #[serde(default)]
    epoch: String,
    #[serde(default)]
    handle: String,
    #[serde(default)]
    decision: String,
    #[serde(default)]
    snapshot_digest: String,
}
struct Helper {
    grant: Grant,
    path: PathBuf,
    journal: Journal,
    observers: HashMap<String, AddressObserver>,
    #[cfg(unix)]
    conflicts: HashMap<String, IPv4ConflictObserver>,
    expires: HashMap<String, Instant>,
    ownership: HashMap<String, (AddressObserver, u32, bool)>,
}
impl Helper {
    fn open(grant: Grant, path: PathBuf) -> Result<Self> {
        let mut journal: Journal = if path.exists() {
            serde_json::from_slice(&fs::read(&path)?)?
        } else {
            Journal::default()
        };
        for scope in journal.scopes.values_mut() {
            if scope.configured || scope.added {
                scope.uncertain = true;
            }
        }
        let mut helper = Self {
            grant,
            path,
            journal,
            observers: HashMap::new(),
            #[cfg(unix)]
            conflicts: HashMap::new(),
            expires: HashMap::new(),
            ownership: HashMap::new(),
        };
        // A missing temporary address permits restoring DHCP after reboot. A
        // present address has lost native ownership continuity and needs review.
        let keys: Vec<_> = helper.journal.scopes.keys().cloned().collect();
        for key in keys {
            let scope = helper.journal.scopes.get_mut(&key).unwrap();
            if scope.configured
                && !host::has_address(scope).unwrap_or(true)
                && host::restore(scope).is_ok()
            {
                scope.uncertain = false;
                scope.added = false;
            }
        }
        helper.persist()?;
        Ok(helper)
    }
    fn persist(&self) -> Result<()> {
        let temporary = self.path.with_extension("tmp");
        let mut options = fs::OpenOptions::new();
        options.write(true).create(true).truncate(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600).custom_flags(libc::O_NOFOLLOW);
        }
        let mut file = options.open(&temporary)?;
        use std::io::Write;
        file.write_all(&serde_json::to_vec(&self.journal)?)?;
        file.sync_all()?;
        fs::rename(&temporary, &self.path)?;
        #[cfg(unix)]
        fs::File::open(self.path.parent().unwrap())?.sync_all()?;
        Ok(())
    }
    fn authorize(&mut self, request: &Request) -> Result<()> {
        if self.journal.revoked || !valid_uuid(&request.network) {
            return Err(refuse("Authorization is disabled or network ID is invalid"));
        }
        #[derive(Deserialize)]
        #[serde(deny_unknown_fields)]
        struct Envelope {
            payload: String,
            signature: String,
        }
        let envelope: Envelope = serde_json::from_str(&request.wire)?;
        let key: [u8; 32] = URL_SAFE_NO_PAD
            .decode(&self.grant.master_public_key)?
            .try_into()
            .map_err(|_| refuse("Invalid signing key"))?;
        let signature = Signature::from_slice(&URL_SAFE_NO_PAD.decode(envelope.signature)?)?;
        let mut message = b"kllm-managed-network-v1\0".to_vec();
        message.extend(envelope.payload.as_bytes());
        VerifyingKey::from_bytes(&key)?.verify_strict(&message, &signature)?;
        let command: Value = serde_json::from_str(&envelope.payload)?;
        if command["type"] != "command"
            || command["target_node_id"] != self.grant.node_id
            || command["cluster_binding_id"] != self.grant.cluster_binding_id
            || command["network_id"] != request.network
        {
            return Err(refuse("Unauthorized cluster or computer"));
        }
        let term = command["master_term"]
            .as_u64()
            .ok_or_else(|| refuse("Missing term"))?;
        let session = command["master_session_id"]
            .as_str()
            .ok_or_else(|| refuse("Missing session"))?
            .to_owned();
        if term < self.journal.term
            || (term == self.journal.term
                && !self.journal.session.is_empty()
                && session != self.journal.session)
        {
            return Err(refuse("Stale master"));
        }
        let revision = command["operation_revision"]
            .as_u64()
            .ok_or_else(|| refuse("Missing revision"))?;
        let operation = command["operation_id"]
            .as_str()
            .ok_or_else(|| refuse("Missing operation"))?
            .to_owned();
        if !valid_uuid(&operation) || term == 0 || revision == 0 {
            return Err(refuse("Invalid operation identity"));
        }
        let previous_operation = self
            .journal
            .scopes
            .get(&request.network)
            .map(|s| s.operation.as_str());
        if previous_operation != Some(operation.as_str())
            && self.journal.operations.contains(&operation)
        {
            return Err(refuse("An old operation cannot be replayed"));
        }
        let phase = command["phase"]
            .as_str()
            .ok_or_else(|| refuse("Missing phase"))?;
        if !matches!(phase, "prepare" | "apply" | "cleanup" | "rollback") {
            return Err(refuse("Unauthorized network phase"));
        }
        if phase == "prepare" {
            let assignment = &command["body"]["assignment"];
            if assignment["node_id"] != self.grant.node_id
                || assignment["network_id"] != request.network
            {
                return Err(refuse("Invalid assignment"));
            }
            if let Some(prior) = self.journal.scopes.get(&request.network) {
                if prior.configured || prior.added || prior.uncertain {
                    return Err(refuse("Prior configuration needs cleanup"));
                }
                if prior.operation == operation {
                    return Err(refuse("Preparation cannot be replayed"));
                }
            }
            if self.journal.scopes.len() >= 1024
                && !self.journal.scopes.contains_key(&request.network)
            {
                return Err(refuse(
                    "Configuration history is full; review local records",
                ));
            }
            let mut scope = Scope {
                network: request.network.clone(),
                adapter: assignment["adapter_id"]
                    .as_str()
                    .ok_or_else(|| refuse("Missing adapter"))?
                    .into(),
                mac: request.snapshot["mac"]
                    .as_str()
                    .ok_or_else(|| refuse("Missing MAC"))?
                    .into(),
                index: request.snapshot["interface_index"]
                    .as_u64()
                    .ok_or_else(|| refuse("Missing interface index"))?
                    as u32,
                address: assignment["address"]
                    .as_str()
                    .ok_or_else(|| refuse("Missing address"))?
                    .into(),
                prefix: assignment["prefix_length"]
                    .as_u64()
                    .ok_or_else(|| refuse("Missing prefix"))? as u8,
                management: serde_json::from_value(command["body"]["management_hosts"].clone())?,
                peers: command["body"]["participants"]
                    .as_array()
                    .ok_or_else(|| refuse("Missing participants"))?
                    .iter()
                    .filter_map(|p| {
                        p["address"]
                            .as_str()
                            .filter(|a| assignment["address"] != *a)
                            .map(str::to_owned)
                    })
                    .collect(),
                ports: ["bridge_port", "diagnostic_port"]
                    .iter()
                    .map(|field| {
                        command["body"][field]
                            .as_u64()
                            .filter(|p| (1..=65535).contains(p))
                            .map(|p| p as u16)
                            .ok_or_else(|| refuse("Invalid dedicated port"))
                    })
                    .collect::<Result<Vec<_>>>()?,
                original: Value::Null,
                configured: false,
                added: false,
                uncertain: false,
                term,
                session: session.clone(),
                operation: operation.clone(),
                revision,
                phase: phase.into(),
            };
            host::validate(&scope)?;
            scope.original = host::configuration(&scope)?;
            self.ownership.insert(
                request.network.clone(),
                (
                    address_events::start(scope.index, request.network.clone())?,
                    0,
                    false,
                ),
            );
            self.journal.scopes.insert(request.network.clone(), scope);
        } else {
            let scope = self
                .journal
                .scopes
                .get_mut(&request.network)
                .ok_or_else(|| refuse("Prepare the adapter first"))?;
            if revision < scope.revision
                || (operation != scope.operation && phase == "apply")
                || (phase == "apply" && scope.phase != "prepare" && scope.phase != "apply")
            {
                return Err(refuse("Stale operation"));
            }
            if phase == "apply"
                && self
                    .expires
                    .get(&request.network)
                    .is_none_or(|deadline| *deadline <= Instant::now())
            {
                return Err(refuse("Preparation expired"));
            }
            scope.term = term;
            scope.session = session.clone();
            scope.operation = operation.clone();
            scope.revision = revision;
            scope.phase = phase.into();
        }
        self.journal.term = term;
        self.journal.session = session;
        if !self.journal.operations.contains(&operation) {
            self.journal.operations.push(operation);
        }
        if phase == "prepare" {
            self.expires.insert(
                request.network.clone(),
                Instant::now() + Duration::from_secs(60),
            );
        }
        self.persist()
    }
    fn dispatch(&mut self, request: Request) -> Result<Value> {
        if request.action == "status" {
            return Ok(
                json!({"ready":!self.journal.revoked,"grant":self.grant,"cleanup_pending":self.journal.scopes.values().filter(|s| s.uncertain).map(|s| &s.network).collect::<Vec<_>>()}),
            );
        }
        if request.action == "revoke" {
            if self
                .journal
                .scopes
                .values()
                .any(|s| s.configured || s.added || s.uncertain)
            {
                return Err(refuse(
                    "Restore private network settings before disabling access",
                ));
            }
            self.journal.revoked = true;
            self.persist()?;
            return Ok(json!({"ready":false}));
        }
        if request.action == "authorize" {
            self.authorize(&request)?;
            return Ok(json!({"ok":true}));
        }
        if request.action == "review" || request.action == "resolve" {
            let scope = self
                .journal
                .scopes
                .get_mut(&request.network)
                .ok_or_else(|| refuse("Recovery resource disappeared"))?;
            if !scope.uncertain {
                return Err(refuse("This adapter does not need recovery"));
            }
            let current = host::review(scope)?;
            let snapshot_digest = format!("{:x}", Sha256::digest(serde_json::to_vec(&current)?));
            if request.action == "review" {
                return Ok(
                    json!({"network_id":scope.network,"exact_address_prefix":format!("{}/{}",scope.address,scope.prefix),"current":current,"original":scope.original,"snapshot_digest":snapshot_digest}),
                );
            }
            if request.snapshot_digest != snapshot_digest {
                return Err(refuse("Settings changed; review them again"));
            }
            if request.decision == "restore_owned" {
                #[cfg(windows)]
                if scope.original["firewall"].is_null() {
                    scope.original["firewall"] = current["firewall"].clone();
                }
                if host::has_address(scope)? {
                    host::delete(scope)?;
                }
                host::restore(scope)?;
            } else if request.decision != "keep_current" {
                return Err(refuse("Choose whether to keep or restore settings"));
            }
            scope.added = false;
            scope.configured = false;
            scope.uncertain = false;
            self.persist()?;
            return Ok(json!({"resolved":true}));
        }
        if !matches!(
            request.action.as_str(),
            "observe_events" | "observe_close" | "conflict_status" | "conflict_close"
        ) {
            self.check_ownership(&request.network)?;
        }
        let scope = self
            .journal
            .scopes
            .get_mut(&request.network)
            .ok_or_else(|| refuse("No authorized assignment"))?;
        match request.action.as_str() {
            "configuration" => Ok(json!({"configured": scope.configured})),
            "configure" | "add" => {
                if scope.phase != "apply"
                    || scope.uncertain
                    || self
                        .expires
                        .get(&request.network)
                        .is_none_or(|deadline| *deadline <= Instant::now())
                {
                    return Err(refuse("Application is not authorized"));
                }
                host::validate(scope)?;
                // Intent is durable before a subprocess can make a change.
                if request.action == "configure" {
                    if !scope.configured {
                        if host::configuration(scope)? != scope.original {
                            return Err(refuse(
                                "Dedicated configuration changed after preparation",
                            ));
                        }
                        scope.configured = true;
                        self.persist()?;
                        let scope = self.journal.scopes.get_mut(&request.network).unwrap();
                        host::disable_dhcp(scope)?;
                    }
                } else if !scope.added {
                    let configuration = host::configuration(scope)?;
                    if configuration["dhcp"] == true || configuration["method"] == "auto" {
                        return Err(refuse(
                            "Dedicated DHCP must be disabled before address application",
                        ));
                    }
                    if host::has_address(scope)? {
                        return Err(refuse("Address already exists; ownership is unknown"));
                    }
                    if let Some((observer, count, changed)) =
                        self.ownership.get_mut(&request.network)
                    {
                        let batch: Value = serde_json::from_str(&observer.events_native()?)?;
                        if batch["health"] != "continuous" {
                            return Err(refuse("Ownership observation lost continuity"));
                        }
                        *count = 0;
                        *changed = false;
                    }
                    scope.added = true;
                    self.persist()?;
                    host::add(self.journal.scopes.get(&request.network).unwrap())?;
                }
                self.persist()?;
                Ok(json!({"ok":true}))
            }
            "delete" | "restore" => {
                if scope.uncertain && !host::has_address(scope)? {
                    scope.added = false;
                    scope.uncertain = false;
                }
                if !matches!(scope.phase.as_str(), "cleanup" | "rollback") || scope.uncertain {
                    return Err(refuse("Cleanup requires current ownership"));
                }
                host::identity(scope)?;
                if request.action == "delete" && scope.added {
                    if host::has_address(scope)? {
                        if self
                            .ownership
                            .get(&request.network)
                            .is_none_or(|(_, count, changed)| *count != 1 || *changed)
                        {
                            return Err(refuse(
                                "Address ownership is uncertain; local review is required",
                            ));
                        }
                        host::delete(scope)?;
                    }
                    scope.added = false;
                }
                if request.action == "restore" {
                    if host::has_address(scope)? && scope.added {
                        return Err(refuse("Remove the owned address first"));
                    }
                    host::restore(scope)?;
                }
                self.persist()?;
                Ok(json!({"ok":true}))
            }
            "observe_start" => {
                if self.observers.len() >= 256 || request.epoch.len() != 36 {
                    return Err(refuse("Observer limit or invalid epoch"));
                }
                self.observers.insert(
                    request.epoch.clone(),
                    address_events::start(scope.index, request.epoch.clone())?,
                );
                Ok(json!({"handle":request.epoch}))
            }
            "observe_events" => Ok(serde_json::from_str(
                &self
                    .observers
                    .get(&request.handle)
                    .ok_or_else(|| refuse("Observer disappeared"))?
                    .events_native()?,
            )?),
            "observe_close" => {
                self.observers.remove(&request.handle);
                Ok(json!({"ok":true}))
            }
            #[cfg(unix)]
            "conflict_start" => {
                if self.conflicts.len() >= 256 {
                    return Err(refuse("Observer limit"));
                }
                let observer = start_native(
                    scope.adapter.clone(),
                    scope.mac.clone(),
                    scope.address.clone(),
                )?;
                self.conflicts.insert(request.epoch.clone(), observer);
                Ok(json!({"handle":request.epoch}))
            }
            #[cfg(unix)]
            "conflict_status" => Ok(json!(self
                .conflicts
                .get(&request.handle)
                .ok_or_else(|| refuse("Conflict observer disappeared"))?
                .status_native())),
            #[cfg(unix)]
            "conflict_close" => {
                self.conflicts.remove(&request.handle);
                Ok(json!({"ok":true}))
            }
            #[cfg(unix)]
            "probe" => {
                let conflict = probe_native(&scope.adapter, &scope.mac, &scope.address)?;
                Ok(json!({"conflict":conflict}))
            }
            _ => Err(refuse("Unsupported helper operation")),
        }
    }

    fn check_ownership(&mut self, network: &str) -> Result<()> {
        let Some(scope) = self.journal.scopes.get_mut(network) else {
            return Ok(());
        };
        if !scope.added {
            return Ok(());
        }
        if let Some((observer, adds, changed)) = self.ownership.get_mut(network) {
            let batch: Value = serde_json::from_str(&observer.events_native()?)?;
            if batch["health"] != "continuous" {
                *changed = true;
            }
            for event in batch["events"]
                .as_array()
                .ok_or_else(|| refuse("Invalid native observation"))?
            {
                if event["address"] == scope.address {
                    if event["kind"] == "address_add" && event["prefix_length"] == scope.prefix {
                        *adds += 1;
                    } else if event["kind"] != "address_change"
                        || event["prefix_length"] != scope.prefix
                        || !matches!(event["dad_state"].as_i64(), Some(0 | 1 | 4))
                    {
                        *changed = true;
                    }
                }
            }
            if *adds > 1 || *changed {
                scope.uncertain = true;
            }
        } else {
            scope.uncertain = true;
        }
        self.persist()
    }
}

fn valid_uuid(value: &str) -> bool {
    value.len() == 36
        && value.bytes().enumerate().all(|(i, c)| {
            if [8, 13, 18, 23].contains(&i) {
                c == b'-'
            } else {
                c.is_ascii_hexdigit() && !c.is_ascii_uppercase()
            }
        })
}

pub fn main() -> Result<()> {
    let args: Vec<_> = std::env::args().collect();
    if args.len() != 3 {
        return Err(refuse(
            "Expected --install or --serve and an authorization file",
        ));
    }
    if args[1] == "--install" {
        return install::install(Path::new(&args[2]));
    }
    if args[1] != "--serve" {
        return Err(refuse("Unsupported command"));
    }
    install::require_admin()?;
    let grant: Grant = serde_json::from_slice(&fs::read(&args[2])?)?;
    grant.validate()?;
    let path = Path::new(&args[2]).with_extension("journal.json");
    let mut helper = Helper::open(grant.clone(), path)?;
    ipc::serve(&grant.account, |raw| {
        let response = if raw.len() > 65536 {
            Err(refuse("Request exceeds 64 KiB"))
        } else {
            serde_json::from_slice::<Request>(raw)
                .map_err(Into::into)
                .and_then(|request| {
                    let network = request.network.clone();
                    let mutating = matches!(
                        request.action.as_str(),
                        "configure" | "add" | "delete" | "restore"
                    );
                    let result = helper.dispatch(request);
                    if mutating && result.is_err() {
                        if let Some(scope) = helper.journal.scopes.get_mut(&network) {
                            if scope.configured || scope.added {
                                scope.uncertain = true;
                            }
                        }
                        let _ = helper.persist();
                    }
                    result
                })
        };
        serde_json::to_vec(&match response {
            Ok(value) => json!({"result":value}),
            Err(error) => json!({"error":error.to_string()}),
        })
        .unwrap()
    })
}
#[cfg(test)]
mod tests {
    use super::*;
    fn isolated() -> Helper {
        let key = ed25519_dalek::SigningKey::from_bytes(&[7; 32]);
        let grant = Grant {
            account: "501".into(),
            node_id: "worker".into(),
            master_public_key: URL_SAFE_NO_PAD.encode(key.verifying_key().to_bytes()),
            cluster_binding_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa".into(),
        };
        let path = std::env::temp_dir().join(format!(
            "kllm-helper-test-{}-{}.json",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        Helper::open(grant, path).unwrap()
    }
    fn request(action: &str) -> Request {
        serde_json::from_value(json!({"action":action})).unwrap()
    }
    fn signed(helper: &Helper, term: u64, target: &str) -> Request {
        use ed25519_dalek::Signer;
        let network = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
        let payload = json!({"type":"command","target_node_id":target,"cluster_binding_id":helper.grant.cluster_binding_id,"network_id":network,"master_term":term,"master_session_id":"session","operation_revision":1,"operation_id":"cccccccc-cccc-4ccc-8ccc-cccccccccccc","phase":"cleanup"}).to_string();
        let mut bytes = b"kllm-managed-network-v1\0".to_vec();
        bytes.extend(payload.as_bytes());
        let signature = ed25519_dalek::SigningKey::from_bytes(&[7; 32]).sign(&bytes);
        serde_json::from_value(json!({"action":"authorize","network":network,"wire":json!({"payload":payload,"signature":URL_SAFE_NO_PAD.encode(signature.to_bytes())}).to_string()})).unwrap()
    }
    #[test]
    fn signed_authority_replay_and_revocation() {
        let mut helper = isolated();
        let network = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
        helper.journal.scopes.insert(
            network.into(),
            Scope {
                network: network.into(),
                adapter: "never-mutated".into(),
                mac: String::new(),
                index: 1,
                address: "192.168.234.2".into(),
                prefix: 24,
                management: vec![],
                peers: vec![],
                ports: vec![50052, 18767],
                original: Value::Null,
                configured: false,
                added: false,
                uncertain: false,
                term: 1,
                session: "session".into(),
                operation: "dddddddd-dddd-4ddd-8ddd-dddddddddddd".into(),
                revision: 1,
                phase: "prepare".into(),
            },
        );
        assert!(helper
            .authorize(&signed(&helper, 2, "other-worker"))
            .is_err());
        let mut tampered = signed(&helper, 2, "worker");
        tampered.wire = tampered.wire.replace("cleanup", "apply");
        assert!(helper.authorize(&tampered).is_err());
        assert!(helper.authorize(&signed(&helper, 2, "worker")).is_ok());
        assert!(helper.authorize(&signed(&helper, 1, "worker")).is_err());
        assert!(helper
            .dispatch(serde_json::from_value(json!({"action":"add","network":network})).unwrap())
            .is_err());
        helper.journal.scopes.get_mut(network).unwrap().uncertain = true;
        assert!(helper.dispatch(request("revoke")).is_err());
        helper.journal.scopes.get_mut(network).unwrap().uncertain = false;
        assert!(helper.dispatch(request("revoke")).is_ok());
        assert_eq!(helper.dispatch(request("status")).unwrap()["ready"], false);
        assert!(helper.authorize(&signed(&helper, 3, "worker")).is_err());
        let path = helper.path.clone();
        let grant = helper.grant.clone();
        drop(helper);
        assert!(!Helper::open(grant, path.clone())
            .unwrap()
            .dispatch(request("status"))
            .unwrap()["ready"]
            .as_bool()
            .unwrap());
        fs::remove_file(path).unwrap();
    }
    #[test]
    fn reject_unbounded_or_arbitrary_operations() {
        assert!(serde_json::from_str::<Request>(r#"{"action":"run","args":["sh"]}"#).is_err());
        let grant = Grant {
            account: "1;rm".into(),
            node_id: "node".into(),
            master_public_key: String::new(),
            cluster_binding_id: String::new(),
        };
        assert!(grant.validate().is_err());
    }
}
