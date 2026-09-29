# Feature Memory

- Configure only an explicitly selected dedicated adapter; preserve default routes, DNS, Wi-Fi, and unrelated interfaces.
- Require connected, OS-reported negotiated speed of at least 10 Gbps; refuse if speed is lower or unavailable.
- Keep addresses temporary on every platform; Windows must use `New-NetIPAddress -PolicyStore ActiveStore`.
- Reuse `network-config/10gbe-bridging-guide.md`; the requested script supplements its current manual OS-specific instructions.
- Keep RPC private. Port 50052 is unauthenticated upstream and must not be exposed publicly.
