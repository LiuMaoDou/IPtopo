"""Registry of native LSDB adapters. Every parser emits the same Batch contract."""
import re
from .model import Batch, Diagnostic
from .huawei import canonical_scope

VENDORS={'huawei':'Huawei VRP','cisco':'Cisco IOS / IOS-XE / IOS-XR','juniper':'Juniper Junos','nokia':'Nokia SR OS'}
SIGNATURES={
    'cisco': r'OSPF Router with ID|(?:ISIS Level-[12]|IS-IS \S+ \(Level-[12]\)) (?:Link State Database|LSP)',
    'juniper': r'OSPF (?:link state )?database,?\s*Area|IS-IS level [12] link-state database',
    'nokia': r'OSPF Link State Database\s*\(Type|OSPFv2\s+\(\d+\)|Router LSA for Area|(?:Router|Rtr) \S+ ISIS Instance|Displaying Level [12] database',
    'huawei': r'OSPF\s+Process\s+\S+\s+with\s+Router\s+ID|Database information for ISIS',
}


def detected_vendors(text):
    return {vendor for vendor,pattern in SIGNATURES.items() if re.search(pattern,text,re.I)}


def detect(text):
    found=detected_vendors(text)
    return next(iter(found)) if len(found)==1 else None


def parse(files, vendor='huawei', **metadata):
    detected=set().union(*(detected_vendors(f['text']) for f in files))
    if vendor=='auto':
        if len(detected)!=1:raise ValueError('无法唯一识别厂商，请选择厂商并检查采集文本。')
        vendor=next(iter(detected))
    if vendor not in VENDORS:raise ValueError('未知 LSDB 厂商。')
    if vendor=='huawei':
        from .huawei import parse as native
        batch=native(files,vendor='huawei',**metadata)
        if detected-{'huawei'}:batch.diagnostics.append(Diagnostic(code='vendor_mismatch',message='文件包含其他厂商格式，请拆分后选择正确厂商。'))
        return batch
    batch=Batch(vendor=vendor,adapter=vendor+'-lsdb/1',**metadata)
    batch.scope=canonical_scope(batch.protocol,batch.scope)
    def issue(code,message,source=None,line=1,blocking=True):
        batch.diagnostics.append(Diagnostic(code=code,message=message,blocking=blocking,source_refs=[dict(source_file_id=source,line=line)] if source else []))
    if detected-{vendor}:issue('vendor_mismatch','一个批次含有其他厂商的采集格式，请拆分导入。')
    if vendor=='cisco':from .cisco import parse_file
    elif vendor=='juniper':from .juniper import parse_file
    else:from .nokia import parse_file
    collectors=set()
    for f in files:
        sid=f['id'];batch.source_files.append({'id':sid,'filename':f['filename']})
        clean=re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]','',f['text']).replace('\xa0',' ')
        rows=list(enumerate(clean.splitlines(),1))
        for n,s in rows:
            if re.search(r'--+\s*More\s*--+|(?:output|输出).*(?:truncated|omitted|截断)',s,re.I) or re.fullmatch(r'\s*(?:\.{3,}|…+)\s*',s):issue('truncated_capture','文本包含分页或省略标记，需要完整详细输出。',sid,n)
        # Numeric identity tables can accompany detailed LSDB files.
        aliases=0
        for n,s in rows:
            m=re.match(r'\s*([\da-fA-F]{4}(?:\.[\da-fA-F]{4}){2})\s+(\S+)',s)
            if m and not re.match(r'(?:0x|[\da-fA-F]{2}-)',m[2],re.I):
                key=m[2].lower();value=m[1].lower()
                if key in batch.aliases and batch.aliases[key]!=value:issue('alias_conflict','同一主机别名映射到不同 System ID。',sid,n)
                batch.aliases[key]=value;aliases+=1
        try:records,seen=parse_file(rows,batch,sid,issue)
        except (ValueError,TypeError,IndexError):
            records,seen=[],set();issue('malformed_capture','采集字段无效或结构不完整，需检查原文。',sid)
        batch.records.extend(records);collectors.update(seen)
        if not records and not aliases:issue('no_records','没有所选协议、进程与 Area / Level 的可识别记录。',sid)
    if batch.protocol=='isis' and batch.aliases:
        def resolved(value):
            m=re.fullmatch(r'(.+)\.([\da-fA-F]{2})',value)
            return batch.aliases.get(m[1].lower(),m[1].lower())+'.'+m[2].lower() if m else value
        for r in batch.records:
            r.origin=resolved(r.origin);r.key=f'{r.origin}-{r.fragment:02x}'
            for link in r.links:link.target=resolved(link.target)
    if len(collectors)>1:issue('mixed_collectors','一个批次出现多个采集设备，请按采集设备及时间拆分。')
    if not batch.collector and len(collectors)==1:batch.collector=next(iter(collectors))
    if not batch.records:issue('empty_lsdb','需要 OSPF Router/Network 或 IS-IS 详细数据库输出。')
    if not batch.declared_complete:issue('unconfirmed_capture','未声明完整采集；可以还原可见拓扑，但不输出完整路径结论。')
    return Batch.model_validate(batch.model_dump())
