"""Protocol evidence, fusion and project persistence acceptance tests."""
from copy import deepcopy
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from backend.app.main import create_app
from backend.app.store import now
from backend.app.lsdb.huawei import parse
from backend.app.lsdb.model import Batch, AnalyzeRequest
from backend.app.lsdb.analysis import build, select_records
from backend.app.routing.spf import calculate
from backend.tests.test_routing import lab

SAMPLES=Path(__file__).resolve().parents[2]/"samples"/"huawei-lsdb"


def batch(protocol="ospf",text=None,**kw):
    data=dict(id="observation",name="test",protocol=protocol,process_id="1",scope="0.0.0.0" if protocol=="ospf" else "2",imported_at=now(),declared_complete=True)
    data.update(kw)
    return parse([dict(id="source",filename=protocol+".txt",text=text if text is not None else (SAMPLES/f"{protocol}.txt").read_text(encoding="utf-8"))],**data)


def query(b,snapshot=None,**kw):
    snapshot=snapshot or lab()[0]
    req=AnalyzeRequest(version=1,**kw)
    result=build(b,snapshot,req)
    src=next(n for n in result["nodes"] if n["protocol_id"]==("10.255.0.1" if b.protocol=="ospf" else "0000.0000.0001.00"))
    dst=next(d for d in result["destinations"] if d["prefix"]=="10.255.0.6/32")
    req.source=src["id"];req.destination=dst["id"]
    return build(b,snapshot,req)


@pytest.mark.parametrize("protocol",["ospf","isis"])
def test_lsdb_and_config_are_independent_and_ecmp_is_per_router(protocol):
    b=batch(protocol);snapshot=lab()[0]
    before=deepcopy(snapshot);original=b.model_dump()
    result=query(b,snapshot)
    assert not result["diagnostics"]
    assert result["simulation"]["status"]=="ok"
    assert result["simulation"]["total_cost"]==30
    assert sorted(p["share_percent"] for p in result["simulation"]["paths"])==[25,25,50]
    assert result["config_simulation"]["path_count"]=="3"
    assert len(result["comparisons"])==14
    assert {c["status"] for c in result["comparisons"]}=={"consistent"}
    assert all(n["instance_id"] for n in result["nodes"])
    assert snapshot==before and b.model_dump()==original


@pytest.mark.parametrize("protocol",["ospf","isis"])
def test_lsdb_only_reconstruction_and_paths(protocol):
    empty={"id":"empty","devices":[],"interfaces":[],"contexts":[],"links":[]}
    r=query(batch(protocol),empty)
    assert r["simulation"]["path_count"]=="3"
    assert all(n["instance_id"] is None for n in r["nodes"])
    assert all(not h["interface_id"] for h in r["simulation"]["hops"])
    assert r["config_simulation"] is None


def test_observed_cost_is_authoritative_and_config_only_edges_stay_out_of_spf():
    b=batch();b.records[0].links[0].cost=40
    r=query(b)
    assert r["simulation"]["path_count"]=="2"
    assert r["config_simulation"]["path_count"]=="3"
    assert any(c["status"]=="cost_difference" and c["observed_cost"]==40 and c["config_cost"]==10 for c in r["comparisons"])
    a,bpeer=b.records[0],b.records[1]
    a.links=[l for l in a.links if l.target!=bpeer.origin]
    bpeer.links=[l for l in bpeer.links if l.target!=a.origin]
    r=query(b)
    assert r["simulation"]["path_count"]=="2"
    assert len([c for c in r["comparisons"] if c["status"]=="config_only"])==2


def test_missing_configuration_does_not_remove_observed_device():
    s=lab()[0];s["routing"]={}
    r=query(batch(),s)
    assert len(r["nodes"])==6 and r["simulation"]["path_count"]=="3"


@pytest.mark.parametrize("transform,code",[(lambda b:setattr(b.records[0],"detailed",False),"summary_only"),
    (lambda b:b.records.pop(1),"missing_record"),(lambda b:setattr(b.records[0],"expired",True),"missing_record"),
    (lambda b:b.records[1].links.pop(0),"one_way_adjacency")])
def test_incomplete_views_never_silently_compute_a_cheaper_alternative(transform,code):
    b=batch();transform(b);r=query(b)
    assert r["simulation"]["status"]=="incomplete"
    assert any(d["code"]==code for d in r["diagnostics"])


def test_summary_inventory_and_truncated_and_unconfirmed_captures():
    text="OSPF Process 1 with Router ID 1.1.1.1\nArea: 0.0.0.0\nRouter 1.1.1.1 1.1.1.1 100 80000001 48\n"
    b=batch(text=text);r=build(b,lab()[0],AnalyzeRequest(version=1))
    assert len(b.records)==1 and any(d["code"]=="summary_only" for d in r["diagnostics"])
    assert query(batch(declared_complete=False))["simulation"]["status"]=="incomplete"
    assert any(d.code=="truncated_capture" for d in batch(text=(SAMPLES/"ospf.txt").read_text(encoding="utf-8")+"\n---- More ----").diagnostics)


def test_record_recency_purge_and_conflicting_versions():
    b=batch();old=deepcopy(b.records[0]);old.sequence-=1;old.links[0].cost=200;b.records.insert(0,old)
    assert query(b)["simulation"]["path_count"]=="3"
    d=[];records=select_records(b,d);assert len(records)==6
    bad=deepcopy(b.records[1]);bad.links[0].cost=999;b.records.append(bad)
    assert query(b)["simulation"]["status"]=="incomplete"
    assert any(d["code"]=="version_conflict" for d in query(b)["diagnostics"])
    b=batch("isis");purge=deepcopy(b.records[1]);purge.sequence+=1;purge.age=0;b.records.append(purge)
    assert any(d["code"]=="expired_record" for d in query(b)["diagnostics"])


def test_isis_fragments_overload_and_dual_encoding():
    b=batch("isis")
    duplicate=deepcopy(b.records[0].links[0]);duplicate.encoding="narrow";b.records[0].links.append(duplicate)
    assert query(b)["simulation"]["path_count"]=="3"
    b.records[1].overload=True
    assert query(b)["simulation"]["path_count"]=="2"
    frag=deepcopy(b.records[-1]);frag.key=frag.key[:-2]+"02";frag.fragment=2;frag.links=[];frag.prefixes=[];b.records.append(frag)
    assert query(b)["simulation"]["status"]=="incomplete"
    assert any(d["code"]=="missing_fragment" for d in query(b)["diagnostics"])


def test_isis_unidentified_parallel_links_are_not_counted_twice():
    b=batch("isis");b.records[0].links.append(deepcopy(b.records[0].links[0]))
    r=query(b)
    assert r["simulation"]["status"]=="incomplete"
    assert any(d["code"]=="parallel_identity_unknown" for d in r["diagnostics"])


def test_ospf_numbered_parallel_links_remain_distinct():
    b=batch();link=deepcopy(b.records[0].links[0]);link.local_ip="10.99.0.1";b.records[0].links.append(link)
    reverse=deepcopy(b.records[1].links[0]);reverse.local_ip="10.99.0.2";b.records[1].links.append(reverse)
    r=query(b,mode="observed")
    assert r["simulation"]["path_count"]=="4"
    assert sorted(round(p["share_percent"],2) for p in r["simulation"]["paths"])==[16.67,16.67,33.33,33.33]


def test_pseudonode_is_not_an_extra_ecmp_next_hop():
    # A broadcast link and a P2P link reach the same router at equal cost.
    from backend.app.lsdb.model import Record,LinkStateLink
    b=batch();a,c=b.records[0],b.records[1]
    a.links.append(LinkStateLink(target="net:10.90.0.1",cost=10,kind="transit",local_ip="10.90.0.1"))
    c.links.append(LinkStateLink(target="net:10.90.0.1",cost=10,kind="transit",local_ip="10.90.0.2"))
    b.records.append(Record(key="network:10.90.0.1:10.255.0.1",kind="network",origin="net:10.90.0.1",sequence=0x80000001,checksum=1,age=1,
        links=[LinkStateLink(target=x,cost=0,kind="member") for x in (a.origin,c.origin)]))
    r=query(b,mode="observed")
    assert r["simulation"]["path_count"]=="4"
    assert len(r["nodes"])==7
    assert all(len(p["device_ids"])<=4 for p in r["simulation"]["paths"])
    assert all(not n.startswith("net:") for p in r["simulation"]["paths"] for n in p["device_ids"])
    assert len(r["simulation"]["forwarding"])==5


def test_isis_pseudonode_and_zero_metric_cycle_guard():
    from backend.app.lsdb.model import Record,LinkStateLink
    b=batch("isis");a,c=b.records[0],b.records[1];pseudo="0000.0000.0001.01"
    a.links[0].target=pseudo;c.links[0].target=pseudo
    b.records.append(Record(key=pseudo+"-00",kind="lsp",origin=pseudo,sequence=1,checksum=1,age=100,
        links=[LinkStateLink(target=x,cost=0,encoding="wide") for x in (a.origin,c.origin)]))
    assert query(b)["simulation"]["path_count"]=="3"
    arcs=[dict(id=a+b,source=a,target=b,cost=c,source_device_id=a,target_device_id=b) for a,b,c in [("s","a",0),("a","s",0),("a","d",1),("s","d",1)]]
    assert calculate(arcs,"s","d")["status"]=="unsupported"


def test_duplicate_identity_stays_unmapped_until_explicit_binding():
    b=batch();s=lab()[0];i=s["routing"]["instances"][0];dup=deepcopy(i);dup["id"]="other";s["routing"]["instances"].append(dup)
    r=query(b,s)
    n=next(n for n in r["nodes"] if n["protocol_id"]==i["router_id"])
    assert n["mapping_status"]=="ambiguous" and r["simulation"]["status"]=="ok"
    r=query(b,s,mappings={i["router_id"]:i["id"]})
    assert next(n for n in r["nodes"] if n["protocol_id"]==i["router_id"])["instance_id"]==i["id"]


def test_path_listing_truncation_keeps_all_traffic_and_count():
    full=query(batch());limited=query(batch(),path_limit=1)
    assert limited["simulation"]["paths_truncated"] and limited["simulation"]["path_count"]=="3"
    assert full["simulation"]["hops"]==limited["simulation"]["hops"]


def test_scope_process_isolation_and_secret_not_copied_into_facts():
    b=batch("isis",text=(SAMPLES/"isis.txt").read_text(encoding="utf-8").replace(" NLPID IPV4", " Auth: SECRET123 Len: 9 Type: Plain Text\n NLPID IPV4"))
    assert "SECRET123" not in b.model_dump_json()
    wrong=batch(process_id="2")
    assert not wrong.records and wrong.diagnostics
    b=batch("isis");r=query(b,mode="observed")
    assert all(n["instance_id"] is None for n in r["nodes"])


def test_lsdb_api_isolation_versions_and_export_restore(tmp_path):
    with TestClient(create_app(tmp_path,worker_mode="inline")) as client:
        p=client.post("/api/projects",json={"name":"LSDB-only"}).json();pid=p["id"]
        assert client.get(f"/api/projects/{pid}/lsdb").json()["batches"]==[]
        payload=dict(expected_version=1,name="view A",protocol="ospf",scope="0",declared_complete="true",collected_at="2026-10-28T01:00:00Z")
        r=client.post(f"/api/projects/{pid}/lsdb",data=payload,files=[("files",("ospf.txt",(SAMPLES/"ospf.txt").read_bytes()))]);assert r.status_code==201,r.text
        first=r.json()["batch"];assert first["record_count"]==6
        assert client.get(f"/api/projects/{pid}").json()["snapshot"] is None
        result=client.post(f"/api/projects/{pid}/lsdb/{first['id']}/analyze",json={"version":2}).json()
        assert len(result["topology"]["nodes"])==6
        assert client.post(f"/api/projects/{pid}/lsdb/{first['id']}/analyze",json={"version":1}).status_code==409
        payload.update(expected_version=2,name="view B")
        second=client.post(f"/api/projects/{pid}/lsdb",data=payload,files=[("files",("ospf.txt",(SAMPLES/"ospf.txt").read_bytes()))]).json()
        assert second["batch"]["id"]!=first["id"]
        assert len(client.get(f"/api/projects/{pid}/lsdb").json()["batches"])==2
        other=client.post("/api/projects",json={"name":"other"}).json()["id"]
        assert client.get(f"/api/projects/{other}/lsdb/{first['id']}").status_code==404
        source_id=first["source_files"][0]["id"]
        assert "OSPF Process" in client.get(f"/api/projects/{pid}/sources/{source_id}").json()["text"]
        for include in ("false","true"):
            bundle=client.get(f"/api/projects/{pid}/exports/json?include_sources={include}").content
            restored=client.post("/api/projects/restore",files={"file":("backup.json",bundle)});assert restored.status_code==201,restored.text
            rid=restored.json()["id"]
            batches=client.get(f"/api/projects/{rid}/lsdb").json()["batches"]
            assert len(batches)==2 and batches[0]["id"]!=first["id"]
            analysis=client.post(f"/api/projects/{rid}/lsdb/{batches[0]['id']}/analyze",json={"version":1})
            assert analysis.status_code==200 and len(analysis.json()["nodes"])==6
            assert client.get(f"/api/projects/{rid}/sources/{source_id}").status_code==(200 if include=="true" else 404)


def test_huawei_network_lsa_without_colon_and_stub_not_a_neighbor():
    text='''OSPF Process 1 with Router ID 1.1.1.1
Area: 0.0.0.0
Type : Router
Ls id : 1.1.1.1
Adv rtr : 1.1.1.1
Ls age : 10
seq# : 80000001
chksum : 0x1234
Link count: 2
Link ID: 10.0.0.1
Data : 10.0.0.1
Link Type: TransNet
Metric : 10
Link ID: 1.1.1.1
Data : 255.255.255.255
Link Type: StubNet
Metric : 0
Type : Network
Ls id : 10.0.0.1
Adv rtr : 1.1.1.1
Ls age : 10
seq# : 80000001
chksum : 0x2345
Net mask : 255.255.255.0
Attached Router    1.1.1.1
'''
    b=batch(text=text)
    assert not b.diagnostics and len(b.records)==2
    assert b.records[0].links[0].target=="net:10.0.0.1"
    assert len(b.records[0].links)==1 and b.records[0].prefixes[0].prefix=="1.1.1.1/32"
    assert b.records[1].links[0].target=="1.1.1.1" and b.records[1].links[0].cost==0
    broken=batch(text=text.replace("Link count: 2","Link count: 3"))
    assert any(d.code=="malformed_lsa" for d in broken.diagnostics)


def test_config_only_device_is_visible_but_not_used_for_observed_paths():
    b=batch();b.records=[r for r in b.records if r.origin!="10.255.0.2"]
    for r in b.records: r.links=[l for l in r.links if l.target!="10.255.0.2"]
    r=query(b)
    n=next(n for n in r["nodes"] if n["label"]=="IGP-A")
    assert n["observed"] is False and n["mapping_status"]=="config_only"
    assert r["simulation"]["path_count"]=="2"
    assert len([c for c in r["comparisons"] if c["status"]=="config_only"])==4
    observed=query(b,mode="observed")
    assert len(observed["nodes"])==5


def test_identifiers_are_scoped_and_ospf_octet_boundaries_are_not_removed():
    s=lab()[0];b=batch()
    source=next(i for i in s["routing"]["instances"] if i["router_id"]=="10.255.0.1")
    source["router_id"]="102.55.0.1"
    r=query(b,s)
    assert next(n for n in r["nodes"] if n["protocol_id"]=="10.255.0.1")["instance_id"] is None
    b.context_name="different-vrf"
    assert all(n["instance_id"] is None for n in query(b,s)["nodes"])


def test_isis_internal_external_and_link_identifiers():
    text=(SAMPLES/"isis.txt").read_text(encoding="utf-8").replace(' +NBR ID 0000.0000.0002.00 COST: 10',
        ' +NBR ID 0000.0000.0002.00 COST: 10\n Link Local Identifier : 22\n Link Remote Identifier : 33\n Local IP Address : 10.80.1.1\n Remote IP Address : 10.80.1.2')
    text=text.replace(' +IP-Extended 10.255.0.6 255.255.255.255 COST: 0',
        ' IP-External 10.255.0.6 255.255.255.255 COST: 0\n +IP-Extended 10.255.0.6 255.255.255.255 COST: 0')
    b=batch('isis',text=text)
    assert b.records[0].links[0].local_id=='22' and b.records[0].links[0].remote_ip=='10.80.1.2'
    assert not b.records[-1].prefixes
    assert any(d.code=='external_prefix_ignored' for d in b.diagnostics)


def test_mapping_persistence_and_config_reimport_does_not_replace_lsdb(tmp_path):
    with TestClient(create_app(tmp_path,worker_mode='inline')) as c:
        p=c.post('/api/projects',json={'name':'fusion'}).json();pid=p['id']
        assert c.post(f'/api/projects/{pid}/demo?kind=huawei-igp').status_code==202
        p=c.get(f'/api/projects/{pid}').json()
        r=c.post(f'/api/projects/{pid}/lsdb/demo',json={'version':p['version']});assert r.status_code==201,r.text
        bid=r.json()['batch']['id'];version=r.json()['project_version']
        inst=next(i for i in p['snapshot']['routing']['instances'] if i['protocol']=='ospf' and i['router_id']=='10.255.0.1')
        mappings={'10.255.0.1':inst['id']}
        m=c.put(f'/api/projects/{pid}/lsdb/{bid}/mappings',json={'version':version,'mappings':mappings});assert m.status_code==200,m.text
        version=m.json()['project_version']
        assert c.post(f'/api/projects/{pid}/lsdb/{bid}/analyze',json={'version':version}).json()['mappings']==mappings
        bundle=c.get(f'/api/projects/{pid}/exports/json?include_sources=true').content
        restored=c.post('/api/projects/restore',files={'file':('project.json',bundle)});assert restored.status_code==201,restored.text
        rid=restored.json()['id'];rbid=c.get(f'/api/projects/{rid}/lsdb').json()['batches'][0]['id']
        assert c.post(f'/api/projects/{rid}/lsdb/{rbid}/analyze',json={'version':1}).json()['mappings']==mappings
        before=c.get(f'/api/projects/{pid}/lsdb/{bid}').json()
        c.post(f'/api/projects/{pid}/demo?kind=huawei-igp')
        assert c.get(f'/api/projects/{pid}/lsdb/{bid}').json()==before


def test_isis_max_metric_is_not_used_but_reverse_still_has_backlink():
    b=batch('isis');b.records[0].links[0].cost=16777215
    r=query(b)
    assert r['simulation']['status']=='ok' and r['simulation']['path_count']=='2'
    assert not any(d['code']=='one_way_adjacency' for d in r['diagnostics'])


def test_configuration_comparison_failure_cannot_break_observed_spf():
    s=lab()[0];src=next(i for i in s['routing']['instances'] if i['router_id']=='10.255.0.1')
    bids={b['id'] for b in s['routing']['bindings'] if b['instance_id']==src['id']}
    s['routing']['bindings']=[b for b in s['routing']['bindings'] if b['id'] not in bids]
    s['routing']['advertisements']=[a for a in s['routing']['advertisements'] if a['binding_id'] not in bids]
    r=query(batch(),s)
    assert r['simulation']['status']=='ok' and r['config_simulation']['status']=='incomplete'


def test_stale_saved_mapping_falls_back_without_mutating_history(tmp_path):
    from backend.app.store import Project
    with TestClient(create_app(tmp_path,worker_mode='inline')) as c:
        p=c.post('/api/projects',json={'name':'stale'}).json();pid=p['id']
        r=c.post(f'/api/projects/{pid}/lsdb/demo',json={'version':1}).json();bid=r['batch']['id']
        with c.app.state.store.session(write=True) as s:
            stored=s.get(Project,pid);stored.settings={**stored.settings,'lsdb_mappings':{bid:{'10.255.0.1':'removed-instance'}}}
        response=c.post(f'/api/projects/{pid}/lsdb/{bid}/analyze',json={'version':2})
        assert response.status_code==200,response.text
        assert response.json()['mappings']=={} and any(d['code']=='stale_mapping' for d in response.json()['diagnostics'])
        assert c.get(f'/api/projects/{pid}/exports/json').json()['project']['settings']['lsdb_mappings'][bid]=={'10.255.0.1':'removed-instance'}


def test_multi_collector_and_lsp_count_mismatch_diagnostics():
    text=(SAMPLES/'ospf.txt').read_text(encoding="utf-8")
    b=batch(text=text+'\n'+text.replace('with Router ID 10.255.0.1','with Router ID 10.255.0.2'))
    assert any(d.code=='mixed_collectors' for d in b.diagnostics)
    b=batch('isis',text=(SAMPLES/'isis.txt').read_text(encoding="utf-8").replace('Total LSP(s): 6','Total LSP(s): 7'))
    assert any(d.code=='lsp_count_mismatch' for d in b.diagnostics)
