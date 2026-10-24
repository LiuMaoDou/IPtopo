#!/usr/bin/env python3
"""Deterministic connected synthetic IOS topology for reproducible scale checks.

Every device has two ring interfaces, one opposite-side chord, and a loopback.
One quarter of relations are dual-stack. There are no uploaded secrets or
real-device claims. All routing contexts are default for immediate rendering.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
from pathlib import Path
import zipfile


IPV4_BASE = int(ipaddress.IPv4Address("10.64.0.0"))
IPV6_BASE = int(ipaddress.IPv6Address("2001:db8:6400::"))
LOOPBACK_BASE = int(ipaddress.IPv4Address("172.20.0.0"))


def validate_count(devices):
    if not isinstance(devices, int) or devices < 6 or devices > 2500 or devices % 2:
        raise ValueError("devices must be an even integer between 6 and 2500")


def metadata(devices):
    validate_count(devices)
    links = devices * 3 // 2
    dual_stack_links = (links + 3) // 4
    return {"generator_version": 1, "synthetic": True, "dialect": "cisco_ios", "devices": devices,
            "interfaces": devices * 4, "ipv4_addresses": devices * 4, "ipv6_addresses": dual_stack_links * 2,
            "logical_links": links, "ring_links": devices, "chord_links": devices // 2,
            "dual_stack_links": dual_stack_links, "segments": links + dual_stack_links,
            "routing_contexts": devices, "address_domain": "default",
            "shape": "connected ring plus opposite-side chords; three routed interfaces and one loopback per device"}


def generate_configs(devices):
    validate_count(devices)
    peers = [[] for _ in range(devices)]
    relations = [(index, (index + 1) % devices, "ring") for index in range(devices)]
    relations.extend((index, index + devices // 2, "chord") for index in range(devices // 2))
    for link, (a, b, kind) in enumerate(relations):
        for endpoint, peer, side in ((a, b, 0), (b, a, 1)):
            peers[endpoint].append((link, peer, side, kind))
    for device, endpoints in enumerate(peers):
        hostname = f"SITE-{device // 20 + 1:03d}-R{device % 20 + 1:02d}"
        lines = ["version 17.9", f"hostname {hostname}", "!", "interface Loopback0",
                 f" ip address {ipaddress.IPv4Address(LOOPBACK_BASE + device + 1)} 255.255.255.255", "!"]
        for number, (link, peer, side, kind) in enumerate(sorted(endpoints)):
            peer_name = f"SITE-{peer // 20 + 1:03d}-R{peer % 20 + 1:02d}"
            lines.extend([f"interface GigabitEthernet0/{number}", f" description {kind} to {peer_name}",
                          f" ip address {ipaddress.IPv4Address(IPV4_BASE + link * 2 + side)} 255.255.255.254"])
            if link % 4 == 0:
                lines.append(f" ipv6 address {ipaddress.IPv6Address(IPV6_BASE + link * 2 + side)}/127")
            lines.extend([" no shutdown", "!"])
        lines.extend(["end", ""])
        yield f"router-{device + 1:04d}.cfg", "\n".join(lines)


def create_archive(devices, output):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest = metadata(devices)
    source_bytes = 0
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for filename, content in generate_configs(devices):
            data = content.encode("utf-8")
            source_bytes += len(data)
            entry = zipfile.ZipInfo(filename, date_time=(2026, 10, 24, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, data)
    manifest.update(source_bytes=source_bytes, zip_bytes=output.stat().st_size, output=str(output.resolve()))
    output.with_suffix(".manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--devices", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True, help="ZIP path; a sibling .manifest.json records expected counts")
    args = parser.parse_args()
    try:
        print(json.dumps(create_archive(args.devices, args.output), indent=2))
    except ValueError as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
