"""Model published segment visits and physical ring traffic without measurements."""

import json


def segments(topology):
    result = []
    for start, end, node in sorted((window.start, window.end, rank.node_id)
                                   for rank in topology.ranks for window in rank.layers):
        if result and result[-1][2] == node and result[-1][1] == start:
            result[-1] = (result[-1][0], end, node)
        else:
            result.append((start, end, node))
    return result


def ring_messages(topology, embedding):
    """One-token activation/settlement payload upper bounds on actual ring hops.

    Includes the native marker and JSON header with the largest wave counter.
    Application throughput and latency are proxies: transport framing, buffering
    and relay CPU costs are not native inference measurements.
    """
    visits = segments(topology)
    ranks = topology.ranks
    nodes = {rank.node_id: rank.rank for rank in ranks}
    head = ranks[0].node_id
    messages = []

    def size(stage, hops, done):
        header = {"version": 1, "wave": 2**64 - 1, "stage": stage,
                  "hops": hops, "rows": 0 if done else 1, "width": embedding,
                  "tokens": 1, "done": done, "manifest": "0" * 64}
        return len("prima_segment") + len(json.dumps(header, sort_keys=True, separators=(",", ":"))) + (0 if done else 4 * embedding)

    source = head
    for stage, target in enumerate([node for _, _, node in visits] + [head]):
        current = nodes[source]
        hops = 0
        while current != nodes[target]:
            following = (current + 1) % len(ranks)
            messages.append((ranks[current].node_id, ranks[following].node_id, size(stage, hops, False)))
            current = following
            hops += 1
        source = target
    # Rank zero waits for the complete ring acknowledgement after its output graph.
    for index, rank in enumerate(ranks):
        following = ranks[(index + 1) % len(ranks)]
        messages.append((rank.node_id, following.node_id, size(len(visits), index, True)))
    # Existing batch metadata reaches every worker before graph execution.
    for rank in ranks[:-1]:
        messages.append((rank.node_id, ranks[rank.rank + 1].node_id, 74))
    return messages
