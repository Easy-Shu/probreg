from __future__ import print_function
from __future__ import division
import abc
from collections import namedtuple
import six
import numpy as np
import open3d as o3
from . import transformation as tf
from . import gauss_transform as gt
from . import math_utils as mu


EstepResult = namedtuple('EstepResult', ['pt1', 'p1', 'px', 'n_p'])
MstepResult = namedtuple('MstepResult', ['transformation', 'sigma2', 'q'])


@six.add_metaclass(abc.ABCMeta)
class CoherentPointDrift():
    """Coherent Point Drigt algorithm.
    This is a abstract class.
    Based on this class, it is inherited by rigid, affine, nonrigid classes
    according to the type of transformation.
    In this class, Estimation step in EM algorithm is implemented and
    Maximazation step is implemented in the inherited classes.
    """
    def __init__(self, source=None):
        self._source = source
        self._tf_type = None

    def set_source(self, source):
        self._source = source

    @abc.abstractmethod
    def _initialize(self, target):
        return MstepResult(None, None, None)

    def expectation_step(self, t_source, target, sigma2, w=0.0):
        """
        Expectation step
        """
        assert t_source.ndim == 2 and target.ndim == 2, "source and target must have 2 dimensions."
        ndim = t_source.shape[1]
        h = np.sqrt(2.0 * sigma2)
        c = (2.0 * np.pi * sigma2) ** (ndim * 0.5)
        c *= w / (1.0 - w) * t_source.shape[0] / target.shape[0]
        gtrans = gt.GaussTransform(t_source, h)
        kt1 = gtrans.compute(target)
        kt1[kt1==0] = np.finfo(np.float32).eps
        a = 1.0 / (kt1 + c)
        pt1 = 1.0 - c * a
        gtrans = gt.GaussTransform(target, h)
        p1 = gtrans.compute(t_source, a)
        px = gtrans.compute(t_source, np.tile(a, (ndim, 1)) * target.T).T
        return EstepResult(pt1, p1, px, np.sum(p1))

    def maximization_step(self, target, estep_res, sigma2_p=None):
        return self._maximization_step(self._source, target, estep_res, sigma2_p)

    @abc.abstractstaticmethod
    def _maximization_step(source, target, estep_res, sigma2_p=None):
        return None

    def registration(self, target, w=0.0,
                     max_iteration=50, tol=0.001):
        assert not self._tf_type is None, "transformation type is None."
        res = self._initialize(target)
        q = res.q
        for _ in range(max_iteration):
            t_source = res.transformation.transform(self._source)
            estep_res = self.expectation_step(t_source, target, res.sigma2, w)
            res = self.maximization_step(target, estep_res, res.sigma2)
            if abs(res.q - q) < tol:
                break
            q = res.q
        return res

class RigidCPD(CoherentPointDrift):
    def __init__(self, source=None):
        super(RigidCPD, self).__init__(source)
        self._tf_type = tf.RigidTransformation

    def _initialize(self, target):
        ndim = self._source.shape[1]
        sigma2 = mu.msn_all_combination(self._source, target)
        q = 1.0 + target.shape[0] * ndim * 0.5 * np.log(sigma2)
        return MstepResult(self._tf_type(np.identity(ndim), np.zeros(ndim)), sigma2, q)

    @staticmethod
    def _maximization_step(source, target, estep_res, sigma2_p=None):
        pt1, p1, px, n_p = estep_res
        ndim = source.shape[1]
        mu_x = np.sum(px, axis=0) / n_p
        mu_y = np.dot(source.T, p1) / n_p
        target_hat = target - mu_x
        source_hat = source - mu_y
        a = np.dot(px.T, source_hat) - np.outer(mu_x, np.dot(p1.T, source_hat))
        u, _, vh = np.linalg.svd(a, full_matrices=True)
        c = np.ones(ndim)
        c[-1] = np.linalg.det(np.dot(u, vh))
        rot = np.dot(u * c, vh)
        tr_atr = np.trace(np.dot(a.T, rot))
        tr_yp1y = np.trace(np.dot(source_hat.T * p1, source_hat))
        scale = tr_atr / tr_yp1y
        t = mu_x - scale * np.dot(rot, mu_y)
        tr_xp1x = np.trace(np.dot(target_hat.T * pt1, target_hat))
        sigma2 = (tr_xp1x - scale * tr_atr) / (n_p * ndim)
        q = (tr_xp1x - 2.0 * scale * tr_atr + (scale ** 2) * tr_yp1y) / (2.0 * sigma2)
        q += ndim * n_p * 0.5 * np.log(sigma2)
        return MstepResult(tf.RigidTransformation(rot, t, scale), sigma2, q)


class AffineCPD(CoherentPointDrift):
    def __init__(self, source=None):
        super(AffineCPD, self).__init__(source)
        self._tf_type = tf.AffineTransformation

    def _initialize(self, target):
        ndim = self._source.shape[1]
        sigma2 = mu.msn_all_combination(self._source, target)
        q = 1.0 + target.shape[0] * ndim * 0.5 * np.log(sigma2)
        return MstepResult(self._tf_type(np.identity(ndim), np.zeros(ndim)),
                           sigma2, q)

    @staticmethod
    def _maximization_step(source, target, estep_res, sigma2_p=None):
        pt1, p1, px, n_p = estep_res
        ndim = source.shape[1]
        mu_x = np.sum(px, axis=0) / n_p
        mu_y = np.dot(source.T, p1) / n_p
        target_hat = target - mu_x
        source_hat = source - mu_y
        a = np.dot(px.T, source_hat) - np.outer(mu_x, np.dot(p1.T, source_hat))
        yp1y = np.dot(source_hat.T * p1, source_hat)
        b = np.linalg.solve(yp1y.T, a.T).T
        t = mu_x - np.dot(b, mu_y)
        tr_xp1x = np.trace(np.dot(target_hat.T * pt1, target_hat))
        tr_xpyb = np.trace(np.dot(a, b.T))
        sigma2 = (tr_xp1x - tr_xpyb) / (n_p * ndim)
        tr_ab = np.trace(np.dot(a, b.T))
        q = (tr_xp1x - 2 * tr_ab + tr_xpyb) / (2.0 * sigma2)
        q += ndim * n_p * 0.5 * np.log(sigma2)
        return MstepResult(tf.AffineTransformation(b, t), sigma2, q)


class NonRigidCPD(CoherentPointDrift):
    def __init__(self, source=None, beta=2.0, lmd=2.0):
        super(NonRigidCPD, self).__init__(source)
        self._tf_type = tf.NonRigidTransformation
        self._beta = beta
        self._lmd = lmd
        self._g = None
        if not self._source is None:
            self._g = mu.gaussian_kernel(self._source, self._beta)

    def set_source(self, source):
        self._source = source
        self._g = mu.gaussian_kernel(self._source, self._beta)

    def maximization_step(self, target, estep_res, sigma2_p=None):
        return self._maximization_step(self._source, target, estep_res,
                                       sigma2_p, self._g, self._lmd)

    def _initialize(self, target):
        ndim = self._source.shape[1]
        sigma2 = mu.msn_all_combination(self._source, target)
        q = 1.0 + target.shape[0] * ndim * 0.5 * np.log(sigma2)
        return MstepResult(self._tf_type(self._g, np.zeros(self._source.shape[0])),
                           sigma2, q)

    @staticmethod
    def _maximization_step(source, target, estep_res, sigma2_p, g, lmd):
        pt1, p1, px, n_p = estep_res
        ndim = source.shape[1]
        dp1_inv = np.diag(1.0 / p1)
        w = np.linalg.solve(g + lmd * sigma2_p * dp1_inv, dp1_inv * px - source)
        t = source + np.dot(g, w)
        tr_xp1x = np.trace(np.dot(target.T * pt1, target))
        tr_pxtt = np.trace(np.dot(px.T, t))
        tr_ttp1t = np.trace(np.dot(t.T * p1, t))
        sigma2 = (tr_xp1x - 2.0 * tr_pxtt + tr_ttp1t) / (n_p * ndim)
        return MstepResult(tf.NonRigidTransformation(g, w), sigma2, sigma2)


def registration_cpd(source, target, tf_type_name='rigid',
                     w=0.0, max_iteration=100, tol=0.001, **kargs):
    if tf_type_name == 'rigid':
        cpd = RigidCPD(np.asarray(source.points), **kargs)
    elif tf_type_name == 'affine':
        cpd = AffineCPD(np.asarray(source.points), **kargs)
    elif tf_type_name == 'nonrigid':
        cpd = NonRigidCPD(np.asarray(source.points), **kargs)
    else:
        raise ValueError('Unknown transform type %s' % tf_type_name)
    return cpd.registration(np.asarray(target.points),
                            w, max_iteration, tol)