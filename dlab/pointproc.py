#!/usr/bin/env python
# -*- coding: iso-8859-1 -*-
"""

module with functions for processing point processes (i.e. vector of event times)

A lot of this code is ported from chronux, using my own
implementations of multitaper spectrograms.

1/2008, CDM
"""

import numpy as nx
import scipy.fftpack as sfft
from scipy.interpolate import interp1d
from signalproc import getfgrid, dpsschk, mtfft
from datautils import nextpow2
from linalg import outer, gemm

def coherencycpt(S, tl, **kwargs):
    """
    Compute the coherency between a continuous process and a point process.

    [C,phi,S12,S1,S2,f] = coherencycpt(S, tl1, **kwargs)
    Input:
              S         continuous data set
              tl        iterable of vectors with event times
    Optional keyword arguments:
              tapers    precalculated tapers from dpss, or the number of tapers to use
                        Default 5
              mtm_p     time-bandwidth parameter for dpss (ignored if tapers is precalced)
                        Default 3
              pad       padding factor for the FFT:
	                   -1 corresponds to no padding,
                           0 corresponds to padding to the next highest power of 2 etc.
                           e.g. For N = 500, if PAD = -1, we do not pad; if PAD = 0, we pad the FFT
                           to 512 points, if pad=1, we pad to 1024 points etc.
                           Defaults to 0.
              Fs        sampling frequency. Default 1
              fpass     frequency band to be used in the calculation in the form
                        [fmin fmax]
                        Default all frequencies between 0 and Fs/2
              err       error calculation [1 p] - Theoretical error bars; [2 p] - Jackknife error bars
	                                  [0 p] or 0 - no error bars) - optional. Default 0.
              trialave  average over channels/trials when 1, don't average when 0) - optional. Default 0
	      fscorr    finite size corrections:
                        0 (don't use finite size corrections)
                        1 (use finite size corrections)
	                Defaults 0
	      tgrid     Time grid over which the tapers are to be calculated:
                        This can be a vector of time points, or a pair of endpoints.
                        By default, the support of the continous process is used
    """
    Fs = kwargs.get('Fs',1)
    fpass = kwargs.get('fpass',(0,Fs/2.))
    pad = kwargs.get('pad', 0)

    if S.ndim==1:
        S.shape = (S.size,1)
        
    N,C = S.shape
    if C != tl.nrepeats:
        if C==1:
            # tile data to match number of trials
            S = nx.tile(S,(1,tl.nrepeats))
            C = tl.nrepeats
        else:
            raise ValueError, "Trial dimensions of data do not match"
        
    
    if kwargs.has_key('tgrid'):
        t = kwargs['tgrid']
    else:
        dt = 1./Fs
        t = nx.arange(0,N*dt,dt)


    nfft = max(2**(nextpow2(N)+pad), N)
    f,findx = getfgrid(Fs,nfft,fpass)
    tapers = dpsschk(N, **kwargs)
    kwargs['tapers'] = tapers

    J1 = mtfft(S, **kwargs)[0]
    J2,Nsp,Msp = _mtfftpt(tl, tapers, nfft, t, f, findx)

    S12 = nx.squeeze(nx.mean(J1.conj() * J2,1))
    S1 =  nx.squeeze(nx.mean(J1.conj() * J1,1))
    S2 =  nx.squeeze(nx.mean(J2.conj() * J2,1))
    if kwargs.get('trialave',False):
        S12 = S12.mean(1)
        S1 = S1.mean(1)
        S2 = S2.mean(1)

    C12 = S12 / nx.sqrt(S1 * S2)
    C = nx.absolute(C12)
    phi = nx.angle(C12)

    return C,phi,S12,S1,S2,f

def mtspectrumpt(tl, **kwargs):
    """
    Multitaper spectrum from point process times

	[S,f,R,Serr]=mtspectrumpt(data, **kwargs)
	Input: 
	      tl        iterable of vectors with event times
        Optional keyword arguments:
              tapers    precalculated tapers from dpss, or the number of tapers to use
                        Default 5
              mtm_p     time-bandwidth parameter for dpss (ignored if tapers is precalced)
                        Default 3
              pad       padding factor for the FFT:
	                   -1 corresponds to no padding,
                           0 corresponds to padding to the next highest power of 2 etc.
                           e.g. For N = 500, if PAD = -1, we do not pad; if PAD = 0, we pad the FFT
                           to 512 points, if pad=1, we pad to 1024 points etc.
                           Defaults to 0.
              Fs        sampling frequency. Default 1
              fpass     frequency band to be used in the calculation in the form
                        [fmin fmax]
                        Default all frequencies between 0 and Fs/2
              err       error calculation [1 p] - Theoretical error bars; [2 p] - Jackknife error bars
	                                  [0 p] or 0 - no error bars) - optional. Default 0.
              trialave  average over channels/trials when 1, don't average when 0) - optional. Default 0
	      fscorr    finite size corrections:
                        0 (don't use finite size corrections)
                        1 (use finite size corrections)
	                Defaults 0
	      tgrid     Time grid over which the tapers are to be calculated:
                        This can be a vector of time points, or a pair of endpoints.
                        By default, the max and min spike time are used to define the grid.                        

	Output:
	      S       (spectrum with dimensions frequency x channels/trials if trialave=0; dimension frequency if trialave=1)
	      f       (frequencies)
	      R       (rate)
	      Serr    (error bars) - only if err(1)>=1
    """
    Fs = kwargs.get('Fs',1)
    J,Msp,Nsp,f = mtfftpt(tl, **kwargs)
    S = nx.mean(nx.real(J.conj() * J), 1)
    if kwargs.get('trialave',False):
        S = S.mean(1)
        Msp = Msp.mean()

    R = Msp * Fs

    return S,f,R


def mtfftpt(tl, **kwargs):
    """
    Multitaper fourier transform from point process times

	[J,Msp,Nsp,f]=mtfftpt(data, **kwargs)
	Input: 
	      tl        iterable of vectors with event times
        Optional keyword arguments:
              tapers    precalculated tapers from dpss, or the number of tapers to use
                        Default 5
              mtm_p     time-bandwidth parameter for dpss (ignored if tapers is precalced)
                        Default 3
              pad       padding factor for the FFT:
	                   -1 corresponds to no padding,
                           0 corresponds to padding to the next highest power of 2 etc.
                           e.g. For N = 500, if PAD = -1, we do not pad; if PAD = 0, we pad the FFT
                           to 512 points, if pad=1, we pad to 1024 points etc.
                           Defaults to 0.
              Fs        sampling frequency. Default 1
              fpass     frequency band to be used in the calculation in the form
                        [fmin fmax]
                        Default all frequencies between 0 and Fs/2
	      tgrid     Time grid over which the tapers are to be calculated:
                        This can be a vector of time points, or a pair of endpoints.
                        By default, the max and min spike time are used to define the grid.

	Output:
	      J       (complex spectrum with dimensions freq x chan x tapers)
              Msp     (mean spikes per sample in each trial)
              Nsp     (total spike count in each trial)
    """    
    Fs = kwargs.get('Fs',1)
    fpass = kwargs.get('fpass',(0,Fs/2.))
    pad = kwargs.get('pad', 0)

    t = kwargs.get('tgrid',tl.range)
    if len(t)==2:
        mintime, maxtime = t
        dt = 1./Fs
        t = nx.arange(mintime-dt,maxtime+2*dt,dt)

    N = len(t)
    nfft = max(2**(nextpow2(N)+pad), N)
    f,findx = getfgrid(Fs,nfft,fpass)
    tapers = dpsschk(N, **kwargs)

    J,Msp,Nsp = _mtfftpt(tl, tapers, nfft, t, f, findx)

    return J,Msp,Nsp,f

def _mtfftpt(tl, tapers, nfft, t, f, findx):
    """
	Multi-taper fourier transform for point process given as times
        (helper function)

	Usage:
	(J,Msp,Nsp) = _mtfftpt (data,tapers,nfft,t,f,findx) - all arguments required
	Input: 
	      tl          (iterable of vectors with event times)
	      tapers      (precalculated tapers from dpss) 
	      nfft        (length of padded data) 
	      t           (time points at which tapers are calculated)
	      f           (frequencies of evaluation)
	      findx       (index corresponding to frequencies f) 
	Output:
	      J (fft in form frequency index x taper index x channels/trials)
	      Msp (number of spikes per sample in each channel)
	      Nsp (number of spikes in each channel)    
    """

    C = len(tl)
    N,K = tapers.shape
    nfreq = f.size

    assert nfreq == findx.size, "Frequency information inconsistent sizes"

    H = sfft.fft(tapers, nfft, axis=0)
    H = H[findx,:]
    w = 2 * f * nx.pi
    Nsp = nx.zeros(C, dtype='i')
    Msp = nx.zeros(C)
    J = nx.zeros((nfreq,K,C), dtype='D')

    chan = 0
    interpolator = interp1d(t, tapers.T)
    for events in tl:
        idx = (events >= t.min()) & (events <= t.max())
        ev = events[idx]
        Nsp[chan] = ev.size
        Msp[chan] = 1. * ev.size / t.size
        if ev.size > 0:
            data_proj = interpolator(ev)
            Y = nx.exp(outer(-1j*w, ev - t[0]))
            J[:,:,chan] = gemm(Y, data_proj, trans_b=1) - H * Msp[chan]
        else:
            J[:,:,chan] = 0
        chan += 1

    return J,Msp,Nsp

if __name__=="__main__":

    import os
    from dlab import toelis

    tl = toelis.readfile(os.path.join(os.environ['HOME'],
                                      'z1/acute_data/st319/20070812/cell_14_1_2',
                                      'cell_14_1_2_Bn.toe_lis'))

    C_stim,phi,S12,S1,S2,f = coherencycpt(tl.histogram(binsize=1,onset=0,offset=1000)[1],
                                          tl.subrange(0,1000),
                                          trialave=1)
    C_base,phi,S12,S1,S2,f = coherencycpt(tl.histogram(binsize=1,onset=-1000,offset=0)[1],
                                          tl.subrange(-1000,0,adjust=True),
                                          trialave=1)