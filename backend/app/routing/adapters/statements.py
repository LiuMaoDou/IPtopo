"""Line-preserving tokenization for routing configuration readers."""
import re
import shlex


def tokens(text):
    try:return shlex.split(text,comments=False)
    except ValueError:return text.split()


def hierarchy(text,newline_leaves=False):
    stack=[];current=[];line=1;start=1;records=[];inactive=[]
    for match in re.finditer(r'"(?:\\.|[^"\\])*"|#[^\n]*|/\*.*?\*/|[{};\n]|[^\s{};]+',text,re.S):
        token=match.group()
        if token.startswith(('#','/*')):line+=token.count('\n');continue
        if token=='\n':
            if newline_leaves and current:records.append(([t for p,_ in stack for t in p]+current,start,any(x for _,x in stack)));current=[]
            line+=1;continue
        if token=='{':
            off='inactive:' in current;current=[t for t in current if t!='inactive:']
            if off:inactive.append([t for p,_ in stack for t in p]+current)
            records.append(([t for p,_ in stack for t in p]+current,start,off or any(x for _,x in stack)))
            stack.append((current,off));current=[]
        elif token in (';','}'):
            if current:
                off='inactive:' in current or any(x for _,x in stack)
                records.append(([t for p,_ in stack for t in p]+[t for t in current if t!='inactive:'],start,off));current=[]
            if token=='}' and stack:stack.pop()
        else:
            if not current:start=line
            current.append(token.strip('"'))
    return records,inactive,bool(stack or current)


def junos(text,dialect):
    if dialect=='junos_hierarchical':return hierarchy(text)
    records=[];inactive=[]
    for n,raw in enumerate(text.splitlines(),1):
        s=re.sub(r'^\S+[>#]\s*','',raw.strip())
        if s.startswith('set '):records.append((tokens(s)[1:],n,False))
        elif s.startswith('deactivate '):inactive.append(tokens(s)[1:])
        elif s.startswith('delete '):records.append((tokens(s)[1:],n,True))
    return records,inactive,False


def nokia(text,dialect):
    if dialect=='nokia_md_hierarchical':
        rows,off,bad=hierarchy(text,True)
        return [(t[1:] if t and t[0] in ('configure','/configure') else t,n,i) for t,n,i in rows],off,bad
    if dialect=='nokia_md_flat':
        rows=[]
        for n,s in enumerate(text.splitlines(),1):
            t=tokens(s.strip())
            if t[:1]==['set']:t=t[1:]
            if t and t[0] in ('configure','/configure'):rows.append((t[1:],n,False))
        return rows,[],False
    rows=[];stack=[]
    containers={'configure','router','service','vprn','ospf','isis','area','interface','level','ipv6','segment-routing','multi-topology'}
    for n,s in enumerate(text.splitlines(),1):
        s=s.strip()
        if not s or s.startswith(('#','!','echo')):continue
        t=tokens(s)
        if s=='exit all':stack=[];continue
        if s in ('exit','back'):
            if stack:stack.pop()
            continue
        path=[v for part in stack for v in part]+t
        path=path[1:] if path[:1]==['configure'] else path
        # Emit containers as well as leaves: interface/area may be empty.
        rows.append((path,n,False))
        if t[0] in containers and not (t[0]=='interface' and len(t)<2):stack.append(t)
    return rows,[],False
