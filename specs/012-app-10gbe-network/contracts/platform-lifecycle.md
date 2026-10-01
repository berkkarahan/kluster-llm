# Platform Configuration and Ownership Contract

Python adapters expose inspect(adapter), prepare(assignment), apply(prepared_digest), observe(journal), and remove_owned(journal). Inputs identify a stable adapter and exact address/prefix; fixed commands have bounded timeout/output. No client-supplied shell string, broad removal, DNS/gateway profile update or unrelated route replacement is accepted. Guided setup may save and disable dedicated IPv4 DHCP through the native helper, with owned restoration.

## IPv4 Conflict and Usability Evidence

Preparation checks local addresses and relevant routing tables on every participant, not just the selected adapter. Reject conflicting non-owned routes and preserve management routing. Known conflicts prevent application. Finite probing cannot guarantee absence of sleeping peers or future collisions; subsequent conflicts invalidate readiness and trigger ownership-safe rollback.

Linux/macOS use bounded interface-specific RFC5227 ARP probing before assignment, without needing an existing address: Linux native AF_PACKET and macOS native BPF inside the locally authorized native helper (or the existing privileged manual agent). Probe the proposed address with selected adapter MAC and zero sender IPv4, using the standard's three probes/random intervals within a 10s preparation deadline. Missing send/capture permissions, unsupported driver, link loss or incomplete observation is unsupported/inconclusive. Complete observation without conflict means no conflict observed, not permanent uniqueness. [IPv4 Address Conflict Detection](https://www.rfc-editor.org/rfc/rfc5227).

Windows preparation uses authoritative existing address/neighbor evidence for the selected interface and rejects known conflicts. It does not claim an unconfigured adapter can prove on-link absence through a source-bound ARP API. After temporary addition, observe native IPv4 address-state/DAD until usable and roll back on duplicate or timeout. A race or previously undetectable conflict discovered after add is an application failure, not successful preparation proof. Linux/macOS recheck after add and monitor conflicts; IPv6 tentative/DAD flags are never IPv4 proof.

All nodes prepare before any add. A post-add conflict triggers rollback on reachable participants; pre-existing user-owned settings are preserved. Hardware acceptance tests active conflicting neighbors and prepare/apply races on each OS. No conflicting or unusable address becomes ready.

## Ownership Observation Continuity

Polling an unchanged fingerprint does not prove ownership. Start the authoritative OS event observer before a pre-mutation snapshot, establish a snapshot/event barrier, persist intent, and account for the exact expected add/delete event. External deletes/re-adds, prefix changes, adapter replacement and route mutations invalidate ownership even when the final snapshot matches.

Linux uses address/route netlink notifications; overflow/ENOBUFS invalidates continuity and requires resynchronization. Windows uses IP Helper address/route callbacks, copying callback rows into bounded ordered queues and invalidating continuity on session/queue gaps or undecidable ordering. macOS uses routing-socket address/route notifications with loss/error detection. These are scoped native observers rather than a second policy service. Sources: [Linux netlink](https://www.man7.org/linux/man-pages/man7/netlink.7.html), [Microsoft address notifications](https://learn.microsoft.com/en-us/windows/win32/api/netioapi/nf-netioapi-notifyunicastipaddresschange), [Apple route monitoring](https://raw.githubusercontent.com/apple-oss-distributions/network_cmds/main/route.tproj/route.8).

This conservative rule is a design inference: OS events are not portable exclusive creator tokens. Undecidable event attribution or an observation gap makes ownership uncertain and forbids automatic deletion. Agent restart creates a gap; reboot absence can prove removal, but matching present settings require local review. No-gap app-created settings may be removed after a current fingerprint/fence check. Only authenticated local operator review confirms an ambiguous exact resource or releases its ownership record. A platform unable to supply trustworthy observation must fail before new mutation rather than promise automatic cleanup.

OS-created connected/local routes should disappear through exact address removal; never flush routes to force snapshot equality. External changes are reported without being rewritten. Journal observer cancellation/restart and close native handles before claiming cleanup complete.

## Privileged configuration boundary

The agent remains the original user. A protected native helper independently verifies signed master assignments, term/session, operation identity and authorized phase, plus live adapter MAC/index, physical link, >=10Gbps speed, management exclusion and default-route exclusion. Local IPC limits requests and responses to 64KiB, verifies the account/server identity and offers no shell/process endpoint. Authorization is revocable only with settled changes.

The helper journals configuration intent before disabling DHCP or adding an exact address. Linux temporary profiles use exact UUID identity; runtime networkd drop-ins must retain the exact owned contents. macOS restores DHCP only if the dedicated service still has Kluster's expected configuration. Windows uses ActiveStore settings and scoped firewall rules whose filters are reviewed before removal. Restart loses ownership continuity; existing addresses require local review, while proven absence permits original configuration restoration. Local snapshot digests fence keep-current/restore decisions. Uncertain restoration remains cleanup_pending.
