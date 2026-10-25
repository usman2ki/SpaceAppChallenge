import numpy as np
import pytest
from scipy import special
from sklearn.linear_model import LogisticRegression, PoissonRegressor

from ember_atlas.science.glm import fit_glm, harmonics


def test_poisson_matches_sklearn_unpenalised():
    rng = np.random.default_rng(0)
    X = np.column_stack([np.ones(2000), rng.normal(size=(2000, 2))])
    beta = np.array([0.3, 0.5, -0.4])
    y = rng.poisson(np.exp(X @ beta))
    ours = fit_glm(X, y, "poisson")
    sk = PoissonRegressor(alpha=0.0, fit_intercept=False, tol=1e-10, max_iter=1000).fit(X, y)
    assert np.allclose(ours.beta, sk.coef_, atol=1e-5)
    assert ours.converged


def test_binomial_with_trials_matches_expanded_logistic_regression():
    rng = np.random.default_rng(1)
    m = 400
    x = rng.normal(size=m)
    X = np.column_stack([np.ones(m), x])
    n = rng.integers(1, 15, m)
    y = rng.binomial(n, special.expit(-0.3 + 0.8 * x))
    ours = fit_glm(X, y, "binomial", trials=n)
    # expand to Bernoulli rows with weights
    Xe = np.vstack([X, X])
    ye = np.r_[np.ones(m), np.zeros(m)]
    we = np.r_[y, n - y]
    keep = we > 0
    sk = LogisticRegression(C=np.inf, fit_intercept=False, tol=1e-10, max_iter=1000).fit(Xe[keep], ye[keep], sample_weight=we[keep])
    assert np.allclose(ours.beta, sk.coef_[0], atol=1e-5)


def test_poisson_offset_rate_has_closed_form_mle():
    # intercept-only Poisson with offset: beta = log(sum y / sum exposure)
    rng = np.random.default_rng(2)
    e = rng.uniform(0.5, 3, 500)
    y = rng.poisson(2.0 * e)
    r = fit_glm(np.ones((500, 1)), y, "poisson", offset=np.log(e))
    assert r.beta[0] == pytest.approx(np.log(y.sum() / e.sum()), abs=1e-8)


def test_negbin_recovers_overdispersion():
    rng = np.random.default_rng(3)
    n = 6000
    X = np.column_stack([np.ones(n), rng.normal(size=n)])
    mu = np.exp(0.5 + 0.3 * X[:, 1])
    alpha = 0.5
    y = rng.poisson(mu * rng.gamma(1 / alpha, alpha, n))
    r = fit_glm(X, y, "negbin")
    assert r.alpha == pytest.approx(alpha, rel=0.15)
    assert np.allclose(r.beta, [0.5, 0.3], atol=0.06)
    pois = fit_glm(X, y, "poisson")
    assert pois.dispersion > 1.3  # overdispersion visible to the Poisson fit


def test_harmonics_shape_and_periodicity():
    h = harmonics(np.array([1.0, 366.25]), 2)
    assert h.shape == (2, 4)
    assert np.allclose(h[0], h[1])
    assert harmonics(np.array([1.0]), 0).shape == (1, 0)


def test_invalid_inputs():
    with pytest.raises(ValueError):
        fit_glm(np.ones((3, 1)), np.array([1, 2, 5]), "binomial", trials=np.array([2, 2, 2]))
    with pytest.raises(ValueError):
        fit_glm(np.ones((2, 1)), np.array([-1, 2]), "poisson")
