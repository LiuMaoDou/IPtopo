"""Cisco IOS/XE, IOS-XR and NX-OS native routing configuration readers."""
import ipaddress
import re
from .builder import Builder,area,name

SAFE_PROCESS=re.compile(r'^(?:log-adjacency-changes|log adjacency changes|nsf|nsr|graceful-restart|passive-interface default|!|description\b)')


def extract(text,parsed,dialect):
    b=Builder(parsed,dialect+'_igp');process_blocks=[];interface_blocks=[];current=None
    for n,raw in enumerate(text.splitlines(),1):
        s=raw.strip()
        if not s or s.startswith('!') and dialect!='cisco_xr':continue
        h=re.match(r'router\s+(ospf|isis)\s*(\S*)',s,re.I)
        i=re.fullmatch(r'interface\s+(\S+)(?:\s+(point-to-point|multipoint))?',s,re.I)
        if h and not raw[:1].isspace():
            current={'protocol':h[1].lower(),'name':h[2] or 'default','line':n,'rows':[],'vrf':(re.search(r'\bvrf\s+(\S+)',s) or [None,'default'])[1]};process_blocks.append(current)
        elif i and not raw[:1].isspace():
            current={'interface':i[1],'line':n,'rows':[],'network_type':('p2p' if i[2]=='point-to-point' else 'unsupported') if i[2] else None};interface_blocks.append(current)
        elif s in ('end','exit') or s=='!' and dialect!='cisco_xr':current=None
        elif current and raw[:1].isspace():current['rows'].append((n,s,len(raw)-len(raw.lstrip())))
        elif raw and not raw[:1].isspace():current=None
    global_interface={}
    for block in interface_blocks:
        iface=b.interfaces.get(name(block['interface']))
        if not iface:continue
        context=next((c['local_name'] for c in parsed['contexts'] if c['id']==iface['routing_context_id']),'default')
        cfg={'context':context,'line':block['line'],'ospf':None,'isis':None,'ospf_options':[],'isis_options':[]}
        if block['network_type']:
            for proto in ('ospf','isis'):cfg[proto+'_options'].append((block['line'],'network '+('point-to-point' if block['network_type']=='p2p' else 'multipoint')))
        for n,s,_ in block['rows']:
            if m:=re.fullmatch(r'ip ospf\s+(\S+)\s+area\s+(\S+)',s,re.I):cfg['ospf']=(m[1],area(m[2]),n)
            elif m:=re.fullmatch(r'ip router ospf\s+(\S+)\s+area\s+(\S+)',s,re.I):cfg['ospf']=(m[1],area(m[2]),n)
            elif m:=re.fullmatch(r'ip router isis(?:\s+(\S+))?',s,re.I):cfg['isis']=(m[1] or 'default',n)
            elif s.startswith('ip ospf '):cfg['ospf_options'].append((n,s[len('ip ospf '):]))
            elif s.startswith('isis '):cfg['isis_options'].append((n,s[len('isis '):]))
            elif re.match(r'encapsulation\s+frame-relay\b',s,re.I):
                # Frame Relay does not inherit HDLC's point-to-point network
                # semantics merely because the interface is named Serial.
                for proto in ('ospf','isis'):cfg[proto+'_options'].append((n,'network frame-relay'))
        global_interface[name(block['interface'])]=cfg
    for block in process_blocks:
        proto=block['protocol'];context=block['vrf'];p=b.process(proto,block['name'],context,block['line'],style='wide' if dialect=='cisco_nxos' and proto=='isis' else None)
        block_processes={id(p):p};xr_defaults={}
        xr_area=None;xr_iface=None;vrf_indent=None;area_indent=None;iface_indent=None;af_indent=None;af=None
        for n,s,indent in block['rows']:
            if af_indent is not None and indent<=af_indent:af_indent=None;af=None
            if af=='address-family ipv6 unicast':continue
            if vrf_indent is not None and indent<=vrf_indent and not s.startswith('vrf '):context=block['vrf'];p=b.process(proto,block['name'],context,block['line']);vrf_indent=None
            if m:=re.fullmatch(r'vrf\s+(\S+)',s):
                context=m[1];p=b.process(proto,block['name'],context,n,style='wide' if dialect=='cisco_nxos' and proto=='isis' else None);block_processes[id(p)]=p
                vrf_indent=indent;xr_area=None;xr_iface=None;area_indent=None;iface_indent=None;continue
            if iface_indent is not None and indent<=iface_indent and not s.startswith('interface '):xr_iface=None;iface_indent=None
            if area_indent is not None and indent<=area_indent and not s.startswith('area '):xr_area=None;area_indent=None
            if s.startswith('address-family '):
                af=s;af_indent=indent
                if xr_iface and s=='address-family ipv4 unicast':b.port(p,xr_iface,n)['ipv4']=True
                if s not in ('address-family ipv4 unicast','address-family ipv6 unicast'):b.issue(p,'unsupported_address_family','只支持 Standard IPv4 IGP。',n)
                continue
            if m:=re.fullmatch(r'router-id\s+([\d.]+)',s):p.router_id=m[1]
            elif m:=re.fullmatch(r'(?:maximum-paths|maximum paths)\s+(\d+)',s):
                if 1<=int(m[1])<=256:p.max_paths=int(m[1])
                else:b.issue(p,'invalid_ecmp_limit','ECMP 上限超出范围。',n)
            elif proto=='ospf' and (m:=re.fullmatch(r'network\s+([\d.]+)\s+([\d.]+)\s+area\s+(\S+)',s)):
                try:p.networks.append((int(ipaddress.IPv4Address(m[1])),int(ipaddress.IPv4Address(m[2])),area(m[3]),n))
                except ValueError:b.issue(p,'invalid_network','OSPF network 地址或通配符无效。',n)
            elif m:=re.fullmatch(r'(no )?passive-interface\s+(.+)',s):
                if m[2]=='default':p.passive_all=not bool(m[1])
                else:(p.active if m[1] else p.passive).add(name(m[2]))
            elif proto=='isis' and (m:=re.fullmatch(r'net\s+(\S+)',s)):b.net(p,m[1],n)
            elif proto=='isis' and (m:=re.fullmatch(r'is-type\s+(level-1|level-1-2|level-2-only)',s)):p.levels=['1','2'] if m[1]=='level-1-2' else ['1' if m[1]=='level-1' else '2']
            elif proto=='isis' and (m:=re.fullmatch(r'metric-style\s+(wide|narrow|transition)(?:\s+level(?:-|\s+)([12]))?',s)):
                style='both' if m[1]=='transition' else m[1]
                if m[2]:p.level_metric_styles[m[2]]=style
                else:p.metric_style=style
            elif proto=='ospf' and dialect=='cisco_xr' and (m:=re.fullmatch(r'area\s+(\S+)',s)):
                xr_area=area(m[1]);area_indent=indent
                if xr_area not in p.areas:p.areas.append(xr_area)
            elif dialect=='cisco_xr' and (m:=re.fullmatch(r'interface\s+(\S+)',s)):
                xr_iface=m[1];iface_indent=indent;port=b.port(p,xr_iface,n)
                if proto=='ospf':port['scope']=xr_area
                else:port['ipv4']=False
            elif proto=='isis' and s.startswith('address-family '):
                if s not in ('address-family ipv4 unicast','address-family ipv6 unicast'):b.issue(p,'unsupported_address_family','只支持 Standard IPv4 IGP。',n)
            elif dialect=='cisco_xr' and xr_iface:
                port=b.port(p,xr_iface,n)
                if m:=re.fullmatch(r'(?:cost|metric)\s+(\d+)(?:\s+level\s*([12]))?',s):
                    if p.protocol=='ospf' and int(m[1])==0:b.issue(p,'invalid_cost','Cisco OSPF 开销必须大于零。',n,name(xr_iface))
                    else:b.cost(p,port,m[1],n,m[2])
                elif m:=re.fullmatch(r'(?:network|network-type)\s+(point-to-point|broadcast)',s):port['network_type']='p2p' if m[1]=='point-to-point' else 'broadcast';port['loopback_host']=m[1]!='point-to-point'
                elif s in ('passive','passive enable'):port['passive']=True
                elif s in ('passive disable','no passive'):port['passive']=False
                elif s=='point-to-point':port['network_type']='p2p'
                elif s=='shutdown':port['disabled']=True
                elif s in ('circuit-type level-1','circuit-type level-2-only'):port['levels']=['1' if 'level-1' in s else '2']
                elif s=='circuit-type level-1-2':port['levels']=['1','2']
                elif m:=re.fullmatch(r'(hello-interval|dead-interval)\s+(\d+)',s):port['hello' if m[1]=='hello-interval' else 'dead']=int(m[2])
                elif s not in ('!','address-family ipv4 unicast','address-family ipv6 unicast') and not s.startswith('description '):b.issue(p,'unsupported_protocol_statement','此 Cisco 协议接口语义尚未适配。',n,name(xr_iface),statement=s)
            elif proto=='ospf' and dialect=='cisco_xr' and re.match(r'(?:cost|hello-interval|dead-interval|network|network-type|passive)\b',s):
                settings=xr_defaults.setdefault((context,xr_area),{'line':n,'costs':{},'levels':None})
                apply_options(b,p,settings,[(n,s)],None)
                p.source_lines.append(n)
            elif proto=='isis' and (m:=re.fullmatch(r'metric\s+(\d+)(?:\s+level(?:-|\s+)([12]))?',s)):b.cost(p,None,m[1],n,m[2])
            elif s in ('!','log adjacency changes','address-family ipv4 unicast','address-family ipv6 unicast') or SAFE_PROCESS.match(s):pass
            else:b.issue(p,'unsupported_protocol_statement','此 Cisco 协议语义尚未适配。',n,statement=s)
        if dialect=='cisco_xr' and proto=='ospf':
            # Resolve after reading the complete native hierarchy, so order of
            # sibling declarations does not change effective inheritance.
            for instance in block_processes.values():
                inherited={**xr_defaults.get((block['vrf'],None),{}),**xr_defaults.get((instance.context,None),{})}
                costs={**xr_defaults.get((block['vrf'],None),{}).get('costs',{}),**xr_defaults.get((instance.context,None),{}).get('costs',{})}
                instance.default_costs.update(costs)
                for port in instance.ports.values():
                    area_defaults=xr_defaults.get((instance.context,port.get('scope')),{});
                    for key,value in {**inherited,**area_defaults}.items():
                        if key not in ('line','costs','levels'):port.setdefault(key,value)
                    for level,value in area_defaults.get('costs',{}).items():port['costs'].setdefault(level,value)
    # Native interface activation (IOS/XE/NX-OS); process numbers remain local.
    for ikey,cfg in global_interface.items():
        for proto in ('ospf','isis'):
            activation=cfg[proto]
            candidates=[p for p in b.processes.values() if p.protocol==proto and p.context==cfg['context']]
            if activation:
                p=b.process(proto,activation[0],cfg['context'],activation[-1]);candidates=[p]
                port=b.port(p,ikey,activation[-1])
                if proto=='ospf':port['scope']=activation[1]
            for p in candidates:
                if not activation and proto=='isis' and ikey not in p.passive:continue
                port=b.port(p,ikey,cfg['line'])
                if not activation and proto=='ospf':
                    # Do not activate every interface merely because an OSPF
                    # process exists: only network statements select it later.
                    if ikey in p.ports and not p.ports[ikey].get('scope'):p.ports.pop(ikey)
                    continue
                apply_options(b,p,port,cfg[proto+'_options'],ikey)
        # Costs may accompany IOS process-level network selection.
        for p in b.processes.values():
            if p.protocol!='ospf' or p.context!=cfg['context'] or cfg['ospf']:continue
            iface=b.interfaces[ikey];matching=[]
            for network,wildcard,scope,line in p.networks:
                if any(a.get('family')==4 and a.get('address') and int(ipaddress.IPv4Address(a['address']))&(~wildcard&0xffffffff)==network&(~wildcard&0xffffffff) for a in iface['addresses']):matching.append((scope,line))
            if matching:
                port=b.port(p,ikey,matching[0][1]);port['scope']=matching[0][0];apply_options(b,p,port,cfg['ospf_options'],ikey)
    if dialect=='cisco_nxos':
        features={}
        for n,raw in enumerate(text.splitlines(),1):
            if flag:=re.fullmatch(r'(no )?feature\s+(ospf|isis)',raw.strip(),re.I):features[flag[2].lower()]=(not bool(flag[1]),n)
        for p in b.processes.values():
            state=features.get(p.protocol)
            if not state:
                p.enabled=False
                b.issue(p,'protocol_feature_not_enabled','NX-OS 完整配置没有启用对应 IGP feature，不能视为活动协议。',p.line)
            elif not state[0]:p.enabled=False
    return b.finish()


def apply_options(b,p,port,options,ikey):
    for n,s in options:
        if m:=re.fullmatch(r'(?:cost|metric)\s+(\d+)(?:\s+level-([12]))?',s):
            if p.protocol=='ospf' and int(m[1])==0:b.issue(p,'invalid_cost','Cisco OSPF 开销必须大于零。',n,ikey)
            else:b.cost(p,port,m[1],n,m[2])
        elif m:=re.fullmatch(r'(?:network|network-type|circuit-type)\s+(point-to-point|broadcast|level-1|level-2-only|level-1-2)',s):
            if m[1].startswith('level'):port['levels']=['1','2'] if m[1]=='level-1-2' else ['1' if m[1]=='level-1' else '2']
            else:port['network_type']='p2p' if m[1]=='point-to-point' else 'broadcast';port['loopback_host']=m[1]!='point-to-point'
        elif m:=re.fullmatch(r'(hello-interval|dead-interval)\s+(\d+)',s):port['hello' if m[1]=='hello-interval' else 'dead']=int(m[2])
        elif s in ('passive','passive-interface','passive enable'):port['passive']=True
        elif s in ('passive disable','no passive'):port['passive']=False
        elif s=='network frame-relay':port['network_type']='unsupported'
        elif s.startswith(('priority ','mtu-ignore','enable')):pass
        else:b.issue(p,'unsupported_protocol_statement','此 Cisco 接口协议语义尚未适配。',n,ikey,statement=s)
