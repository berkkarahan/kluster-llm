"""Real agent/native socket lab with explicitly simulated OS address topology.

Logical adapters/aliases and their kernel observation are controlled. Dedicated
addresses are mapped onto this host's real private source IP and distinct ports.
RPC/GPU binaries and inference are controlled subprocesses. No production
eligibility bypass or address mutation is installed; all substitutions are local
pytest monkeypatches. Fingerprints, channels, SQLite, native relay/probe, process
identity, serial RPC ownership and socket drain are actual boundaries.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import uvicorn
import pytest
from fastapi import FastAPI, Request, HTTPException

from kllm import native
from kllm.agent.handlers.inference import InferenceDependencies, InferenceHandlers
from kllm.agent.handlers.managed_network import build_router
from kllm.agent.services.managed_network import ManagedNetworkService
from kllm.managed_network import executor, platform
from kllm.managed_network.models import RPCChildIdentity
from kllm.managed_network.platform.base import (
    AdapterSnapshot,
    PlatformAdapter,
    PlatformRefusal,
    Prepared,
)
from kllm.managed_network.platform.inspection import InterfaceState
from kllm.managed_network.store import digest
from kllm.managed_network.verification import DedicatedDiagnostics
from kllm.run_settings import RunBody

RPC_SERVER = r"""
import socket,struct,sys
listener=socket.socket();listener.bind((sys.argv[1],int(sys.argv[2])));listener.listen()
open(sys.argv[3]+'.ready','w').close()
def read(stream,n):
 data=b''
 while len(data)<n:
  part=stream.recv(n-len(data))
  if not part: return None
  data+=part
 return data
while True:
 stream,_=listener.accept()
 open(sys.argv[3]+'.active','w').close()
 with open(sys.argv[3],'a') as events:events.write('accepted\n')
 try:
  while True:
   header=read(stream,9)
   if header is None:break
   command=header[0];length=struct.unpack('<Q',header[1:])[0]
   if length>64:raise ValueError('unexpected fixture frame')
   body=read(stream,length)
   if command==14 and body==bytes(24):reply=bytes([7])+bytes(27)
   elif command==15 and length==0:reply=struct.pack('<I',1)
   else:raise ValueError('invalid protocol')
   stream.sendall(struct.pack('<Q',len(reply))+reply)
 except (ConnectionError,OSError):pass
 finally:
  stream.close()
  import os
  os.unlink(sys.argv[3]+'.active')
"""
INFERENCE = r"""
import json,socket,struct,sys,time
streams=[]
def read(stream,n):
 result=b''
 while len(result)<n:
  part=stream.recv(n-len(result))
  if not part:raise RuntimeError('RPC closed')
  result+=part
 return result
for endpoint in json.loads(sys.argv[1]):
 host,port=endpoint.rsplit(':',1);stream=socket.create_connection((host,int(port)),3)
 stream.sendall(bytes([14])+struct.pack('<Q',24)+bytes(24));size=struct.unpack('<Q',read(stream,8))[0];assert read(stream,size)[0]==7
 stream.sendall(bytes([15])+bytes(8));size=struct.unpack('<Q',read(stream,8))[0];assert struct.unpack('<I',read(stream,size))[0]==1
 streams.append(stream)
open(sys.argv[2],'w').write('connected')
while True:time.sleep(1)
"""


def available_port(host):
    with socket.socket() as listener:
        listener.bind((host, 0))
        return listener.getsockname()[1]


class SimulatedAdapter(PlatformAdapter):
    def __init__(self, name):
        self.name, self.addresses, self.deltas = name, [], []

    def inventory(self):
        return [self.inspect("ethernet")]

    def inspect(self, adapter_id):
        return AdapterSnapshot(
            adapter_id,
            adapter_id,
            1,
            digest(self.name),
            "02:00:00:00:00:01",
            InterfaceState(adapter_id, True, 10000, False, tuple(self.addresses), ()),
        )

    def prepare(self, assignment, inventory_revision, management_hosts):
        snapshot = self.inspect(assignment.adapter_id)
        assert snapshot.public()["inventory_revision"] == inventory_revision
        return Prepared(
            assignment,
            snapshot,
            digest(snapshot.public()),
            None,
            str(uuid4()),
            0,
            False,
            float("inf"),
        )

    def apply(self, prepared, journal, fence):
        fence()
        exact = ipaddress.IPv4Interface(
            f"{prepared.assignment.address}/{prepared.assignment.prefix_length}"
        )
        self.addresses.append(exact)
        self.deltas.append(("add", str(exact)))
        prepared.owns_resource = True
        prepared.high_water += 1
        return self.inspect(prepared.assignment.adapter_id)

    def observe(self, prepared):
        return self.inspect(prepared.assignment.adapter_id)

    def check_conflict(self, prepared):
        pass

    def check_source_route(self, snapshot, source, peer):
        assert ipaddress.IPv4Address(source) in {a.ip for a in snapshot.state.addresses}

    def close_prepared(self, prepared):
        pass

    def remove_owned(self, prepared, journal, fence):
        fence()
        if prepared.ownership_conflict:
            raise PlatformRefusal(
                "OWNERSHIP_CONFLICT", "Simulated kernel observation continuity was lost"
            )
        self.delete_exact(self.inspect("ethernet"), prepared.assignment)
        return True

    def add_exact(self, snapshot, assignment):
        self.addresses.append(
            ipaddress.IPv4Interface(f"{assignment.address}/{assignment.prefix_length}")
        )

    def delete_exact(self, snapshot, assignment):
        exact = ipaddress.IPv4Interface(f"{assignment.address}/{assignment.prefix_length}")
        if exact in self.addresses:
            self.addresses.remove(exact)
            self.deltas.append(("remove", str(exact)))


class NativeAgentLab:
    def __init__(self, home: Path, monkeypatch):
        self.home, self.monkeypatch = home, monkeypatch
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
            route.connect(("192.0.2.1", 9))
            self.source = route.getsockname()[0]
        if not ipaddress.IPv4Address(self.source).is_private or self.source.startswith("127."):
            pytest.skip("Native socket lab requires one available RFC1918 source address")
        self.listeners, self.servers, self.server_tasks, self.children, self.nodes = (
            [],
            [],
            [],
            [],
            [],
        )
        self.bridge_ports, self.diagnostic_ports, self.configurations = {}, {}, []
        self.inference_child = None
        for _ in range(3):
            listener = socket.socket()
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            listener.setblocking(False)
            self.listeners.append(listener)
        self.master_port = self.listeners[0].getsockname()[1]
        names = ("master", "worker-a", "worker-b")
        members = {
            name: {
                "role": "master" if i == 0 else "worker",
                "host": self.source,
                "control_port": self.listeners[i].getsockname()[1],
                "rpc_port": available_port(self.source),
            }
            for i, name in enumerate(names)
        }
        self.monkeypatch.setattr(platform.base, "is_admin", lambda: True)
        self.monkeypatch.setattr(
            executor, "require_tcp_runtime", lambda *args: {"controlled_tcp_rpc_workload": True}
        )
        real_bridge = native.start_network_bridge

        def bridge(configuration):
            mapped = dict(configuration)
            original_bind = configuration["bind_host"]
            if configuration["role"] == "worker":
                self.bridge_ports[original_bind] = available_port(self.source)
                mapped["bind_port"] = self.bridge_ports[original_bind]
            else:
                mapped["target_port"] = self.bridge_ports[configuration["target_host"]]
            for field in ("bind_host", "target_host", "source_host", "allowed_peer"):
                if mapped[field].startswith("192.168.234."):
                    mapped[field] = self.source
            self.configurations.append((dict(configuration), mapped))
            return real_bridge(mapped)

        self.monkeypatch.setattr(native, "start_network_bridge", bridge)
        lab = self

        class MappedDiagnostics(DedicatedDiagnostics):
            def __init__(
                self, identity, store, network_id, revision, authority, address, port, peers
            ):
                lab.diagnostic_ports[identity.node_id] = available_port(lab.source)
                super().__init__(
                    identity,
                    store,
                    network_id,
                    revision,
                    authority,
                    lab.source,
                    lab.diagnostic_ports[identity.node_id],
                    {node: lab.source for node in peers},
                )

            async def challenge(self, node_id, revision, port):
                return await super().challenge(node_id, revision, lab.diagnostic_ports[node_id])

        self.monkeypatch.setattr(executor, "DedicatedDiagnostics", MappedDiagnostics)

        def rpc_child(selected):
            state = selected.state()
            token, _ = native._managed_native()._process_identity(state.process_pid)
            return RPCChildIdentity(
                pid=state.process_pid,
                start_token=token,
                bind=lab.source,
                port=state.rpc_port,
                llama_tag="v0.5.0",
                tcp_only=True,
                compatibility_digest=digest({"controlled_serial_rpc": state.rpc_port}),
            )

        self.monkeypatch.setattr(executor.LocalNetworkExecutor, "rpc_child", rpc_child)

        def rpc_idle(selected, child):
            # Process socket inventory on macOS requires root. The controlled
            # serial RPC workload reports its actual accepted-owner lifetime.
            node = next(node for node in lab.nodes if node.state.process_pid == child.pid)
            if Path(str(node.rpc_events) + ".active").exists():
                raise PlatformRefusal("RPC_BUSY", "Controlled serial RPC has an accepted owner")

        self.monkeypatch.setattr(executor.LocalNetworkExecutor, "require_rpc_idle", rpc_idle)
        for index, name in enumerate(names):
            state = SimpleNamespace(
                role="master" if index == 0 else "worker",
                node_id=name,
                members=members,
                master_host=None if index == 0 else "127.0.0.1",
                master_port=self.master_port,
                process_pid=None,
                process_role=None,
                rpc_port=members[name]["rpc_port"],
                rpc_bind=self.source,
                infer_port=18766,
                gpu_ids=None,
                save=lambda: None,
            )
            adapter = SimulatedAdapter(name)
            node = SimpleNamespace(
                name=name,
                state=state,
                adapter=adapter,
                home=home / name,
                token=f"Bearer isolated-{name}",
                rpc_events=home / f"{name}-rpc-events",
            )
            if index:
                child = subprocess.Popen(
                    [
                        sys.executable,
                        "-c",
                        RPC_SERVER,
                        self.source,
                        str(state.rpc_port),
                        str(node.rpc_events),
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=(home / f'{name}-rpc-error').open('w'),
                )
                self.children.append(child)
                state.process_pid, state.process_role = child.pid, "worker"
            node.service = self.service(node)
            self.nodes.append(node)

    def service(self, node):
        return ManagedNetworkService(
            node.home,
            lambda: node.state,
            lambda: f"http://127.0.0.1:{self.master_port}",
            platform=node.adapter,
        )

    async def start(self):
        for node in self.nodes[1:]:
            await self.wait(lambda node=node: Path(str(node.rpc_events) + ".ready").exists())
        for index, node in enumerate(self.nodes):

            @asynccontextmanager
            async def lifespan(app, node=node):
                await node.service.start()
                try:
                    yield
                finally:
                    await node.service.close()

            app = FastAPI(lifespan=lifespan)
            app.include_router(
                build_router(
                    lambda node=node: node.service, lambda token, node=node: token == node.token
                )
            )
            if index == 0:

                @app.post("/v2/inference/runs")
                def run(body: RunBody, request: Request):
                    if request.headers.get("authorization") != self.nodes[0].token:
                        raise HTTPException(401)
                    return self.handlers().run(body)

                @app.post("/v2/inference/runs/stop")
                def stop(request: Request):
                    if request.headers.get("authorization") != self.nodes[0].token:
                        raise HTTPException(401)
                    return self.handlers().stop()

            server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="on"))
            self.servers.append(server)
            self.server_tasks.append(
                asyncio.create_task(server.serve(sockets=[self.listeners[index]]))
            )
        await self.wait(lambda: all(server.started for server in self.servers))
        self.client = httpx.AsyncClient(trust_env=False, timeout=10)
        identities = [
            (await self.request(i, "GET", "/v2/node/network-identity")).json() for i in range(3)
        ]
        binding = identities[0]["cluster_binding_id"]
        for index in (1, 2):
            for local, peer, role in ((0, index, "worker"), (index, 0, "master")):
                identity = identities[peer]
                paired = await self.request(
                    local,
                    "PUT",
                    f"/v2/node/network-identity/peers/{identity['node_id']}",
                    json={key: identity[key] for key in ("node_id", "public_key", "key_id")}
                    | {"cluster_binding_id": binding, "expected_role": role},
                )
                assert paired.status_code == 200, paired.text
        await self.wait(lambda: len(self.nodes[0].service.channels.sessions) == 2)
        for index in (1, 2):
            snapshot = self.nodes[index].adapter.inspect("ethernet").public()
            selected = await self.request(
                index,
                "PUT",
                "/v2/node/network-selection",
                json={
                    "adapter_id": "ethernet",
                    "expected_inventory_revision": snapshot["inventory_revision"],
                },
            )
            assert selected.status_code == 200, selected.text

    async def request(self, index, method, path, **kwargs):
        return await self.client.request(
            method,
            f"http://127.0.0.1:{self.listeners[index].getsockname()[1]}{path}",
            headers={"authorization": self.nodes[index].token},
            **kwargs,
        )

    async def wait(self, condition, seconds=8):
        deadline = asyncio.get_running_loop().time() + seconds
        while not condition():
            assert asyncio.get_running_loop().time() < deadline, "native lab boundary timed out"
            await asyncio.sleep(0.02)

    async def setup(self):
        inventory = (await self.request(0, "GET", "/v2/cluster/network-adapters")).json()["nodes"]
        payload = {
            "request_id": str(uuid4()),
            "subnet": "192.168.234.0/24",
            "participants": [
                {
                    "node_id": name,
                    "adapter_id": "ethernet",
                    "inventory_revision": value["adapters"][0]["inventory_revision"],
                }
                for name, value in inventory.items()
            ],
        }
        response = await self.request(0, "POST", "/v2/cluster/networks", json=payload)
        assert response.status_code == 202, response.text
        return response.json()

    def handlers(self):
        import json

        node = self.nodes[0]
        model = self.home / "controlled.gguf"
        model.touch()

        def plan(body, members):
            return {
                "fit_status": "ok",
                "rpc_endpoints": [
                    f"{member['host']}:{member['rpc_port']}"
                    for member in members.values()
                    if member["role"] == "worker"
                ],
            }

        def launch(configuration):
            ready = self.home / "inference-ready"
            ready.unlink(missing_ok=True)
            self.inference_child = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    INFERENCE,
                    json.dumps(configuration["endpoints"]),
                    str(ready),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.children.append(self.inference_child)
            return {"pid": self.inference_child.pid}

        def finish(path, result, launched, persist, remember):
            node.state.process_pid, node.state.process_role = launched["pid"], "master"

        def stop():
            if self.inference_child and self.inference_child.poll() is None:
                self.inference_child.terminate()
                self.inference_child.wait(timeout=3)
            node.state.process_pid = None

        return InferenceHandlers(
            InferenceDependencies(
                state=node.state,
                ensure_master_member=lambda: True,
                merge_extra=lambda body: [],
                execute_plan=plan,
                resolve_model=lambda name: model,
                persist_last_settings=lambda body: None,
                remember=lambda body: None,
                validate_vision=lambda body: None,
                ensure_rpc_peers_reachable=lambda endpoints: None,
                repository_root=lambda: None,
                stop_previous_master=stop,
                gpu_visibility_env=lambda *args: {},
                sanitize_extra_args=lambda args: args,
                build_master_launch=lambda body, result, **kwargs: {
                    "endpoints": result["rpc_endpoints"]
                },
                spawn_master=launch,
                finish_master_launch=finish,
                stop_managed_inference=stop,
                inference_idle=lambda: {},
                network_members=node.service.admission.members,
                network_run=node.service.admission.context,
                network_diagnostics=node.service.admission.diagnostics,
                network_stop=node.service.admission.stop_context,
                network_release=lambda: node.service.admission.call(
                    node.service.admission.release_stopped(lock_held=True)
                ),
            )
        )

    async def restart_worker(self, index):
        node = self.nodes[index]
        await node.service.close()
        node.service = self.service(node)
        await node.service.start()
        await self.wait(
            lambda: (
                node.name in self.nodes[0].service.channels.sessions
                and self.nodes[0].service.channels.sessions[node.name].fence.authority.accepted_channel_id
                == node.service.channels.sessions["master"].fence.authority.accepted_channel_id
                if "master" in node.service.channels.sessions
                else False
            )
        )

    async def close(self):
        for child in self.children:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=3)
        operations = [task for node in self.nodes for task in node.service.tasks.values() if not task.done()]
        for task in operations:
            task.cancel()
        if operations:
            await asyncio.wait_for(asyncio.gather(*operations, return_exceptions=True), 30)
        for server in self.servers:
            server.should_exit = True
        if self.server_tasks:
            await asyncio.wait_for(asyncio.gather(*self.server_tasks, return_exceptions=True), 10)
        for node in self.nodes:
            await node.service.close()
        if hasattr(self, "client"):
            await self.client.aclose()
        for listener in self.listeners:
            listener.close()
