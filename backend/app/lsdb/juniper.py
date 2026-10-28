"""Junos OSPF detail/extensive and IS-IS detail/extensive adapters."""
import re
from .common import IP,LSP,area,node,ospf_record,ospf_link,isis_record,prefix,finish_isis
from .model import LinkStateLink


def parse_file(rows,batch,source,issue):
    return ospf(rows,batch,source,issue) if batch.protocol=='ospf' else isis(rows,batch,source,issue)


def ospf(rows,batch,source,issue):
    chunks=[];current=None;scope=None;instance='default';collectors=set()
    header=re.compile(r"(Router|Network|Summary|ASBRSum|Extern|NSSA|OpaqArea)\s+(\*?)?("+IP+r")\s+("+IP+r")\s+(0x[\da-f]+)\s+(\d+)\s+0x[\da-f]+\s+(0x[\da-f]+)\s+(\d+)",re.I)
    for n,raw in rows:
        s=raw.strip()
        a=re.search(r'OSPF (?:link state )?database,?\s*Area\s+(\S+)',s,re.I)
        inst=re.search(r'Routing Instance:\s*(\S+)',s,re.I)
        command=re.search(r'show ospf database.*\binstance\s+(\S+)',s,re.I)
        start=header.match(s)
        if a or inst or start:
            if current:chunks.append(current);current=None
        if inst or command:
            instance=(inst or command)[1].rstrip(',')
            if instance=='master':instance='default'
        if a:scope=area(a[1].rstrip(','))
        elif start and scope==batch.scope and instance==batch.context_name and batch.process_id in ('1','0','default','master',instance):
            current=[(n,s)]
            if start[2] and start[1].lower()=='router':collectors.add(start[4])
        elif current is not None:current.append((n,s))
    if current:chunks.append(current)
    records=[]
    for chunk in chunks:
        m=header.match(chunk[0][1]);ref=lambda n:dict(source_file_id=source,line=n)
        rec=ospf_record(m[1],m[3],m[4],m[5],m[7],m[6],ref(chunk[0][0]),detailed=False)
        try:
            if rec.kind=='router':
                count=None;pending=None;links=0
                for n,s in chunk[1:]:
                    if c:=re.search(r'link count\s+(\d+)',s,re.I):count=int(c[1]);rec.detailed=True
                    if l:=re.match(r'id\s+(\S+),\s*data\s+(\S+),\s*Type\s+(\S+)',s,re.I):
                        if pending:raise ValueError('missing metric')
                        pending=(n,l[1].rstrip(','),l[2].rstrip(','),l[3])
                    elif cost:=re.search(r'TOS 0 metric\s+(\d+)',s,re.I):
                        if not pending:raise ValueError('missing link')
                        ln,target,data,typ=pending;ospf_link(rec,typ,target,data,cost[1],ref(ln));links+=1;pending=None
                if rec.detailed and (count is None or links!=count or pending):raise ValueError('link count')
            elif rec.kind=='network':
                rec.links=[LinkStateLink(target=m[1],cost=0,kind='member',source_refs=[ref(n)]) for n,s in chunk[1:] if (m:=re.match(r'attached router\s+('+IP+r')',s,re.I))]
                rec.detailed=bool(rec.links)
            records.append(rec)
        except ValueError:issue('malformed_lsa','Junos LSA 的 link count、连接或度量字段不完整。',source,chunk[0][0])
    return records,collectors


def isis(rows,batch,source,issue):
    records,chunks,collectors=[],[],set()
    scope=None;current=None;instance='default';process='default'
    start_re=re.compile(r'('+LSP+r')\s+Sequence:\s*(0x[\da-f]+),\s*Checksum:\s*(0x[\da-f]+),\s*Lifetime:\s*(\d+)\s+secs',re.I)
    for n,raw in rows:
        s=raw.strip()
        h=re.search(r'IS-IS level\s+([12])\s+link-state database',s,re.I)
        ins=re.search(r'ISIS instance:\s*(\S+),\s*Routing Instance:\s*(\S+)',s,re.I)
        start=start_re.match(s)
        if h or ins or start:
            if current:chunks.append(current);current=None
        if h:scope=h[1]
        if ins:process=ins[1].lower();instance=ins[2].lower()
        if instance=='master':instance='default'
        if start and scope==batch.scope and instance==batch.context_name.lower() and (process==batch.process_id.lower() or process=='default' and batch.process_id in ('0','1','default','master')):
            current=[(n,s)]
        elif current is not None:current.append((n,s))
    if current:chunks.append(current)
    for chunk in chunks:
        m=start_re.match(chunk[0][1]);ref=lambda n:dict(source_file_id=source,line=n)
        rec=isis_record(m[1],m[5],m[6],m[7],ref(chunk[0][0]),detailed=False)
        links=[];prefixes=[];last_link=None;last_prefix=None;excluded=set()
        for n,s in chunk[1:]:
            try:
                if re.match(r'IS (?:extended )?neighbor:|IP (?:extended )?prefix:|LSP length:|TLVs:|Speaks:',s,re.I):rec.detailed=True
                if re.search(r'(?:Speaks:\s*(?:IP|IPv4)$|IPV4 Index:)',s,re.I):rec.ipv4=True
                if h:=re.match(r'Hostname:\s*(\S+)',s,re.I):rec.hostname=h[1]
                if a:=re.match(r'(?:IP router id|IP address):\s*('+IP+r')',s,re.I):
                    if last_link is None:rec.addresses.append(a[1])
                if re.search(r'\boverload\b.*(?:true|set)|\bOL\b',s,re.I):rec.overload=True
                nbr=re.match(r'IS (extended )?neighbor:\s*(\S+),?\s+(?:Internal,\s*)?Metric:\s*(?:default\s+)?(\d+)',s,re.I)
                if nbr:
                    last_prefix=None
                    last_link=LinkStateLink(target=node(nbr[2].rstrip(',')),cost=int(nbr[3]),encoding='wide' if nbr[1] else 'narrow',source_refs=[ref(n)])
                    links.append(last_link)
                elif p:=re.match(r'IP (extended )?prefix:\s*('+IP+r'/\d+)\s+Metric:?\s*(\d+)(.*)',s,re.I):
                    last_link=None
                    if re.search(r'\b(?:External|Down)\b',p[4],re.I):excluded.add(p[2]);last_prefix=None;continue
                    last_prefix=prefix(p[2],p[3],ref(n));prefixes.append(last_prefix);rec.ipv4=True
                elif re.match(r'(?:IPv6|V6|ISO) (?:extended )?prefix:',s,re.I):last_prefix=None;last_link=None
                elif last_link and (a:=re.match(r"(?:IP address|IPv4 address):\s*("+IP+r")",s,re.I)):last_link.local_ip=a[1]
                elif last_link and (a:=re.match(r"Neighbor's IP address:\s*("+IP+r")",s,re.I)):last_link.remote_ip=a[1]
                elif last_link and (ids:=re.search(r'Local interface index:\s*(\S+),\s*Remote interface index:\s*(\S+)',s,re.I)):last_link.local_id=ids[1];last_link.remote_id=ids[2]
                elif last_prefix and (flags:=re.search(r'Prefix-Attribute-Flags:.*\(X:([01]),R:([01])',s)):
                    if flags[1]=='1' or flags[2]=='1':excluded.add(last_prefix.prefix)
                elif re.match(r'IS (?:extended )?neighbor:|IP (?:extended )?prefix:',s,re.I):issue('malformed_reachability','Junos 邻接 / IPv4 前缀详细字段未能完整解析。',source,n)
            except ValueError:issue('malformed_lsp','Junos LSP 的地址或开销无效。',source,n)
        records.append(finish_isis(rec,links,[p for p in prefixes if p.prefix not in excluded]))
    # Plain tables are inventory only.
    if not records:
        scope=None
        for n,raw in rows:
            s=raw.strip()
            if h:=re.search(r'IS-IS level\s+([12])\s+link-state database',s,re.I):scope=h[1]
            m=re.match(r'('+LSP+r')\s+(0x[\da-f]+)\s+(0x[\da-f]+)\s+(\d+)',s,re.I)
            if m and scope==batch.scope:records.append(isis_record(m[1],m[5],m[6],m[7],dict(source_file_id=source,line=n),detailed=False))
    return records,collectors
