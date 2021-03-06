# coding: utf-8 -*-

import numpy as np

from breze.learn.pca import Pca


def test_pca():
    l = np.arange(0,  1, 0.001).reshape((1000, 1))
    expand = np.array([2.5, 1.2]).reshape((1, 2))

    X = np.dot(l, expand)
    X += np.random.normal(0, 1E-6, X.shape)

    pca  = Pca(1)
    pca.fit(X)

    desired = np.array([[-0.9015], [-0.4327]])

    assert np.allclose(pca.weights, desired, 1e-3)


def test_pca_white():
    l = np.arange(0,  1, 0.001).reshape((1000, 1))
    expand = np.array([2.5, 1.2]).reshape((1, 2))

    X = np.dot(l, expand)
    X += np.random.normal(0, 1E-6, X.shape)

    pca = Pca(2, whiten=True)
    pca.fit(X)
    w, s = pca.weights, pca.singular_values

    X = pca.transform(X)
    assert np.allclose(np.cov(X, rowvar=0), np.eye(2), 1e-2), 'covariance not white'
