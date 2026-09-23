# -*- coding: utf-8 -*-
"""第二轮：GPT 提出“无标签终止应看置信上界而非点估计”。量化两种终止规则的误杀率与可用性。
统计量：每个机会 X_i=(B1-A1)(B2-A2)，E[X_i]=稳定改变量的平方；阈值=(delta_min/2)^2。"""
import math, json
import numpy as np
rng = np.random.default_rng(20260924)
DMIN = .005; THR = (DMIN / 2) ** 2          # 6.25e-6，即稳定改变量 RMS 0.25pp
T95 = {19: 1.729132812, 49: 1.676550893}    # t 分布单侧 95% 分位数（表值）

def rules(n, m_s, tau_s, sA, sB, reps=200_000):
    s = m_s + tau_s * rng.standard_normal((reps, n))
    A1, A2 = [sA * rng.standard_normal((reps, n)) for _ in range(2)]
    B1, B2 = [s + sB * rng.standard_normal((reps, n)) for _ in range(2)]
    X = (B1 - A1) * (B2 - A2)
    m = X.mean(1); ucb = m + T95[n - 1] * X.std(1, ddof=1) / math.sqrt(n)
    return dict(kill_point=float((m < THR).mean()), kill_ucb=float((ucb < THR).mean()),
                median_ucb_as_RMS_pp=100 * math.sqrt(max(float(np.median(ucb)), 0)))

out = {}
for n in (20, 50):
    out[f'n={n}'] = {
        # 真零效应：理想上应该判死
        **{f'null_noise={s}': rules(n, 0, 0, s, s) for s in (.001, .002, .005, .01, .03)},
        # 真有 RMS 3.2pp 稳定改变量：不应判死
        'real_shift_RMS3.2pp_noise.03/.05': rules(n, .01, .03, .03, .05),
        'real_shift_RMS1pp_noise.01': rules(n, .005, .0087, .01, .01),
    }
print(json.dumps(out, indent=1, ensure_ascii=False))
