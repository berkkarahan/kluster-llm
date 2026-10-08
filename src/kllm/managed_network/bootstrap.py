"""Mutual join-secret bootstrap; signing pins are never replaced implicitly."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

import httpx
from pydantic import Field

from .identity import IdentityError, LocalIdentity
from .models import NodeIdentity, PeerIdentity, Record
from .store import NetworkStore, StoreConflict

DOMAIN = b"kllm-network-bootstrap-v2\0"


class BootstrapRequest(Record):
    identity: NodeIdentity
    nonce: str = Field(pattern=r"^[0-9a-f]{64}$")
    proof: str = Field(pattern=r"^[0-9a-f]{64}$")


class BootstrapCommit(Record):
    node_id: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,128}$")
    nonce: str = Field(pattern=r"^[0-9a-f]{64}$")
    signed_transcript: str = Field(max_length=8192)
    proof: str = Field(pattern=r"^[0-9a-f]{64}$")


def proof(key: str, stage: str, payload: dict[str, Any]) -> str:
    if not key:
        raise IdentityError("Join the intended cluster before setup")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hmac.new(
        key.encode(), DOMAIN + stage.encode() + b"\0" + encoded, hashlib.sha256
    ).hexdigest()


def adopt_rotated_master(store: NetworkStore, prior: PeerIdentity, peer: PeerIdentity) -> None:
    """Adopt a rebuilt master's label/binding when its signing key is unchanged.

    The Ed25519 key is the cryptographic identity. A node label or cluster
    binding changes when the master's own network state is rebuilt or restored
    (for example a restored ``cluster.json``). Refusing then leaves every
    signed feature stuck with no supported recovery, so the current identity
    replaces the pin exactly like the local label-rotation policy in
    ``NetworkStore.pin_identity``. A changed key is an identity swap and stays
    refused until the operator re-pairs locally.
    """
    if (prior.key_id, prior.public_key) != (peer.key_id, peer.public_key):
        raise IdentityError("Master identity key changed; explicit re-pairing required")
    try:
        store.pair(peer, replace=True)
    except StoreConflict as error:
        raise IdentityError(
            "Master identity changed and local networking is not settled; "
            f"resolve Kluster network state on this node before re-pairing ({error})"
        ) from None


class IdentityBootstrap:
    def __init__(self, service: Any):
        self.service = service
        self.pending: dict[str, tuple[float, dict[str, Any], PeerIdentity]] = {}
        self.lock = asyncio.Lock()

    def challenge(self, request: BootstrapRequest) -> dict[str, Any]:
        service, state = self.service, self.service.state()
        identity = request.identity.model_dump(mode="json")
        if (
            state.role != "master"
            or request.identity.node_id == state.node_id
            or request.identity.node_id not in state.members
        ):
            raise IdentityError("Only a currently joined worker may connect")
        if not hmac.compare_digest(
            request.proof,
            proof(state.join_key, "request", {"identity": identity, "nonce": request.nonce}),
        ):
            raise IdentityError("Cluster authentication failed")
        self.pending = {k: v for k, v in self.pending.items() if v[0] > time.monotonic()}
        if len(self.pending) >= 256:
            raise IdentityError("Pairing is busy; retry shortly")
        authority = service._authority()
        master = service.identity.public(authority.cluster_binding_id).model_dump(mode="json")
        peer = PeerIdentity.model_validate(
            {
                **identity,
                "cluster_binding_id": str(authority.cluster_binding_id),
                "expected_role": "worker",
            }
        )
        prior = service.store.peer(peer.node_id)
        if prior is not None and prior != peer:
            raise IdentityError(
                "This computer’s identity changed; review the existing pairing locally"
            )
        nonce = secrets.token_hex(32)
        transcript = {
            "version": 2,
            "master": master,
            "worker": identity,
            "worker_nonce": request.nonce,
            "master_nonce": nonce,
        }
        self.pending[nonce] = (time.monotonic() + 30, transcript, peer)
        return {
            "transcript": transcript,
            "signature": service.identity.sign(transcript, 8192),
            "proof": proof(state.join_key, "challenge", transcript),
        }

    def commit(self, request: BootstrapCommit) -> dict[str, bool]:
        pending = self.pending.pop(request.nonce, None)
        if pending is None or pending[0] <= time.monotonic():
            raise IdentityError("Pairing expired; retry")
        _, transcript, peer = pending
        state = self.service.state()
        if (
            state.role != "master"
            or request.node_id != peer.node_id
            or peer.node_id not in state.members
            or transcript["master"]
            != self.service.identity.public(
                self.service._authority().cluster_binding_id
            ).model_dump(mode="json")
        ):
            raise IdentityError("Cluster membership changed during pairing")
        if (
            not hmac.compare_digest(request.proof, proof(state.join_key, "commit", transcript))
            or LocalIdentity.verify(request.signed_transcript, peer.public_key, 8192) != transcript
        ):
            raise IdentityError("Pairing transcript mismatch")
        self.service.store.pair(peer)
        return {"paired": True}

    async def connect(self) -> None:
        async with self.lock:
            service, state = self.service, self.service.state()
            if (
                state.role != "worker"
                or not getattr(state, "join_key", None)
                or not state.master_host
            ):
                return
            base = service.channels.master_base()
            identity = service.identity.public().model_dump(mode="json")
            nonce = secrets.token_hex(32)
            async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
                response = await client.post(
                    base + "/v2/node/network-bootstrap/challenge",
                    json={
                        "identity": identity,
                        "nonce": nonce,
                        "proof": proof(
                            state.join_key, "request", {"identity": identity, "nonce": nonce}
                        ),
                    },
                )
                response.raise_for_status()
                data = response.json()
                transcript = data["transcript"]
                master = NodeIdentity.model_validate(transcript["master"])
                if (
                    not master.cluster_binding_id
                    or transcript.get("worker") != identity
                    or transcript.get("worker_nonce") != nonce
                    or not hmac.compare_digest(
                        data["proof"], proof(state.join_key, "challenge", transcript)
                    )
                    or LocalIdentity.verify(data["signature"], master.public_key, 8192)
                    != transcript
                ):
                    raise IdentityError("Master pairing proof is invalid")
                peer = PeerIdentity.model_validate(
                    {**master.model_dump(), "expected_role": "master"}
                )
                prior = next(
                    (p for p in service.store.peers() if p.expected_role == "master"), None
                )
                if service.state().role != "worker" or service.channels.master_base() != base:
                    raise IdentityError("Cluster changed during pairing")
                if prior is not None and prior != peer:
                    adopt_rotated_master(service.store, prior, peer)
                response = await client.post(
                    base + "/v2/node/network-bootstrap/commit",
                    json={
                        "node_id": identity["node_id"],
                        "nonce": transcript["master_nonce"],
                        "signed_transcript": service.identity.sign(transcript, 8192),
                        "proof": proof(state.join_key, "commit", transcript),
                    },
                )
                response.raise_for_status()
                service.store.pair(peer, replace=prior is not None)
