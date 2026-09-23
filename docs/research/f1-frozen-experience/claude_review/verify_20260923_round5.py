# -*- coding: utf-8 -*-
"""第五轮：(1) 复核 GPT 的两点世界数字；(2) 下限是 B 的下确界，U=0.5 时对任意 q 分布精确；
(3) B、对照带生成噪声时下限仍成立；(4) 要求改善 >= delta_min（H3 型）的下限 7.85(m^2-V)V/(V-δ)^2 及可达性；
(5) 对照为比 U 更准的 C*（q∈[0.3,0.7]）时的对应下限。精确矩计算，不用蒙特卡洛。"""
import math, json
import numpy as np
from statistics import NormalDist
rng = np.random.default_rng(20260927)
Z2 = (NormalDist().inv_cdf(.975) + NormalDist().inv_cdf(.8)) ** 2

def moments(w, q, mu_b, mu_c, sb=0.0, sc=0.0):
    """B=mu_b+sb*N, C=mu_c+sc*N，两者噪声相互独立且与 Y 独立；d=(B-Y)^2-(C-Y)^2，返回 E[d]、Var(d)。"""
    Ed = Ed2 = 0.0
    for y, py in ((1.0, q), (0.0, 1 - q)):
        eb, ec = mu_b - y, mu_c - y
        b2, b4 = eb**2 + sb**2, eb**4 + 6*eb**2*sb**2 + 3*sb**4
        c2, c4 = ec**2 + sc**2, ec**4 + 6*ec**2*sc**2 + 3*sc**4
        Ed += np.sum(w * py * (b2 - c2)); Ed2 += np.sum(w * py * (b4 - 2*b2*c2 + c4))
    return float(Ed), float(Ed2 - Ed**2)

def n_req(Ed, Vd, delta=0.0):
    I = -Ed
    return Z2 * Vd / (I - delta) ** 2 if I > delta else math.inf

def world(sym=False, lo=0.0, hi=1.0):
    k = int(rng.integers(1, 6)); w = rng.dirichlet(np.ones(k))
    q = rng.uniform(lo, hi, k) if rng.random() < .5 else np.clip(.5 + rng.normal(0, rng.uniform(.01, .3), k), lo, hi)
    if sym: w, q = np.concatenate([w, w]) / 2, np.concatenate([q, 1 - q])
    return w, q

out = {}
# ---- (1) GPT 两点世界：q∈{0.4,0.6}，U=0.5 ----
w2, q2 = np.array([.5, .5]), np.array([.4, .6])
out['1_two_point'] = {
    'oracle_H1': n_req(*moments(w2, q2, q2, .5)),
    'p=.499/.501_H1': n_req(*moments(w2, q2, np.array([.499, .501]), .5)),
    'p=.49999/.50001_H1': n_req(*moments(w2, q2, np.array([.49999, .50001]), .5)),
    'floor_H1': Z2 * (.25 - .01) / .01,
    'oracle_H3_d.005': n_req(*moments(w2, q2, q2, .5), .005),
    'p=.499/.501_improvement': -moments(w2, q2, np.array([.499, .501]), .5)[0],
    'floor_H3_d.005': Z2 * (.25 - .01) * .01 / (.01 - .005) ** 2,
}
g = np.arange(.30, .70001, .0005); bl, bh = np.meshgrid(g, g, indexing='ij')
Ed = Ed2 = 0.0
for b, qq in ((bl, .4), (bh, .6)):
    d1, d0 = (b - 1) ** 2 - .25, b ** 2 - .25
    Ed = Ed + .5 * (qq * d1 + (1 - qq) * d0); Ed2 = Ed2 + .5 * (qq * d1 ** 2 + (1 - qq) * d0 ** 2)
I = -Ed; nH3 = np.where(I > .005, Z2 * (Ed2 - Ed ** 2) / np.maximum(I - .005, 1e-12) ** 2, np.inf)
j = np.unravel_index(np.argmin(nH3), nH3.shape)
out['1_two_point']['best_B_H3_grid'] = dict(n=float(nH3[j]), b_lo=float(bl[j]), b_hi=float(bh[j]))
we, qe = np.array([.02, .96, .02]), np.array([0., .5, 1.])
out['1_extreme_world'] = dict(oracle_H1=n_req(*moments(we, qe, qe, .5)), oracle_H3_d005=n_req(*moments(we, qe, qe, .5), .005))

# ---- (2)(3)(4) 对照为已校准 U，B 任意（可带噪声）----
r_inf, r_floor, r_step, r_exact, r_h3 = [], [], [], [], {.002: [], .005: [], .01: []}
for t in range(60000):
    w, q = world(sym=(t % 2 == 0))
    U = float(np.sum(w * q)); V = float(np.sum(w * (q - U) ** 2)); m2 = min(U, 1 - U) ** 2
    if V < 1e-5 or m2 - V < 1e-6: continue          # m2=V 时下限为 0（q 只取 0/1 且 U=0.5），无意义
    h = (q - U) ** 2 + q * (1 - q); K = float(np.sum(w * (q - U) ** 2 / h)); winf = Z2 * (1 - K) / K
    floor = Z2 * (m2 - V) / V
    if t % 2 == 0: r_exact.append(winf / floor)                       # 对称世界 U=0.5
    mode = rng.random(); sb = rng.uniform(0, .08) if rng.random() < .5 else 0.0
    mu = (U + (q - U) * rng.uniform(.001, 2)) if mode < .4 else (rng.uniform(0, 1, len(q)) if mode < .7 else U + rng.normal(0, .05, len(q)))
    Edx, Vdx = moments(w, q, mu, U, sb)
    n = n_req(Edx, Vdx)
    if math.isfinite(n): r_inf.append(n / winf); r_floor.append(n / floor)
    for dl in r_h3:
        nh = n_req(Edx, Vdx, dl)
        if math.isfinite(nh): r_h3[dl].append(nh / (Z2 * (m2 - V) * V / (V - dl) ** 2))
    v = (q - U) / h                                                    # 下确界方向，小步长
    r_step.append(n_req(*moments(w, q, U + 1e-4 * v / np.max(np.abs(v)), U)) / winf)
out['2_calibrated_U'] = dict(
    trials=len(r_inf), min_n_over_world_inf=min(r_inf), min_n_over_shape_free_floor=min(r_floor),
    small_step_max_ratio=max(r_step), U05_world_inf_over_floor_range=[min(r_exact), max(r_exact)],
    H3_min_ratio={str(k): (min(v) if v else None) for k, v in r_h3.items()}, H3_trials={str(k): len(v) for k, v in r_h3.items()})

# ---- (5) 对照为比 U 更准的 C*（可带噪声），q∈[0.3,0.7] ----
r1, r3 = [], []
for t in range(60000):
    w, q = world(lo=.3, hi=.7)
    U = float(np.sum(w * q)); V = float(np.sum(w * (q - U) ** 2))
    if V < 1e-5: continue
    lam = rng.uniform(0, 1.9); room = V - (1 - lam) ** 2 * V
    sc = math.sqrt(max(room, 0) * rng.uniform(0, 1)); muc = U + lam * (q - U)   # 保证 E[(C-q)^2] <= V
    sb = rng.uniform(0, .08) if rng.random() < .5 else 0.0
    mu = muc + (q - muc) * rng.uniform(.001, 2) if rng.random() < .5 else rng.uniform(0, 1, len(q))
    Edx, Vdx = moments(w, q, mu, muc, sb, sc)
    n = n_req(Edx, Vdx)
    if math.isfinite(n): r1.append(n / (Z2 * (.21 - V) / V))
    nh = n_req(Edx, Vdx, .005)
    if math.isfinite(nh) and V > .005: r3.append(nh / (Z2 * (.21 - V) * V / (V - .005) ** 2))
out['5_better_than_U_comparator_q_in_.3_.7'] = dict(H1_trials=len(r1), H1_min_ratio=min(r1), H3_trials=len(r3), H3_min_ratio=min(r3))

# ---- 表 ----
sds = (.15, .12, .10, .08, .07, .05, .03, .02)
def fl(U, s, dl=0.0):
    V = s * s; m2 = min(U, 1 - U) ** 2
    return math.ceil(Z2 * (m2 - V) * V / (V - dl) ** 2) if V > dl else 'impossible'
out['table'] = {'H1_U.5': [fl(.5, s) for s in sds], 'H1_U.52': [fl(.52, s) for s in sds],
                'H3_U.5_d.005': [fl(.5, s, .005) for s in sds], 'SD(q)': list(sds)}
print(json.dumps(out, indent=1, ensure_ascii=False, default=float))
