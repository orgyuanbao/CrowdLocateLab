"""Independent numerical witnesses; prose proofs live in the research context."""
import numpy as np

def verify(proposition='all'):
    results={}
    if proposition in ('all','self_weight_bias'):
        rng=np.random.default_rng(783221);d=20.;s=.35;n=200000
        y=d*np.exp(rng.normal(-s*s/2,s,n))
        empirical_weighted=float(np.mean(1/y)/np.mean(1/y**2))
        empirical_log=float(np.exp(np.mean(np.log(y))+s*s/2))
        results['self_weight_bias']={
          'status':'analytical result with independent Monte Carlo witness',
          'assumptions':'Scalar range q>0; Y=d*exp(eps-s²/2); eps~N(0,s²); weight=Y^-2; no coordinate/additive error.',
          'formula':'argmin_q E[(q-Y)^2/Y^2] = d*exp(-2*s^2)',
          'log_formula':'argmin_q E[(log(q)-log(Y)-s²/2)^2] = d',
          'derivation':'E[Y^k]=d^k exp((k²-k)s²/2). Setting derivative to zero gives E[Y^-1]/E[Y^-2].',
          'truth_m':d,'noise_log_sd':s,'mc_samples':n,
          'analytical_weighted_optimum_m':float(d*np.exp(-2*s*s)),
          'mc_weighted_optimum_m':empirical_weighted,'mc_corrected_log_optimum_m':empirical_log,
          'limitation':'Not a theorem about the full nonlinear multianchor problem or its finite-sample optimizer.'}
    if proposition in ('all','mirror'):
        anchors=np.column_stack(([-7.,-2.,5.,-5.,7.,1.],np.zeros(6)))
        times=np.array([-8.,-6.,-4.,-2.,-1.,0.]);x=np.array([1.5,3.]);vel=np.array([.2,.1]);R=np.diag([1,-1])
        ranges=lambda a,b:np.linalg.norm(a+times[:,None]*b-anchors,axis=1)
        delta=np.abs(ranges(x,vel)-ranges(R@x,R@vel))
        relative=x+times[:,None]*vel-anchors;u=relative/np.linalg.norm(relative,axis=1)[:,None]
        j=np.column_stack((u,times[:,None]*u))
        results['mirror']={'status':'exact identity plus numerical witness','max_distance_difference_m':float(delta.max()),
          'position_separation_m':float(np.linalg.norm(x-R@x)),
          'anchors':anchors.tolist(),'times':times.tolist(),'position':x.tolist(),'velocity':vel.tolist(),
          'jacobian_rank':int(np.linalg.matrix_rank(j)),'singular_values':np.linalg.svd(j,compute_uv=False).tolist(),
          'proof':'R a_i=a_i and ||Rz||=||z||; thus ranges of (x,v) and (Rx,Rv) coincide for all times.',
          'limitation':'A full-rank local Jacobian does not remove global reflection ambiguity.'}
    if proposition in ('all','conformal'):
        results['conformal']={'status':'theorem conditions checked; empirical coverage requires frozen benchmark',
          'statement':'For m exchangeable calibration cases and one new case, frozen estimator and positive frozen scale g, use k=ceil((m+1)*(1-alpha)); q=S_(k), S=error/g, q=inf if k>m. Then marginal coverage is >=1-alpha.',
          'assumptions':['estimator frozen before calibration','calibration and test cases exchangeable','true positions available for calibration only'],
          'not_implied':['conditional coverage for each scenario','coverage after data-dependent rejection','coverage under distribution shift']}
    if not results:raise ValueError('Unknown proposition')
    return results
