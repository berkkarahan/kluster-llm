from kllm.mpi_devices import _parse_windows_inventory, physical_inventory


def test_linux_inventory_uses_pci_identity(tmp_path):
    drm = tmp_path / "class" / "drm"
    drm.mkdir(parents=True)
    card = drm / "card0"
    card.mkdir()
    device = card / "device"
    device.mkdir()
    (device / "vendor").write_text("0x10de\n", encoding="ascii")
    (device / "device").write_text("0x2684\n", encoding="ascii")
    (device / "uevent").write_text("PCI_SLOT_NAME=0000:03:00.0\n", encoding="ascii")
    assert physical_inventory(platform="linux", sys_root=tmp_path) == [{
        "physical_id": "pci:0000:03:00.0",
        "vendor_id": "0x10de",
        "device_id": "0x2684",
        "platform_handle": "card0",
    }]


def test_windows_inventory_uses_pnp_vendor_id():
    rows = _parse_windows_inventory(
        '[{"Name":"AMD Radeon","PNPDeviceID":"PCI\\\\VEN_1002&DEV_744C\\\\1"}]'
    )
    assert rows[0]["physical_id"].startswith("pnp:pci\\ven_1002")
    assert rows[0]["vendor_id"] == "0x1002"


def test_mac_inventory_blocks_ambiguous_multiple_devices():
    assert physical_inventory([{"name": "one"}, {"name": "two"}], platform="darwin") == []
    assert physical_inventory([{"name": "Apple M4"}], platform="darwin")[0]["vendor_id"] == "0x106b"
