"""Manual administrator adapter with the shared native Prima listener policy."""

from kllm.modules.managed_network.interactors.helper_platform import HelperPlatform
from kllm.shared.platform.base import PlatformAdapter, PlatformRefusal


class DirectPortPlatform(PlatformAdapter):
    def __init__(self, underlying, client, grant, busy=lambda: False):
        self.underlying, self.client, self.grant, self.busy = underlying, client, grant, busy
        self.runtime = underlying.runtime
        self.ports = HelperPlatform(underlying, client, grant)

    def inventory(self):
        return self.underlying.inventory()

    def inspect(self, adapter_id):
        return self.underlying.inspect(adapter_id)

    def eligible(self, snapshot):
        self.underlying.eligible(snapshot)  # Manual mode still requires disabled DHCP.

    def authorize(self, command):
        if command.phase in {"cleanup", "rollback"} and self.busy():
            raise PlatformRefusal("INFERENCE_ACTIVE", "Stop the owned Prima run before changing its private address")
        if command.phase in {"cleanup", "rollback"}:
            # Direct scope bookkeeping owns no addresses. The executor already
            # authenticated cleanup and retains its exact manual address journal.
            if self.client.request("direct_forget", network=str(command.network_id)) != {"ok": True}:
                raise PlatformRefusal("CLEANUP_PENDING", "Drain direct native listeners before address cleanup")
            return
        snapshot = self.inspect(command.body["assignment"]["adapter_id"]).public() if command.phase == "prepare" else {}
        result = self.client.request("authorize", network=str(command.network_id), wire=command._wire, snapshot=snapshot)
        if result != {"ok": True}:
            raise PlatformRefusal("HELPER_REFUSED", "Direct native helper did not acknowledge signed network authorization")

    def before_add(self, snapshot, prepared):
        return self.underlying.before_add(snapshot, prepared)

    def reconcile(self, command):
        if self.busy():
            raise PlatformRefusal("INFERENCE_ACTIVE", "Stop Prima before reconciling its private assignments")
        self.authorize(command)

    def apply(self, prepared, journal, fence):
        snapshot = super().apply(prepared, journal, fence)
        fence()
        if self.client.request("direct_observe", network=str(prepared.assignment.network_id)) != {"ok": True}:
            raise PlatformRefusal("CLEANUP_PENDING", "Direct native helper did not observe the exact manual address")
        fence()
        return snapshot

    def restore_configuration(self, network_id):
        if self.busy():
            raise PlatformRefusal("INFERENCE_ACTIVE", "Stop the owned Prima run before private network cleanup")
        self.underlying.restore_configuration(network_id)
        if self.client.request("direct_forget", network=network_id) != {"ok": True}:
            raise PlatformRefusal("CLEANUP_PENDING", "Direct native scope has not finished cleanup")

    def add_exact(self, snapshot, assignment):
        self.underlying.add_exact(snapshot, assignment)

    def delete_exact(self, snapshot, assignment):
        if self.busy():
            raise PlatformRefusal("INFERENCE_ACTIVE", "Stop the owned Prima run before removing its private address")
        self.underlying.delete_exact(snapshot, assignment)

    def check_source_route(self, snapshot, source, peer):
        self.underlying.check_source_route(snapshot, source, peer)

    def address_usable(self, snapshot, assignment):
        return self.underlying.address_usable(snapshot, assignment)

    def conflict_preflight(self, snapshot, address):
        self.underlying.conflict_preflight(snapshot, address)

    def apply_prima_ports(self, command):
        return self.ports.apply_prima_ports(command)

    def renew_prima_ports(self, command):
        return self.ports.renew_prima_ports(command)

    def prima_ports_available(self):
        return self.ports.prima_ports_available()

    def prima_ports_status(self, network_id, run_id):
        return self.ports.prima_ports_status(network_id, run_id)

    def release_prima_ports(self, network_id, run_id):
        return self.ports.release_prima_ports(network_id, run_id)

    def review_prima_ports(self, network_id, run_id):
        return self.ports.review_prima_ports(network_id, run_id)

    def resolve_prima_ports(self, network_id, run_id, decision, snapshot_digest):
        return self.ports.resolve_prima_ports(network_id, run_id, decision, snapshot_digest)

    def close(self):
        self.client.close()
