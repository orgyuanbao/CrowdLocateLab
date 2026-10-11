from pathlib import Path
import hashlib,json,math
import numpy as np

def clean(x):
    if isinstance(x,dict):return {str(k):clean(v) for k,v in x.items()}
    if isinstance(x,(list,tuple)):return [clean(v) for v in x]
    if isinstance(x,np.ndarray):return clean(x.tolist())
    if isinstance(x,np.integer):return int(x)
    if isinstance(x,(float,np.floating)):return float(x) if math.isfinite(float(x)) else None
    if isinstance(x,np.bool_):return bool(x)
    return x
def canonical(x):return json.dumps(clean(x),ensure_ascii=False,sort_keys=True,separators=(',',':'))
def digest(x):return hashlib.sha256(canonical(x).encode()).hexdigest()
def save(path,x):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+'.tmp');temp.write_text(json.dumps(clean(x),ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(path)
def load(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def metrics(rows):
    e=np.array([r['error_m'] for r in rows]);work=np.array([r['work_units'] for r in rows])
    return dict(n=len(e),mean_m=float(e.mean()),rmse_m=float(np.sqrt(np.mean(e**2))),
        median_m=float(np.median(e)),p90_m=float(np.quantile(e,.9)),p95_m=float(np.quantile(e,.95)),
        max_m=float(e.max()),within_5m=float(np.mean(e<=5)),within_30m=float(np.mean(e<=30)),
        mean_work_units=float(work.mean()),mean_seconds=float(np.mean([r['elapsed_seconds'] for r in rows])))
