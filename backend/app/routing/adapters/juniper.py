"""Junos set/hierarchical OSPF and IS-IS -> neutral routing facts."""
import re
from .builder import Builder,area,name
from .statements import junos


def forwarding_profile(records):
    """Resolve only an unconditional, single forwarding-table export policy.

    An IGP equal-cost candidate set does not establish a Junos forwarding-table
    load-balancing policy. Complex policy evaluation stays outside this reader.
    ``records`` contains effective paths scoped to the routing instance.
    """
    exports=[]
    for t,_ in records:
        if t[:1]==['apply-groups'] or t[:1]==['routing-options'] and 'apply-groups' in t:
            return 'unknown',None,[]
        if t[:3]==['routing-options','forwarding-table','export']:
            exports.append(t[3:])
    if not exports:return 'single_next_hop',1,[]
    names=set()
    for export in exports:
        names.update(' '.join(export).replace('[',' ').replace(']',' ').split())
    if len(names)!=1:return 'unknown',None,[]
    policy=next(iter(names));children=[];lines=[]
    for t,n in records:
        if t[:3]==['policy-options','policy-statement',policy]:
            children.append(t[3:]);lines.append(n)
    if not children:return 'unknown',None,[]
    terms={t[1] for t in children if t[:1]==['term'] and len(t)>1}
    if len(terms)>1:return 'unknown',None,lines
    modes=set()
    for child in children:
        if child[:1]==['term']:child=child[2:]
        if not child or child==['then']:continue
        if child[:2]==['then','load-balance'] and len(child)==3 and child[2] in ('per-flow','per-packet'):
            modes.add(child[2])
        elif child==['then','accept']:continue
        else:return 'unknown',None,lines
    return ('ecmp',None,lines) if modes else ('unknown',None,lines)


def extract(text,parsed,dialect):
    b=Builder(parsed,'junos_igp');records,inactive,truncated=junos(text,dialect);router_ids={};nets=[];iso_interfaces=set();effective=[]
    def scoped(tokens):
        if tokens[:1]==['routing-instances'] and len(tokens)>2:return tokens[1],tokens[2:]
        return 'default',tokens
    def off(tokens,disabled):return disabled or any(tokens[:len(path)]==path for path in inactive)
    for tokens,n,disabled in records:
        context,t=scoped(tokens)
        if off(tokens,disabled):continue
        effective.append((context,t,n))
        if t[:2]==['routing-options','router-id'] and len(t)>2:router_ids[context]=t[2]
        if len(t)>=6 and t[0]=='interfaces' and t[2]=='unit' and t[4:6]==['family','iso']:
            iso_interfaces.add(name(t[1]+'.'+t[3]))
            if len(t)>=8 and t[6]=='address':nets.append((t[1]+'.'+t[3],t[7],n))
    # Process settings before interface settings so global defaults are available.
    for tokens,n,disabled in records:
        context,t=scoped(tokens)
        if len(t)<3 or t[0]!='protocols' or t[1] not in ('ospf','isis') or off(tokens,disabled):continue
        proto=t[1];p=b.process(proto,'default',context,n,style='both' if proto=='isis' else 'standard')
        if off(tokens,disabled):continue
        s=t[2:]
        if s==['disable']:p.enabled=False
        elif s[0]=='interface' or s[0]=='area':continue
        elif s[0]=='level' and len(s)==2:continue
        elif s[0]=='level' and len(s)>=3 and s[1] in ('1','2'):
            if s[2]=='disable':p.levels=[v for v in p.levels if v!=s[1]]
            elif s[2]=='wide-metrics-only':p.level_metric_styles[s[1]]='wide'
            else:b.issue(p,'unsupported_protocol_statement','Junos IS-IS Level 语义尚未适配。',n)
        elif s[0]=='export' or s[0] in ('import','overload','traffic-engineering','spf-options','lsp-lifetime'):b.issue(p,'unsupported_protocol_statement','Junos 策略或高级协议语义尚未适配。',n)
        elif s[0]=='reference-bandwidth' and p.protocol=='isis':b.issue(p,'unsupported_protocol_statement','IS-IS 自动带宽开销需要额外带宽证据。',n)
        elif s[0] in ('traceoptions','reference-bandwidth'):pass
        else:b.issue(p,'unsupported_protocol_statement','Junos 协议语义尚未适配。',n)
    for tokens,n,disabled in records:
        context,t=scoped(tokens)
        if len(t)<3 or t[0]!='protocols' or t[1] not in ('ospf','isis') or off(tokens,disabled):continue
        p=b.process(t[1],'default',context,n,style='both' if t[1]=='isis' else 'standard');s=t[2:]
        if off(tokens,disabled):continue
        scope=None
        if p.protocol=='ospf':
            if s[:1]!=['area'] or len(s)<3:continue
            try:scope=area(s[1])
            except ValueError:b.issue(p,'invalid_area','OSPF Area 无效。',n);continue
            s=s[2:]
            if s[:1]!=['interface']:
                b.issue(p,'unsupported_area_statement','Junos Area 的非普通接口语义尚未适配。',n);continue
        if s[:1]!=['interface'] or len(s)<2:continue
        port=b.port(p,s[1],n)
        if scope:port['scope']=scope
        rest=s[2:]
        if not rest:continue
        if rest[:1]==['disable']:port['disabled']=True
        elif rest[:1]==['passive']:port['passive']=True
        elif rest[:1]==['interface-type'] and len(rest)>1:port['network_type']={'p2p':'p2p','broadcast':'broadcast','nbma':'unsupported','p2mp':'unsupported'}.get(rest[1],'unsupported')
        elif rest[:1]==['point-to-point']:port['network_type']='p2p'
        elif rest[:1]==['metric'] and len(rest)>1:b.cost(p,port,rest[1],n)
        elif rest[:1]==['level'] and len(rest)>=3:
            level=rest[1]
            if rest[2]=='disable':port['levels']=[v for v in (port['levels'] if port.get('levels') is not None else ['1','2']) if v!=level]
            elif rest[2]=='metric' and len(rest)>3:b.cost(p,port,rest[3],n,level)
            elif rest[2]=='passive':port['passive']=True
            else:b.issue(p,'unsupported_protocol_statement','Junos 接口 Level 语义尚未适配。',n,name(s[1]))
        elif rest[:1]==['level'] and len(rest)==2:continue
        elif rest[:1] in (['hello-interval'],['dead-interval']) and len(rest)>1:port['hello' if rest[0]=='hello-interval' else 'dead']=int(rest[1])
        elif rest[0] in ('priority','te-metric','no-advertise-adjacency-segment'):pass
        else:b.issue(p,'unsupported_protocol_statement','Junos 接口协议语义尚未适配。',n,name(s[1]))
    for p in b.processes.values():
        # Routing-instance forwarding-table export can reference a global
        # policy-options definition. Do not inherit the default instance's
        # forwarding-table export into a different routing instance.
        policy_records=[(t,n) for ctx,t,n in effective if ctx==p.context or ctx=='default' and t[:1]==['policy-options']]
        p.forwarding_mode,p.max_paths,policy_lines=forwarding_profile(policy_records)
        p.source_lines.extend(policy_lines)
        if p.protocol=='ospf':p.router_id=router_ids.get(p.context)
        else:
            for interface,net,n in nets:
                iface=b.interfaces.get(name(interface))
                if iface and iface['routing_context_id']==b.contexts.get(p.context):b.net(p,net,n)
            if 'all' in p.ports:
                all_port=p.ports.pop('all')
                for key,iface in b.interfaces.items():
                    if iface['routing_context_id']==b.contexts.get(p.context) and any(a.get('family')==4 for a in iface.get('addresses',[])):
                        p.ports.setdefault(key,{**all_port,'costs':dict(all_port['costs'])})
            for key,port in p.ports.items():
                if key not in iso_interfaces and not port.get('disabled'):
                    port['disabled']=True
                    b.issue(p,'missing_iso_family','Junos IS-IS 接口需要有效的 family iso；请提供含继承展开结果的完整配置。',port['line'],key)
        if truncated:b.issue(p,'incomplete_routing_configuration','协议配置包含未闭合结构。',1)
        # Unexpanded protocol groups can contain cost/area/disable statements.
        for tokens,n,disabled in records:
            context,t=scoped(tokens)
            if context==p.context and not off(tokens,disabled) and 'apply-groups' in t and (t[0]=='apply-groups' or t[:2]==['protocols',p.protocol]):b.issue(p,'unresolved_routing_inheritance','协议继承组需要展开后才能仿真。',n)
    return b.finish()
