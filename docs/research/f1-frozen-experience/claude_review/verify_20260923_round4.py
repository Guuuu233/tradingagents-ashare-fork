# -*- coding: utf-8 -*-
"""第四轮：修正样本下限定理。对任意确定性对照 c(x)（含已校准常数 U）与任意确定性候选 b(x)：
   n >= 7.85 (1-K)/K,  K = E[(q-c)^2 / ((q-c)^2 + q(1-q))]
   又 (q-c)^2 + q(1-q) = E[(Y-c)^2|x] >= min(c,1-c)^2，故 K <= E[(q-c)^2]/min(c,1-c)^2。
   对已校准 U（U=E[q]）：n >= 7.85 (min(U,1-U)^2 - Var(q)) / Var(q) —— 与 q 的分布形状无关。
   证明要点：取 W=(q-c)(Y-c)/h，则 -E[d] <= -E[dW]，E[W^2]=K，Cauchy-Schwarz 得 E[d]^2 <= K E[d^2]。"""
import math, json
import numpy as np
from statistics import NormalDist
rng = np.random.default_rng(20260926)
Z2 = (NormalDist().inv_cdf(.975) + NormalDist().inv_cdf(.8)) ** 2

def exact_n(w, q, c, b):
    """独立事件、零生成噪声时配对 Brier 差 d=(b-Y)^2-(c-Y)^2 的精确 E[d]、Var(d)、所需 n。"""
    d1 = (b - 1) ** 2 - (c - 1) ** 2; d0 = b ** 2 - c ** 2        # Y=1 / Y=0 时的 d
    Ed = float(np.sum(w * (q * d1 + (1 - q) * d0)))
    Ed2 = float(np.sum(w * (q * d1 ** 2 + (1 - q) * d0 ** 2)))
    return Ed, Ed2 - Ed ** 2, (Z2 * (Ed2 - Ed ** 2) / Ed ** 2 if Ed < 0 else math.inf)

def K_of(w, q, c):
    h = (q - c) ** 2 + q * (1 - q)
    return float(np.sum(w * (q - c) ** 2 / h))

out = {}
# (1) 随机世界：q 可含极端值；对照为已校准 U；候选 b 任意（含大幅平移）
r1, r2 = [], []
for _ in range(60000):
    k = int(rng.integers(2, 7)); w = rng.dirichlet(np.ones(k))
    q = rng.uniform(0, 1, k) if rng.random() < .5 else np.clip(.5 + rng.normal(0, rng.uniform(.01, .3), k), 0, 1)
    U = float(np.sum(w * q)); V = float(np.sum(w * (q - U) ** 2))
    if V < 1e-6: continue
    mode = rng.random()
    b = (U + (q - U) * rng.uniform(.001, 2)) if mode < .4 else (rng.uniform(0, 1, k) if mode < .7 else U + rng.normal(0, .05, k))
    b = np.clip(b, 0, 1)
    Ed, Vd, n = exact_n(w, q, U, b)
    if not math.isfinite(n): continue
    K = K_of(w, q, U); hmin = min(U, 1 - U) ** 2
    r1.append(n / (Z2 * (1 - K) / K))
    if V < hmin: r2.append(n / (Z2 * (hmin - V) / V))
out['1_calibrated_U_random_worlds'] = dict(trials=len(r1), min_n_over_Kbound=min(r1), min_n_over_distribution_free_bound=min(r2))

# (2) GPT 的反例构造：SD(q)=10pp、大多数 q=0.5、少数 q∈{0,1}；以及近 0.5 的两点世界
def best_small_shift(w, q, c):
    h = (q - c) ** 2 + q * (1 - q); v = (q - c) / h            # 最优方向 w ∝ (q-c)/h
    best = math.inf
    for k in np.geomspace(1e-5, 1, 200):
        _, _, n = exact_n(w, q, c, np.clip(c + k * v, 0, 1)); best = min(best, n)
    return best
worlds = {
    'two_point_q=0.4/0.6': (np.array([.5, .5]), np.array([.4, .6])),
    'extreme_4pct_q=0or1_rest_0.5': (np.array([.02, .96, .02]), np.array([0., .5, 1.])),
    'extreme_q=0.02/0.98_mixed': None,
}
# 第三个世界：q∈{0.02,0.5,0.98}，按 SD=10pp 解出极端比例
p = .01 / (2 * .48 ** 2); worlds['extreme_q=0.02/0.98_mixed'] = (np.array([p, 1 - 2 * p, p]), np.array([.02, .5, .98]))
out['2_named_worlds_SD10pp_U0.5'] = {}
for name, (w, q) in worlds.items():
    V = float(np.sum(w * (q - .5) ** 2))
    out['2_named_worlds_SD10pp_U0.5'][name] = dict(SD_q=math.sqrt(V), best_n_found=best_small_shift(w, q, .5),
                                                  oracle_n=exact_n(w, q, .5, q)[2], bound=Z2 * (.25 - V) / V,
                                                  old_c0_bound=(Z2 * float(np.min(q * (1 - q))) / V))

# (3) 非恒定对照（如 A 的输出 a(x)）同样成立：K 用 (q-a)^2/((q-a)^2+q(1-q))
r3 = []
for _ in range(40000):
    k = int(rng.integers(2, 7)); w = rng.dirichlet(np.ones(k)); q = rng.uniform(0, 1, k); a = rng.uniform(.3, .7, k)
    b = np.clip(a + (q - a) * rng.uniform(.001, 2) if rng.random() < .5 else rng.uniform(0, 1, k), 0, 1)
    _, _, n = exact_n(w, q, a, b)
    if not math.isfinite(n): continue
    K = K_of(w, q, a)
    if K >= 1: continue
    r3.append(n / (Z2 * (1 - K) / K))
out['3_nonconstant_comparator'] = dict(trials=len(r3), min_n_over_Kbound=min(r3))

# (4) 表：已校准 U 时与分布形状无关的下限
tab = {}
for U in (.5, .52):
    hmin = min(U, 1 - U) ** 2
    tab[f'U={U}'] = {f'SD(q)={s}': math.ceil(Z2 * (hmin - s * s) / (s * s)) for s in (.10, .07, .05, .03, .02)}
out['4_distribution_free_table'] = tab
print(json.dumps(out, indent=1, ensure_ascii=False))
