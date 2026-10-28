"""Public vendor examples: keep compatibility findings reproducible offline."""
import json
import re

import pytest
from fastapi.testclient import TestClient

from backend.app.lsdb.huawei import parse as parse_lsdb
from backend.app.lsdb.analysis import build
from backend.app.lsdb.model import AnalyzeRequest
from backend.app.main import create_app
from scripts.validate_public_network import ROOT, verify, huawei_batch, config_facts

PUBLIC_CHECKS = verify()["checks"]


@pytest.mark.parametrize("check", PUBLIC_CHECKS, ids=lambda row: row["name"])
def test_public_vendor_corpus(check):
    assert check["passed"], json.dumps(check, ensure_ascii=False)


def test_huawei_starred_links_preserve_source_lines():
    # Format variation derived from the unchanged official two-link record.
    # Do not repair the inconsistent four-link official sample to make it pass.
    file = "huawei/lsdb/ospf-router.txt"
    original = (ROOT/file).read_text(encoding="utf-8")
    text = re.sub(r"(?m)^(\s*)Link ID:", r"\1* Link ID:", original)
    batch = parse_lsdb([dict(id=file,filename="starred.txt",text=text)],id="variant",name="variant",protocol="ospf",process_id="1",scope="0",imported_at="2026-10-28T00:00:00Z",declared_complete=True)
    assert len(batch.records)==1 and len(batch.records[0].prefixes)==2
    assert not batch.diagnostics
    for prefix in batch.records[0].prefixes:
        assert "* Link ID:" in text.splitlines()[prefix.source_refs[0].line-1]


def test_real_huawei_summary_sequence_is_not_the_length_column():
    batch = huawei_batch("huawei/lsdb/ospf-summary.txt","ospf","0")
    assert [(r.origin,r.age,r.sequence,r.detailed) for r in batch.records]==[
        ("192.168.1.1",98,0x8000000B,False),("1.1.1.1",92,0x80000005,False)]


def test_real_cisco_point_to_point_subinterface_and_case_are_preserved():
    result,_=config_facts("cisco/config/ospf-r2.cfg")
    interface=next(i for i in result['interfaces'] if i['raw_name']=='ATM1/0.20')
    assert interface['addresses'][0]['address']=='200.0.0.2'
    result,_=config_facts("cisco/config/isis-r2.cfg")
    assert {i['raw_name'] for i in result['interfaces']}=={'Loopback0','Ethernet0','Serial0'}


def test_nokia_published_nonbreaking_spaces_do_not_drop_addresses():
    text=(ROOT/'nokia/config/router-basic-classic.cfg').read_text(encoding="utf-8")
    assert '\xa0' in text
    result,transforms=config_facts('nokia/config/router-basic-classic.cfg')
    assert not transforms and len(result['interfaces'])==2
    assert all(i['addresses'][0]['address']=='192.0.2.1' for i in result['interfaces'])


def test_isis_source_field_is_optional_when_reachability_is_present():
    file='huawei/lsdb/isis-extended-attributes.txt'
    text='\n'.join(s for s in (ROOT/file).read_text(encoding="utf-8").splitlines() if not s.strip().startswith('SOURCE'))
    batch=parse_lsdb([dict(id=file,filename='no-source.txt',text=text)],id='no-source',name='no-source',protocol='isis',process_id='1',scope='2',imported_at='2026-10-28T00:00:00Z',declared_complete=False)
    assert len(batch.records)==1 and batch.records[0].detailed
    assert len(batch.records[0].links)==2
    assert {p.prefix for p in batch.records[0].prefixes}=={'10.1.1.0/24','1.1.1.1/32'}


def test_ipv6_prefix_flags_cannot_remove_a_previous_ipv4_prefix():
    file='huawei/lsdb/isis-extended-attributes.txt'
    text=(ROOT/file).read_text(encoding="utf-8")
    # Derived variation: leave 1.1.1.1 as the preceding IPv4 prefix and mark
    # the following IPv6 prefix external. This must not erase the IPv4 prefix.
    text,removed=re.subn(r'^\+IP-Extended\s+2\.2\.2\.2[^\n]*\n[^\n]*Extended Reach Attr[^\n]*\n','',text,flags=re.M)
    assert removed==1
    text,changed=re.subn(r'(IPV6\s+2001:DB8:1::1/128[^\n]*\n\s*Extended Reach Attr\s+Flag:) X:0 R:0',r'\1 X:1 R:1',text)
    assert changed==1
    batch=parse_lsdb([dict(id=file,filename='ipv6-attributes.txt',text=text)],id='v6',name='v6',protocol='isis',process_id='1',scope='2',imported_at='2026-10-28T00:00:00Z',declared_complete=False)
    assert '1.1.1.1/32' in {p.prefix for p in batch.records[0].prefixes}


def test_public_incomplete_capture_cannot_produce_spf_even_if_user_claims_complete():
    file='huawei/lsdb/isis-old-verbose.txt'
    batch=huawei_batch(file,'isis','1').model_copy(update={'declared_complete':True,'diagnostics':[]})
    empty=dict(id='empty',devices=[],interfaces=[],contexts=[],links=[])
    analysis=build(batch,empty,AnalyzeRequest(version=1))
    source=next(n['id'] for n in analysis['nodes'] if n['protocol_id']=='0000.0000.0003.00')
    destination=analysis['destinations'][0]['id']
    analysis=build(batch,empty,AnalyzeRequest(version=1,source=source,destination=destination))
    assert analysis['simulation']['status']=='incomplete'
    assert any(d['code']=='missing_record' for d in analysis['diagnostics'])


def test_public_lsdb_api_retains_original_and_refuses_partial_view_paths(tmp_path):
    with TestClient(create_app(tmp_path,worker_mode='inline')) as client:
        project=client.post('/api/projects',json={'name':'Public source validation'}).json()
        pid=project['id'];file='huawei/lsdb/isis-extended-attributes.txt'
        original=(ROOT/file).read_bytes()
        response=client.post(f'/api/projects/{pid}/lsdb',data=dict(expected_version=1,name='Huawei official excerpt',protocol='isis',scope='2',declared_complete='false'),files={'files':('official.txt',original)})
        assert response.status_code==201,response.text
        batch=response.json()['batch'];assert batch['record_count']==1
        source_id=batch['source_files'][0]['id']
        source=client.get(f'/api/projects/{pid}/sources/{source_id}').json()
        assert source['text'].encode()==original
        result=client.post(f'/api/projects/{pid}/lsdb/{batch["id"]}/analyze',json={'version':2}).json()
        assert len(result['nodes'])==2 and any(d['code']=='unconfirmed_capture' for d in result['diagnostics'])
