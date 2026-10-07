"""Bounded records shared by persistence and the signed control channel."""
from __future__ import annotations

import ipaddress
import base64
import hashlib
from datetime import datetime, timezone
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

MAX_COUNTER = (1 << 53) - 1
Counter = Annotated[int, Field(strict=True, ge=0, le=MAX_COUNTER)]
Revision = Annotated[int, Field(strict=True, ge=1, le=MAX_COUNTER)]
Port = Annotated[int, Field(strict=True, ge=1, le=65535)]
Identifier = Annotated[str, StringConstraints(pattern=r'^[A-Za-z0-9_.:-]{1,128}$')]
Digest = Annotated[str, StringConstraints(pattern=r'^[0-9a-f]{64}$')]
Message = Annotated[str, StringConstraints(max_length=512, pattern=r'^[^\x00-\x1f\x7f]*$')]
NetworkState = Literal['preparing', 'applying', 'verifying', 'ready', 'degraded', 'failed', 'stopping', 'cleanup_pending', 'stopped']
AssignmentState = Literal['unselected', 'preparing', 'prepared', 'applying', 'configured', 'verification_pending', 'ready', 'degraded', 'failed', 'cleaning', 'cleanup_pending', 'stopped']
Phase = Literal['inspect', 'prepare', 'apply', 'verify', 'reconcile', 'rollback', 'cleanup', 'admit_probe', 'admit_run', 'renew_admission', 'release_admission', 'release_runtime']
PRIVATE_NETWORKS = tuple(ipaddress.IPv4Network(n) for n in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def private_subnet(value: str) -> str:
    subnet = ipaddress.ip_network(value, strict=True)
    if not isinstance(subnet, ipaddress.IPv4Network) or subnet.prefixlen > 30 or str(subnet) != value or not any(subnet.subnet_of(n) for n in PRIVATE_NETWORKS):
        raise ValueError('canonical RFC1918 IPv4 subnet with usable hosts required')
    return value


class Record(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, validate_assignment=True)

    @field_validator('*', mode='before')
    @classmethod
    def parse_uuid(cls, value: Any, info: Any) -> Any:
        field = cls.model_fields[info.field_name]
        if info.field_name == 'version' and isinstance(value, bool):
            raise ValueError('integer version required')
        if field.annotation == list[UUID] and isinstance(value, list):
            parsed_values = []
            for item in value:
                if isinstance(item, str):
                    parsed = UUID(item)
                    if str(parsed) != item:
                        raise ValueError('canonical UUID required')
                    parsed_values.append(parsed)
                else:
                    parsed_values.append(item)
            return parsed_values
        if isinstance(value, str) and field.annotation is datetime:
            return datetime.fromisoformat(value.replace('Z', '+00:00'))
        if isinstance(value, str) and (field.annotation is UUID or field.annotation == UUID | None):
            parsed = UUID(value)
            if str(parsed) != value:
                raise ValueError('canonical UUID required')
            return parsed
        return value

    @field_validator('*')
    @classmethod
    def utc_timestamps(cls, value: Any) -> Any:
        if isinstance(value, datetime) and (value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value)):
            raise ValueError('UTC timestamp required')
        return value


class NodeIdentity(Record):
    node_id: Identifier
    key_id: Digest
    public_key: str
    cluster_binding_id: UUID | None = None

    @model_validator(mode='after')
    def exact_key(self) -> NodeIdentity:
        try:
            raw = base64.b64decode(self.public_key + '=' * (-len(self.public_key) % 4), altchars=b'-_', validate=True)
        except ValueError:
            raise ValueError('invalid public key encoding') from None
        if len(raw) != 32 or base64.urlsafe_b64encode(raw).decode().rstrip('=') != self.public_key or hashlib.sha256(raw).hexdigest() != self.key_id:
            raise ValueError('public key fingerprint mismatch')
        return self


class MasterAuthority(Record):
    cluster_binding_id: UUID
    master_node_id: Identifier
    master_key_id: Digest
    master_term: Revision
    master_session_id: UUID
    accepted_channel_id: UUID | None = None


class PeerIdentity(NodeIdentity):
    expected_role: Literal['master', 'worker']


class ManagedNetwork(Record):
    network_id: UUID
    cluster_binding_id: UUID
    master_node_id: Identifier
    master_key_id: Digest
    subnet: str
    revision: Revision = 1
    desired_state: Literal['active', 'stopped'] = 'active'
    observed_state: NetworkState = 'preparing'
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    current_operation_id: UUID | None = None

    _subnet = field_validator('subnet')(private_subnet)


class RPCChildIdentity(Record):
    pid: Revision
    start_token: Identifier
    bind: str
    port: Port
    llama_tag: Identifier
    tcp_only: bool
    compatibility_digest: Digest


class NodeAssignment(Record):
    network_id: UUID
    node_id: Identifier
    assignment_revision: Revision = 1
    adapter_id: Identifier
    os_interface: Identifier
    identity_fingerprint: Digest
    address: str
    prefix_length: Annotated[int, Field(strict=True, ge=8, le=30)]
    ownership: Literal['user_owned', 'app_owned', 'uncertain'] = 'uncertain'
    link_speed_mbps: Counter | None = None
    dhcp_state: Literal['enabled', 'disabled', 'unknown'] = 'unknown'
    permission_state: Literal['granted', 'denied', 'unknown'] = 'unknown'
    state: AssignmentState = 'unselected'
    error_code: Identifier | None = None
    message: Message = ''
    rpc_child_identity: RPCChildIdentity | None = None
    logical_rpc_endpoint: str | None = None
    launch_rpc_endpoint: str | None = None
    probe_endpoint: str | None = None

    @field_validator('address')
    @classmethod
    def address_valid(cls, value: str) -> str:
        ip = ipaddress.IPv4Address(value)
        if str(ip) != value or not any(ip in n for n in PRIVATE_NETWORKS):
            raise ValueError('canonical private IPv4 address required')
        return value


class ParticipantResult(Record):
    node_id: Identifier
    assignment_revision: Revision
    expected_local_revision: Counter
    stage: Phase
    phase_sequence: Revision
    status: AssignmentState
    signed_result_digest: Digest | None = None
    prepared_snapshot_digest: Digest | None = None
    ownership_journal_id: UUID | None = None
    error_code: Identifier | None = None
    receipt_time: datetime = Field(default_factory=utc_now)


class NetworkOperation(Record):
    operation_id: UUID
    request_id: UUID
    network_id: UUID
    cluster_binding_id: UUID
    action: Literal['start', 'add', 'retry', 'stop', 'cancel']
    master_term: Revision
    master_session_id: UUID
    expected_network_revision: Counter
    operation_revision: Revision
    participants: Annotated[list[Identifier], Field(min_length=2, max_length=256)]
    proposal_digest: Digest
    phase: Literal['preparing', 'applying', 'verifying', 'rolling_back', 'cleaning', 'complete', 'failed', 'cancelled']
    started_at: datetime = Field(default_factory=utc_now)
    deadline: datetime
    terminal_result: dict[str, Any] | None = None
    stage_budgets: dict[str, Counter] = Field(default_factory=lambda: {'prepare': 15000, 'apply': 20000, 'verify': 20000, 'publish': 5000, 'cleanup': 60000})

    @field_validator('participants')
    @classmethod
    def unique_participants(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError('duplicate participant')
        return value


class LocalMutationJournal(Record):
    journal_id: UUID
    network_id: UUID
    operation_id: UUID
    node_id: Identifier
    adapter_fingerprint: Digest
    exact_address_prefix: str
    boot_id: Identifier
    observation_epoch: UUID
    event_high_water: Counter
    observer_health: Literal['continuous', 'gap', 'closed', 'uncertain']
    pre_snapshot: dict[str, Any]
    intended_delta: dict[str, Any]
    phase: Literal['intent_written', 'applied_observed', 'removal_intent', 'removed_observed', 'ownership_conflict', 'cleanup_pending']
    observed_snapshot_digest: Digest | None = None
    command_result: Message | None = None
    authority_fence: MasterAuthority
    retained_error: Message | None = None

    @field_validator('exact_address_prefix')
    @classmethod
    def exact_prefix(cls, value: str) -> str:
        parsed = ipaddress.IPv4Interface(value)
        if str(parsed) != value or not any(parsed.network.subnet_of(n) for n in PRIVATE_NETWORKS) or parsed.network.prefixlen > 30 or parsed.ip in (parsed.network.network_address, parsed.network.broadcast_address):
            raise ValueError('usable exact private address/prefix required')
        return value


class VerificationEvidence(Record):
    network_id: UUID
    node_id: Identifier
    assignment_revision: Revision
    master_term: Revision
    master_session_id: UUID
    adapter_fingerprint: Digest
    address: str
    route_interface_source: dict[str, Any]
    signed_bidirectional_proof: dict[str, Any]
    idle_rpc_probe_result: dict[str, Any]
    runtime_identity: RPCChildIdentity
    relay_instance_ids: list[UUID]
    observed_at: datetime = Field(default_factory=utc_now)
    # Always re-established in memory, never restored as fresh from SQLite.
    live_deadline: float = 0.0
    invalidation_reason: Message | None = None


class RunNetworkReservation(Record):
    run_id: UUID
    network_id: UUID
    assignment_revisions: dict[Identifier, Revision]
    logical_endpoints: list[str]
    launch_endpoints: list[str]
    relay_instance_ids: list[UUID]
    child_pid: Revision | None = None
    child_start_token: Identifier | None = None
    state: Literal['reserved', 'active', 'releasing', 'released', 'uncertain']
    created_at: datetime = Field(default_factory=utc_now)


class AdmissionLease(Record):
    expected_rpc_child_identity: RPCChildIdentity
    lease_id: UUID
    network_id: UUID
    node_id: Identifier
    generation: Revision
    kind: Literal['probe', 'run']
    run_id: UUID | None = None
    assignment_revision: Revision
    authority: MasterAuthority
    relay_instance_ids: list[UUID]
    monotonic_deadline: float = 0.0
    grant_ack: bool = False
    renew_ack: bool = False
    release_ack: bool = False
    drain_ack: bool = False
    state: Literal['requested', 'granted', 'consuming', 'draining', 'released', 'uncertain']

    @model_validator(mode='after')
    def run_required(self) -> AdmissionLease:
        if (self.kind == 'run') != (self.run_id is not None):
            raise ValueError('run lease requires run_id; probe lease forbids it')
        return self


NETWORK_TRANSITIONS: dict[str, set[str]] = {
    'preparing': {'applying', 'failed'},
    'applying': {'degraded', 'verifying', 'cleanup_pending', 'failed'},
    'verifying': {'applying', 'ready', 'cleanup_pending', 'failed', 'stopping', 'degraded'},
    'ready': {'verifying', 'degraded', 'stopping'},
    'degraded': {'verifying', 'stopping'},
    'failed': {'preparing', 'stopping'},
    'stopping': {'stopped', 'cleanup_pending'},
    'cleanup_pending': {'stopped', 'stopping'},
    'stopped': set(),
}


def validate_transition(before: str, after: str) -> None:
    if before != after and after not in NETWORK_TRANSITIONS[before]:
        raise ValueError(f'invalid network transition {before} -> {after}')
