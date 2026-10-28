"""Default-derived facts must not imply unsupported platform/FIB behavior."""
from backend.app.parser import parse_config
from backend.app.routing.spf import calculate
from backend.app.routing.simulation import simulate
from backend.tests.test_routing import lab


def test_unknown_nxos_implicit_isis_metric_is_not_invented():
    text='''feature isis
hostname N
router isis CORE
 net 49.0001.0000.0000.0001.00
!
interface Ethernet1/1
 ip address 10.0.0.1/30
 ip router isis CORE
!
interface Loopback0
 ip address 10.255.0.1/32
 ip router isis CORE
!
'''
    facts=parse_config(text,'nx','nx','nx','cisco_nxos')['routing']
    assert facts['bindings'] and all(b['cost'] is None and b['cost_origin']=='unknown' for b in facts['bindings'])
    assert facts['instances'][0]['defaults_profile']=='cisco_nxos_igp/defaults-1'


def test_neutral_spf_enforces_path_metric_bound_and_terminal_prefix_cost():
    arcs=[dict(id=str(i),source=str(i),target=str(i+1),source_device_id=str(i),target_device_id=str(i+1),cost=63) for i in range(16)]
    nodes={'0':{'metric_path_limit':1023}}
    assert calculate(arcs,'0','16',terminal_cost=15,nodes=nodes)['total_cost']==1023
    blocked=calculate(arcs,'0','16',terminal_cost=16,nodes=nodes)
    assert blocked['status']=='unreachable' and any(d['code']=='metric_path_limit' for d in blocked['diagnostics'])
    assert calculate(arcs,'0','16',terminal_cost=16)['total_cost']==1024


def test_config_engine_passes_neutral_metric_path_bound_to_spf():
    snapshot,request=lab('isis','2')
    source=next(i for i in snapshot['routing']['instances'] if i['id']==request.source_instance_id)
    source['metric_path_limit']=29
    assert simulate(snapshot,request)['status']=='unreachable'
    source['metric_path_limit']=30
    assert simulate(snapshot,request)['total_cost']==30


def test_protocol_ecmp_candidates_are_preserved_when_default_fib_has_one_hop():
    arcs=[dict(id=key,source=s,target=t,cost=cost,source_device_id=s,target_device_id=t) for key,s,t,cost in [('sa','S','A',1),('sb','S','B',1),('ad','A','D',1),('bd','B','D',1)]]
    result=calculate(arcs,'S','D',nodes={'S':{'forwarding_mode':'single_next_hop','max_paths':1}})
    assert result['path_count']=='2' and result['forwarding_status']=='limit_ambiguous'
    assert [p['share_percent'] for p in result['paths']]==[50,50]
    assert result['forwarding'][0]['forwarding_mode']=='single_next_hop'


def test_unresolved_narrow_wide_receive_policy_does_not_select_an_alternative():
    snapshot,request=lab('isis','2')
    source=next(i for i in snapshot['routing']['instances'] if i['id']==request.source_instance_id)
    source['metric_style']='narrow'
    result=simulate(snapshot,request)
    assert result['status']=='unsupported' and not result['paths']
    assert any(d['code']=='metric_encoding_unknown' for d in result['diagnostics'])


def test_dual_encoding_does_not_invent_the_narrow_value_of_large_wide_metric():
    snapshot,request=lab('isis','2')
    for instance in snapshot['routing']['instances']:
        if instance['protocol']=='isis':instance['metric_style']='both'
    source=next(i for i in snapshot['routing']['instances'] if i['id']==request.source_instance_id)
    source['metric_style']='narrow'
    binding=next(b for b in snapshot['routing']['bindings'] if b['device_id']=='03-B' and b['scope']=='2' and not b['passive'])
    binding['cost']=100
    result=simulate(snapshot,request)
    assert result['status']=='unsupported' and not result['paths']
