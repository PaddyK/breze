# -*- coding: utf-8 -*-

import theano.tensor as T

from breze.arch.construct.neural import (
    Mlp, FastDropoutMlp, Rnn, FastDropoutRnn)

from breze.arch.util import lookup
from breze.arch.component import transfer as _transfer
from breze.arch.construct.layer.distributions import DiagGauss, Bernoulli


class ConcatTransfer(object):

    def __init__(self, mean_transfer, var_transfer):
        self.mean_transfer = mean_transfer
        self.var_transfer = var_transfer

    def __call__(self, inpt):
        f_mean_transfer = lookup(self.mean_transfer, _transfer)
        f_var_transfer = lookup(self.var_transfer, _transfer)

        half = inpt.shape[-1] // 2

        if inpt.ndim == 3:
            mean, var = inpt[:, :, :half], inpt[:, :, half:]
            res = T.concatenate([f_mean_transfer(mean),
                                f_var_transfer(var)], axis=2)
        else:
            mean, var = inpt[:, :half], inpt[:, half:]
            res = T.concatenate([f_mean_transfer(mean),
                                f_var_transfer(var)], axis=1)
        return res


def var_transfer(var):
    return var ** 2 + 1e-5


class MlpDiagGauss(DiagGauss):

    def __init__(self, inpt, n_inpt, n_hiddens, n_output,
                 hidden_transfers, out_transfer_mean='identity',
                 out_transfer_var=var_transfer,
                 declare=None, name=None, rng=None):
        self.inpt = inpt
        self.n_inpt = n_inpt
        self.n_hiddens = n_hiddens
        self.n_output = n_output
        self.hidden_transfers = hidden_transfers
        self.out_transfer_mean = out_transfer_mean
        self.out_transfer_var = out_transfer_var

        self.mlp = Mlp(
            self.inpt, self.n_inpt, self.n_hiddens, self.n_output * 2,
            self.hidden_transfers,
            ConcatTransfer(self.out_transfer_mean, self.out_transfer_var),
            declare=declare)

        super(MlpDiagGauss, self).__init__(
            self.mlp.output[:, :self.n_output],
            self.mlp.output[:, self.n_output:],
            rng)


class RnnDiagGauss(DiagGauss):

    def __init__(self, inpt,
                 n_inpt, n_hiddens, n_output,
                 hidden_transfers, out_transfer_mean='identity',
                 out_transfer_var=var_transfer,
                 pooling=None,
                 declare=None, name=None, rng=None):
        self.inpt = inpt
        self.n_inpt = n_inpt
        self.n_hiddens = n_hiddens
        self.n_output = n_output
        self.hidden_transfers = hidden_transfers
        self.out_transfer_mean = out_transfer_mean
        self.out_transfer_var = out_transfer_var
        self.pooling = pooling

        self.rnn = Rnn(
            self.inpt, self.n_inpt, self.n_hiddens, self.n_output * 2,
            self.hidden_transfers,
            ConcatTransfer(self.out_transfer_mean, self.out_transfer_var),
            pooling=pooling,
            declare=declare)

        super(RnnDiagGauss, self).__init__(self.rnn.output[:, :self.n_output],
                                           self.rnn.output[:, self.n_output:],
                                           rng)


class FastDropoutMlpDiagGauss(DiagGauss):

    def __init__(self, inpt, n_inpt, n_hiddens, n_output,
                 hidden_transfers, out_transfer='identity',
                 p_dropout_inpt=.1,
                 p_dropout_hiddens=.1, dropout_parameterized=False,
                 declare=None, name=None, rng=None):
        self.inpt = inpt
        self.n_inpt = n_inpt
        self.n_hiddens = n_hiddens
        self.n_output = n_output
        self.hidden_transfers = hidden_transfers
        self.out_transfer = out_transfer
        self.p_dropout_inpt = p_dropout_inpt
        self.p_dropout_hiddens = p_dropout_hiddens
        self.dropout_parameterized = dropout_parameterized

        self.mlp = FastDropoutMlp(
            self.inpt, self.n_inpt, self.n_hiddens,
            self.n_output, self.hidden_transfers,
            self.out_transfer, self.p_dropout_inpt,
            self.p_dropout_hiddens,
            dropout_parameterized=self.dropout_parameterized,
            declare=declare)

        super(FastDropoutMlpDiagGauss, self).__init__(
            self.mlp.output[:, :self.n_output],
            self.mlp.output[:, self.n_output:],
            rng)


class FastDropoutMlpBernoulli(Bernoulli):

    def __init__(self, inpt, n_inpt, n_hiddens, n_output,
                 hidden_transfers, out_transfer='sigmoid',
                 p_dropout_inpt=.1,
                 p_dropout_hiddens=.1, dropout_parameterized=False,
                 declare=None, name=None, rng=None):
        self.inpt = inpt
        self.n_inpt = n_inpt
        self.n_hiddens = n_hiddens
        self.n_output = n_output
        self.hidden_transfers = hidden_transfers
        self.out_transfer = out_transfer
        self.p_dropout_inpt = p_dropout_inpt
        self.p_dropout_hiddens = p_dropout_hiddens
        self.dropout_parameterized = dropout_parameterized

        self.mlp = FastDropoutMlp(
            self.inpt, self.n_inpt, self.n_hiddens,
            self.n_output, self.hidden_transfers,
            self.out_transfer, self.p_dropout_inpt,
            self.p_dropout_hiddens,
            dropout_parameterized=self.dropout_parameterized,
            declare=declare)

        super(FastDropoutMlpBernoulli, self).__init__(
            self.mlp.output[:, :self.n_output],
            rng)


class MlpBernoulli(Bernoulli):

    def __init__(self, inpt, n_inpt, n_hiddens, n_output,
                 hidden_transfers, out_transfer='sigmoid',
                 declare=None, name=None, rng=None):
        self.inpt = inpt
        self.n_inpt = n_inpt
        self.n_hiddens = n_hiddens
        self.n_output = n_output
        self.out_transfer = out_transfer
        self.hidden_transfers = hidden_transfers

        self.mlp = Mlp(self.inpt, self.n_inpt, self.n_hiddens,
                       self.n_output, self.hidden_transfers,
                       self.out_transfer, declare=declare)

        super(MlpBernoulli, self).__init__(self.mlp.output, rng)


class RnnBernoulli(Bernoulli):

    def __init__(self, inpt,
                 n_inpt, n_hiddens, n_output,
                 hidden_transfers, out_transfer='sigmoid',
                 pooling=None,
                 declare=None, name=None, rng=None):
        self.inpt = inpt
        self.n_inpt = n_inpt
        self.n_hiddens = n_hiddens
        self.n_output = n_output
        self.hidden_transfers = hidden_transfers
        self.out_transfer = out_transfer
        self.pooling = pooling

        self.rnn = Rnn(
            self.inpt, self.n_inpt, self.n_hiddens, self.n_output * 2,
            self.hidden_transfers,
            self.out_transfer,
            pooling=pooling,
            declare=declare)

        super(RnnBernoulli, self).__init__(self.rnn.output, rng)


class FastDropoutRnnBernoulli(Bernoulli):

    def __init__(self, inpt,
                 n_inpt, n_hiddens, n_output,
                 hidden_transfers, out_transfer='sigmoid',
                 pooling=None,
                 p_dropout_inpt=.1, p_dropout_hiddens=.1,
                 p_dropout_hidden_to_out=None,
                 declare=None, name=None, rng=None):
        self.inpt = inpt
        self.n_inpt = n_inpt
        self.n_hiddens = n_hiddens
        self.n_output = n_output
        self.hidden_transfers = hidden_transfers
        self.out_transfer = out_transfer
        self.pooling = pooling

        self.p_dropout_inpt = p_dropout_inpt
        self.p_dropout_hiddens = p_dropout_hiddens
        self.p_dropout_hidden_to_out = p_dropout_hidden_to_out

        self.rnn = FastDropoutRnn(
            self.inpt, self.n_inpt, self.n_hiddens, self.n_output * 2,
            self.hidden_transfers,
            self.out_transfer,
            pooling=pooling,
            p_dropout_inpt=self.p_dropout_inpt,
            p_dropout_hiddens=self.p_dropout_hiddens,
            p_dropout_hidden_to_out=p_dropout_hidden_to_out,
            declare=declare
        )

        if self.pooling:
            super(FastDropoutRnnBernoulli, self).__init__(
                self.rnn.output[:, :self.n_output],
                rng)
        else:
            super(FastDropoutRnnBernoulli, self).__init__(
                self.rnn.output[:, :, :self.n_output],
                rng)


class FastDropoutRnnDiagGauss(DiagGauss):

    def __init__(self, inpt,
                 n_inpt, n_hiddens, n_output,
                 hidden_transfers, out_transfer='identity',
                 pooling=None,
                 p_dropout_inpt=.1, p_dropout_hiddens=.1,
                 p_dropout_hidden_to_out=None,
                 declare=None, name=None, rng=None):
        self.inpt = inpt
        self.n_inpt = n_inpt
        self.n_hiddens = n_hiddens
        self.n_output = n_output
        self.hidden_transfers = hidden_transfers
        self.out_transfer = out_transfer
        self.pooling = pooling

        self.p_dropout_inpt = p_dropout_inpt
        self.p_dropout_hiddens = p_dropout_hiddens

        self.rnn = FastDropoutRnn(
            self.inpt, self.n_inpt, self.n_hiddens, self.n_output,
            self.hidden_transfers,
            self.out_transfer,
            pooling=pooling,
            p_dropout_inpt=self.p_dropout_inpt,
            p_dropout_hiddens=self.p_dropout_hiddens,
            p_dropout_hidden_to_out=p_dropout_hidden_to_out,
            declare=declare
        )

        if self.pooling:
            super(FastDropoutRnnDiagGauss, self).__init__(
                self.rnn.output[:, :self.n_output],
                self.rnn.output[:, self.n_output:],
                rng)
        else:
            super(FastDropoutRnnDiagGauss, self).__init__(
                self.rnn.output[:, :, :self.n_output],
                self.rnn.output[:, :, self.n_output:],
                rng)
