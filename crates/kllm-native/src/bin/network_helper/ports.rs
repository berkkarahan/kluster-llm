//! Manifest-bound private listeners. This module accepts no programs or shell text.

use super::{host, refuse, valid_uuid, Helper, Request, Result, Scope};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use ed25519_dalek::{Signature, VerifyingKey};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::HashSet;
use std::net::Ipv4Addr;
use std::time::{Duration, Instant};

const ADMISSION_LEASE: Duration = Duration::from_secs(600);
const RUNNING_LEASE: Duration = Duration::from_secs(90);

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Rule {
    pub port: u16,
    pub peers: Vec<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Contract {
    pub network_id: String,
    pub manifest_digest: String,
    pub address: String,
    pub assignment_revision: u64,
    pub rules: Vec<Rule>,
}

#[derive(Clone, Deserialize, Serialize)]
pub struct Owner {
    pub run_id: String,
    pub contract: Contract,
    pub term: u64,
    pub session: String,
    pub snapshots: Vec<Value>,
    pub phase: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Command {
    version: u8,
    #[serde(rename = "type")]
    kind: String,
    cluster_binding_id: String,
    master_node_id: String,
    master_key_id: String,
    master_term: u64,
    master_session_id: String,
    channel_id: String,
    sender_node_id: String,
    target_node_id: String,
    sequence: u64,
    request_id: String,
    run_id: String,
    action: String,
    ttl_ms: u32,
    body: Body,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Body {
    contract: Contract,
}

impl Helper {
    fn authorize_prima_ports(&mut self, request: &Request) -> Result<Owner> {
        #[derive(Deserialize)]
        #[serde(deny_unknown_fields)]
        struct Envelope {
            payload: String,
            signature: String,
        }
        if self.journal.revoked {
            return Err(refuse("Administrator access is disabled"));
        }
        let envelope: Envelope = serde_json::from_str(&request.wire)?;
        let key: [u8; 32] = URL_SAFE_NO_PAD
            .decode(&self.grant.master_public_key)?
            .try_into()
            .map_err(|_| refuse("Invalid signing key"))?;
        let mut bytes = b"kllm-managed-network-v1\0".to_vec();
        bytes.extend(envelope.payload.as_bytes());
        VerifyingKey::from_bytes(&key)?.verify_strict(
            &bytes,
            &Signature::from_slice(&URL_SAFE_NO_PAD.decode(envelope.signature)?)?,
        )?;
        let command: Command = serde_json::from_str(&envelope.payload)?;
        let key_id = Sha256::digest(key)
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect::<String>();
        let scope = self
            .journal
            .scopes
            .get(&request.network)
            .ok_or_else(|| refuse("Prepare the private adapter before Prima admission"))?;
        if command.version != 1
            || command.kind != "rpc_command"
            || command.action
                != match request.action.as_str() {
                    "prima_ports_apply" => "prima_admit_ports",
                    "prima_ports_renew" => "prima_renew_ports",
                    _ => return Err(refuse("Unsupported signed Prima port operation")),
                }
            || command.cluster_binding_id != self.grant.cluster_binding_id
            || command.target_node_id != self.grant.node_id
            || command.sender_node_id != command.master_node_id
            || command.master_key_id != key_id
            || command.master_node_id.is_empty()
            || command.master_term == 0
            || command.master_term != self.journal.term
            || command.master_session_id != self.journal.session
            || command.master_term != scope.term
            || command.master_session_id != scope.session
            || !valid_uuid(&command.master_session_id)
            || !valid_uuid(&command.channel_id)
            || !valid_uuid(&command.request_id)
            || !valid_uuid(&command.run_id)
            || command.run_id != request.run_id
            || !(1..=60000).contains(&command.ttl_ms)
            || !(1..=(1u64 << 53) - 1).contains(&command.sequence)
            || command.body.contract.network_id != request.network
            || scope.uncertain
            || !scope.added
            || !scope.configured
            || scope.phase != "apply"
            || scope.direct_ports != self.direct_ports
        {
            return Err(refuse(
                "Prima admission does not match the current signed private assignment",
            ));
        }
        command.body.contract.validate(scope)?;
        let sequence_key = format!(
            "{}:{}:{}",
            command.master_term, command.master_session_id, command.channel_id
        );
        if self
            .journal
            .prima_sequences
            .get(&sequence_key)
            .is_some_and(|n| command.sequence <= *n)
        {
            return Err(refuse("Replayed Prima admission command"));
        }
        if self.journal.prima_sequences.len() >= 4096
            && !self.journal.prima_sequences.contains_key(&sequence_key)
        {
            return Err(refuse(
                "Prima admission history is full; review local records",
            ));
        }
        // Replay fences survive both mutation failure and helper restart.
        self.journal
            .prima_sequences
            .insert(sequence_key, command.sequence);
        self.persist()?;
        Ok(Owner {
            run_id: command.run_id,
            contract: command.body.contract,
            term: command.master_term,
            session: command.master_session_id,
            snapshots: vec![],
            phase: "applying".into(),
        })
    }

    fn apply_port_rules(
        &mut self,
        mut owner: Owner,
        mut create: impl FnMut(&Scope, &Owner, &Rule) -> Result<Value>,
    ) -> Result<()> {
        if self.journal.prima_ports.contains_key(&owner.run_id) {
            return Err(refuse(
                "Existing Prima admission must be checked or released before application",
            ));
        }
        if self.journal.prima_ports.len() >= 64
            || self.journal.prima_ports.values().any(|old| {
                old.contract.address == owner.contract.address
                    && old
                        .contract
                        .rules
                        .iter()
                        .any(|a| owner.contract.rules.iter().any(|b| a.port == b.port))
            })
        {
            return Err(refuse("Prima listener is reserved by another owned run"));
        }
        let scope = self
            .journal
            .scopes
            .get(&owner.contract.network_id)
            .ok_or_else(|| refuse("Prima private assignment disappeared"))?
            .clone();
        owner.contract.validate(&scope)?;
        owner.snapshots = vec![Value::Null; owner.contract.rules.len()];
        self.journal
            .prima_ports
            .insert(owner.run_id.clone(), owner.clone());
        // Journal the complete intent before the first host mutation.
        self.persist()?;
        let result = (|| {
            for (index, rule) in owner.contract.rules.iter().enumerate() {
                let snapshot = create(&scope, &owner, rule)?;
                if snapshot.is_null() {
                    return Err(refuse("Prima rule creation lacks an ownership snapshot"));
                }
                self.journal
                    .prima_ports
                    .get_mut(&owner.run_id)
                    .unwrap()
                    .snapshots[index] = snapshot;
                self.persist()?;
            }
            self.journal
                .prima_ports
                .get_mut(&owner.run_id)
                .unwrap()
                .phase = "active".into();
            self.persist()
        })();
        if result.is_err() {
            self.journal
                .prima_ports
                .get_mut(&owner.run_id)
                .unwrap()
                .phase = "cleanup_pending".into();
            let _ = self.persist();
        } else {
            self.port_deadlines
                .insert(owner.run_id.clone(), Instant::now() + ADMISSION_LEASE);
        }
        result
    }

    fn release_port_rules(
        &mut self,
        network: &str,
        run: &str,
        mut remove: impl FnMut(&Scope, &Owner, usize) -> Result<()>,
    ) -> Result<()> {
        let Some(owner) = self.journal.prima_ports.get(run).cloned() else {
            return Ok(());
        };
        if owner.contract.network_id != network {
            return Err(refuse("Prima cleanup network does not own this run"));
        }
        self.journal.prima_ports.get_mut(run).unwrap().phase = "cleanup_pending".into();
        self.persist()?;
        let scope = self
            .journal
            .scopes
            .get(network)
            .ok_or_else(|| refuse("Prima cleanup private assignment disappeared"))?
            .clone();
        for index in 0..owner.contract.rules.len() {
            remove(&scope, &owner, index)?;
            self.journal.prima_ports.get_mut(run).unwrap().snapshots[index] = Value::Null;
            self.persist()?;
        }
        let drained = self.journal.prima_ports.remove(run).unwrap();
        if let Err(error) = self.persist() {
            self.journal.prima_ports.insert(run.into(), drained);
            return Err(error);
        }
        self.port_deadlines.remove(run);
        Ok(())
    }

    fn renew_port_rules(
        &mut self,
        candidate: &Owner,
        now: Instant,
        verify: impl FnOnce(&Scope, &Owner) -> Result<Value>,
    ) -> Result<Value> {
        let owner = self
            .journal
            .prima_ports
            .get(&candidate.run_id)
            .ok_or_else(|| refuse("Prima native admission is absent"))?;
        if owner.phase != "active"
            || owner.contract != candidate.contract
            || owner.term != candidate.term
            || owner.session != candidate.session
            || self
                .port_deadlines
                .get(&candidate.run_id)
                .is_none_or(|deadline| now >= *deadline)
        {
            return Err(refuse(
                "Prima native lease expired or renewal changed ownership",
            ));
        }
        let scope = self
            .journal
            .scopes
            .get(&owner.contract.network_id)
            .ok_or_else(|| refuse("Prima private assignment disappeared"))?;
        let result = verify(scope, owner)?;
        if result != json!({"phase":"admitted", "manifest_digest":owner.contract.manifest_digest}) {
            return Err(refuse("Prima native renewal lacks exact rule ownership"));
        }
        self.port_deadlines
            .insert(candidate.run_id.clone(), Instant::now() + RUNNING_LEASE);
        Ok(result)
    }

    pub(super) fn expire_port_rules(
        &mut self,
        now: Instant,
        mut remove: impl FnMut(&Scope, &Owner, usize) -> Result<()>,
    ) -> Result<()> {
        let expired: Vec<_> = self
            .journal
            .prima_ports
            .values()
            .filter(|o| {
                o.phase != "active"
                    || self
                        .port_deadlines
                        .get(&o.run_id)
                        .is_none_or(|deadline| now >= *deadline)
            })
            .map(|o| (o.contract.network_id.clone(), o.run_id.clone()))
            .collect();
        let mut pending = vec![];
        for (network, run) in expired {
            if self
                .release_port_rules(&network, &run, &mut remove)
                .is_err()
            {
                pending.push(run);
            }
        }
        if pending.is_empty() {
            Ok(())
        } else {
            Err(refuse(&format!(
                "Prima native lease cleanup pending on {}",
                pending.join(", ")
            )))
        }
    }

    pub(super) fn dispatch_prima_ports(&mut self, request: &Request) -> Result<Value> {
        if !valid_uuid(&request.network) || !valid_uuid(&request.run_id) {
            return Err(refuse("Invalid Prima listener ownership identity"));
        }
        match request.action.as_str() {
            "prima_ports_review" => self.review_port_rules(request, snapshot),
            "prima_ports_resolve" => self.resolve_port_rules(request, snapshot, remove),
            "prima_ports_renew" => {
                self.check_ownership(&request.network)?;
                let candidate = self.authorize_prima_ports(request)?;
                self.renew_port_rules(&candidate, Instant::now(), |scope, owner| {
                    host::identity(scope)?;
                    if !host::has_address(scope)? {
                        return Err(refuse("Prima private address disappeared"));
                    }
                    status(scope, owner)
                })
            }
            "prima_ports_apply" => {
                self.check_ownership(&request.network)?;
                let owner = self.authorize_prima_ports(request)?;
                self.apply_port_rules(owner, create)?;
                let status_request = serde_json::from_value(json!({"action":"prima_ports_status",
                    "network":request.network, "run_id":request.run_id}))?;
                self.dispatch_prima_ports(&status_request)
            }
            "prima_ports_status" => {
                self.check_ownership(&request.network)?;
                let owner = self
                    .journal
                    .prima_ports
                    .get(&request.run_id)
                    .ok_or_else(|| refuse("Prima listener admission is absent"))?;
                if owner.contract.network_id != request.network {
                    return Err(refuse("Prima listener network differs"));
                }
                if self
                    .port_deadlines
                    .get(&request.run_id)
                    .is_none_or(|deadline| Instant::now() >= *deadline)
                {
                    return Err(refuse("Prima native lease expired or requires cleanup"));
                }
                let scope = self
                    .journal
                    .scopes
                    .get(&request.network)
                    .ok_or_else(|| refuse("Private assignment disappeared"))?;
                owner.contract.validate(scope)?;
                if scope.uncertain
                    || scope.term != owner.term
                    || scope.session != owner.session
                    || self.journal.term != owner.term
                    || self.journal.session != owner.session
                {
                    return Err(refuse(
                        "Prima private address ownership or authority changed",
                    ));
                }
                host::identity(scope)?;
                if !host::has_address(scope)? {
                    return Err(refuse("Prima private address disappeared"));
                }
                status(scope, owner)
            }
            "prima_ports_release" => {
                // Protected local IPC may only narrow rules already in the durable ledger.
                // Cleanup stays available after authority changes or revocation.
                self.release_port_rules(&request.network, &request.run_id, remove)?;
                Ok(json!({"phase":"released"}))
            }
            _ => Err(refuse("Unsupported Prima listener operation")),
        }
    }

    fn review_port_rules(
        &self,
        request: &Request,
        mut inspect: impl FnMut(&Scope, &str, u16) -> Result<Value>,
    ) -> Result<Value> {
        let owner = self
            .journal
            .prima_ports
            .get(&request.run_id)
            .ok_or_else(|| refuse("Prima listener recovery resource disappeared"))?;
        if owner.contract.network_id != request.network {
            return Err(refuse("Prima listener recovery network differs"));
        }
        if owner.phase == "active"
            && self
                .port_deadlines
                .get(&request.run_id)
                .is_some_and(|deadline| Instant::now() < *deadline)
        {
            return Err(refuse(
                "Stop Prima listener admission before local recovery",
            ));
        }
        let scope = self
            .journal
            .scopes
            .get(&request.network)
            .ok_or_else(|| refuse("Prima private assignment disappeared"))?;
        let mut rules = vec![];
        for (index, rule) in owner.contract.rules.iter().enumerate() {
            let current = inspect(scope, &owner.run_id, rule.port)?;
            let original = owner.snapshots.get(index).cloned().unwrap_or(Value::Null);
            let ownership = if current.is_null() {
                "absent"
            } else if original.is_null() {
                "unknown"
            } else if original == current {
                "exact"
            } else {
                "changed"
            };
            rules.push(json!({"port":rule.port,"peers":rule.peers,
                "original":original,"current":current,"ownership":ownership}));
        }
        // Bind the complete durable owner, adapter scope and current observations.
        let binding = json!({"owner":owner,"scope":scope,"rules":rules});
        let snapshot_digest = Sha256::digest(serde_json::to_vec(&binding)?)
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect::<String>();
        Ok(json!({"network_id":request.network,"run_id":request.run_id,
            "manifest_digest":owner.contract.manifest_digest,"rules":rules,
            "snapshot_digest":snapshot_digest}))
    }

    fn resolve_port_rules(
        &mut self,
        request: &Request,
        inspect: impl FnMut(&Scope, &str, u16) -> Result<Value>,
        remove: impl FnMut(&Scope, &Owner, usize) -> Result<()>,
    ) -> Result<Value> {
        if !matches!(request.decision.as_str(), "keep_current" | "restore_owned") {
            return Err(refuse(
                "Choose whether to keep current or remove exact owned listeners",
            ));
        }
        let current = self.review_port_rules(request, inspect)?;
        if current["snapshot_digest"].as_str() != Some(request.snapshot_digest.as_str()) {
            return Err(refuse("Prima rules changed; review them again"));
        }
        if request.decision == "restore_owned" {
            // No reviewed snapshot is adopted as a creation receipt. Exact cleanup
            // rechecks the CIM instance at removal; changed/unknown rules stay pending.
            self.release_port_rules(&request.network, &request.run_id, remove)?;
        } else {
            // Explicit local user choice preserves every current rule. Forgetting
            // admission ownership neither deletes a rule nor resumes a rank.
            let owner = self.journal.prima_ports.remove(&request.run_id).unwrap();
            if let Err(error) = self.persist() {
                self.journal
                    .prima_ports
                    .insert(request.run_id.clone(), owner);
                return Err(error);
            }
            self.port_deadlines.remove(&request.run_id);
        }
        Ok(json!({"phase":"released","decision":request.decision}))
    }
}

impl Contract {
    pub fn validate(&self, scope: &Scope) -> Result<()> {
        if self.network_id != scope.network
            || self.address != scope.address
            || self.assignment_revision == 0
            || self.assignment_revision != scope.assignment_revision
            || !(8..=30).contains(&scope.prefix)
            || self.rules.is_empty()
            || self.rules.len() > 2
            || self.manifest_digest.len() != 64
            || !self
                .manifest_digest
                .bytes()
                .all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase())
        {
            return Err(refuse("Invalid Prima port contract"));
        }
        let own: Ipv4Addr = self.address.parse()?;
        if !own.is_private() || own.to_string() != self.address {
            return Err(refuse(
                "Prima listener requires a canonical private address",
            ));
        }
        let mut ports = HashSet::new();
        let mask = u32::MAX << (32 - scope.prefix);
        let subnet = u32::from(own) & mask;
        let broadcast = subnet | !mask;
        for rule in &self.rules {
            if rule.port < 1024
                || !ports.insert(rule.port)
                || scope.ports.contains(&rule.port)
                || rule.peers.is_empty()
                || rule.peers.len() > 32
            {
                return Err(refuse("Invalid or occupied Prima listener port"));
            }
            let mut peers = HashSet::new();
            for peer in &rule.peers {
                let ip: Ipv4Addr = peer.parse()?;
                if ip.to_string() != *peer
                    || !scope.peers.contains(peer)
                    || ip == own
                    || u32::from(ip) == subnet
                    || u32::from(ip) == broadcast
                    || !peers.insert(peer)
                    || u32::from(ip) >> (32 - scope.prefix) != u32::from(own) >> (32 - scope.prefix)
                {
                    return Err(refuse(
                        "Prima listener peer is outside its private assignment",
                    ));
                }
            }
        }
        Ok(())
    }
}

pub fn name(scope: &Scope, run: &str, port: u16) -> Result<String> {
    if !valid_uuid(run) || !valid_uuid(&scope.network) {
        return Err(refuse("Invalid Prima run identity"));
    }
    Ok(format!("kllm-prima-{}-{}-{port}", scope.index, run))
}

#[cfg(windows)]
fn snapshot_script(name: &str) -> String {
    format!("$r=@(Get-NetFirewallRule -Name '{name}' -PolicyStore PersistentStore -ErrorAction SilentlyContinue); $current=$null; if ($r.Count -gt 1) {{throw 'Ambiguous Prima firewall ownership'}}; if ($r.Count -eq 1) {{$p=$r|Get-NetFirewallPortFilter; $a=$r|Get-NetFirewallAddressFilter; $f=$r|Get-NetFirewallInterfaceFilter; $current=[pscustomobject]@{{Name=$r[0].Name; InstanceID=$r[0].InstanceID; Direction=$r[0].Direction.ToString(); Action=$r[0].Action.ToString(); Enabled=$r[0].Enabled.ToString(); Profile=$r[0].Profile.ToString(); Protocol=$p.Protocol; Ports=@($p.LocalPort); Local=@($a.LocalAddress); Remote=@($a.RemoteAddress); Interface=@($f.InterfaceAlias)}}}};")
}

#[cfg(windows)]
fn creation_script(name: &str, rule: &Rule) -> String {
    let peers = rule
        .peers
        .iter()
        .map(|p| format!("'{p}'"))
        .collect::<Vec<_>>()
        .join(",");
    format!("$a=Get-NetAdapter -InterfaceIndex $i; $created=New-NetFirewallRule -Name '{name}' -DisplayName 'Kluster Prima private rank' -Direction Inbound -Action Allow -Enabled True -Protocol TCP -LocalAddress $ip -RemoteAddress @({peers}) -LocalPort {} -InterfaceAlias $a.Name -Profile Any -PolicyStore PersistentStore; ConvertTo-Json -InputObject $created.InstanceID -Compress", rule.port)
}

#[cfg(windows)]
fn removal_script(name: &str, current: &Value) -> Result<String> {
    let expected = base64::engine::general_purpose::STANDARD.encode(serde_json::to_vec(current)?);
    // Remove the rechecked CIM instance, never a newly looked-up name.
    Ok(format!("{} if ($null -ne $current) {{$old=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{expected}'))|ConvertFrom-Json; foreach ($field in $old.PSObject.Properties.Name) {{if ((ConvertTo-Json -InputObject $old.$field -Compress -Depth 5) -cne (ConvertTo-Json -InputObject $current.$field -Compress -Depth 5)) {{throw 'Prima firewall ownership changed; cleanup retained'}}}}; $r|Remove-NetFirewallRule}}", snapshot_script(name)))
}

pub fn snapshot(scope: &Scope, run: &str, port: u16) -> Result<Value> {
    let name = name(scope, run, port)?;
    #[cfg(windows)]
    {
        let output = host::powershell(
            scope,
            &format!(
                "{} ConvertTo-Json -InputObject $current -Compress -Depth 5",
                snapshot_script(&name)
            ),
        )?;
        return Ok(serde_json::from_str(&output)?);
    }
    #[cfg(not(windows))]
    {
        let _ = name;
        Err(refuse(
            "Prima private firewall backend requires implementation for this OS",
        ))
    }
}

pub fn create(scope: &Scope, owner: &Owner, rule: &Rule) -> Result<Value> {
    owner.contract.validate(scope)?;
    host::identity(scope)?;
    if !host::has_address(scope)? {
        return Err(refuse("Prima private address disappeared"));
    }
    if !snapshot(scope, &owner.run_id, rule.port)?.is_null() {
        return Err(refuse(
            "Prima firewall rule already exists; ownership not adopted",
        ));
    }
    #[cfg(windows)]
    {
        let name = name(scope, &owner.run_id, rule.port)?;
        let created: Value =
            serde_json::from_str(&host::powershell(scope, &creation_script(&name, rule))?)?;
        let current = snapshot(scope, &owner.run_id, rule.port)?;
        if !created.is_string() || current["InstanceID"] != created {
            return Err(refuse(
                "Created Prima rule identity changed; cleanup requires review",
            ));
        }
        return Ok(current);
    }
    #[cfg(not(windows))]
    {
        Err(refuse(
            "Prima private firewall backend requires implementation for this OS",
        ))
    }
}

pub fn remove(scope: &Scope, owner: &Owner, index: usize) -> Result<()> {
    host::identity(scope)?;
    let rule = &owner.contract.rules[index];
    let current = snapshot(scope, &owner.run_id, rule.port)?;
    if current.is_null() {
        return Ok(());
    }
    if owner
        .snapshots
        .get(index)
        .is_none_or(|old| old.is_null() || old != &current)
    {
        return Err(refuse("Prima firewall ownership changed; cleanup retained"));
    }
    #[cfg(windows)]
    {
        let name = name(scope, &owner.run_id, rule.port)?;
        host::powershell(scope, &removal_script(&name, &current)?)?;
        if !snapshot(scope, &owner.run_id, rule.port)?.is_null() {
            return Err(refuse("Prima firewall rule has not drained"));
        }
        return Ok(());
    }
    #[cfg(not(windows))]
    {
        Err(refuse(
            "Prima private firewall backend requires implementation for this OS",
        ))
    }
}

pub fn status(scope: &Scope, owner: &Owner) -> Result<Value> {
    if owner.phase != "active" || owner.snapshots.len() != owner.contract.rules.len() {
        return Err(refuse("Prima firewall admission is not active"));
    }
    for (i, rule) in owner.contract.rules.iter().enumerate() {
        let current = snapshot(scope, &owner.run_id, rule.port)?;
        if current.is_null() || current != owner.snapshots[i] {
            return Err(refuse("Prima firewall admission changed"));
        }
    }
    Ok(json!({"phase":"admitted", "manifest_digest":owner.contract.manifest_digest}))
}

#[cfg(test)]
mod tests {
    use super::*;
    use ed25519_dalek::Signer;
    use std::fs;
    const RUN: &str = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
    const SESSION: &str = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
    fn scope() -> Scope {
        Scope {
            network: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa".into(),
            adapter: "4".into(),
            mac: String::new(),
            index: 4,
            address: "192.168.234.1".into(),
            prefix: 24,
            management: vec![],
            peers: vec!["192.168.234.2".into()],
            ports: vec![18767, 18768],
            original: Value::Null,
            configured: true,
            added: true,
            uncertain: false,
            term: 1,
            session: "s".into(),
            operation: String::new(),
            revision: 1,
            assignment_revision: 1,
            phase: "apply".into(),
            direct_ports: false,
        }
    }
    fn contract() -> Contract {
        Contract {
            network_id: scope().network,
            manifest_digest: "a".repeat(64),
            address: scope().address,
            assignment_revision: 1,
            rules: vec![Rule {
                port: 9000,
                peers: scope().peers,
            }],
        }
    }
    fn helper(directory: &tempfile::TempDir) -> Helper {
        let grant = super::super::Grant {
            account: "501".into(),
            node_id: "worker".into(),
            master_public_key: URL_SAFE_NO_PAD.encode(
                ed25519_dalek::SigningKey::from_bytes(&[7; 32])
                    .verifying_key()
                    .to_bytes(),
            ),
            cluster_binding_id: scope().network,
        };
        let mut helper = Helper::open(grant, directory.path().join("journal.json")).unwrap();
        let mut s = scope();
        s.session = SESSION.into();
        helper.journal.term = 1;
        helper.journal.session = SESSION.into();
        helper.journal.scopes.insert(s.network.clone(), s);
        helper.persist().unwrap();
        helper
    }
    fn payload() -> Value {
        let key = ed25519_dalek::SigningKey::from_bytes(&[7; 32]);
        json!({"version":1,"type":"rpc_command","cluster_binding_id":scope().network,
            "master_node_id":"master","master_key_id":Sha256::digest(key.verifying_key().to_bytes()).iter().map(|b|format!("{b:02x}")).collect::<String>(),
            "master_term":1,"master_session_id":SESSION,"channel_id":"dddddddd-dddd-4ddd-8ddd-dddddddddddd",
            "sender_node_id":"master","target_node_id":"worker","sequence":1,
            "request_id":"eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee","run_id":RUN,
            "action":"prima_admit_ports","ttl_ms":10000,"body":{"contract":contract()}})
    }
    fn signed(payload: Value) -> Request {
        let payload = payload.to_string();
        let mut bytes = b"kllm-managed-network-v1\0".to_vec();
        bytes.extend(payload.as_bytes());
        let signature = ed25519_dalek::SigningKey::from_bytes(&[7; 32]).sign(&bytes);
        serde_json::from_value(json!({"action":"prima_ports_apply","network":scope().network,"run_id":RUN,
            "wire":json!({"payload":payload,"signature":URL_SAFE_NO_PAD.encode(signature.to_bytes())}).to_string()})).unwrap()
    }
    fn owned() -> Owner {
        Owner {
            run_id: RUN.into(),
            contract: contract(),
            term: 1,
            session: SESSION.into(),
            snapshots: vec![],
            phase: "applying".into(),
        }
    }
    #[test]
    fn exact_private_contract_refuses_broad_or_changed_access() {
        let s = scope();
        let good = contract();
        assert!(good.validate(&s).is_ok());
        for peer in [
            "0.0.0.0",
            "8.8.8.8",
            "192.168.234.3",
            "192.168.234.2/24",
            "192.168.234.2';x",
        ] {
            let mut bad = good.clone();
            bad.rules[0].peers = vec![peer.into()];
            assert!(bad.validate(&s).is_err());
        }
        for port in [1, 1023, 18767] {
            let mut bad = good.clone();
            bad.rules[0].port = port;
            assert!(bad.validate(&s).is_err());
        }
        let mut bad = good.clone();
        bad.rules.push(bad.rules[0].clone());
        assert!(bad.validate(&s).is_err());
        assert!(name(&s, "not-a-run';delete", 9000).is_err());
        for prefix in [0, 7, 31, 32, 255] {
            let mut bad = s.clone();
            bad.prefix = prefix;
            assert!(good.validate(&bad).is_err());
        }
        let mut stale = good.clone();
        stale.assignment_revision = 2;
        assert!(stale.validate(&s).is_err());
        let mut old = s.clone();
        old.assignment_revision = 0;
        assert!(good.validate(&old).is_err());
    }
    #[test]
    #[cfg(windows)]
    fn windows_rule_scripts_parse_without_executing_firewall_commands() {
        let s = scope();
        let rule = &contract().rules[0];
        let name = name(&s, RUN, rule.port).unwrap();
        for script in [
            snapshot_script(&name),
            creation_script(&name, rule),
            removal_script(&name, &json!({"InstanceID":"owned", "Ports":["9000"]})).unwrap(),
        ] {
            let encoded = base64::engine::general_purpose::STANDARD.encode(script.as_bytes());
            let parser = format!("$tokens=$null; $errors=$null; [System.Management.Automation.Language.Parser]::ParseInput([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded}')), [ref]$tokens, [ref]$errors)|Out-Null; if ($errors.Count) {{throw ($errors|Out-String)}}; 'parsed'");
            assert_eq!(host::powershell(&s, &parser).unwrap().trim(), "parsed");
        }
    }
    #[test]
    fn accepts_python_signed_runtime_contract_vector() {
        let dir = tempfile::tempdir().unwrap();
        let mut h = helper(&dir);
        let request = serde_json::from_value(json!({"action":"prima_ports_apply","network":scope().network,
            "run_id":RUN,"wire":include_str!("../../../../../tests/fixtures/prima-ports-command.json").trim()})).unwrap();
        let owner = h.authorize_prima_ports(&request).unwrap();
        assert_eq!(owner.contract, contract());
        assert_eq!(owner.run_id, RUN);
        assert!(h.authorize_prima_ports(&request).is_err());
    }
    #[test]
    fn direct_mode_uses_the_same_signature_contract_and_never_adopts_other_backend_scope() {
        let dir = tempfile::tempdir().unwrap();
        let mut h = helper(&dir);
        h.direct_ports = true;
        let request = signed(payload());
        assert!(h.authorize_prima_ports(&request).is_err());
        assert!(h.journal.prima_sequences.is_empty());
        h.journal
            .scopes
            .get_mut(&scope().network)
            .unwrap()
            .direct_ports = true;
        assert_eq!(
            h.authorize_prima_ports(&request).unwrap().contract,
            contract()
        );
        assert!(h.authorize_prima_ports(&request).is_err());
    }
    #[test]
    fn signed_renewal_requires_exact_current_unexpired_owner() {
        let dir = tempfile::tempdir().unwrap();
        let mut h = helper(&dir);
        h.apply_port_rules(owned(), |_, _, _| Ok(json!({"InstanceID":"owned"})))
            .unwrap();
        let mut p = payload();
        p["action"] = json!("prima_renew_ports");
        let mut request = signed(p.clone());
        request.action = "prima_ports_renew".into();
        let candidate = h.authorize_prima_ports(&request).unwrap();
        let now = Instant::now();
        h.renew_port_rules(&candidate, now, |_, _| {
            Ok(json!({"phase":"admitted","manifest_digest":"a".repeat(64)}))
        })
        .unwrap();
        assert!(h.port_deadlines[RUN] >= now + RUNNING_LEASE);
        assert!(h.authorize_prima_ports(&request).is_err());
        let previous = h.port_deadlines[RUN];
        let mut changed = candidate.clone();
        changed.contract.rules[0].port += 1;
        assert!(h
            .renew_port_rules(&changed, now, |_, _| panic!(
                "changed renewal cannot inspect/mutate rules"
            ))
            .is_err());
        assert!(h
            .renew_port_rules(&candidate, now, |_, _| Err(refuse("CIM identity changed")))
            .is_err());
        assert_eq!(h.port_deadlines[RUN], previous);
        assert!(h
            .renew_port_rules(&candidate, previous, |_, _| panic!(
                "expired lease cannot revive"
            ))
            .is_err());
        h.journal.prima_ports.get_mut(RUN).unwrap().phase = "cleanup_pending".into();
        assert!(h
            .renew_port_rules(&candidate, now, |_, _| panic!("cleanup cannot resume"))
            .is_err());
    }
    #[test]
    fn expiry_retains_changed_rules_and_drains_other_expired_runs() {
        let dir = tempfile::tempdir().unwrap();
        let mut h = helper(&dir);
        h.apply_port_rules(owned(), |_, _, _| Ok(json!({"InstanceID":"changed"})))
            .unwrap();
        let mut other = owned();
        other.run_id = SESSION.into();
        other.contract.rules[0].port = 9100;
        h.apply_port_rules(other, |_, _, _| Ok(json!({"InstanceID":"exact"})))
            .unwrap();
        let future = Instant::now() + ADMISSION_LEASE + Duration::from_secs(1);
        let mut calls = vec![];
        assert!(h
            .expire_port_rules(future, |_, owner, _| {
                calls.push(owner.run_id.clone());
                if owner.run_id == RUN {
                    Err(refuse("Changed rule is not adopted"))
                } else {
                    Ok(())
                }
            })
            .is_err());
        assert_eq!(calls.len(), 2);
        assert_eq!(h.journal.prima_ports[RUN].phase, "cleanup_pending");
        assert!(!h.journal.prima_ports.contains_key(SESSION));
        assert!(!h.port_deadlines.contains_key(SESSION));
        let saved: super::super::Journal =
            serde_json::from_slice(&fs::read(&h.path).unwrap()).unwrap();
        assert_eq!(saved.prima_ports.len(), 1);
        h.expire_port_rules(Instant::now(), |_, _, _| Ok(()))
            .unwrap();
        assert!(h.journal.prima_ports.is_empty());
    }
    #[test]
    fn native_watchdog_expires_without_agent_or_ipc_requests() {
        use super::super::watchdog::Watchdog;
        use std::sync::{mpsc, Arc, Mutex};
        let dir = tempfile::tempdir().unwrap();
        let mut h = helper(&dir);
        h.apply_port_rules(owned(), |_, _, _| Ok(json!({"InstanceID":"owned"})))
            .unwrap();
        h.port_deadlines.insert(RUN.into(), Instant::now());
        let shared = Arc::new(Mutex::new(h));
        let (expired, observed) = mpsc::channel();
        let timer = Watchdog::start(
            Arc::downgrade(&shared),
            Duration::from_millis(10),
            move |helper, now| {
                helper.expire_port_rules(now, |_, owner, _| {
                    assert_eq!(owner.run_id, RUN);
                    assert_eq!(owner.snapshots[0]["InstanceID"], "owned");
                    expired.send(()).unwrap();
                    Ok(())
                })
            },
        );
        observed.recv_timeout(Duration::from_secs(2)).unwrap();
        drop(timer);
        let helper = shared.lock().unwrap();
        assert!(helper.journal.prima_ports.is_empty());
        let saved: super::super::Journal =
            serde_json::from_slice(&fs::read(&helper.path).unwrap()).unwrap();
        assert!(saved.prima_ports.is_empty());
    }
    #[test]
    fn renewed_native_lease_survives_idle_watchdog_ticks() {
        use super::super::watchdog::Watchdog;
        use std::sync::{mpsc, Arc, Mutex};
        let dir = tempfile::tempdir().unwrap();
        let mut h = helper(&dir);
        h.apply_port_rules(owned(), |_, _, _| Ok(json!({"InstanceID":"owned"})))
            .unwrap();
        h.renew_port_rules(&owned(), Instant::now(), |_, _| {
            Ok(json!({"phase":"admitted","manifest_digest":"a".repeat(64)}))
        })
        .unwrap();
        let shared = Arc::new(Mutex::new(h));
        let (ticked, observed) = mpsc::channel();
        let timer = Watchdog::start(
            Arc::downgrade(&shared),
            Duration::from_millis(10),
            move |helper, now| {
                helper.expire_port_rules(now, |_, _, _| {
                    panic!("current lease must not remove any rule")
                })?;
                let _ = ticked.send(());
                Ok(())
            },
        );
        observed.recv_timeout(Duration::from_secs(2)).unwrap();
        drop(timer);
        assert_eq!(
            shared.lock().unwrap().journal.prima_ports[RUN].phase,
            "active"
        );
    }
    #[test]
    fn signed_assignment_and_replay_fences_are_durable() {
        let dir = tempfile::tempdir().unwrap();
        let mut h = helper(&dir);
        assert!(h.authorize_prima_ports(&signed(payload())).is_ok());
        assert!(h.authorize_prima_ports(&signed(payload())).is_err());
        let saved: super::super::Journal =
            serde_json::from_slice(&fs::read(&h.path).unwrap()).unwrap();
        assert_eq!(
            saved.prima_sequences.values().copied().collect::<Vec<_>>(),
            vec![1]
        );
        let mut next = payload();
        next["sequence"] = json!(2);
        assert!(h.authorize_prima_ports(&signed(next)).is_ok());
        h.journal.revoked = true;
        let mut next = payload();
        next["sequence"] = json!(3);
        assert!(h.authorize_prima_ports(&signed(next)).is_err());
    }
    #[test]
    fn signed_contract_rejects_foreign_or_stale_commands_before_mutation() {
        let dir = tempfile::tempdir().unwrap();
        let mut h = helper(&dir);
        for (field, value) in [
            ("version", json!(2)),
            ("type", json!("rpc_result")),
            ("action", json!("start")),
            ("target_node_id", json!("other")),
            ("sender_node_id", json!("worker")),
            ("master_key_id", json!("f".repeat(64))),
            ("master_term", json!(2)),
            ("master_session_id", json!(RUN)),
            ("channel_id", json!("invalid")),
            ("request_id", json!("invalid")),
            ("run_id", json!(SESSION)),
            ("sequence", json!(true)),
            ("ttl_ms", json!(60001)),
        ] {
            let mut p = payload();
            p[field] = value;
            assert!(h.authorize_prima_ports(&signed(p)).is_err(), "{field}");
        }
        let mut p = payload();
        p["body"]["contract"]["assignment_revision"] = json!(2);
        assert!(h.authorize_prima_ports(&signed(p)).is_err());
        let mut p = payload();
        p["body"]["program"] = json!("powershell.exe");
        assert!(h.authorize_prima_ports(&signed(p)).is_err());
        let mut req = signed(payload());
        req.wire = req.wire.replace("9000", "9001");
        assert!(h.authorize_prima_ports(&req).is_err());
        assert!(h.journal.prima_sequences.is_empty());
        assert!(h.journal.prima_ports.is_empty());
    }
    #[test]
    fn intent_precedes_mutation_and_partial_failure_retains_owned_cleanup() {
        let dir = tempfile::tempdir().unwrap();
        let mut h = helper(&dir);
        let path = h.path.clone();
        let mut owner = owned();
        owner.contract.rules.push(Rule {
            port: 9001,
            peers: scope().peers,
        });
        let mut calls = 0;
        assert!(h
            .apply_port_rules(owner, |_, owner, rule| {
                let saved: super::super::Journal =
                    serde_json::from_slice(&fs::read(&path)?).unwrap();
                assert_eq!(saved.prima_ports[RUN].phase, "applying");
                assert_eq!(saved.prima_ports[RUN].contract, owner.contract);
                calls += 1;
                if rule.port == 9001 {
                    return Err(refuse("second rule failed"));
                }
                Ok(json!({"InstanceID":"owned-first"}))
            })
            .is_err());
        assert_eq!(calls, 2);
        let saved: super::super::Journal =
            serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
        assert_eq!(saved.prima_ports[RUN].phase, "cleanup_pending");
        assert_eq!(
            saved.prima_ports[RUN].snapshots,
            vec![json!({"InstanceID":"owned-first"}), Value::Null]
        );
        assert!(h
            .dispatch(serde_json::from_value(json!({"action":"revoke"})).unwrap())
            .is_err());
        assert!(h
            .dispatch(
                serde_json::from_value(json!({"action":"restore","network":scope().network}))
                    .unwrap()
            )
            .is_err());
        h.release_port_rules(&scope().network, RUN, |_, o, i| {
            if i == 0 {
                assert_eq!(o.snapshots[i]["InstanceID"], "owned-first");
            }
            Ok(())
        })
        .unwrap();
        assert!(h.journal.prima_ports.is_empty());
    }
    #[test]
    fn cleanup_failure_never_publishes_release_or_forgets_other_runs() {
        let dir = tempfile::tempdir().unwrap();
        let mut h = helper(&dir);
        h.apply_port_rules(owned(), |_, _, _| Ok(json!({"InstanceID":"owned"})))
            .unwrap();
        let mut other = owned();
        other.run_id = SESSION.into();
        other.contract.rules[0].port = 9100;
        h.apply_port_rules(other, |_, _, _| Ok(json!({"InstanceID":"unrelated"})))
            .unwrap();
        assert!(h
            .release_port_rules(&scope().network, RUN, |_, _, _| Err(refuse("rule changed")))
            .is_err());
        assert_eq!(h.journal.prima_ports[RUN].phase, "cleanup_pending");
        assert_eq!(
            h.journal.prima_ports[RUN].snapshots[0]["InstanceID"],
            "owned"
        );
        assert_eq!(h.journal.prima_ports[SESSION].phase, "active");
        assert!(h
            .release_port_rules(SESSION, RUN, |_, _, _| panic!(
                "foreign cleanup must not mutate"
            ))
            .is_err());
        h.release_port_rules(&scope().network, RUN, |_, _, _| Ok(()))
            .unwrap();
        assert!(!h.journal.prima_ports.contains_key(RUN));
        assert!(h.journal.prima_ports.contains_key(SESSION));
    }

    fn recovery_request(action: &str) -> Request {
        serde_json::from_value(json!({"action":action,"network":scope().network,
            "run_id":RUN,"decision":"keep_current"}))
        .unwrap()
    }
    #[test]
    fn local_review_binds_unknown_changed_absent_rules_and_durable_owner() {
        let dir = tempfile::tempdir().unwrap();
        let mut h = helper(&dir);
        h.apply_port_rules(owned(), |_, _, _| Ok(json!({"InstanceID":"owned"})))
            .unwrap();
        let req = recovery_request("prima_ports_review");
        assert!(h
            .review_port_rules(&req, |_, _, _| panic!("Live admission cannot be reviewed"))
            .is_err());
        h.journal.prima_ports.get_mut(RUN).unwrap().phase = "cleanup_pending".into();
        for (observed, expected) in [
            (json!({"InstanceID":"owned"}), "exact"),
            (json!({"InstanceID":"foreign"}), "changed"),
            (Value::Null, "absent"),
        ] {
            let review = h
                .review_port_rules(&req, |_, _, _| Ok(observed.clone()))
                .unwrap();
            assert_eq!(review["rules"][0]["ownership"], expected);
        }
        h.journal.prima_ports.get_mut(RUN).unwrap().snapshots[0] = Value::Null;
        let review = h
            .review_port_rules(&req, |_, _, _| Ok(json!({"InstanceID":"lost"})))
            .unwrap();
        assert_eq!(review["rules"][0]["ownership"], "unknown");
        let mut foreign = recovery_request("prima_ports_review");
        foreign.network = SESSION.into();
        assert!(h
            .review_port_rules(&foreign, |_, _, _| panic!("Foreign network cannot inspect"))
            .is_err());
    }
    #[test]
    fn explicit_keep_preserves_rules_and_refuses_stale_review() {
        let dir = tempfile::tempdir().unwrap();
        let mut h = helper(&dir);
        h.apply_port_rules(owned(), |_, _, _| Ok(json!({"InstanceID":"owned"})))
            .unwrap();
        h.journal.prima_ports.get_mut(RUN).unwrap().phase = "cleanup_pending".into();
        let mut req = recovery_request("prima_ports_resolve");
        let observed = json!({"InstanceID":"foreign","Local":["192.168.234.1"]});
        let inspect = |_: &Scope, _: &str, _: u16| Ok(observed.clone());
        let review = h.review_port_rules(&req, inspect).unwrap();
        req.snapshot_digest = review["snapshot_digest"].as_str().unwrap().into();
        assert!(h
            .resolve_port_rules(
                &req,
                |_, _, _| Ok(Value::Null),
                |_, _, _| panic!("Stale review cannot delete")
            )
            .is_err());
        h.journal.prima_ports.get_mut(RUN).unwrap().term += 1;
        assert!(h
            .resolve_port_rules(&req, inspect, |_, _, _| panic!(
                "Changed owner cannot delete"
            ))
            .is_err());
        h.journal.prima_ports.get_mut(RUN).unwrap().term -= 1;
        assert_eq!(
            h.resolve_port_rules(&req, inspect, |_, _, _| panic!(
                "Keep cannot mutate any OS rule"
            ))
            .unwrap(),
            json!({"phase":"released","decision":"keep_current"})
        );
        assert!(h.journal.prima_ports.is_empty() && h.port_deadlines.is_empty());
        let saved: super::super::Journal =
            serde_json::from_slice(&fs::read(&h.path).unwrap()).unwrap();
        assert!(saved.prima_ports.is_empty());
    }
    #[test]
    fn reviewed_restore_cannot_adopt_changed_rules_and_retains_failure() {
        let dir = tempfile::tempdir().unwrap();
        let mut h = helper(&dir);
        h.apply_port_rules(owned(), |_, _, _| Ok(json!({"InstanceID":"owned"})))
            .unwrap();
        h.journal.prima_ports.get_mut(RUN).unwrap().phase = "cleanup_pending".into();
        let mut req = recovery_request("prima_ports_resolve");
        req.decision = "restore_owned".into();
        let inspect = |_: &Scope, _: &str, _: u16| Ok(json!({"InstanceID":"changed"}));
        let review = h.review_port_rules(&req, inspect).unwrap();
        req.snapshot_digest = review["snapshot_digest"].as_str().unwrap().into();
        assert!(h
            .resolve_port_rules(&req, inspect, |_, owner, _| {
                assert_eq!(owner.snapshots[0]["InstanceID"], "owned");
                Err(refuse("Exact CIM snapshot differs"))
            })
            .is_err());
        assert_eq!(h.journal.prima_ports[RUN].phase, "cleanup_pending");
        assert_eq!(
            h.journal.prima_ports[RUN].snapshots[0]["InstanceID"],
            "owned"
        );
    }
    #[test]
    fn failed_keep_persistence_restores_ledger_and_preserves_other_runs() {
        let dir = tempfile::tempdir().unwrap();
        let mut h = helper(&dir);
        h.apply_port_rules(owned(), |_, _, _| Ok(json!({"InstanceID":"owned"})))
            .unwrap();
        h.journal.prima_ports.get_mut(RUN).unwrap().phase = "cleanup_pending".into();
        let mut other = owned();
        other.run_id = SESSION.into();
        other.contract.rules[0].port = 9100;
        h.apply_port_rules(other, |_, _, _| Ok(json!({"InstanceID":"unrelated"})))
            .unwrap();
        let mut req = recovery_request("prima_ports_resolve");
        let inspect = |_: &Scope, _: &str, _: u16| Ok(json!({"InstanceID":"changed"}));
        let review = h.review_port_rules(&req, inspect).unwrap();
        req.snapshot_digest = review["snapshot_digest"].as_str().unwrap().into();
        let good_path = h.path.clone();
        h.path = dir.path().join("missing-parent").join("journal.json");
        assert!(h
            .resolve_port_rules(&req, inspect, |_, _, _| panic!("Keep cannot delete"))
            .is_err());
        assert!(h.journal.prima_ports.contains_key(RUN) && h.port_deadlines.contains_key(RUN));
        h.path = good_path;
        h.resolve_port_rules(&req, inspect, |_, _, _| panic!("Keep cannot delete"))
            .unwrap();
        assert!(!h.journal.prima_ports.contains_key(RUN));
        assert_eq!(
            h.journal.prima_ports[SESSION].snapshots[0]["InstanceID"],
            "unrelated"
        );
        let status = h
            .dispatch(serde_json::from_value(json!({"action":"status"})).unwrap())
            .unwrap();
        assert_eq!(status["prima_ports_recovery_version"], 1);
        assert_eq!(status["prima_listener_recovery"], json!([]));
        h.port_deadlines.insert(SESSION.into(), Instant::now());
        let status = h
            .dispatch(serde_json::from_value(json!({"action":"status"})).unwrap())
            .unwrap();
        assert_eq!(status["prima_listener_recovery"][0]["run_id"], SESSION);
    }
    #[test]
    fn expired_review_and_exact_restore_drain_without_adopting_observation() {
        let dir = tempfile::tempdir().unwrap();
        let mut h = helper(&dir);
        h.apply_port_rules(owned(), |_, _, _| Ok(json!({"InstanceID":"owned"})))
            .unwrap();
        h.port_deadlines.insert(RUN.into(), Instant::now());
        let mut req = recovery_request("prima_ports_resolve");
        req.decision = "restore_owned".into();
        let inspect = |_: &Scope, _: &str, _: u16| Ok(json!({"InstanceID":"owned"}));
        let review = h.review_port_rules(&req, inspect).unwrap();
        req.snapshot_digest = review["snapshot_digest"].as_str().unwrap().into();
        let result = h
            .resolve_port_rules(&req, inspect, |_, owner, index| {
                assert_eq!(owner.snapshots[index]["InstanceID"], "owned");
                Ok(())
            })
            .unwrap();
        assert_eq!(
            result,
            json!({"phase":"released","decision":"restore_owned"})
        );
        assert!(h.journal.prima_ports.is_empty() && h.port_deadlines.is_empty());
    }
    #[test]
    fn restart_restores_cleanup_only_and_refuses_listener_collision() {
        let dir = tempfile::tempdir().unwrap();
        let mut h = helper(&dir);
        h.apply_port_rules(owned(), |_, _, _| Ok(json!({"InstanceID":"owned"})))
            .unwrap();
        let mut collision = owned();
        collision.run_id = SESSION.into();
        assert!(h
            .apply_port_rules(collision, |_, _, _| panic!(
                "reserved listener must not mutate"
            ))
            .is_err());
        // This test never invokes platform commands; address recovery has its own tests.
        let assignment = h.journal.scopes.get_mut(&scope().network).unwrap();
        assignment.configured = false;
        assignment.added = false;
        h.persist().unwrap();
        let path = h.path.clone();
        let grant = h.grant.clone();
        drop(h);
        let mut restored = Helper::open(grant, path).unwrap();
        assert_eq!(restored.journal.prima_ports[RUN].phase, "cleanup_pending");
        assert_eq!(
            restored.journal.prima_ports[RUN].snapshots[0]["InstanceID"],
            "owned"
        );
        assert!(restored.authorize_prima_ports(&signed(payload())).is_err());
        restored
            .release_port_rules(&scope().network, RUN, |_, _, _| Ok(()))
            .unwrap();
        assert!(restored.journal.prima_ports.is_empty());
    }
}
