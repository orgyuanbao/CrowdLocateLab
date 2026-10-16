"""Independent small exact check of the archived mathematical claim and provenance."""
from pathlib import Path
from fractions import Fraction as F
from itertools import permutations
from collections import Counter
import hashlib
import importlib.util
import json
import sys
import urllib.request

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[3] / 'work/github_publish/CrowdLocateLab'
STUDY = ROOT / 'studies/creative_intelligence/mirror_ambiguity'
OUT = Path(__file__).resolve().parent

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def determinant(rows):
    total = F(0)
    for p in permutations(range(4)):
        sign = (-1) ** sum(p[i] > p[j] for i in range(4) for j in range(i + 1, 4))
        product = F(sign)
        for i in range(4):
            product *= rows[i][p[i]]
        total += product
    return total

spec = importlib.util.spec_from_file_location('review_diagnostic', STUDY / 'artifacts/diagnostic_v2.py')
diagnostic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostic)
anchors = [(F(-4),F(0)),(F(-3),F(1)),(F(-2),F(0)),(F(-5,2),F(0)),(F(-4),F(0))]
times = list(map(F,[-4,-3,-2,-1,0]))
thetas = [list(map(F,[0,3,1,1])),list(map(F,[0,-3,1,-1]))]
ranges = []
determinants = []
for theta in thetas:
    residuals = [[theta[j] + t*theta[j+2]-a[j] for j in range(2)] for a,t in zip(anchors,times)]
    ranges.append([sum(v*v for v in z) for z in residuals])
    rows = [[*z, t*z[0], t*z[1]] for z,t in zip(residuals,times)]
    determinants.append(determinant([rows[i] for i in [0,1,3,4]]))
assert ranges == [[F(1),F(1),F(1),F(25,4),F(25)]]*2
assert determinants == [F(-6),F(6)]
area2 = (anchors[1][0]-anchors[0][0])*(anchors[2][1]-anchors[0][1])-(anchors[1][1]-anchors[0][1])*(anchors[2][0]-anchors[0][0])
assert area2 == -2
data = dict(anchors=anchors, times=times, epsilon=[0]*5, y=[1,1,1,F(5,2),5], candidates=thetas)
certificate = diagnostic.diagnose(data)
assert certificate['status'] == 'witness'
assert certificate['witnesses'][0]['position_separation_squared'] == '36'
assert certificate['witnesses'][0]['worst_case_error_lower_bound_squared'] == '9'
# Direct exact interval intersection for the rational-distance boundary example.
assert diagnostic.common_row(F(1),F(9),F(1))
assert diagnostic.fixed_row(F(1),F(1),F(1))
assert not diagnostic.fixed_row(F(9),F(1),F(1))
assert not diagnostic.common_row(F(1),F(9),F(999,1000))

manifest = json.loads((STUDY/'SOURCE_MANIFEST.json').read_text(encoding='utf8'))
assert all(digest(STUDY/name)==entry['sha256'] for name,entry in manifest['files'].items())
provenance = json.loads((ROOT/'integrations/creative_intelligence/UPSTREAM.json').read_text(encoding='utf8'))
vendor = ROOT/'integrations/creative_intelligence/vendor'
assert all(digest(vendor/name)==h for name,h in provenance['files_sha256'].items())
url='https://raw.githubusercontent.com/DeepMathLLM/Creative-Intelligence/'+provenance['commit']+'/mathematical-concept-agent/agent.py'
with urllib.request.urlopen(url, timeout=30) as response:
    upstream_bytes=response.read()
assert hashlib.sha256(upstream_bytes).hexdigest()==digest(vendor/'agent.py')
sessions=[]
turn_records=[]
for n in [1,2]:
    evidence=STUDY/'evidence'/f'turn-{n:03d}'
    result=json.loads((evidence/'result.json').read_text(encoding='utf8'))
    before=json.loads((evidence/'state_before.json').read_text(encoding='utf8'))
    after=json.loads((evidence/'state_after.json').read_text(encoding='utf8'))
    command=json.loads((evidence/'command.json').read_text(encoding='utf8'))
    assert result['accepted_structured_research_turn'] and result['execute_turn_calls']==1
    assert after['turn']==before['turn']+1==n
    sessions.append(result['session_id'])
    events=[json.loads(line) for line in (evidence/'events.raw.jsonl').read_text(encoding='utf8').splitlines()]
    types=Counter(event.get('type') for event in events)
    item_types=Counter(event.get('item',{}).get('type') for event in events if 'item' in event)
    assert types['turn.completed'] and item_types['command_execution']
    if n==2:
        assert 'resume' in command['argv'] and command['resumed_session_id']==sessions[0]
    turn_records.append({'turn':n,'status':after['status'],'event_types':dict(types),'item_types':dict(item_types)})
assert sessions[0]==sessions[1]
report={'status':'passed','date':'2026-10-16','scope':'Independent bounded exact check and archived-evidence consistency; not formal proof or full security audit',
        'exact_squared_ranges':[[str(x) for x in row] for row in ranges],
        'exact_jacobian_minors':[str(x) for x in determinants], 'twice_anchor_area':str(area2),
        'position_separation_squared':'36','worst_case_error_lower_bound_squared':'9',
        'archive_files_verified':len(manifest['files']),'vendor_files_verified':len(provenance['files_sha256']),
        'upstream_agent_sha256':digest(vendor/'agent.py'),'upstream_agent_verified_from_fixed_public_url':url,
        'same_session':True,'turns':turn_records,
        'limitations':['Finite supplied candidates only; absence of witness is not global uniqueness.',
                      'Exact anchors/timestamps and deterministic epsilon bounds are assumptions, not calibrated field guarantees.',
                      'Two accepted controller units are not two backend requests or a formal mathematical proof.',
                      'PDF/report consistency and final publication state require parent review.']}
(OUT/'review.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
print(json.dumps(report,ensure_ascii=False,indent=2))
