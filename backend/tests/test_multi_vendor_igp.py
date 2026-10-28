"""Native adapters must agree through the neutral graph, including mixed vendors."""
from copy import deepcopy
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from backend.app.inference import infer
from backend.app.lsdb.adapters import parse, detect
from backend.app.lsdb.analysis import build
from backend.app.lsdb.model import AnalyzeRequest, Batch
from backend.app.main import create_app
from backend.app.parser import parse_config
from backend.app.routing.adapters.statements import hierarchy
from backend.app.routing.model import RoutingFacts
from backend.app.routing.simulation import simulate
from backend.tests.test_routing import query as config_query

SAMPLES = Path(__file__).resolve().parents[2] / "samples"
VENDORS = ("huawei", "cisco", "juniper", "nokia")


def mixed_lab():
    snapshot = dict(id="mixed", devices=[], interfaces=[], contexts=[], routing=RoutingFacts().model_dump())
    for path in sorted((SAMPLES / "multi-vendor-igp").glob("*.cfg")):
        parsed = parse_config(path.read_text(encoding="utf-8"), path.name, path.name, path.stem)
        snapshot["devices"].append(parsed["device"])
        for key in ("interfaces", "contexts"):
            snapshot[key].extend(parsed[key])
        for key in ("instances", "bindings", "advertisements", "issues", "adapter_devices"):
            snapshot["routing"][key].extend(parsed["routing"][key])
        snapshot["routing"]["adapter_versions"].update(parsed["routing"]["adapter_versions"])
    snapshot.update(infer(snapshot))
    return snapshot


def observation(vendor, protocol, text=None, **kwargs):
    metadata = dict(vendor=vendor, id="batch", name="synthetic", protocol=protocol,
        process_id="6" if vendor == "huawei" else "default" if vendor == "juniper" else "CORE" if vendor == "cisco" and protocol == "isis" else "1",
        scope="0" if protocol == "ospf" else "2", imported_at="2026-10-28T00:00:00Z", declared_complete=True)
    metadata.update(kwargs)
    return parse([dict(id="source", filename="native.txt", text=text if text is not None else
        (SAMPLES / "multi-vendor-lsdb" / f"{vendor}-{protocol}.txt").read_text(encoding="utf-8"))], **metadata)


def run_observation(batch, snapshot, **kwargs):
    request = AnalyzeRequest(version=1, **kwargs)
    initial = build(batch, snapshot, request)
    source = next(n for n in initial["nodes"] if n["protocol_id"] in ("10.255.0.1", "0000.0000.0001.00"))
    target = next(d for d in initial["destinations"] if d["prefix"] == "10.255.0.6/32")
    return build(batch, snapshot, request.model_copy(update={"source":source["id"], "destination":target["id"]}))


@pytest.mark.parametrize("protocol,scope", [("ospf", "0.0.0.0"), ("isis", "2")])
def test_native_mixed_configuration_spf_and_directional_metrics(protocol, scope):
    snapshot = mixed_lab()
    before = deepcopy(snapshot)
    assert {d["vendor"] for d in snapshot["devices"]} == {"Huawei", "Cisco", "Juniper", "Nokia"}
    assert len(snapshot["routing"]["instances"]) == 12
    assert not snapshot["routing"]["issues"]
    result = simulate(snapshot, config_query(snapshot, protocol, scope))
    assert result["status"] == "ok" and result["total_cost"] == 30
    assert result["path_count"] == "3"
    assert sorted(p["share_percent"] for p in result["paths"]) == [25, 25, 50]
    assert len(result["hops"]) == 7
    assert snapshot == before


@pytest.mark.parametrize("vendor", VENDORS)
@pytest.mark.parametrize("protocol", ["ospf", "isis"])
def test_every_native_lsdb_has_same_spf_and_can_fuse_with_all_four_vendors(vendor, protocol):
    batch = observation(vendor, protocol)
    assert len(batch.records) == 6 and not batch.diagnostics
    snapshot = mixed_lab()
    before, original = deepcopy(snapshot), batch.model_dump()
    result = run_observation(batch, snapshot)
    assert not result["diagnostics"]
    assert len(result["nodes"]) == 6 and all(n["instance_id"] for n in result["nodes"])
    assert len(result["comparisons"]) == 14
    assert {c["status"] for c in result["comparisons"]} == {"consistent"}
    assert result["config_simulation"]["total_cost"] == 30
    assert result["simulation"]["total_cost"] == 30 and result["simulation"]["path_count"] == "3"
    assert sorted(p["share_percent"] for p in result["simulation"]["paths"]) == [25,25,50]
    assert batch.model_dump() == original and snapshot == before
    request=AnalyzeRequest(version=1)
    view=build(batch,snapshot,request)
    source=next(n['id'] for n in view['nodes'] if n['label']=='IGP-D')
    destination=next(p['id'] for p in view['destinations'] if p['prefix']=='10.255.0.1/32')
    reverse=build(batch,snapshot,request.model_copy(update={'source':source,'destination':destination}))
    expected=31 if protocol=='ospf' else 30
    assert reverse['simulation']['total_cost']==expected and reverse['config_simulation']['total_cost']==expected
    # The engine must work without any configuration or device vendor metadata.
    empty = dict(id="empty",devices=[],interfaces=[],contexts=[],links=[])
    standalone = run_observation(batch, empty, mode="observed")
    assert standalone["simulation"]["path_count"] == "3"
    assert all(n["instance_id"] is None for n in standalone["nodes"])


@pytest.mark.parametrize("vendor", VENDORS)
@pytest.mark.parametrize("protocol", ["ospf", "isis"])
def test_native_evidence_lines_and_automatic_vendor_detection(vendor, protocol):
    text = (SAMPLES / "multi-vendor-lsdb" / f"{vendor}-{protocol}.txt").read_text(encoding="utf-8")
    assert detect(text) == vendor
    batch = observation(vendor, protocol)
    automatic = parse([dict(id="source",filename="x",text=text)], **{
        k:v for k,v in batch.model_dump().items() if k not in
        ("vendor","adapter","records","diagnostics","source_files","aliases")}, vendor="auto")
    assert automatic.vendor == vendor and len(automatic.records) == 6
    for record in batch.records:
        assert record.source_refs and record.source_refs[0].source_file_id == "source"
        assert 1 <= record.source_refs[0].line <= len(text.splitlines())
        assert all(p.source_refs for p in record.prefixes)


@pytest.mark.parametrize("vendor", ("cisco", "juniper", "nokia"))
def test_native_incomplete_capture_cannot_silently_select_other_path(vendor):
    batch = observation(vendor, "ospf", declared_complete=False)
    assert run_observation(batch, mixed_lab())["simulation"]["status"] == "incomplete"
    batch = observation(vendor, "ospf")
    batch.records.pop(1)
    result = run_observation(batch, mixed_lab())
    assert result["simulation"]["status"] == "incomplete"
    assert any(d["code"] == "missing_record" for d in result["diagnostics"])


@pytest.mark.parametrize("vendor", ("cisco", "juniper", "nokia"))
def test_native_isis_aliases_map_by_explicit_advertised_addresses(vendor):
    text = (SAMPLES/"multi-vendor-lsdb"/f"{vendor}-isis.txt").read_text(encoding="utf-8")
    for number, label in enumerate(("S","A","B","C","E","D"), 1):
        text = text.replace(f"0000.0000.{number:04d}", f"IGP-{label}")
    batch = observation(vendor,"isis",text)
    result = build(batch, mixed_lab(), AnalyzeRequest(version=1))
    assert all(n["instance_id"] for n in result["nodes"])
    # Hostnames on their own never establish identity.
    for record in batch.records: record.addresses = []
    result = build(batch,mixed_lab(),AnalyzeRequest(version=1))
    assert all(n["instance_id"] is None for n in result["nodes"] if n.get("observed", True))
    table = "\n".join(f"0000.0000.{i:04d} IGP-{label}" for i,label in enumerate(("S","A","B","C","E","D"),1))
    batch = observation(vendor,"isis",text+"\n"+table)
    assert all(r.origin.startswith("0000.0000.") for r in batch.records)


def test_old_huawei_batches_still_load_without_new_vendor_fields():
    data=observation("huawei","ospf").model_dump()
    for key in ("vendor","adapter","aliases"):data.pop(key)
    assert Batch.model_validate(data).vendor == "huawei"


def test_remaining_native_configuration_dialects_preserve_protocol_facts():
    ios=(SAMPLES/"multi-vendor-igp/01-S-cisco-ios.cfg").read_text(encoding="utf-8")
    nxos='feature ospf\nfeature isis\n'+ios.replace("ip ospf 1 area", "ip router ospf 1 area")
    parsed=parse_config(nxos,"nxos.cfg","nxos","nxos","cisco_nxos")
    assert len(parsed["routing"]["instances"])==2 and not parsed["routing"]["issues"]
    md=(SAMPLES/"multi-vendor-igp/05-E-nokia-md.cfg").read_text(encoding="utf-8")
    records,_,_=hierarchy(md,True)
    flat="\n".join('/configure '+" ".join('"'+t+'"' if ' ' in t else t for t in tokens[1:]) for tokens,_,off in records if not off and tokens[:1]==['configure'])
    parsed=parse_config(flat,"md.cfg","md","md","nokia_md_flat")
    assert len(parsed["routing"]["bindings"])==6 and not parsed["routing"]["issues"]
    junos='''system { host-name J; }
interfaces { lo0 { unit 0 { family inet { address 10.255.1.1/32; } family iso { address 49.0001.0000.0000.0011.00; } } }
 ge-0/0/0 { unit 0 { family inet { address 10.0.1.1/30; } family iso; } } }
routing-options { router-id 10.255.1.1; }
protocols { ospf { area 0 { interface lo0.0 { passive; metric 1; } interface ge-0/0/0.0 { interface-type p2p; metric 10; } } }
 isis { level 1 { disable; } level 2 { wide-metrics-only; } interface lo0.0 { passive; } interface ge-0/0/0.0 { point-to-point; } } }
'''
    parsed=parse_config(junos,"junos.cfg","junos","junos","junos_hierarchical")
    assert len(parsed["routing"]["bindings"])==4 and not parsed["routing"]["issues"]
    # Empty interface containers also activate a native protocol interface.
    junos=junos.replace('interface ge-0/0/0.0 { point-to-point; }','interface ge-0/0/0.0 { }')
    assert len(parse_config(junos,"j","j","j","junos_hierarchical")["routing"]["bindings"])==4


def test_ipv6_metrics_do_not_overwrite_cisco_xr_ipv4_metrics():
    text=(SAMPLES/"multi-vendor-igp/04-C-cisco-xr.cfg").read_text(encoding="utf-8")
    text=text.replace('   metric 10\n  !','   metric 10\n  !\n  address-family ipv6 unicast\n   metric 999\n  !')
    facts=parse_config(text,"xr","xr","xr","cisco_xr")["routing"]
    assert {b["cost"] for b in facts["bindings"] if b["scope"]=="2"}=={0,10}
    assert not facts["issues"]


def test_junos_disabled_protocol_and_all_interface_levels_stay_disabled():
    text=(SAMPLES/"multi-vendor-igp/02-A-juniper-set.cfg").read_text(encoding="utf-8")
    text+='\ndeactivate protocols isis\n'
    facts=parse_config(text,"j","j","j","junos_set")["routing"]
    assert {i["protocol"] for i in facts["instances"]}=={"ospf"}
    text=text.replace('deactivate protocols isis','set protocols isis interface ge-0/0/0.0 level 1 disable\nset protocols isis interface ge-0/0/0.0 level 2 disable')
    parsed=parse_config(text,"j","j","j","junos_set")
    interface=next(i for i in parsed["interfaces"] if i["raw_name"]=='ge-0/0/0.0')
    assert not [b for b in parsed['routing']['bindings'] if b['scope']=='2' and b['interface_id']==interface['id']]


def test_nokia_derived_system_id_respects_router_id_precedence_and_global_ecmp():
    text=(SAMPLES/'multi-vendor-igp/03-B-nokia-classic.cfg').read_text(encoding="utf-8").replace('   system-id 0000.0000.0003','   router-id 192.0.2.1')
    facts=parse_config(text,'n','n','n','nokia_classic')['routing']
    instance=next(i for i in facts['instances'] if i['protocol']=='isis')
    assert instance['router_id']=='192000002001'
    assert all(i['max_paths']==8 for i in facts['instances'])


def test_nokia_each_neighbor_requires_its_own_metric():
    text=(SAMPLES/'multi-vendor-lsdb/nokia-isis.txt').read_text(encoding="utf-8").replace('  Default Metric : 10\n','',1)
    assert any(d.code=='missing_link_metric' for d in observation('nokia','isis',text).diagnostics)


def test_cisco_prefix_attributes_and_ipv6_context_do_not_leak_to_ipv4():
    text=(SAMPLES/'multi-vendor-lsdb/cisco-isis.txt').read_text(encoding="utf-8")
    text=text.replace(' Metric: 0 IP-Extended 10.255.0.6/32',' Metric: 0 IP-Extended 10.255.0.6/32\n Prefix Attribute Flags: X:0 R:1 N:0')
    batch=observation('cisco','isis',text)
    assert not batch.records[-1].prefixes
    text=text.replace(' Prefix Attribute Flags: X:0 R:1 N:0',' Metric: 10 IPv6 2001:db8::1/128\n Prefix Attribute Flags: X:1 R:1 N:0')
    batch=observation('cisco','isis',text)
    assert batch.records[-1].prefixes[0].prefix=='10.255.0.6/32'
    assert not [d for d in batch.diagnostics if d.blocking]


def test_instance_metric_encoding_is_selected_per_level():
    text=(SAMPLES/'multi-vendor-igp/02-A-juniper-set.cfg').read_text(encoding="utf-8").replace('set protocols isis level 1 disable','')
    facts=parse_config(text,'j','j','j','junos_set')['routing']
    instance=next(i for i in facts['instances'] if i['protocol']=='isis')
    assert instance['metric_style']=='both' and instance['level_metric_styles']=={'2':'wide'}
    text=(SAMPLES/'multi-vendor-igp/04-C-cisco-xr.cfg').read_text(encoding="utf-8").replace('  metric-style wide','  metric-style wide level 2').replace(' is-type level-2-only',' is-type level-1-2')
    facts=parse_config(text,'x','x','x','cisco_xr')['routing']
    instance=next(i for i in facts['instances'] if i['protocol']=='isis')
    assert instance['metric_style']=='narrow' and instance['level_metric_styles']=={'2':'wide'}
    assert {b['metric_limit'] for b in facts['bindings'] if b['scope']=='1'}=={63}


def test_xr_ipv6_only_interface_does_not_form_an_ipv4_adjacency():
    text=(SAMPLES/'multi-vendor-igp/04-C-cisco-xr.cfg').read_text(encoding="utf-8")
    # Keep IPv4 configured on the interface, but activate IS-IS only for IPv6.
    text=text.replace('  address-family ipv4 unicast','  address-family ipv6 unicast')
    facts=parse_config(text,'x','x','x','cisco_xr')['routing']
    assert not [b for b in facts['bindings'] if b['scope']=='2']


@pytest.mark.parametrize('protocol',['ospf','isis'])
def test_junos_native_routing_instances_do_not_merge_into_default(protocol):
    batch=observation('juniper',protocol,context_name='blue')
    assert not batch.records
    text=(SAMPLES/'multi-vendor-lsdb'/f'juniper-{protocol}.txt').read_text(encoding="utf-8")
    header='Routing Instance: blue\n' if protocol=='ospf' else 'ISIS instance: Default, Routing Instance: blue\n'
    line='OSPF link state database, Area 0.0.0.0' if protocol=='ospf' else 'IS-IS level 2 link-state database:'
    text=text.replace(line,header+line)
    assert len(observation('juniper',protocol,text,context_name='blue').records)==6
    assert not observation('juniper',protocol,text).records


def test_native_mixed_vendor_batch_requires_separate_captures():
    text=(SAMPLES/'multi-vendor-lsdb/cisco-ospf.txt').read_text(encoding="utf-8")+'\n'+(SAMPLES/'multi-vendor-lsdb/juniper-ospf.txt').read_text(encoding="utf-8")
    assert detect(text) is None
    assert any(d.code=='vendor_mismatch' for d in observation('cisco','ospf',text).diagnostics)
    with pytest.raises(ValueError):
        parse([dict(id='s',filename='mixed',text=text)],vendor='auto')


@pytest.mark.parametrize("file,dialect,needle",[("01-S-cisco-ios.cfg","cisco_ios"," maximum-paths 8"),("02-A-juniper-set.cfg","junos_set","set protocols ospf area 0.0.0.0 interface lo0.0 metric 1"),("03-B-nokia-classic.cfg","nokia_classic","  ecmp 8")])
def test_invalid_protocol_values_are_diagnosed_without_losing_config(file,dialect,needle):
    text=(SAMPLES/'multi-vendor-igp'/file).read_text(encoding="utf-8").replace(needle,needle.rsplit(' ',1)[0]+' invalid')
    parsed=parse_config(text,'bad','bad','bad',dialect)
    assert parsed['interfaces']
    assert parsed['routing']['issues']


def test_multivendor_api_demo_import_backup_restore_and_no_config_mutation(tmp_path):
    with TestClient(create_app(tmp_path,worker_mode='inline')) as c:
        capabilities=c.get('/api/capabilities').json()
        assert len(capabilities['routing']['dialects'])==9 and len(capabilities['lsdb']['vendors'])==4
        project=c.post('/api/projects',json={'name':'Mixed acceptance'}).json();pid=project['id']
        job=c.post(f'/api/projects/{pid}/demo?kind=multi-vendor-igp').json()
        assert c.get('/api/imports/'+job['id']).json()['status']=='completed'
        project=c.get(f'/api/projects/{pid}').json();snapshot=project['snapshot'];version=project['version']
        for vendor in VENDORS:
            for protocol in ('ospf','isis'):
                response=c.post(f'/api/projects/{pid}/lsdb/demo?protocol={protocol}&vendor={vendor}',json={'version':version})
                assert response.status_code==201,response.text
                value=response.json();version=value['project_version'];b=value['batch']
                assert b['vendor']==vendor and b['record_count']==6
                assert c.get(f'/api/projects/{pid}/lsdb/{b["id"]}').json()['adapter'].startswith(vendor)
        assert c.get(f'/api/projects/{pid}').json()['snapshot']==snapshot
        # Actual multipart import, including automatic native selection.
        text=(SAMPLES/'multi-vendor-lsdb/cisco-ospf.txt').read_bytes()
        response=c.post(f'/api/projects/{pid}/lsdb',data=dict(expected_version=version,name='Native upload',vendor='auto',protocol='ospf',scope='0',process_id='1',declared_complete='true'),files={'files':('native.txt',text)})
        assert response.status_code==201,response.text
        b=response.json()['batch'];assert b['vendor']=='cisco'
        original=c.get(f'/api/projects/{pid}/sources/{b["source_files"][0]["id"]}').json()['text']
        assert original.encode()==text
        bundle=c.get(f'/api/projects/{pid}/exports/json?include_sources=true').content
        restored=c.post('/api/projects/restore',files={'file':('backup.json',bundle)})
        assert restored.status_code==201,restored.text
        rid=restored.json()['id'];batches=c.get(f'/api/projects/{rid}/lsdb').json()['batches']
        assert len(batches)==9 and {b['vendor'] for b in batches}==set(VENDORS)
        for batch in batches:
            result=c.post(f'/api/projects/{rid}/lsdb/{batch["id"]}/analyze',json={'version':1})
            assert result.status_code==200,result.text
            assert len(result.json()['nodes'])==6 and all(n['instance_id'] for n in result.json()['nodes'])
