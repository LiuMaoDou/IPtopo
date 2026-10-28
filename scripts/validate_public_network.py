"""Offline verification of frozen public vendor CLI samples.

Run: .venv/bin/python -m scripts.validate_public_network --report /tmp/public-validation.json
Exercises native product adapters plus an independent published-route reader.
"""
from __future__ import annotations
import argparse
from collections import defaultdict
from hashlib import sha256
import ipaddress
import json
from pathlib import Path
import re

from backend.app.inference import infer
from backend.app.lsdb.huawei import parse as parse_lsdb
from backend.app.lsdb.adapters import parse as parse_native
from backend.app.lsdb.analysis import build
from backend.app.lsdb.model import AnalyzeRequest
from backend.app.parser import parse_config
from backend.app.routing.spf import calculate

ROOT = Path(__file__).resolve().parents[1] / "samples" / "public-network"

# Expected addresses are independently transcribed from the cited configuration
# examples. Do not derive these assertions from the parser's own output.
CONFIG_CASES = {
    "cisco/config/ospf-r1.cfg": ("cisco_ios", ["1.1.1.1", "4.0.0.1"]),
    "cisco/config/ospf-r2.cfg": ("cisco_ios", ["2.2.2.2", "6.0.0.2", "200.0.0.2"]),
    "cisco/config/ospf-r3.cfg": ("cisco_ios", ["3.3.3.3", "200.0.0.3"]),
    "cisco/config/isis-r1.cfg": ("cisco_ios", ["172.16.1.1", "172.16.12.1"]),
    "cisco/config/isis-r2.cfg": ("cisco_ios", ["172.16.2.2", "172.16.12.2", "172.16.23.1"]),
    "cisco/config/isis-r3.cfg": ("cisco_ios", ["172.16.3.3", "172.16.23.2"]),
    "juniper/config/isis-r1-set.cfg": ("junos_set", ["10.100.12.1", "192.168.0.1"]),
    "juniper/config/isis-r2-set.cfg": ("junos_set", ["10.100.12.2", "192.168.0.2"]),
    "juniper/config/isis-r1-hierarchy.cfg": ("junos_hierarchical", ["10.100.12.1", "192.168.0.1"]),
    "juniper/config/ospf-r1.cfg": ("junos_set", ["192.0.2.45"]),
    "juniper/config/ospf-r2.cfg": ("junos_set", ["192.0.2.37"]),
    "nokia/config/router-basic-classic.cfg": ("nokia_classic", ["192.0.2.1", "192.0.2.1"]),
    "nokia/config/ospf-router-context.cfg": ("nokia_classic", ["10.10.10.104", "10.0.0.103"]),
    "huawei/config/isis-ipv6-switchA.cfg": ("huawei_vrp", ["fc00:0:0:11::2"]),
    "huawei/config/isis-ipv6-switchB.cfg": ("huawei_vrp", ["fc00:0:0:12::2"]),
    "huawei/config/isis-ipv6-switchC.cfg": ("huawei_vrp", ["fc00:0:0:11::1", "fc00:0:0:12::1", "fc00:0:0:30::1"]),
    "huawei/config/isis-ipv6-switchD.cfg": ("huawei_vrp", ["fc00:0:0:30::2", "fc00:0:0:20::1"]),
}


def prepare_config(file, text):
    """Explicit document extraction steps; original files remain untouched."""
    transformations = []
    if file.startswith("cisco/config/isis-"):
        # Cisco's published example is rendered flat. Restore only indentation
        # implied by interface/router commands, keeping every CLI token unchanged.
        current = None
        rows = []
        for line in text.splitlines():
            s = line.strip()
            if re.match(r"interface\s|router\s", s, re.I):
                current = s.split()[0].lower(); rows.append(s)
            elif s == "!":
                current = None; rows.append(s)
            elif current and s:
                rows.append(" " + s)
            else:
                rows.append(s)
        text = "\n".join(rows) + "\n"
        transformations.append("Restored submode indentation lost in the official rendered example; tokens unchanged")
    if file == "nokia/config/ospf-router-context.cfg":
        # The prompt proves config>router; the excerpt omits its ancestors.
        rows = [s for s in text.splitlines() if s.strip() and not re.match(r"(?:A:|[-]+|IP Configuration)", s.strip())]
        text = "configure\n router\n" + "\n".join(rows) + "\n exit\nexit\n"
        transformations.append("Wrapped the explicit config>router excerpt in configure/router; no address or protocol values added")
    return text, transformations


def config_facts(file):
    dialect, _ = CONFIG_CASES[file]
    text, transformations = prepare_config(file, (ROOT / file).read_text(encoding="utf-8"))
    parsed = parse_config(text, Path(file).name, file, file, dialect)
    return parsed, transformations


def huawei_batch(file, protocol, scope, process="1"):
    return parse_lsdb([dict(id=file, filename=Path(file).name, text=(ROOT/file).read_text(encoding="utf-8"))],
        id="public:"+file+scope, name=file, protocol=protocol, process_id=process, scope=scope,
        imported_at="2026-10-28T00:00:00Z", declared_complete=False)


def cisco_area0_reference():
    """Independent narrow reader for the two published area-0 Router LSAs.

    Purposefully outside the product adapter registry, for an independent oracle.
    """
    text = (ROOT/"cisco/lsdb/ospf-r2-router.txt").read_text(encoding="utf-8")
    # The third block starts Area 1. Never union the two areas.
    text = text.split("Router Link States (Area 1)", 1)[0]
    blocks = re.split(r"(?=\s*LS age:)", text)
    arcs, prefixes = [], []
    for block in blocks:
        adv = re.search(r"Advertising Router:\s*(\S+)", block)
        if not adv:
            continue
        origin = adv[1]
        for link in re.split(r"Link connected to:", block)[1:]:
            metric = re.search(r"TOS 0 Metrics:\s*(\d+)", link)
            if not metric:
                raise ValueError("Published Cisco link has no metric")
            peer = re.search(r"Neighboring Router ID:\s*(\S+)", link)
            prefix = re.search(r"Network/subnet number:\s*(\S+)", link)
            mask = re.search(r"Network Mask:\s*(\S+)", link)
            if peer:
                arcs.append(dict(id=origin+">"+peer[1], source=origin, target=peer[1], cost=int(metric[1]),
                                 source_device_id=origin, target_device_id=peer[1]))
            elif prefix and mask:
                prefixes.append(dict(origin=origin, prefix=str(ipaddress.IPv4Network(f"{prefix[1]}/{mask[1]}", strict=False)), cost=int(metric[1])))
    return arcs, prefixes


def verify():
    manifest = json.loads((ROOT/"manifest.json").read_text(encoding="utf-8"))
    checks = []
    def check(name, passed, **details):
        checks.append(dict(name=name, passed=bool(passed), **details))
    for entry in manifest["entries"]:
        path = ROOT/entry["file"]
        check("source_integrity:"+entry["file"], path.is_file() and sha256(path.read_bytes()).hexdigest()==entry["sha256"], source_url=entry["source_url"])
    parsed = {}
    for file, (dialect, expected) in CONFIG_CASES.items():
        result, transformations = config_facts(file)
        actual = sorted(str(ipaddress.ip_address(a["address"])) for i in result["interfaces"] for a in i["addresses"] if a.get("address"))
        parsed[file] = result
        check("config_addresses:"+file, actual == sorted(str(ipaddress.ip_address(a)) for a in expected),
              dialect=dialect, expected=sorted(expected), actual=actual, transformations=transformations,
              diagnostics=[d["code"] for d in result["diagnostics"]])
    for vendor, protocol, files, expected_pairs in [
        ("cisco", "isis", [f"cisco/config/isis-r{i}.cfg" for i in (1,2,3)], 2),
        ("juniper", "isis", [f"juniper/config/isis-r{i}-set.cfg" for i in (1,2)], 1),
        ("juniper", "ospf", [f"juniper/config/ospf-r{i}.cfg" for i in (1,2)], 1),
    ]:
        snapshot = dict(devices=[parsed[f]["device"] for f in files],
            interfaces=[i for f in files for i in parsed[f]["interfaces"]], contexts=[c for f in files for c in parsed[f]["contexts"]])
        inferred = infer(snapshot)
        check(f"config_topology:{vendor}:{protocol}", len(inferred["links"])==expected_pairs, expected_links=expected_pairs, actual_links=len(inferred["links"]))
    for file, result in parsed.items():
        if file.startswith('cisco/') or file.startswith('juniper/config/isis-'):
            check('native_routing_config:'+file, len(result['routing']['instances'])==1 and bool(result['routing']['bindings']) and not result['routing']['issues'],
                instances=len(result['routing']['instances']),bindings=len(result['routing']['bindings']))
    # Primary publication fields, not generated synthetic LSAs.
    empty = dict(id="none",devices=[],interfaces=[],contexts=[],links=[])
    for file,protocol,scope,process,count in [
        ("huawei/lsdb/ospf-router.txt","ospf","0","1",1),
        ("huawei/lsdb/ospf-network.txt","ospf","0","1",1),
        ("huawei/lsdb/ospf-summary.txt","ospf","0","1",2),
        ("huawei/lsdb/isis-old-verbose.txt","isis","1","1",2),
        ("huawei/lsdb/isis-old-verbose.txt","isis","2","1",2),
        ("huawei/lsdb/isis-ipv6-summary.txt","isis","1","1",5),
        ("huawei/lsdb/isis-ipv6-summary.txt","isis","2","1",3),
        ("huawei/lsdb/isis-extended-attributes.txt","isis","2","1",1),
    ]:
        batch = huawei_batch(file,protocol,scope,process)
        analysis = build(batch,empty,AnalyzeRequest(version=1))
        check(f"lsdb_records:{file}:{scope}", len(batch.records)==count, expected_records=count, actual_records=len(batch.records),
            blocking_codes=sorted({d["code"] for d in analysis["diagnostics"] if d["blocking"]}))
        check(f"partial_view_guard:{file}:{scope}", any(d["code"]=="unconfirmed_capture" and d["blocking"] for d in analysis["diagnostics"]))
    batch = huawei_batch("huawei/lsdb/ospf-star-inconsistent.txt","ospf","0","100")
    check("inconsistent_official_sample_is_rejected", not batch.records and any(d.code=="malformed_lsa" for d in batch.diagnostics))
    router = huawei_batch("huawei/lsdb/ospf-router.txt","ospf","0").records[0]
    check("stub_prefixes_are_not_links", not router.links and {p.prefix for p in router.prefixes}=={"11.1.1.0/24","3.3.3.3/32"})
    network = huawei_batch("huawei/lsdb/ospf-network.txt","ospf","0").records[0]
    check("network_members_are_zero_metric", {l.target for l in network.links}=={"1.1.1.1","3.3.3.3"} and all(l.cost==0 for l in network.links))
    modern = huawei_batch("huawei/lsdb/isis-extended-attributes.txt","isis","2")
    check("interlevel_prefix_is_excluded", all(p.prefix!="2.2.2.2/32" for r in modern.records for p in r.prefixes))
    old = huawei_batch("huawei/lsdb/isis-old-verbose.txt","isis","1")
    check("holdtime_zero_is_expired", old.records[0].age==0 and old.records[0].expired)
    # Source-published OSPF routes: 1->6/8 and 2->4/8 both have cost 74.
    arcs, prefixes = cisco_area0_reference()
    route_text = (ROOT/"cisco/verification/ospf-routes.txt").read_text(encoding="utf-8")
    check("cisco_reference_graph", len(arcs)==2 and len(prefixes)==2)
    for source, prefix in [("1.1.1.1","6.0.0.0/8"),("2.2.2.2","4.0.0.0/8")]:
        target = next(p for p in prefixes if p["prefix"]==prefix)
        result = calculate(arcs,source,target["origin"],terminal_cost=target["cost"])
        published = re.search(re.escape(prefix)+r"\s+\[110/(\d+)\]",route_text)
        check("published_route_cost:"+source+":"+prefix, published is not None and result["status"]=="ok" and result["total_cost"]==int(published[1]),
              published_cost=int(published[1]) if published else None, calculated_cost=result["total_cost"], source_protocol="Cisco IOS OSPF area 0")
    # The product registry reads original native CLI, without translating it to
    # Huawei syntax. Incomplete public examples remain explicitly incomplete.
    native_cases=[
        ('cisco/lsdb/ospf-r2-router.txt','ospf','0','2',2),
        ('cisco/lsdb/isis-r2-detail.txt','isis','1','default',1),
        ('cisco/lsdb/isis-r2-detail.txt','isis','2','default',1),
        ('juniper/lsdb/ospf-detail.txt','ospf','0.0.0.1','default',5),
        ('juniper/lsdb/ospf-detail.txt','ospf','0.0.0.2','default',6),
        ('juniper/lsdb/isis-detail.txt','isis','1','default',1),
        ('juniper/lsdb/isis-detail.txt','isis','2','default',1),
        ('juniper/lsdb/isis-extensive.txt','isis','1','default',1),
        ('nokia/lsdb/ospf-database.txt','ospf','0','0',1),
        ('nokia/lsdb/isis-database.txt','isis','2','1',1),
        ('nokia/lsdb/isis-ipv6-detail.txt','isis','1','0',1),
    ]
    for file,protocol,scope,process,count in native_cases:
        batch=parse_native([dict(id=file,filename=Path(file).name,text=(ROOT/file).read_text(encoding="utf-8"))],vendor=file.split('/')[0],
            id='native:'+file+scope,name=file,protocol=protocol,process_id=process,scope=scope,
            imported_at='2026-10-28T00:00:00Z',declared_complete=False)
        check('native_lsdb_records:'+file+':'+scope,len(batch.records)==count,expected_records=count,actual_records=len(batch.records))
        analysis=build(batch,empty,AnalyzeRequest(version=1))
        check('native_partial_view_guard:'+file+':'+scope,any(d['code']=='unconfirmed_capture' and d['blocking'] for d in analysis['diagnostics']))
    # Read the same paired, two-router area through the product adapter and
    # compare both published routes, not only the separate reference reader.
    file='cisco/lsdb/ospf-r2-router.txt'
    batch=parse_native([dict(id=file,filename='cisco.txt',text=(ROOT/file).read_text(encoding="utf-8"))],vendor='cisco',
        id='paired-area0',name='Published area 0',protocol='ospf',process_id='2',scope='0',
        imported_at='2026-10-28T00:00:00Z',declared_complete=True)
    view=build(batch,empty,AnalyzeRequest(version=1,mode='observed'))
    for origin,prefix in [('1.1.1.1','6.0.0.0/8'),('2.2.2.2','4.0.0.0/8')]:
        source=next(n['id'] for n in view['nodes'] if n['protocol_id']==origin)
        target=next(p['id'] for p in view['destinations'] if p['prefix']==prefix)
        result=build(batch,empty,AnalyzeRequest(version=1,mode='observed',source=source,destination=target))['simulation']
        check('native_published_route_cost:'+origin+':'+prefix,result['status']=='ok' and result['total_cost']==74,calculated_cost=result['total_cost'])
    # Other CLI formats are deliberately not passed off as Huawei support.
    for vendor in ("cisco","juniper","nokia"):
        entries = [e for e in manifest["entries"] if e["vendor"]==vendor and e["kind"]=="lsdb"]
        for entry in entries:
            text=(ROOT/entry["file"]).read_text(encoding="utf-8")
            batch = parse_lsdb([dict(id="foreign",filename=Path(entry["file"]).name,text=text)],id="foreign",name="foreign",
                protocol=entry["protocol"],process_id="1",scope="0" if entry["protocol"]=="ospf" else "2",imported_at="2026-10-28T00:00:00Z",declared_complete=True)
            check("unsupported_vendor_guard:"+entry["file"], not batch.records and any(d.blocking for d in batch.diagnostics))
    return dict(dataset=dict(files=len(manifest["entries"]),vendors=sorted({e["vendor"] for e in manifest["entries"]})),
                checks=checks,passed=sum(c["passed"] for c in checks),failed=sum(not c["passed"] for c in checks),
                limitation="Official documentation examples and partial views, not customer-site captures. Native four-vendor adapters cover Standard IPv4 single-area/level SPF; advanced routing semantics remain out of scope.")


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report",type=Path)
    args=parser.parse_args()
    result=verify()
    if args.report:
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n", encoding="utf-8")
    print(json.dumps({k:v for k,v in result.items() if k!="checks"},ensure_ascii=False,indent=2))
    for check in result["checks"]:
        if not check["passed"]: print("FAILED",json.dumps(check,ensure_ascii=False))
    return 1 if result["failed"] else 0


if __name__=="__main__":
    raise SystemExit(main())
