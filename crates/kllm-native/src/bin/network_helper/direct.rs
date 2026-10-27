//! Administrator-owned stdio process: native listener policy, never address mutation.
use super::{host, ports, refuse, valid_uuid, Helper, Request, Result};
use serde_json::{json, Value};
use std::{
    io::{Read, Write},
    sync::{Arc, Mutex},
    time::Instant,
};

pub(super) fn reconcile_scope(
    prior: &super::Scope,
    command: &Value,
    node: &str,
) -> Result<super::Scope> {
    let assignment = &command["body"]["assignment"];
    if prior.ports.len() != 2
        || !(8..=30).contains(&prior.prefix)
        || prior.uncertain
        || !prior.added
        || !prior.configured
        || prior.phase != "apply"
        || assignment["network_id"] != prior.network
        || assignment["node_id"] != node
        || assignment["adapter_id"] != prior.adapter
        || assignment["address"] != prior.address
        || assignment["prefix_length"] != prior.prefix
        || assignment["assignment_revision"] != prior.assignment_revision
        || command["assignment_revision"] != prior.assignment_revision
        || command["body"]["bridge_port"] != prior.ports[0]
        || command["body"]["diagnostic_port"] != prior.ports[1]
        || command["operation_revision"]
            .as_u64()
            .is_none_or(|v| v < prior.revision)
    {
        return Err(refuse(
            "Reconcile cannot change or revive a private assignment",
        ));
    }
    let mut next = prior.clone();
    let participants = command["body"]["participants"]
        .as_array()
        .ok_or_else(|| refuse("Missing participants"))?;
    let mut peers = vec![];
    let mut nodes = std::collections::HashSet::new();
    let mut addresses = std::collections::HashSet::new();
    for member in participants {
        let member_node = member["node_id"]
            .as_str()
            .ok_or_else(|| refuse("Missing participant identity"))?;
        let address = member["address"]
            .as_str()
            .ok_or_else(|| refuse("Missing peer address"))?;
        if member["network_id"] != prior.network
            || member["prefix_length"] != prior.prefix
            || !nodes.insert(member_node)
            || !addresses.insert(address)
        {
            return Err(refuse(
                "Reconcile participants are duplicated or outside the assignment",
            ));
        }
        if member_node == node {
            if address != prior.address {
                return Err(refuse("Reconcile local address differs"));
            }
        } else {
            peers.push(address.to_owned());
        }
    }
    if !nodes.contains(node) || peers.is_empty() || peers.len() > 255 {
        return Err(refuse("Reconcile participant set is incomplete"));
    }
    next.peers = peers;
    // Reuse exact private subnet/host validation without manufacturing admission.
    for peer in &next.peers {
        let ip: std::net::Ipv4Addr = peer.parse()?;
        let own: std::net::Ipv4Addr = next.address.parse()?;
        let mask = u32::MAX << (32 - next.prefix);
        let bits = u32::from(ip);
        let network = u32::from(own) & mask;
        if ip.to_string() != *peer
            || !ip.is_private()
            || bits & mask != network
            || bits == network
            || bits == network | !mask
            || ip == own
        {
            return Err(refuse(
                "Reconcile peer is outside the usable private subnet",
            ));
        }
    }
    next.operation = command["operation_id"].as_str().unwrap().into();
    next.revision = command["operation_revision"].as_u64().unwrap();
    next.term = command["master_term"].as_u64().unwrap();
    next.session = command["master_session_id"].as_str().unwrap().into();
    next.phase = "apply".into();
    Ok(next)
}

impl Helper {
    pub(super) fn dispatch_direct(&mut self, request: &Request) -> Result<Value> {
        if !self.direct_ports || !valid_uuid(&request.network) {
            return Err(refuse(
                "Direct Prima mode requires its owned private network",
            ));
        }
        if self
            .journal
            .prima_ports
            .values()
            .any(|o| o.contract.network_id == request.network)
        {
            return Err(refuse(
                "Drain native Prima listeners before changing their direct scope",
            ));
        }
        if request.action == "direct_forget" && !self.journal.scopes.contains_key(&request.network)
        {
            return Ok(json!({"ok":true})); // Legacy manual address, never owned by this ledger.
        }
        let scope = self
            .journal
            .scopes
            .get(&request.network)
            .ok_or_else(|| refuse("Direct private assignment is absent"))?
            .clone();
        if !scope.direct_ports {
            return Err(refuse(
                "Direct mode cannot adopt protected-helper address ownership",
            ));
        }
        if request.action == "direct_observe" {
            if scope.phase != "apply" || scope.uncertain {
                return Err(refuse(
                    "Direct private assignment is not currently authorized",
                ));
            }
            host::validate(&scope)?;
            if host::configuration(&scope)?["dhcp"] != false || !host::has_address(&scope)? {
                return Err(refuse(
                    "Manual direct mode requires its configured private address and disabled DHCP",
                ));
            }
            let current = self.journal.scopes.get_mut(&request.network).unwrap();
            // These flags track admission continuity. No address/DHCP mutation
            // was performed and direct mode never calls host::restore/delete.
            current.added = true;
            current.configured = true;
            self.check_ownership(&request.network)?;
            if self.journal.scopes[&request.network].uncertain {
                return Err(refuse("Direct private address observation lost continuity"));
            }
        } else {
            // Trusted local administrator narrows this bookkeeping only. The
            // agent separately fences rank stop and reviewed/manual address cleanup.
            let current = self.journal.scopes.get_mut(&request.network).unwrap();
            current.added = false;
            current.configured = false;
            current.uncertain = false;
            self.ownership.remove(&request.network);
        }
        self.persist()?;
        Ok(json!({"ok":true}))
    }

    fn direct_response(&mut self, raw: &[u8]) -> Value {
        let request = serde_json::from_slice::<Request>(raw);
        let request_id = request
            .as_ref()
            .map(|r| r.request_id.clone())
            .unwrap_or_default();
        let result = request.map_err(Into::into).and_then(|request| {
            if !valid_uuid(&request.request_id) {
                return Err(refuse(
                    "Direct helper requests require a transport identity",
                ));
            }
            self.dispatch(request)
        });
        match result {
            Ok(value) => json!({"request_id":request_id,"result":value}),
            Err(error) => json!({"request_id":request_id,"error":error.to_string()}),
        }
    }
}

pub(super) fn serve(helper: Arc<Mutex<Helper>>) -> Result<()> {
    let mut input = std::io::stdin().lock();
    let mut output = std::io::stdout().lock();
    let result = (|| {
        loop {
            let mut header = [0; 4];
            match input.read_exact(&mut header) {
                Ok(()) => {}
                Err(error) if error.kind() == std::io::ErrorKind::UnexpectedEof => break,
                Err(error) => return Err(error.into()),
            }
            let length = u32::from_be_bytes(header) as usize;
            if length > 65536 {
                return Err(refuse("Direct helper request exceeds 64 KiB"));
            }
            let mut raw = vec![0; length];
            input.read_exact(&mut raw)?;
            let response = helper
                .lock()
                .map_err(|_| refuse("Direct helper ownership state is unavailable"))?
                .direct_response(&raw);
            let bytes = serde_json::to_vec(&response)?;
            if bytes.len() > 65536 {
                return Err(refuse("Direct helper response exceeds 64 KiB"));
            }
            output.write_all(&(bytes.len() as u32).to_be_bytes())?;
            output.write_all(&bytes)?;
            output.flush()?;
        }
        Ok(())
    })();
    // Agent exit or broken stdio never grants a fresh lease. Retain changed/failed
    // cleanup in the durable ledger; removing rules is not a rank-stop proof.
    let mut state = helper
        .lock()
        .map_err(|_| refuse("Direct helper cleanup state is unavailable"))?;
    for owner in state.journal.prima_ports.values_mut() {
        owner.phase = "cleanup_pending".into();
    }
    state.persist()?;
    let cleanup = state.expire_port_rules(Instant::now(), ports::remove);
    result.and(cleanup)
}

#[cfg(test)]
mod tests {
    use super::super::{Grant, Scope};
    use super::*;
    const NET: &str = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
    const ID: &str = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
    fn scope() -> Scope {
        serde_json::from_value(json!({"network":NET,"adapter":"4","mac":"never-inspected",
            "index":4,"address":"192.168.234.1","prefix":24,"management":[],
            "peers":["192.168.234.2"],"ports":[18767,18768],"original":{"dhcp":false},
            "configured":true,"added":true,"uncertain":false,"term":1,"session":ID,
            "operation":ID,"revision":1,"assignment_revision":1,"phase":"apply","direct_ports":true})).unwrap()
    }
    fn helper(dir: &tempfile::TempDir) -> Helper {
        Helper::open_mode(
            Grant {
                account: "501".into(),
                node_id: "worker".into(),
                master_public_key: String::new(),
                cluster_binding_id: NET.into(),
            },
            dir.path().join("journal.json"),
            true,
        )
        .unwrap()
    }
    #[test]
    fn direct_surface_refuses_all_address_and_authorization_mutations() {
        let dir = tempfile::tempdir().unwrap();
        let mut h = helper(&dir);
        for action in [
            "add",
            "delete",
            "configure",
            "restore",
            "revoke",
            "observe_start",
        ] {
            let response = h.direct_response(
                json!({"action":action,"request_id":ID,"network":NET})
                    .to_string()
                    .as_bytes(),
            );
            assert!(response.get("error").is_some());
            assert_eq!(response["request_id"], ID);
        }
        assert!(h
            .direct_response(b"{\"action\":\"status\"}")
            .get("error")
            .is_some());
        let response = h.direct_response(
            json!({"action":"status","request_id":ID})
                .to_string()
                .as_bytes(),
        );
        assert_eq!(response["result"]["prima_direct_version"], 1);
    }
    #[test]
    fn direct_restart_never_restores_dhcp_or_deletes_manual_address() {
        let dir = tempfile::tempdir().unwrap();
        let mut h = helper(&dir);
        h.journal.scopes.insert(NET.into(), scope());
        h.persist().unwrap();
        let grant = h.grant.clone();
        let path = h.path.clone();
        drop(h);
        // The deliberately invalid adapter would fail a host mutation. Direct
        // reconstruction must retain continuity loss without calling that host.
        let mut h = Helper::open_mode(grant, path, true).unwrap();
        assert!(h.journal.scopes[NET].uncertain);
        assert_eq!(h.journal.scopes[NET].original, json!({"dhcp":false}));
        let response = h.direct_response(
            json!({"action":"direct_forget","network":NET,"request_id":ID})
                .to_string()
                .as_bytes(),
        );
        assert_eq!(response["result"], json!({"ok":true}));
        assert!(!h.journal.scopes[NET].configured && !h.journal.scopes[NET].added);
        assert_eq!(h.journal.scopes[NET].original, json!({"dhcp":false}));
    }
    fn reconcile() -> Value {
        let assignment = json!({"network_id":NET,"node_id":"worker","adapter_id":"4",
            "address":"192.168.234.1","prefix_length":24,"assignment_revision":1});
        json!({"assignment_revision":1,"master_term":1,"master_session_id":ID,"operation_id":ID,
            "operation_revision":1,"body":{"assignment":assignment.clone(),"bridge_port":18767,"diagnostic_port":18768,
                "participants":[assignment,{"network_id":NET,"node_id":"master","address":"192.168.234.2","prefix_length":24},
                    {"network_id":NET,"node_id":"new-worker","address":"192.168.234.3","prefix_length":24}]}})
    }
    #[test]
    fn reconciliation_updates_peers_without_changing_address_ownership() {
        let prior = scope();
        let next = reconcile_scope(&prior, &reconcile(), "worker").unwrap();
        assert_eq!(next.peers, vec!["192.168.234.2", "192.168.234.3"]);
        assert_eq!(next.address, prior.address);
        assert_eq!(next.original, prior.original);
        assert!(next.added && next.configured && next.direct_ports);
        for field in ["address", "adapter_id", "assignment_revision"] {
            let mut bad = reconcile();
            bad["body"]["assignment"][field] = json!("changed");
            assert!(reconcile_scope(&prior, &bad, "worker").is_err());
        }
        let mut bad = reconcile();
        bad["body"]["participants"][2]["address"] = json!("8.8.8.8");
        assert!(reconcile_scope(&prior, &bad, "worker").is_err());
        let mut uncertain = prior.clone();
        uncertain.uncertain = true;
        assert!(reconcile_scope(&uncertain, &reconcile(), "worker").is_err());
    }
}
