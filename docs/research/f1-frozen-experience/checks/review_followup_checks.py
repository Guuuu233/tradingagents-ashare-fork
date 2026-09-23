import math,json
import numpy as np
from statistics import NormalDist
from pathlib import Path
z=NormalDist().inv_cdf(.975); power_z=z+NormalDist().inv_cdf(.8)
# Exact marginal false-positive probability of original independent-row normal test.
m,G,a=20,40,4.5
logb=lambda x,y:math.lgamma(x)+math.lgamma(y)-math.lgamma(x+y)
q=np.array([math.exp(math.lgamma(m+1)-math.lgamma(k+1)-math.lgamma(m-k+1)+logb(k+a,m-k+a)-logb(a,a)) for k in range(m+1)])
q/=q.sum(); pmf=np.array([1.])
for _ in range(G): pmf=np.convolve(pmf,q)
N=m*G;K=np.arange(N+1,dtype=float)
with np.errstate(divide='ignore',invalid='ignore'): stat=np.abs((N-2*K)*np.sqrt(N-1)/(2*np.sqrt(K*(N-K))))
exact=float(pmf[stat>z].sum())
normal_approx=2*(1-NormalDist().cdf(z/math.sqrt(1+(m-1)*.1)))
# Independent seed and count-level simulator; 200,000 simulated experiments.
rng=np.random.default_rng(20260923);R=200000;naive=cluster=0
for start in range(0,R,2000):
 theta=rng.beta(a,a,size=(2000,G))
 successes=rng.binomial(m,theta)
 total=successes.sum(axis=1)
 t=np.abs((N-2*total)*np.sqrt(N-1)/(2*np.sqrt(total*(N-total))))
 clustermeans=.1*(1-2*successes/m)
 tc=np.abs(clustermeans.mean(axis=1)/(clustermeans.std(axis=1,ddof=1)/np.sqrt(G)))
 naive+=int((t>z).sum());cluster+=int((tc>2.02269092003676).sum())
# Fixed-probability counterexample, truly independent outcomes.
pa,pb,ptrue=.5,.51,.6;u=pb-pa
delta=u*(pa+pb-2*ptrue);sd=2*abs(u)*math.sqrt(ptrue*(1-ptrue))
neff=math.ceil((power_z*sd/abs(delta))**2)
# Verify sharp no-label endpoints and generic bound for arbitrary probabilities.
rng2=np.random.default_rng(42);p=rng2.random(100000);q2=rng2.random(100000)
i0=p*p-q2*q2;i1=(1-p)**2-(1-q2)**2
assert np.all(np.maximum(abs(i0),abs(i1))<=2*abs(q2-p)+1e-14)
# Correct three-valued S may carry material predictive information.
# Three equal-sized groups with true probabilities .2,.5,.8; U=.5.
ps=np.array([.2,.5,.8]);brierU=float(np.mean(ps*(1-ps)+(ps-.5)**2));brierS=float(np.mean(ps*(1-ps)))
# DEFF table with original effective N and consistent ceiling.
de=[{'m':mm, 'values':[math.ceil(785*(1+(mm-1)*rho)) for rho in [.005,.01,.02,.03]]} for mm in [25,200,800]]
out={'original_test_exact_null_false_positive':exact,'normal_DEFF_approximation':normal_approx,'independent_simulation':{'seed':20260923,'R':R,'naive':naive/R,'cluster':cluster/R},'small_shift_counterexample':{'pA':pa,'pB':pb,'true_probability':ptrue,'delta_B_minus_A':delta,'sd_loss_difference':sd,'independent_n_approx':neff,'max_possible_improvement_no_labels':max(pa*pa-pb*pb,(1-pa)**2-(1-pb)**2)},'three_bin_counterexample':{'probabilities':ps.tolist(),'brier_U':brierU,'brier_S':brierS,'improvement':brierU-brierS},'DEFF_table':de,'rounding':{'continuous_neff':(power_z*.1/.01)**2,'continuous_total':(power_z*.1/.01)**2*1.72/.85,'sequential_ceiling':math.ceil(math.ceil((power_z*.1/.01)**2)*1.72/.85),'final_ceiling':math.ceil((power_z*.1/.01)**2*1.72/.85)}}
Path('work/review_followup_checks.json').write_text(json.dumps(out,indent=2));print(json.dumps(out,indent=2))
