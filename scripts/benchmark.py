"""Synthetic parse/inference/export timing. Does not claim real-device compatibility."""
from pathlib import Path
import json
import platform
import sys
import time
import tracemalloc
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app.parser import parse_config
from backend.app.inference import infer, project_graph
from backend.app.exports import drawio_export

results = []
for count in [100, 500, 2000]:
    snapshot = {"id": "benchmark", "devices": [], "interfaces": [], "contexts": [], "diagnostics": []}
    started = time.perf_counter()
    source_bytes = 0
    for n in range(count):
        import ipaddress
        ip = ipaddress.ip_address(int(ipaddress.ip_address('10.0.0.0')) + n)
        text = f'hostname r{n}\ninterface GigabitEthernet0/0\n ip address {ip} 255.255.255.254\n!\nend\n'
        source_bytes += len(text)
        parsed = parse_config(text, f'r{n}.cfg', f'src{n}', f'dev{n}', 'cisco_ios')
        snapshot['devices'].append(parsed['device'])
        snapshot['interfaces'].extend(parsed['interfaces'])
        snapshot['contexts'].extend(parsed['contexts'])
    parsed_at = time.perf_counter()
    tracemalloc.start()
    snapshot.update(infer(snapshot, {}))
    inferred_at = time.perf_counter()
    graph = project_graph(snapshot)
    projected_at = time.perf_counter()
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    xml = drawio_export(graph, None, 'Benchmark')
    exported_at = time.perf_counter()
    row = {'devices': count, 'source_bytes': source_bytes, 'interfaces': len(snapshot['interfaces']),
           'edges': len(graph['edges']), 'parse_s': round(parsed_at-started,3),
           'infer_s': round(inferred_at-parsed_at,3), 'project_s':round(projected_at-inferred_at,3),
           'drawio_s':round(exported_at-projected_at,3), 'inference_projection_peak_mib':round(peak/1024**2,2), 'drawio_bytes':len(xml)}
    results.append(row)
    print(json.dumps(row),flush=True)
print(json.dumps({'python':platform.python_version(),'system':platform.system(),'machine':platform.machine(),'results':results},indent=2))
