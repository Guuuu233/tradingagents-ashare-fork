import numpy as np, math, json
from statistics import NormalDist
from pathlib import Path
rng=np.random.default_rng(20260922)
z=NormalDist().inv_cdf(.975)+NormalDist().inv_cdf(.8)
rows=[]
for sd,delta in [(.10,.005),(.10,.01),(.10,.02),(.15,.01)]:
 n=math.ceil((z*sd/delta)**2)
 rows.append(dict(sd=sd,delta=delta,n_independent=n,n_discovered_example=math.ceil(n*1.72/.85)))
# Null experiment: both p=.45 and p=.55 are equally accurate for marginal Y~Bernoulli(.5).
# Within-cluster Y is beta-binomial correlated; clusters are independent.
R,G,m,rho=10000,40,20,.10
alpha=(1/rho-1)/2
naive=cluster=0
means=[]
for start in range(0,R,250):
 b=min(250,R-start)
 theta=rng.beta(alpha,alpha,size=(b,G,1))
 y=(rng.random((b,G,m))<theta).astype(float)
 d=(.55-y)**2-(.45-y)**2
 mean=d.mean(axis=(1,2));means.extend(mean.tolist())
 se=d.reshape(b,-1).std(axis=1,ddof=1)/np.sqrt(G*m)
 se_cluster=d.mean(axis=2).std(axis=1,ddof=1)/np.sqrt(G)
 naive+=np.count_nonzero(np.abs(mean/se)>1.959963984540054)
 cluster+=np.count_nonzero(np.abs(mean/se_cluster)>2.02269092003676)
# algebra consistency checks
pa=rng.random(10000);pb=rng.random(10000);y=rng.integers(0,2,10000)
d=(pb-y)**2-(pa-y)**2
identity_error=float(np.max(np.abs(d-((pb-pa)**2+2*(pb-pa)*(pa-y)))))
ps=rng.random((1000,3)); yy=rng.integers(0,2,1000)
lhs=((ps-yy[:,None])**2).mean(axis=1)
rhs=(ps.mean(axis=1)-yy)**2+((ps-ps.mean(axis=1)[:,None])**2).mean(axis=1)
ensemble_error=float(np.max(np.abs(lhs-rhs)))
# Shrinkage example and mixture coefficient optimum on a finite grid.
shrink=(7+20*.52)/(10+20)
v=pb-pa;r=y-pa;w=float(np.clip(np.dot(v,r)/np.dot(v,v),0,1))
loss=lambda x:float(np.mean((pa+x*v-y)**2))
assert loss(w)<=min(loss(x) for x in np.linspace(0,1,1001))+1e-12
out=dict(seed=20260922,power_z=z,power_table=rows,simulation=dict(replications=R,clusters=G,cluster_size=m,intracluster_correlation=rho,true_delta=0,pA=.45,pB=.55,nominal_alpha=.05,naive_rejection=naive/R,cluster_rejection=cluster/R,mcse_naive=math.sqrt((naive/R)*(1-naive/R)/R),mcse_cluster=math.sqrt((cluster/R)*(1-cluster/R)/R)),checks=dict(brier_identity_maxerror=identity_error,ensemble_identity_maxerror=ensemble_error,shrinkage_example=shrink,mixture_weight=w,mixture_grid_check='passed'))
Path(__file__).with_name('original_results_rerun.json').write_text(json.dumps(out,indent=2))
print(json.dumps(out,indent=2))
