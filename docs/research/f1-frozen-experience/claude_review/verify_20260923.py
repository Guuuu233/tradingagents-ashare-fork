# -*- coding: utf-8 -*-
"""Claude 复核（2026-09-23）：Astra / GPT / Opus 第二轮讨论中的数值主张。
只用 numpy + 标准库。所有数字都是情景或反例，不是 TradingAgents 实测。"""
import math, json
import numpy as np
from statistics import NormalDist

rng = np.random.default_rng(20260923)
Z = NormalDist().inv_cdf(0.975) + NormalDist().inv_cdf(0.80)   # 2.8016
Z2 = Z * Z
R = {}

def t_ppf(p, df):
    """t 分布分位数：Simpson 积分 + 二分，避免依赖 scipy。"""
    c = math.exp(math.lgamma((df + 1) / 2) - math.lgamma(df / 2)) / math.sqrt(df * math.pi)
    def cdf(x):
        xs = np.linspace(0, x, 40001)
        f = c * (1 + xs * xs / df) ** (-(df + 1) / 2)
        h = xs[1] - xs[0]
        return 0.5 + h / 3 * (f[0] + f[-1] + 4 * f[1:-1:2].sum() + 2 * f[2:-1:2].sum())
    lo, hi = 0.0, 200.0
    for _ in range(80):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if cdf(mid) < p else (lo, mid)
    return (lo + hi) / 2

# ---------- 1. Astra 反例与 Opus 表（常数平移、零生成噪声、独立事件）----------
def const_shift(a, u, q):
    Ed = u * u + 2 * u * (a - q)
    sd = 2 * abs(u) * math.sqrt(q * (1 - q))
    return dict(E_d=Ed, SD_d=sd, n=Z2 * sd * sd / Ed ** 2)
R['1_const_shift_a.5_q.6'] = {str(u): const_shift(.5, u, .6) for u in [.001, .01, .02, .05, .10]}

# ---------- 2. 普适下界：任何 B，零噪声、独立事件、对零检验 80%/双侧5% ----------
#   Var(d) >= 4*c0*E[u^2]（c0 = min q(1-q)），|E d| <= 2*R*D - R^2（R=RMS u, D=RMS(q-a)）
#   => n >= Z^2 * c0 / D^2 ，与 |u| 大小无关，只取决于 A 离真实概率有多远
ratios = []
for _ in range(20000):
    k = int(rng.integers(1, 6)); w = rng.dirichlet(np.ones(k))
    q = rng.uniform(.4, .6, k); a = rng.uniform(.3, .7, k)
    if rng.random() < .5:                     # 一半试验用“方向正确”的平移，逼近下界
        u = (q - a) * rng.uniform(0.001, 1.5)
    else:
        u = rng.normal(0, rng.uniform(.001, .2), k)
    u = np.clip(a + u, 0, 1) - a
    Edx = u * u + 2 * u * (a - q); Ed = float(np.sum(w * Edx))
    if Ed >= 0: continue
    Vd = float(np.sum(w * 4 * u * u * q * (1 - q)) + np.sum(w * Edx ** 2) - Ed ** 2)
    n = Z2 * Vd / Ed ** 2
    bound = Z2 * float(np.min(q * (1 - q))) / float(np.sum(w * (q - a) ** 2))
    ratios.append(n / bound)
R['2_lower_bound_check'] = dict(trials=len(ratios), min_n_over_bound=min(ratios))
R['2_bound_table_c0=.25'] = {f'RMS(q-a)={D}': math.ceil(Z2 * .25 / D ** 2) for D in [.1, .07, .05, .03, .02]}

# ---------- 3. 生成噪声把 Astra 的 209 放大多少（两臂各自独立噪声 sigma）----------
def noisy_ce(sig, a=.5, u=.01, q=.6, n_mc=4_000_000):
    Er, Er2 = a - q, q * (1 - a) ** 2 + (1 - q) * a ** 2   # r = a - Y
    Ed = u * u + 2 * u * Er
    Vd = 4*u*u*sig**2 + 4*sig**4 + 4*Er2*(u*u + 2*sig**2) - 4*Er**2*u*u + 8*u*Er*sig**2
    Y = rng.random(n_mc) < q
    d = (a + u + sig * rng.standard_normal(n_mc) - Y) ** 2 - (a + sig * rng.standard_normal(n_mc) - Y) ** 2
    return dict(E_d=Ed, SD_d=math.sqrt(Vd), n=math.ceil(Z2 * Vd / Ed ** 2),
                mc_E_d=float(d.mean()), mc_SD_d=float(d.std()))
R['3_counterexample_with_noise'] = {f'sigma={s}': noisy_ce(s) for s in [0, .01, .02, .03, .05]}

# ---------- 4. Opus“噪声税”：两臂都有噪声时 E[d] 的噪声项是 sB^2 - sA^2 ----------
def tax(sA, sB, qv=(.35, .65), a=.5, n_mc=4_000_000):
    q = rng.choice(qv, n_mc); Y = rng.random(n_mc) < q
    pA = a + sA * rng.standard_normal(n_mc); pB = q + sB * rng.standard_normal(n_mc)  # B 系统部分 = 真值（神谕）
    d = (pB - Y) ** 2 - (pA - Y) ** 2
    dU = (pB - Y) ** 2 - (a - Y) ** 2                                                  # B 对确定性 U=0.5
    return dict(sig_eps2=sA**2 + sB**2, E_d_BvsA=float(d.mean()), E_d_BvsU=float(dU.mean()),
                theory_BvsA=-.0225 + sB**2 - sA**2, theory_BvsU=-.0225 + sB**2)
R['4_noise_tax'] = {
    'no_noise': tax(0, 0),
    'opus_model_B_only_sigma_eps2=.006': tax(0, math.sqrt(.006)),
    'both_arms_equal_sigma_eps2=.006': tax(math.sqrt(.003), math.sqrt(.003)),
    'both_arms_B_noisier': tax(.03, .07),
    'zero_effect_equal_noise': None,
}
q = rng.random(4_000_000) < .5
pA = .5 + .1 * rng.standard_normal(q.size); pB = .5 + .1 * rng.standard_normal(q.size)
R['4_noise_tax']['zero_effect_equal_noise'] = dict(sigma=.1, E_d=float(((pB - q) ** 2 - (pA - q) ** 2).mean()))

# ---------- 5. 20 个机会×每臂 2 次：三种“稳定改变量”估计量 ----------
def pilot(n=20, m_s=.01, tau_s=.03, sA=.03, sB=.05, reps=200_000):
    s = m_s + tau_s * rng.standard_normal((reps, n))
    mu = .5 + .05 * rng.standard_normal((reps, n))
    A1, A2 = [mu + sA * rng.standard_normal((reps, n)) for _ in range(2)]
    B1, B2 = [mu + s + sB * rng.standard_normal((reps, n)) for _ in range(2)]
    u1, u2 = B1 - A1, B2 - A2
    est = {
        'opus_Var(B1-A1)-Var(A1-A2)  [目标 Var(s)=%.5f]' % tau_s**2:
            u1.var(axis=1, ddof=1) - (A1 - A2).var(axis=1, ddof=1),
        'cov(u1,u2)                  [目标 Var(s)=%.5f]' % tau_s**2:
            ((u1 - u1.mean(1, keepdims=True)) * (u2 - u2.mean(1, keepdims=True))).sum(1) / (n - 1),
        'astra_mean[(B1-A1)^2-.5(A1-A2)^2-.5(B1-B2)^2] [目标 E s^2=%.5f]' % (m_s**2 + tau_s**2):
            ((B1 - A1) ** 2 - .5 * (A1 - A2) ** 2 - .5 * (B1 - B2) ** 2).mean(1),
        'cross_mean[(B1-A1)(B2-A2)]  [目标 E s^2=%.5f]' % (m_s**2 + tau_s**2):
            (u1 * u2).mean(1),
    }
    out = {k: dict(mean=float(v.mean()), sd=float(v.std()), p05=float(np.quantile(v, .05)),
                   p95=float(np.quantile(v, .95)), P_le_0=float((v <= 0).mean())) for k, v in est.items()}
    # 可复现性检验：u1 与 u2 的相关，单侧 5%
    r = ((u1 - u1.mean(1, keepdims=True)) * (u2 - u2.mean(1, keepdims=True))).sum(1) / np.sqrt(
        ((u1 - u1.mean(1, keepdims=True)) ** 2).sum(1) * ((u2 - u2.mean(1, keepdims=True)) ** 2).sum(1))
    tcrit = t_ppf(.95, n - 2)
    out['reliability_true'] = tau_s**2 / (tau_s**2 + sA**2 + sB**2)
    out['power_reliability_test'] = float((r * np.sqrt((n - 2) / (1 - r * r)) > tcrit).mean())
    return out
R['5_pilot_B_noisier'] = pilot()
R['5_pilot_equal_noise'] = pilot(sA=.04, sB=.04)
zz = NormalDist().inv_cdf(.95) + NormalDist().inv_cdf(.8)
R['5_min_reliability_80pct_power_n20_onesided'] = math.tanh(zz / math.sqrt(20 - 3))

# ---------- 6. 无标签上界 C-bar 在“零效应、纯噪声”下是否会判死 ----------
def cbar(sig, K=1, n_mc=400_000):
    mu = rng.uniform(.4, .6, n_mc)
    a = np.clip(mu + sig / math.sqrt(K) * rng.standard_normal(n_mc), 0, 1)
    b = np.clip(mu + sig / math.sqrt(K) * rng.standard_normal(n_mc), 0, 1)
    return float(np.maximum(a * a - b * b, (1 - a) ** 2 - (1 - b) ** 2).mean())
R['6_Cbar_pure_noise_delta_min=.005'] = {f'sigma={s},K={K}': cbar(s, K) for s in [.005, .01, .03, .05] for K in [1, 2]}
# Opus 的近似 C≈2|u|max(a,1-a) vs 精确（a=0.8, u=+0.01 向较近端点移动）
a_, u_ = .8, .01
R['6_opus_approx_check'] = dict(exact=max(a_**2 - (a_+u_)**2, (1-a_)**2 - (1-a_-u_)**2), opus=2*u_*max(a_, 1-a_), first_order=2*u_*(1-a_))

# ---------- 7. 季内共同冲击传入 d 的比例：DEFF = 1 + rho_Y (m c - 1), c = (mean u)^2/mean(u^2) ----------
def icc(pattern, lam=.1, m=50, G=40000):
    f = rng.standard_normal((G, 1)); e = rng.standard_normal((G, m))
    Y = (math.sqrt(lam) * f + math.sqrt(1 - lam) * e) > 0
    sgn = np.where(np.arange(m) % 2 == 0, 1., -1.)
    u = {'coherent': np.full(m, .03), 'balanced': .03 * sgn, 'partial': .015 + .026 * sgn}[pattern]
    d = u * u + 2 * u * (.5 - Y)
    deff = m * d.mean(1).var() / d.var(0).mean()
    rhoY = 2 / math.pi * math.asin(lam)
    c = u.mean() ** 2 / (u * u).mean()
    return dict(rho_Y=rhoY, c=float(c), deff_sim=float(deff), deff_formula=1 + rhoY * (m * c - 1))
R['7_icc_transmission_m50'] = {p: icc(p) for p in ['coherent', 'partial', 'balanced']}

# ---------- 8. 神谕 B（b=q）要证明“改善 >= delta_min=.005”需要多少独立配对 ----------
tab = {}
for t2 in [.0025, .005, .0075, .01, .02]:
    Vd = t2 - 4 * t2 * t2          # q = .5 ± tau, a = .5, b = q
    tab[f'tau2={t2}'] = dict(n_vs_zero=math.ceil(Z2 * Vd / t2 ** 2),
                             n_vs_dmin=(math.ceil(Z2 * Vd / (t2 - .005) ** 2) if t2 > .005 else 'impossible'))
qq = .5 + .1 * np.where(rng.random(4_000_000) < .5, 1, -1); YY = rng.random(qq.size) < qq
dd = (qq - YY) ** 2 - (.5 - YY) ** 2
tab['mc_check_tau2=.01'] = dict(E_d=float(dd.mean()), SD_d=float(dd.std()), theory_SD=math.sqrt(.01 - 4e-4))
R['8_oracle_B'] = tab

# ---------- 9. 少簇：Ibragimov-Mueller（按季估计再做 t(G-1)）要求的跨季 均值/SD ----------
R['9_IM_required_mean_over_sd'] = {f'G={G}': dict(t975=t_ppf(.975, G - 1), mean_over_sd=t_ppf(.975, G - 1) / math.sqrt(G))
                                   for G in [2, 3, 4, 5, 8, 10]}

# ---------- 10. Opus 噪声表 sigma=0 行（E d=-0.01433, SD=0.0050）是否可能出自近五五开的世界 ----------
best = 9.0
for _ in range(200000):
    k = int(rng.integers(1, 4)); w = rng.dirichlet(np.ones(k))
    q = rng.uniform(.2, .8, k); a = rng.uniform(0, 1, k)
    u = np.clip(a + (q - a) * rng.uniform(1e-4, 1, k), 0, 1) - a
    Edx = u * u + 2 * u * (a - q); Ed = float(np.sum(w * Edx))
    if Ed >= 0: continue
    Vd = float(np.sum(w * 4 * u * u * q * (1 - q)) + np.sum(w * Edx ** 2) - Ed ** 2)
    best = min(best, math.sqrt(Vd) / -Ed)
R['10_min_SD_over_absEd_q_in_[.2,.8]'] = dict(random_search_min=best, analytic_limit=.5, opus_row=.0050 / .01433)

print(json.dumps(R, indent=1, ensure_ascii=False, default=float))
