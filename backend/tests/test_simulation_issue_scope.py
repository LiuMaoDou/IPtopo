"""An interface error blocks only a potentially relevant numbered adjacency."""
from copy import deepcopy

import pytest

from backend.app.routing.simulation import simulate
from backend.tests.test_ignored_routing_semantics import snapshot_with
from backend.tests.test_routing import lab, query


def test_unconnected_tunnel_issues_do_not_block_physical_paths():
    snapshot = snapshot_with('06-D-huawei.cfg', lambda text: text.replace('return', '''interface Tunnel1
 ip address 10.199.0.1 255.255.255.252
 isis enable 6
 isis cost INVALID
#
return'''))
    before = deepcopy(snapshot)
    result = simulate(snapshot,query(snapshot,'isis','2'))
    assert result['status'] == 'ok' and result['total_cost'] == 30
    tunnel = next(i for i in snapshot['interfaces'] if i['raw_name']=='Tunnel1')
    assert not any(d.get('interface_id')==tunnel['id'] for d in result['diagnostics'])
    assert snapshot == before


@pytest.mark.parametrize('code',['invalid_cost','unsupported_network_type'])
def test_error_on_a_candidate_transit_port_still_blocks_even_when_not_an_endpoint(code):
    snapshot,request = lab('isis','2')
    binding = next(b for b in snapshot['routing']['bindings'] if b['device_id']=='02-A' and b['scope']=='2' and not b['passive'])
    issue = {'code':code,'message':'Unknown candidate transit fact','device_id':binding['device_id'],
        'instance_id':binding['instance_id'],'interface_id':binding['interface_id'],'protocol':'isis','source_refs':[]}
    snapshot['routing']['issues'].extend([issue,deepcopy(issue)])
    result = simulate(snapshot,request)
    assert result['status'] == 'incomplete' and not result['paths']
    assert len([d for d in result['diagnostics'] if d['code']==code]) == 1


def test_disabled_interface_issue_is_not_replayed_as_an_active_path_blocker():
    snapshot,request = lab()
    binding = next(b for b in snapshot['routing']['bindings'] if b['device_id']=='01-S' and b['scope']==request.scope and not b['passive'])
    iface = next(i for i in snapshot['interfaces'] if i['id']==binding['interface_id'])
    iface['admin_state']='disabled'
    snapshot['routing']['issues'].append({'code':'invalid_cost','message':'Disabled port cost',
        'device_id':binding['device_id'],'instance_id':binding['instance_id'],'interface_id':iface['id'],'protocol':'ospf'})
    result = simulate(snapshot,request)
    assert result['status'] == 'ok'
    assert not any(d['message']=='Disabled port cost' for d in result['diagnostics'])
