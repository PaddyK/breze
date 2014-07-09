# -*- coding: utf-8 -*-

"""Module for learning models with stochastic gradient variational Bayes (SGVB).

The method has been introduced in parallel by [SGVB]_ and [DLGM]_. The general
idea is to optimize a variational upper bound on the negative log-likelihood of
the data with stochastic gradient descent. The user is urged to review these
papers before using the models.


Methodological review
---------------------

We will give a short review for notation here. We consider the problem of
estimating a model for the data density :math:`p(x)`, where we assume it to be
driven by a set of latent variables :math:`z`. The data negative log likelihood
can then be bounded from above

.. math::
   -\\log p(x) \\le {\\text{KL}}(q(z|x)|p(z)) - \mathbb{E}_{z \\sim q}[\\log p(x|z)].

We will refer to :math:`q(z)` as the recognition model or approxiamte posterior.
:math:`p(x|z)` is the generating model. :math:`p(z)` is the prior over the
latent variables.
The first term is the Kullback-Leibler divergence between the approximate
posterior and the prior.
The second term is the expected negative log-likelihood of the data given the
recognition model.

Training of the model is performed by stochastic gradient descent. In practice,
we will select a mini batch of the data for which we can obtain :math:`q(z|x)`.
This can directly be used to calculate the first term. By sampling from that
model and putting it through the generating model we can approximate the second
term.


References
----------

.. [SGVB] Kingma, Diederik P., and Max Welling. "Auto-encoding variational bayes." arXiv preprint arXiv:1312.6114 (2013).
.. [DLGM] Rezende, Danilo Jimenez, Shakir Mohamed, and Daan Wierstra. "Stochastic Back-propagation and Variational Inference in Deep Latent Gaussian Models." arXiv preprint arXiv:1401.4082 (2014).
"""

# TODO assumption classes
# TODO rename denoise to map denoise
# TODO fix estimation of nll
# TODO rename recurrent models to the ones used in the paper
# TODO make function for missing value imputation

import theano
import theano.tensor as T
import theano.tensor.nnet
import numpy as np

from breze.arch.component.common import supervised_loss
from breze.arch.component.distributions.mvn import logpdf
from breze.arch.component.misc import inter_gauss_kl
from breze.arch.component.transfer import sigmoid, diag_gauss
from breze.arch.component.varprop.loss import diag_gaussian_nll as diag_gauss_nll
from breze.arch.component.loss import bern_ces
from breze.arch.model import sgvb
from breze.arch.model.neural import mlp
from breze.arch.model.varprop import brnn as vpbrnn
from breze.arch.model.varprop import rnn as vprnn
from breze.arch.model.rnn import rnn
from breze.arch.util import ParameterSet, Model
from breze.learn.base import (
    UnsupervisedBrezeWrapperBase, TransformBrezeWrapperMixin,
    ReconstructBrezeWrapperMixin)
from breze.learn.utils import theano_floatx


# TODO find a better home for the following functions.

def assert_no_time(X):
    if X.ndim == 2:
        return X
    if X.ndim != 3:
        raise ValueError('ndim must be 2 or 3, but it is %i' % X.ndim)
    return X.reshape((-1, X.shape[2]))


def recover_time(X, time_steps):
    return X.reshape((time_steps, -1, X.shape[1]))


def normal_logpdf(xs, means, vrs):
    residual = xs - means
    divisor = 2 * vrs
    logz = -(vrs * 2 * np.pi) ** 0.5
    return -(residual ** 2 / divisor) - logz


# Taken from osdfutils.
def logsumexp(array, axis):
    """Compute the log of a sum of exps of ``array`` along some ``axis``.

    If _axis_ is not zero, caller must transform result in suitable shape."""
    arr = np.rollaxis(array, axis)
    axis_max = np.max(arr, axis=0)
    return axis_max + np.log(np.sum(np.exp(arr - axis_max), axis=0))


# TODO document
def estimate_nll(X, f_nll_z, f_nll_x_given_z, f_nll_z_given_x,
                f_sample_z_given_x, n_samples):
    d = np.empty((n_samples, X.shape[0]))
    for i in range(n_samples):
        Z = f_sample_z_given_x(X)
        loga = -f_nll_z(Z)
        logb = -f_nll_x_given_z(X, Z)
        logc = -f_nll_z_given_x(Z, X)
        d[i] = loga + logb - logc

    ll = logsumexp(d, 0) - np.log(n_samples)
    return -ll


class Assumptions(object):

    def sample_latents(self, stt, rng):
        """Given the output statistis of the recognition model, return samples
        from that distribution."""
        # TODO docstring
        raise NotImplemented()

    def kl_recog_prior(self, stt):
        """Given the output statistics of the recogition model, return the KL
        divergence to the prior."""
        # TODO docstring
        raise NotImplemented()

    def nll_prior(self, X):
        """Given some X, calculate the negative log likelihood of that X under
        the prior."""
        # TODO docstring
        raise NotImplemented()

    def nll_recog_model(self, Z, stt):
        """Given the output statistics of the recognition model, return the
        probability of latent variables Z."""
        # TODO docstring
        raise NotImplemented()

    def nll_gen_model(self, X, stt):
        """Given the output statistics of the generating model, return the
        probability of observed variables X."""
        # TODO docstring
        raise NotImplemented()

    def statify_latent(self, X):
        """Given values X, transform them into valid sufficient statistics for
        the latent distribution."""
        # TODO docstring
        raise NotImplemented()

    def statify_visible(self, X):
        """Given values X, transform them into valid sufficient statistics for
        the visible distribution."""
        # TODO docstring
        raise NotImplemented()


class DiagGaussLatentAssumption(object):

    def statify_latent(self, X):
        return diag_gauss(X)

    def nll_recog_model(self, Z, stt):
        return diag_gauss_nll(Z, stt)

    def kl_recog_prior(self, stt):
        if stt.ndim == 3:
            stt_flat = stt.reshape((-1, stt.shape[2]))
        else:
            stt_flat = stt

        mean, var = stt_flat[:, :stt_flat.shape[1] // 2], stt_flat[:, stt_flat.shape[1] // 2:]
        return inter_gauss_kl(mean, var)

    def nll_prior(self, X):
        X_flat = X.flatten()
        nll = normal_logpdf(X_flat, T.zeros_like(X_flat), T.ones_like(X_flat))
        return nll.reshape(X.shape)

    def latent_layer_size(self, n_latents):
        """Return the cardinality of the sufficient statistics given we want to
        model ``n_latents`` variables.

        For example, a diagonal Gaussian needs two sufficient statistics for a
        single random variable, the mean and the variance. A Bernoulli only
        needs one, which is the probability of it being 0 or 1."""
        return n_latents * 2

    def sample_latents(self, stt, rng):
        stt_flat = assert_no_time(stt)
        n_latent = stt_flat.shape[1] // 2
        latent_mean = stt_flat[:, :n_latent]
        latent_var = stt_flat[:, n_latent:]
        noise = rng.normal(size=latent_mean.shape)
        sample = latent_mean + T.sqrt(latent_var + 1e-8) * noise
        if stt.ndim == 3:
            return recover_time(sample, stt.shape[0])
        else:
            return sample


class DiagGaussVisibleAssumption(object):

    def statify_visible(self, X):
        return diag_gauss(X)

    def nll_gen_model(self, X, stt):
        return diag_gauss_nll(X, stt)

    def visible_layer_size(self, n_latents):
        """Return the cardinality of the sufficient statistics given we want to
        model ``n_latents`` variables.

        For example, a diagonal Gaussian needs two sufficient statistics for a
        single random variable, the mean and the variance. A Bernoulli only
        needs one, which is the probability of it being 0 or 1."""
        return n_latents * 2


class BernoulliVisibleAssumption(object):

    def statify_visible(self, X):
        return sigmoid(X)

    def nll_gen_model(self, X, stt):
        return bern_ces(X, stt)

    def visible_layer_size(self, n_visibles):
        """Return the cardinality of the sufficient statistics given we want to
        model ``n_visibles`` variables.

        For example, a diagonal Gaussian needs two sufficient statistics for a
        single random variable, the mean and the variance. A Bernoulli only
        needs one, which is the probability of it being 0 or 1."""
        return n_visibles


class VariationalAutoEncoder(Model, UnsupervisedBrezeWrapperBase,
                             TransformBrezeWrapperMixin,
                             ReconstructBrezeWrapperMixin):
    """Class representing a variational auto encoder.

    Described in [SGVB]_.

    Attributes
    ----------

    See parameters of ``__init__``.
    """

    transform_expr_name = 'latent'
    shortcut = None

    def __init__(self, n_inpt, n_hiddens_recog, n_latent, n_hiddens_gen,
                 recog_transfers, gen_transfers,
                 assumptions,
                 imp_weight=False,
                 batch_size=None, optimizer='rprop',
                 max_iter=1000, verbose=False):
        """Create a VariationalAutoEncoder object.

        Parameters
        ----------

        n_inpt : int
            Dimensionality of the visibles.

        n_hiddens_recog : list of integers
            List containing the sizes of the hidden layers of the recognition
            model.

        n_latent : int
            Dimensionality of the latent dimension.

        n_hiddens_gen : list of integers
            List containing the sizes of the hidden layers of the generating
            model.

        recog_transfers : list
            List containing the transfer functions for the hidden layers of the
            recognition model.

        gen_transfers : list
            List containing the transfer cuntions for the hidden layers of the
            generating model.

        assumptions : Assumptions object
            Object encoding the assumptions about the data.

        imp_weight : boolean
            Flag indicating whether importance weights are used.

        batch_size : int
            Size of each mini batch during training.

        optimizer: string or pair
            Can be either a string or a pair. In any case,
            ``climin.util.optimizer`` is used to construct an optimizer. In the
            case of a string, the string is used as an identifier for the
            optimizer which is then instantiated with default arguments. If a
            pair, expected to be (`identifier`, `kwargs`) for more fine control
            of the optimizer.

        max_iter : integer
            Maximum number of optimization iterations to perform.

        verbose : boolean
            Flag indicating whether to print out information during fitting.
        """
        self.n_inpt = n_inpt
        self.n_hiddens_recog = n_hiddens_recog
        self.n_latent = n_latent
        self.n_hiddens_gen = n_hiddens_gen
        self.recog_transfers = recog_transfers
        self.gen_transfers = gen_transfers

        self.assumptions = assumptions

        self.imp_weight = imp_weight
        self.batch_size = batch_size
        self.optimizer = optimizer
        self.max_iter = max_iter
        self.verbose = verbose

        super(VariationalAutoEncoder, self).__init__()

        self.f_latent_mean = None
        self.f_latent_mean_var = None
        self.f_out_from_sample = None
        self.f_rec_loss_of_sample = None
        self.f_mvn_logpdf = None
        self.f_estimate_nll = None

    def _init_pars(self):
        spec = {
            'recog': self._recog_par_spec(),
            'gen': self._gen_par_spec(),
        }
        self.parameters = ParameterSet(**spec)
        self.parameters.data[:] = np.random.standard_normal(
            self.parameters.data.shape).astype(theano.config.floatX)

    def _recog_par_spec(self):
        """Return the specification of the recognition model."""
        n_code_units = self.assumptions.latent_layer_size(self.n_latent)
        return mlp.parameters(self.n_inpt, self.n_hiddens_recog,
                              n_code_units)

    def _recog_exprs(self, inpt):
        """Return the exprssions of the recognition model."""
        P = self.parameters.recog

        n_layers = len(self.n_hiddens_recog)
        hidden_to_hiddens = [getattr(P, 'hidden_to_hidden_%i' % i)
                             for i in range(n_layers - 1)]
        hidden_biases = [getattr(P, 'hidden_bias_%i' % i)
                         for i in range(n_layers)]

        exprs = mlp.exprs(
            inpt, P.in_to_hidden, hidden_to_hiddens, P.hidden_to_out,
            hidden_biases, P.out_bias,
            self.recog_transfers, self.assumptions.statify_latent)

        return exprs

    def _gen_par_spec(self):
        """Return the parameter specification of the generating model."""
        n_output = self.assumptions.visible_layer_size(self.n_inpt)
        return mlp.parameters(self.n_latent, self.n_hiddens_recog,
                              n_output)

    def _gen_exprs(self, inpt):
        """Return the expression of the generating model."""
        P = self.parameters.gen

        n_layers = len(self.n_hiddens_recog)
        hidden_to_hiddens = [getattr(P, 'hidden_to_hidden_%i' % i)
                             for i in range(n_layers - 1)]
        hidden_biases = [getattr(P, 'hidden_bias_%i' % i)
                         for i in range(n_layers)]

        exprs = mlp.exprs(
            inpt, P.in_to_hidden, hidden_to_hiddens, P.hidden_to_out,
            hidden_biases, P.out_bias,
            self.recog_transfers, self.assumptions.statify_visible)

        return exprs

    def _make_start_exprs(self):
        exprs = {
            'inpt': T.matrix('inpt')
        }
        exprs['inpt'].tag.test_value, = theano_floatx(np.ones((3, self.n_inpt)))
        if self.imp_weight:
            exprs['imp_weight'] = T.matrix('imp_weight')
            exprs['imp_weight'].tag.test_value, = theano_floatx(np.ones((3, 1)))

        return exprs

    def _make_kl_loss(self):
        n_dim = self.exprs['inpt'].ndim
        kl_coord_wise = self.assumptions.kl_recog_prior(self.exprs['latent'])

        if self.imp_weight:
            kl_coord_wise *= self._fix_imp_weight(n_dim)
        kl_sample_wise = kl_coord_wise.sum(axis=n_dim - 1)
        kl = kl_sample_wise.mean()

        return {
            'kl': kl,
            'kl_coord_wise': kl_coord_wise,
            'kl_sample_wise': kl_sample_wise,
        }

    def _fix_imp_weight(self, ndim):
        # For the VAE, the importance weights cannot be coordinate
        # wise, but have to be sample wise. In numpy, we can just use an
        # array where the last dimensionality is 1 and the broadcasting
        # rules will make this work as one would expect. In theano's case,
        # we have to be explicit about whether broadcasting along that
        # dimension is allowed though, since normal tensor3s do not allow
        # it. The following code achieves this.
        return T.addbroadcast(self.exprs['imp_weight'], ndim - 1)

    def _init_exprs(self):
        E = self.exprs = self._make_start_exprs()
        n_dim = E['inpt'].ndim

        # Make the expression of the model.
        E.update(sgvb.exprs(
            E['inpt'],
            self._recog_exprs, self._gen_exprs,
            self.assumptions.sample_latents,
            shortcut_key=self.shortcut))

        # TODO this is not going to work with variance propagation.
        imp_weight = False if not self.imp_weight else self._fix_imp_weight(n_dim)
        rec_loss = supervised_loss(
            E['inpt'], E['gen']['output'], self.assumptions.nll_gen_model,
            prefix='rec_', coord_axis=n_dim - 1, imp_weight=imp_weight)

        # Create the KL divergence part of the loss.
        kl_loss = self._make_kl_loss()

        E.update(rec_loss)
        E.update(kl_loss)

        E.update({
            'loss_sample_wise': E['kl_sample_wise'] + E['rec_loss_sample_wise'],
            'loss': E['kl'] + E['rec_loss'],
        })

    def _latent_mean(self, X):
        # TODO this is only necessary for a Gaussian assumption.
        if self.f_latent_mean is None:
            self.f_latent_mean = self.function(['inpt'], 'latent_mean')
        return self.f_latent_mean(X)

    def _output_from_sample(self, S):
        if self.f_out_from_sample is None:
            self.f_out_from_sample = self.function(['sample'], 'output')
        return self.f_out_from_sample(S)

    def _rec_loss_of_sample(self, X, S):
        if self.f_rec_loss_of_sample is None:
            self.f_rec_loss_of_sample = self.function(
                ['inpt', 'sample'], 'rec_loss_sample_wise')
        return self.f_rec_loss_of_sample(X, S)

    def _latent_mean_var(self, X):
        if self.f_latent_mean_var is None:
            self.f_latent_mean_var = self.function(
                ['inpt'], ['latent_mean', 'latent_var'])
        return self.f_latent_mean_var(X)

    def _mvn_logpdf(self, sample, mean, cov):
        if self.f_mvn_logpdf is None:
            s = T.matrix()
            m = T.vector()
            c = T.matrix()
            mvnlogpdf = logpdf(s, m, c)
            self.f_mvnlogpdf = theano.function([s, m, c], mvnlogpdf)
        return self.f_mvnlogpdf(sample, mean, cov)

    def denoise(self, X):
        """Denoise data from the input distribution.

        The denoising is done as follows. The recognition model is used to
        obtain the posterior of the latent variables. The mode of that is then
        fed through the generating model, of which the result is returned.

        Parameters
        ----------

        X : array_like
            Input to denoise.

        Returns
        -------

        Y : array_like
            Denoised input. Same shape as ``X``.
        """
        H = self._latent_mean(X)
        return self._output_from_sample(H)

    def estimate_nll(self, X, n_samples=10):
        """Return an estimate of the negative log-likelihood of ``X``.

        The estimate is obtained via importance sampling, as described in the
        appendix of [DLGM]_.

        Parameters
        ----------

        X : array_like
            Input to estimate the likelihood of.

        n_samples : int
            Number of samples for importance sampling. The more, the more
            reliable the estimator becomes.

        Returns
        -------

        nll : array_like
            Array of shape ``(n,)`` where each entry corresponds to the nll of
            corresponding sample in ``X``.
        """
        if self.f_estimate_nll is None:
            self.f_estimate_nll = self._make_f_estimate_nll()
        return self.f_estimate_nll(X, n_samples)

    def _make_f_estimate_nll(self):
        latent_sample = T.matrix()

        # Map a sample s to the prior log probability p(z)
        nll_z = self.assumptions.nll_prior(latent_sample).sum(axis=1)
        f_nll_z = theano.function([latent_sample], nll_z)

        # Map a given visible x and a sample z to the generating probability p(x|z).
        nll_x_given_z = self.assumptions.nll_gen_model(self.exprs['inpt'], self.exprs['output']).sum(axis=1)
        f_nll_x_given_z = self.function(['inpt', 'sample'], nll_x_given_z)

        # Map a given visible x and a sample z to the recognition probability q(z|x).
        nll_z_given_x = self.assumptions.nll_recog_model(
            latent_sample, self.exprs['latent']).sum(axis=1)
        f_nll_z_given_x = self.function([latent_sample, self.exprs['inpt']], nll_z_given_x)

        # Sample some z from q(z|x).
        f_sample_z_given_x = self.function(['inpt'], 'sample')

        def inner(X, n):
            return estimate_nll(X, f_nll_z, f_nll_x_given_z, f_nll_z_given_x, f_sample_z_given_x, n)

        return inner


class VariationalSequenceAE(VariationalAutoEncoder):

    def _recog_par_spec(self):
        """Return the specification of the recognition model."""
        n_code_units = self._layer_size_by_dist(
            self.n_latent, self.latent_posterior)
        spec = rnn.parameters(self.n_inpt, self.n_hiddens_recog,
                              n_code_units)
        return spec

    def _recog_exprs(self, inpt):
        """Return the exprssions of the recognition model."""
        P = self.parameters.recog

        print P.keys()
        n_layers = len(self.n_hiddens_recog)
        hidden_to_hiddens = [getattr(P, 'hidden_to_hidden_%i' % i)
                             for i in range(n_layers - 1)]
        hidden_biases = [getattr(P, 'hidden_bias_%i' % i)
                         for i in range(n_layers)]
        initial_hiddens = [getattr(P, 'initial_hiddens_%i' % i)
                           for i in range(n_layers)]
        recurrents = [getattr(P, 'recurrent_%i' % i)
                      for i in range(n_layers)]

        if self.latent_posterior == 'diag_gauss':
            out_transfer = diag_gauss
        else:
            raise ValueError('unknown latent posterior distribution:%s'
                             % self.latent_posterior)

        exprs = rnn.exprs(
            inpt, P.in_to_hidden, hidden_to_hiddens, P.hidden_to_out,
            hidden_biases, initial_hiddens, recurrents, P.out_bias,
            self.recog_transfers, out_transfer)

        return exprs

    def _gen_par_spec(self):
        """Return the parameter specification of the generating model."""
        n_output = self._layer_size_by_dist(self.n_inpt, self.visible)
        return rnn.parameters(self.n_latent, self.n_hiddens_recog,
                              n_output)

    def _gen_exprs(self, inpt):
        """Return the expression of the generating model."""
        P = self.parameters.gen

        n_layers = len(self.n_hiddens_gen)
        hidden_to_hiddens = [getattr(P, 'hidden_to_hidden_%i' % i)
                             for i in range(n_layers - 1)]
        recurrents = [getattr(P, 'recurrent_%i' % i)
                      for i in range(n_layers)]
        hidden_biases = [getattr(P, 'hidden_bias_%i' % i)
                         for i in range(n_layers)]
        initial_hiddens = [getattr(P, 'initial_hiddens_%i' % i)
                           for i in range(n_layers)]

        if self.visible == 'diag_gauss':
            out_transfer = diag_gauss
        elif self.visible == 'bern':
            out_transfer = sigmoid
        else:
            raise ValueError('unknown visible distribution: %s'
                             % self.latent_posterior)
        exprs = rnn.exprs(
            inpt, P.in_to_hidden, hidden_to_hiddens, P.hidden_to_out,
            hidden_biases, initial_hiddens, recurrents, P.out_bias,
            self.gen_transfers, out_transfer)

        return exprs

    def _make_start_exprs(self):
        exprs = {
            'inpt': T.tensor3('inpt')
        }
        exprs['inpt'].tag.test_value, = theano_floatx(np.ones((3, 2, self.n_inpt)))

        if self.imp_weight:
            exprs['imp_weight'] = T.tensor3('imp_weight')
            exprs['imp_weight'].tag.test_value, = theano_floatx(np.ones((3, 2, 1)))
        return exprs

    def _make_kl_loss(self):
        E = self.exprs
        n_dim = E['inpt'].ndim

        if self.latent_posterior == 'diag_gauss':
            output = E['recog']['output']
            n_output = output.shape[-1]
            # TODO there is probably a nicer way to do this.
            if n_dim == 3:
                E['latent_mean'] = output[:, :, :n_output // 2]
                E['latent_var'] = output[:, :, n_output // 2:]
            else:
                E['latent_mean'] = output[:, :n_output // 2]
                E['latent_var'] = output[:, n_output // 2:]
        else:
            raise ValueError('unknown latent posterior distribution:%s'
                             % self.latent_posterior)

        if self.latent_posterior == 'diag_gauss' and self.latent_prior == 'slow_white_gauss':
            d_latent_mean = E['latent_mean'][1:] - E['latent_mean'][:-1]
            d_latent_var = E['latent_var'][1:] + E['latent_var'][:-1]

            kl_first = inter_gauss_kl(E['latent_mean'][:1], E['latent_var'][:1])
            kl_diff = inter_gauss_kl(d_latent_mean, d_latent_var)
            kl_coord_wise = T.concatenate([kl_first, kl_diff])
        elif self.latent_posterior == 'diag_gauss' and self.latent_prior == 'white_gauss':
            kl_coord_wise = inter_gauss_kl(E['latent_mean'], E['latent_var'])
        else:
            raise ValueError(
                'unknown combination for latent_prior and latent_posterior:'
                ' %s, %s' % (self.latent_prior, self.latent_posterior))

        if self.imp_weight:
            kl_coord_wise *= self._fix_imp_weight(n_dim)
        kl_sample_wise = kl_coord_wise.sum(axis=n_dim - 1)
        kl = kl_sample_wise.mean()

        return {
            'kl': kl,
            'kl_coord_wise': kl_coord_wise,
            'kl_sample_wise': kl_sample_wise,
        }

    def _lp_h(self, latent):
        """Return the prior log probability of latent sequences."""
        h_0 = latent[0]
        d_h1_ht = latent[1:] - latent[:-1]

        lp_h_0 = normal_logpdf(
            h_0, np.zeros_like(h_0), np.ones_like(h_0)).sum(axis=1)
        lp_d_h_1_t = normal_logpdf(
            d_h1_ht, np.zeros_like(d_h1_ht), np.ones_like(d_h1_ht)).sum(axis=2)

        return np.concatenate([lp_h_0[np.newaxis], lp_d_h_1_t])

    def _lq_h_given_v(self, latent, visible):
        """Return the log propability of latent variables under the recognition
        model given the visibles."""
        mean, var = self._latent_mean_var(visible)
        lp = normal_logpdf(latent, mean, var)
        return lp.sum(axis=2)

    def _lp_v_given_s(self, visible, latent):
        """Return the log probability of the visibles given the latent
        variables."""
        return -self._rec_loss_of_sample(visible, latent)


class VariationalFDSequenceAE(VariationalSequenceAE):

    shortcut = None

    def _recog_par_spec(self):
        """Return the specification of the recognition model."""
        spec = vpbrnn.parameters(self.n_inpt, self.n_hiddens_recog,
                                 self.n_latent)

        spec['p_dropout'] = {
            'inpt': 1,
            'hiddens': [1 for _ in self.n_hiddens_recog],
            'hidden_to_out': 1,
        }

        return spec

    def _recog_exprs(self, inpt):
        """Return the exprssions of the recognition model."""
        P = self.parameters.recog

        n_layers = len(self.n_hiddens_recog)
        hidden_to_hiddens = [getattr(P, 'hidden_to_hidden_%i' % i)
                             for i in range(n_layers - 1)]
        hidden_biases = [getattr(P, 'hidden_bias_%i' % i)
                         for i in range(n_layers)]
        initial_hiddens_fwd = [getattr(P, 'initial_hiddens_fwd_%i' % i)
                               for i in range(n_layers)]
        initial_hiddens_bwd = [getattr(P, 'initial_hiddens_bwd_%i' % i)
                               for i in range(n_layers)]
        recurrents_fwd = [getattr(P, 'recurrent_fwd_%i' % i)
                          for i in range(n_layers)]
        recurrents_bwd = [getattr(P, 'recurrent_bwd_%i' % i)
                          for i in range(n_layers)]

        p_dropouts = (
            [P.p_dropout.inpt] + P.p_dropout.hiddens
            + [P.p_dropout.hidden_to_out])

        # Reparametrize to assert the rates lie in (0.025, 1-0.025).
        p_dropouts = [T.nnet.sigmoid(i) * 0.95 + 0.025 for i in p_dropouts]

        if self.latent_posterior == 'diag_gauss':
            out_transfer = 'identity'
        else:
            raise ValueError('unknown latent posterior distribution:%s'
                             % self.latent_posterior)

        exprs = vpbrnn.exprs(
            inpt, T.zeros_like(inpt), P.in_to_hidden, hidden_to_hiddens, P.hidden_to_out,
            hidden_biases, [1 for _ in hidden_biases],
            initial_hiddens_fwd, initial_hiddens_bwd,
            recurrents_fwd, recurrents_bwd,
            P.out_bias, 1, self.recog_transfers, out_transfer,
            p_dropouts=p_dropouts)

        return exprs


class VariationalFDFDSequenceAE(VariationalFDSequenceAE):

    def _gen_par_spec(self):
        """Return the parameter specification of the generating model."""
        n_output = self._layer_size_by_dist(self.n_inpt, self.visible)
        return vpbrnn.parameters(self.n_latent, self.n_hiddens_gen,
                                 n_output)

    def _gen_exprs(self, inpt):
        """Return the expression of the generating model."""
        P = self.parameters.gen

        n_layers = len(self.n_hiddens_gen)
        hidden_to_hiddens = [getattr(P, 'hidden_to_hidden_%i' % i)
                             for i in range(n_layers - 1)]
        recurrents_fwd = [getattr(P, 'recurrent_fwd_%i' % i)
                          for i in range(n_layers)]
        recurrents_bwd = [getattr(P, 'recurrent_bwd_%i' % i)
                          for i in range(n_layers)]
        hidden_biases = [getattr(P, 'hidden_bias_%i' % i)
                         for i in range(n_layers)]
        initial_hiddens_fwd = [getattr(P, 'initial_hiddens_fwd_%i' % i)
                               for i in range(n_layers)]
        initial_hiddens_bwd = [getattr(P, 'initial_hiddens_bwd_%i' % i)
                               for i in range(n_layers)]

        if self.visible == 'diag_gauss':
            out_transfer = 'identity'
        elif self.visible == 'bern':
            out_transfer = 'sigmoid'
        else:
            raise ValueError('unknown visible distribution: %s'
                             % self.latent_posterior)

        # TODO Need to crossvalidate at some point.
        p_dropouts = [0.1] + [0.1] * len(self.n_hiddens_gen) + [0.1]

        exprs = vpbrnn.exprs(
            inpt, T.zeros_like(inpt), P.in_to_hidden, hidden_to_hiddens, P.hidden_to_out,
            hidden_biases, [1 for b in hidden_biases],
            initial_hiddens_fwd, initial_hiddens_bwd,
            recurrents_fwd, recurrents_bwd,
            P.out_bias, 1, self.recog_transfers, out_transfer,
            p_dropouts=p_dropouts)
        exprs['output_uncut'] = exprs['output']

        # FD-RNNs have twice as many outputs as we care about here.
        exprs['output'] = exprs['output'][:, :, :self.n_inpt]

        return exprs


class VariationalOneStepPredictor(VariationalAutoEncoder):

    shortcut = 'shortcut'

    def _make_start_exprs(self):
        exprs = {
            'inpt': T.tensor3('inpt')
        }
        exprs['inpt'].tag.test_value, = theano_floatx(np.ones((3, 2, self.n_inpt)))

        if self.imp_weight:
            exprs['imp_weight'] = T.tensor3('imp_weight')
            exprs['imp_weight'].tag.test_value, = theano_floatx(np.ones((3, 2, 1)))
        return exprs

    def _recog_par_spec(self):
        """Return the parameter specification of the recognition model."""
        return rnn.parameters(self.n_inpt, self.n_hiddens_recog, self.n_latent)

    def _recog_exprs(self, inpt):
        """Return the exprssions of the recognition model."""
        P = self.parameters.recog

        n_layers = len(self.n_hiddens_recog)
        hidden_to_hiddens = [getattr(P, 'hidden_to_hidden_%i' % i)
                             for i in range(n_layers - 1)]
        hidden_biases = [getattr(P, 'hidden_bias_%i' % i)
                         for i in range(n_layers)]
        initial_hiddens = [getattr(P, 'initial_hiddens_%i' % i)
                           for i in range(n_layers)]
        recurrents = [getattr(P, 'recurrent_%i' % i)
                      for i in range(n_layers)]

        p_dropouts = (len(self.n_hiddens_recog) + 2) * [0.1]

        if self.latent_posterior == 'diag_gauss':
            out_transfer = 'identity'
        else:
            raise ValueError('unknown latent posterior distribution:%s'
                             % self.latent_posterior)

        exprs = vprnn.exprs(
            inpt, T.zeros_like(inpt), P.in_to_hidden, hidden_to_hiddens, P.hidden_to_out,
            hidden_biases, [1 for _ in hidden_biases],
            initial_hiddens, recurrents,
            P.out_bias, 1, self.recog_transfers, out_transfer,
            p_dropouts=p_dropouts)

        # TODO also integrate variance!
        last_hidden_layer = exprs['hidden_mean_%i' % (len(self.n_hiddens_recog) - 1)]
        shortcut = T.zeros_like(last_hidden_layer)
        T.set_subtensor(shortcut[1:], last_hidden_layer[:-1], inplace=True)
        exprs['shortcut'] = shortcut

        return exprs

    def _gen_par_spec(self):
        """Return the parameter specification of the generating model."""
        n_output = self._layer_size_by_dist(self.n_inpt, self.visible)
        return mlp.parameters(
            self.n_latent + self.n_hiddens_recog[-1], self.n_hiddens_recog,
            n_output)

    def _gen_exprs(self, inpt):
        inpt_flat = inpt.reshape((-1, inpt.shape[2]))
        exprs = super(VariationalOneStepPredictor, self)._gen_exprs(inpt_flat)
        exprs['output_flat'] = exprs['output']
        exprs['output'] = exprs['output_flat'].reshape(
            (inpt.shape[0], inpt.shape[1], -1))
        return exprs

    def _make_kl_loss(self):
        E = self.exprs
        n_dim = E['inpt'].ndim

        if self.latent_posterior == 'diag_gauss':
            output = E['recog']['output']
            n_output = output.shape[-1]
            # TODO there is probably a nicer way to do this.
            if n_dim == 3:
                E['latent_mean'] = output[:, :, :n_output // 2]
                E['latent_var'] = output[:, :, n_output // 2:]
            else:
                E['latent_mean'] = output[:, :n_output // 2]
                E['latent_var'] = output[:, n_output // 2:]
        else:
            raise ValueError('unknown latent posterior distribution:%s'
                             % self.latent_posterior)

        if self.latent_posterior == 'diag_gauss' and self.latent_prior == 'slow_white_gauss':
            d_latent_mean = E['latent_mean'][1:] - E['latent_mean'][:-1]
            d_latent_var = E['latent_var'][1:] + E['latent_var'][:-1]

            kl_first = inter_gauss_kl(E['latent_mean'][:1], E['latent_var'][:1])
            kl_diff = inter_gauss_kl(d_latent_mean, d_latent_var)
            kl_coord_wise = T.concatenate([kl_first, kl_diff])
        elif self.latent_posterior == 'diag_gauss' and self.latent_prior == 'white_gauss':
            kl_coord_wise = inter_gauss_kl(E['latent_mean'], E['latent_var'])
        else:
            raise ValueError(
                'unknown combination for latent_prior and latent_posterior:'
                ' %s, %s' % (self.latent_prior, self.latent_posterior))

        if self.imp_weight:
            kl_coord_wise *= self._fix_imp_weight(n_dim)
        kl_sample_wise = kl_coord_wise.sum(axis=n_dim - 1)
        kl = kl_sample_wise.mean()

        return {
            'kl': kl,
            'kl_coord_wise': kl_coord_wise,
            'kl_sample_wise': kl_sample_wise,
        }