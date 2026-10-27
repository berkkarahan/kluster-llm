"""Controlled helper substitute; verifies real signed frames but mutates no OS rules."""

from copy import deepcopy

from kllm.modules.managed_network.interactors.identity import LocalIdentity


class FixturePorts:
    def __init__(self, master_key, node, events):
        self.master_key, self.node, self.events = master_key, node, events
        self.rules = {}
        self.fail_apply = False
        self.fail_release = False
        self.renewals = 0

    def install(self, platform, monkeypatch):
        for name in (
            "apply_prima_ports",
            "renew_prima_ports",
            "prima_ports_status",
            "release_prima_ports",
            "prima_ports_available",
        ):
            monkeypatch.setattr(platform, name, getattr(self, name), raising=False)

    def prima_ports_available(self):
        return not self.rules

    def apply_prima_ports(self, command):
        payload = LocalIdentity.verify(command._wire, self.master_key)
        assert payload == command.model_dump(mode="json")
        assert (
            payload["target_node_id"] == self.node
            and payload["sender_node_id"] == payload["master_node_id"]
        )
        key = (command.body["contract"]["network_id"], str(command.run_id))
        assert key not in self.rules
        self.rules[key] = deepcopy(command.body["contract"])
        self.events.append((self.node, "admit", str(command.run_id)))
        if self.fail_apply:
            raise OSError("Controlled lost helper reply")
        return {"phase": "admitted", "manifest_digest": command.body["contract"]["manifest_digest"]}

    def prima_ports_status(self, network_id, run_id):
        return {
            "phase": "admitted",
            "manifest_digest": self.rules[network_id, run_id]["manifest_digest"],
        }

    def renew_prima_ports(self, command):
        payload = LocalIdentity.verify(command._wire, self.master_key)
        assert payload == command.model_dump(mode="json") and payload["action"] == "prima_renew_ports"
        key = (command.body["contract"]["network_id"], str(command.run_id))
        assert self.rules[key] == command.body["contract"]
        self.renewals += 1
        return {"phase": "admitted", "manifest_digest": self.rules[key]["manifest_digest"]}

    def release_prima_ports(self, network_id, run_id):
        if self.fail_release:
            raise OSError("Controlled native cleanup failure")
        self.rules.pop((network_id, run_id), None)
        self.events.append((self.node, "release", run_id))
        return {"phase": "released"}
