import pandas as pd, numpy as np, json
A = pd.read_parquet("out_repro/chunks/year=2026/data.parquet")
B = pd.read_parquet("/Users/davidliu/Documents/TradingAgents-AShare-cache/phase2/v2-20261016/out_v2/chunks/year=2026/data.parquet")
key=["ts_code","signal_date"]
A=A.sort_values(key,kind="mergesort").reset_index(drop=True)
B=B.sort_values(key,kind="mergesort").reset_index(drop=True)
assert list(A.columns)==list(B.columns)
assert A[key].equals(B[key]), "key mismatch"
diffs={}
for c in A.columns:
    a,b=A[c],B[c]
    if pd.api.types.is_float_dtype(a):
        eq = ((a.isna()&b.isna()) | (a.to_numpy()==b.to_numpy())).all()
    else:
        eq = a.fillna("__NA__").astype(str).equals(b.fillna("__NA__").astype(str))
    if not eq: diffs[c]=int(((a.isna()&b.isna())|(a.fillna("__NA__").astype(str)!=b.fillna("__NA__").astype(str))).sum())
print(json.dumps({"rows":len(A),"differing_columns":diffs,"dtypes_equal":list(map(str,A.dtypes))==list(map(str,B.dtypes))},indent=1))
