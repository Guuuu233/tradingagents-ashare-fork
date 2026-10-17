import hashlib, sys, json
import pandas as pd, numpy as np, pyarrow.parquet as pq

def rowhash(p):
    df = pd.read_parquet(p)
    df = df.sort_values(["ts_code","signal_date"], kind="mergesort").reset_index(drop=True)
    cols = list(df.columns)
    h = hashlib.sha256()
    h.update(("|".join(cols)).encode())
    for c in cols:
        s = df[c]
        if pd.api.types.is_float_dtype(s):
            v = s.to_numpy(dtype="float64", na_value=np.nan)
            # canonical NaN
            buf = np.where(np.isnan(v), np.float64(np.nan), v).tobytes()
        elif pd.api.types.is_integer_dtype(s) or pd.api.types.is_bool_dtype(s):
            buf = s.to_numpy().tobytes()
        else:
            buf = "\x1f".join("" if (x is None or (isinstance(x,float) and np.isnan(x))) else str(x) for x in s.tolist()).encode()
        h.update(c.encode()); h.update(len(buf).to_bytes(8,"little")); h.update(buf)
    return h.hexdigest(), len(df), cols

a, na, ca = rowhash(sys.argv[1])
b, nb, cb = rowhash(sys.argv[2])
print(json.dumps({"A_rows":na,"B_rows":nb,"cols_equal":ca==cb,
                  "A_rowhash":a,"B_rowhash":b,"EQUAL":a==b and na==nb and ca==cb}, indent=1))
