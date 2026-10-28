"""Shared observation utilities; native syntax remains in each adapter."""
import ipaddress
import re
from .model import Record, LinkStateLink, Prefix

IP = r"(?:\d{1,3}\.){3}\d{1,3}"
SYS = r"[\da-fA-F]{4}(?:\.[\da-fA-F]{4}){2}"
LSP = r"([\w.:-]+)\.([\da-fA-F]{2})-([\da-fA-F]{2})"


def area(value):
    return str(ipaddress.IPv4Address(int(value) if value.isdigit() else value))


def node(value):
    return value.rstrip(',').lower()


def local_address(value):
    """0/8 Router-LSA link data is an ifIndex, not an IPv4 next hop."""
    address = ipaddress.IPv4Address(value)
    return (None, str(int(address))) if int(address) < 2**24 else (str(address), None)


def canonical_lsp(value):
    m = re.fullmatch(LSP, value.rstrip('*'), re.I)
    if not m:
        raise ValueError("LSP ID 必须包含 System ID / 主机别名、伪节点及分片号。")
    return f"{m[1].lower()}.{m[2].lower()}", int(m[3], 16)


def ospf_record(kind, lsid, adv, sequence, checksum, age, ref, detailed=True):
    lsid=str(ipaddress.IPv4Address(lsid));adv=str(ipaddress.IPv4Address(adv))
    kind = kind.lower()
    normalized = "router" if kind in ("router", "router links") else "network" if kind in ("network", "network links") else "other"
    if normalized=='router' and lsid!=adv:raise ValueError('Router LSA ID differs from Router ID')
    return Record(key=f"{normalized if normalized != 'other' else kind}:{lsid}:{adv}", kind=normalized,
        origin="net:"+lsid if normalized=="network" else adv, sequence=int(sequence.removeprefix("0x"),16),
        checksum=int(checksum.removeprefix("0x"),16), age=int(age), expired=int(age)>=3600,
        detailed=detailed, source_refs=[ref])


def ospf_link(record, kind, target, data, cost, ref):
    kind=kind.lower().replace("-", "").replace(" ", "")
    if kind in ("stub", "stubnetwork", "stubnet", "3"):
        record.prefixes.append(Prefix(prefix=str(ipaddress.IPv4Network(f"{target}/{data}",strict=False)),cost=int(cost),source_refs=[ref]))
    elif kind in ("pointtopoint", "p2p", "1", "transit", "transitnetwork", "2"):
        local_ip,local_id=local_address(data)
        transit=kind in ("transit", "transitnetwork", "2")
        record.links.append(LinkStateLink(target=("net:" if transit else "")+target,cost=int(cost),kind="transit" if transit else "p2p",
            local_ip=local_ip,local_id=local_id,source_refs=[ref]))
    else:
        raise ValueError("暂不支持的 OSPF 链路类型。")


def isis_record(lspid, seq, checksum, lifetime, ref, overload=False, detailed=True):
    origin,fragment=canonical_lsp(lspid)
    return Record(key=f"{origin}-{fragment:02x}",kind="lsp",origin=origin,fragment=fragment,
        sequence=int(seq.removeprefix("0x"),16),checksum=int(checksum.removeprefix("0x"),16),
        age=int(lifetime),expired=int(lifetime)==0,overload=overload,detailed=detailed,ipv4=False,source_refs=[ref])


def prefix(value, cost, ref):
    return Prefix(prefix=str(ipaddress.IPv4Network(value,strict=False)),cost=int(cost),source_refs=[ref])


def finish_isis(record, links, prefixes):
    # Native outputs often repeat the summary view and its decoded TLVs.
    wide_targets={l.target for l in links if l.encoding=="wide"}
    record.links=[l for l in links if l.encoding!="narrow" or l.target not in wide_targets]
    record.prefixes=prefixes
    record.detailed=record.detailed or bool(links or prefixes)
    return record
