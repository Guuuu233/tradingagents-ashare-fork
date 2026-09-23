# -*- coding: utf-8 -*-
"""第三轮：(1) 无标签 futility 何时可启用的一般式；(2) 胜过已校准 U 的样本下限只取决于目标离散度 Var(q)；
(3) 按日均值聚合 vs 事件等权比值估计的方差差异。"""
import math, json
import numpy as np
from statistics import NormalDist
rng = np.random.default_rng(20260925)
Z2 = (NormalDist().inv_cdf(.975) + NormalDist().inv_cdf(.8)) ** 2
T95 = {19: 1.729132812, 49: 1.676550893, 199: 1.652546746}
out = {}

# (1) 零效应、两臂等噪声 sigma、每臂 R 次重复、n 个机会；统计量为逐机会重复间交叉乘积的 U 统计量。
#     中位置信上界 = theta^2 时的“盈亏平衡噪声”：sigma* = theta * sqrt( sqrt(n)*sqrt(R(R-1)/2) / (2 t) )
def sigma_star(theta, n, R): return theta * math.sqrt(math.sqrt(n) * math.sqrt(R * (R - 1) / 2) / (2 * T95[n - 1]))
def sim_median_ucb(sig, n, R, reps=40000):
    D = (sig * rng.standard_normal((reps, n, R))) - (sig * rng.standard_normal((reps, n, R)))   # B_r - A_r
    S = D.sum(2); X = (S * S - (D * D).sum(2)) / (R * (R - 1))                                 # 所有重复对的平均交叉乘积
    ucb = X.mean(1) + T95[n - 1] * X.std(1, ddof=1) / math.sqrt(n)
    return float(np.median(ucb))
th = .0025
out['1_breakeven_sigma_pp_theta=0.25pp'] = {f'n={n},R={R}': round(100 * sigma_star(th, n, R), 3) for n in (20, 50, 200) for R in (2, 4, 10)}
out['1_breakeven_sigma_pp_theta=0.50pp'] = {f'n={n},R={R}': round(100 * sigma_star(.005, n, R), 3) for n in (20, 50, 200) for R in (2, 4, 10)}
out['1_sim_check'] = {f'n={n},R={R}': dict(median_ucb_over_theta2=sim_median_ucb(sigma_star(th, n, R), n, R) / th ** 2)
                      for n, R in ((20, 2), (50, 4), (20, 10))}

# (2) 任何（无噪声）预测 B 对已校准常数 U=E[q]：n >= Z^2 * c0 / Var(q)
ratios = []
for _ in range(20000):
    k = int(rng.integers(2, 7)); w = rng.dirichlet(np.ones(k)); q = rng.uniform(.4, .6, k)
    U = float(np.sum(w * q)); u = (q - U) * rng.uniform(.001, 1.5) if rng.random() < .5 else rng.normal(0, .1, k)
    Edx = u * u + 2 * u * (U - q); Ed = float(np.sum(w * Edx))
    if Ed >= 0: continue
    Vd = float(np.sum(w * 4 * u * u * q * (1 - q)) + np.sum(w * Edx ** 2) - Ed ** 2)
    varq = float(np.sum(w * (q - U) ** 2)); ratios.append((Z2 * Vd / Ed ** 2) / (Z2 * float(np.min(q * (1 - q))) / varq))
out['2_beat_calibrated_U_bound'] = dict(trials=len(ratios), min_n_over_bound=min(ratios),
                                        table_c0_025={f'SD(q)={s}': math.ceil(Z2 * .25 / s ** 2) for s in (.1, .07, .05, .03, .02)})

# (3) 日历：5 个高峰日各 150 个事件 + 25 个稀疏日各 2 个事件；独立同方差 d
N = np.array([150] * 5 + [2] * 25); T = len(N)
var_event = 1 / N.sum(); var_day = float(np.sum(1 / N)) / T ** 2
out['3_day_vs_event_weighting'] = dict(events=int(N.sum()), days=T, var_ratio_day_over_event=var_day / var_event,
                                       weight_ratio_sparse_event_vs_peak_event=150 / 2)
print(json.dumps(out, indent=1, ensure_ascii=False))
