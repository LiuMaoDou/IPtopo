"""SR OS Classic and MD-CLI routing configuration adapters."""
import re
from .builder import Builder,area,name
from .statements import nokia


def extract(text,parsed,dialect):
    b=Builder(parsed,'nokia_sros_igp');records,off,truncated=nokia(text,dialect)
    router_ids={};ecmp={};isis_router_ids={};initialized=set();ipv4_enabled={};protocol_caps={}
    def scope(t):
        if t[:1]==['router']:
            if len(t)>1 and t[1] not in ('interface','router-id','ospf','isis','ecmp','no','shutdown'):return ('default' if t[1]=='Base' else t[1]),t[2:]
            return 'default',t[1:]
        if t[:2]==['service','vprn'] and len(t)>2:return 'VPRN:'+t[2],t[3:]
        return None,[]
    for t,n,inactive in records:
        ctx,s=scope(t)
        if not ctx:continue
        if s[:1]==['router-id'] and len(s)>1:router_ids[ctx]=s[1]
        if s[:1]==['ecmp'] and len(s)>1:
            value=int(s[1])
            if not 1<=value<=128:raise ValueError('invalid SR OS ECMP limit')
            ecmp[ctx]=value
        elif s[:2]==['no','ecmp']:ecmp[ctx]=1
    for t,n,inactive in records:
        ctx,s=scope(t)
        if not ctx or not s or s[0] not in ('ospf','isis'):continue
        proto=s[0];s=s[1:];local='0'
        if s and s[0].isdigit():local=s.pop(0)
        p=b.process(proto,local,ctx,n,style='both' if proto=='isis' else 'standard')
        if id(p) not in initialized:
            # Classic OSPF is created enabled; IS-IS and MD-CLI protocol
            # instances require administrative enablement. Interface protocol
            # admin-state defaults to enable in both MD-CLI protocol models.
            p.enabled=proto=='ospf' and dialect=='nokia_classic'
            initialized.add(id(p))
        if s and re.fullmatch(r'\d+\.\d+\.\d+\.\d+',s[0]) and proto=='ospf':p.router_id=s.pop(0)
        if not s:continue
        if inactive:continue
        if s[0]=='admin-state' and len(s)>1:p.enabled=s[1]!='disable';continue
        if s[0]=='shutdown':p.enabled=False;continue
        if s[:2]==['no','shutdown']:p.enabled=True;continue
        if s[:2]==['no','router-id']:
            if proto=='isis':isis_router_ids.pop(id(p),None)
            else:p.router_id=None
            continue
        if s[:2]==['no','system-id'] and proto=='isis':p.router_id=None;continue
        if s[0]=='router-id' and len(s)>1:
            if proto=='isis':isis_router_ids[id(p)]=s[1]
            else:p.router_id=s[1]
            continue
        if s[0]=='system-id' and len(s)>1:p.router_id=s[1].replace('.','').lower();continue
        if s[0] in ('area-id','area-address') and len(s)>1:
            for value in s[1:]:
                a=value.strip('[]').replace('.','').lower()
                if a and a not in p.areas:p.areas.append(a)
            continue
        if s[0]=='level-capability' and len(s)>1:p.levels=['1','2'] if s[1] in ('level-1/2','level-1-2','1/2') else ['1' if s[1] in ('level-1','1') else '2'];continue
        if s[0]=='level' and len(s)>2 and s[1] in ('1','2'):
            if s[2:] in (['wide-metrics-only'],['wide-metrics-only','true']):p.level_metric_styles[s[1]]='wide';continue
            if s[2:] in (['no','wide-metrics-only'],['wide-metrics-only','false']):p.level_metric_styles[s[1]]='both';continue
            b.issue(p,'unsupported_protocol_statement','Nokia IS-IS Level 语义尚未适配。',n);continue
        if s[0]=='level' and len(s)==2:continue
        if s[0] in ('ecmp','max-ecmp-routes') and len(s)>1:
            value=int(s[1])
            if not 1<=value<=128:raise ValueError('invalid SR OS protocol ECMP limit')
            protocol_caps[id(p)]=value
            continue
        if s[:2]==['no','max-ecmp-routes']:protocol_caps.pop(id(p),None);continue
        scope_id=None
        if proto=='ospf':
            if s[0]!='area':
                if s[0] not in ('reference-bandwidth',):b.issue(p,'unsupported_protocol_statement','Nokia OSPF 高级语义尚未适配。',n)
                continue
            if len(s)<2:continue
            try:scope_id=area(s[1])
            except ValueError:b.issue(p,'invalid_area','OSPF Area 无效。',n);continue
            s=s[2:]
            if not s:continue
        if s[0]!='interface':
            if s[:2]==['no','ipv4-routing'] or s[:2]==['ipv4-routing','false']:ipv4_enabled[id(p)]=False
            elif s[0]=='ipv4-routing':ipv4_enabled[id(p)]=True
            elif s[0] not in ('ipv4-routing','ipv6-routing'):b.issue(p,'unsupported_protocol_statement','Nokia 非普通接口协议语义尚未适配。',n)
            continue
        if len(s)<2:continue
        port=b.port(p,s[1],n)
        if scope_id:port['scope']=scope_id
        rest=s[2:]
        if not rest:continue
        if rest[0]=='level' and len(rest)==2:continue
        if rest[0]=='level' and len(rest)>2:
            level=rest[1];rest=rest[2:]
        else:level=None
        if rest[0]=='metric' and len(rest)>1:
            # The circuitless system cost may be zero by default, but SR OS
            # does not permit an explicitly configured OSPF/IS-IS metric zero.
            if int(rest[1])<=0:b.issue(p,'invalid_cost','SR OS 显式协议接口开销必须为正整数；system 的默认 0 不需要配置。',n,name(s[1]))
            else:b.cost(p,port,rest[1],n,level)
        elif rest[:2]==['no','metric']:port['costs'].pop(level or 'all',None)
        elif rest[0]=='interface-type' and len(rest)>1:port['network_type']='p2p' if rest[1] in ('point-to-point','p2p') else 'broadcast' if rest[1]=='broadcast' else 'unsupported'
        elif rest[0]=='passive':port['passive']=len(rest)<2 or rest[1] not in ('false','disable')
        elif rest[0]=='shutdown':port['disabled']=True
        elif rest[:2]==['no','shutdown']:port['disabled']=False
        elif rest[:2]==['no','passive']:port['passive']=False
        elif rest[:2]==['no','interface-type']:port.pop('network_type',None)
        elif rest[:2]==['no','level-capability']:port['levels']=None
        elif rest[:2]==['no','hello-interval']:port.pop('hello',None)
        elif rest[:2]==['no','dead-interval']:port.pop('dead',None)
        elif rest[0]=='admin-state' and len(rest)>1:port['disabled']=rest[1]=='disable'
        elif rest[0]=='level-capability' and len(rest)>1:port['levels']=['1','2'] if rest[1] in ('level-1/2','1/2') else ['1' if rest[1] in ('level-1','1') else '2']
        elif rest[0] in ('hello-interval','dead-interval') and len(rest)>1:port['hello' if rest[0]=='hello-interval' else 'dead']=int(rest[1])
        elif rest[0] in ('priority','retransmit-interval','transit-delay'):pass
        else:b.issue(p,'unsupported_protocol_statement','Nokia 接口协议语义尚未适配。',n,name(s[1]))
    for p in b.processes.values():
        if ipv4_enabled.get(id(p)) is False:p.enabled=False
        if p.enabled and p.protocol=='ospf':
            for ikey,port in p.ports.items():
                if port.get('dead',40)<2*port.get('hello',10):b.issue(p,'invalid_ospf_timers','SR OS OSPF Dead Interval 至少为 Hello Interval 的两倍。',port['line'],ikey)
        # Protocol-level caps constrain rather than replace the router RTM cap.
        # Router ECMP defaults to one (Classic "no ecmp", MD-CLI ecmp 1).
        router_cap=ecmp.get(p.context,1)
        protocol_cap=protocol_caps.get(id(p))
        p.max_paths=min(router_cap,protocol_cap) if protocol_cap is not None else router_cap if router_cap<=64 else None
        p.forwarding_mode='single_next_hop' if p.max_paths==1 else 'ecmp'
        system=next((i for i in parsed['interfaces'] if i['routing_context_id']==b.contexts.get(p.context) and name(i['raw_name'])=='system'),None)
        system_address=next((a['address'] for a in system.get('addresses',[]) if a.get('family')==4 and a.get('address') and a.get('role')=='primary'),None) if system else None
        if p.protocol=='ospf' and not p.router_id:p.router_id=router_ids.get(p.context) or system_address
        if p.protocol=='isis' and not p.router_id:
            # SR OS derives the default six-byte System ID from the dotted
            # decimal system address: 192.0.2.1 -> 1920.0000.2001.
            address=isis_router_ids.get(id(p)) or router_ids.get(p.context) or system_address
            if address:p.router_id=''.join(f'{int(v):03d}' for v in address.split('.'))
        if truncated:b.issue(p,'incomplete_routing_configuration','协议配置结构不完整。',1)
    return b.finish()
