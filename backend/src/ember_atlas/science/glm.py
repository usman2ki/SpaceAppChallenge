"""Generalised linear models by iteratively re-weighted least squares (IRLS).

Families: poisson (log link), binomial with trials (logit link), negbin
(NB2, log link, alpha estimated by profile likelihood). Offsets are
supported. Kept small and dependency-free so the statistical core is
transparent; tests/test_glm.py checks results against scikit-learn and
against closed-form maximum-likelihood solutions.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import optimize, special

EPS = 1e-10


@dataclass
class GLMResult:
    family: str
    beta: np.ndarray
    cov_unscaled: np.ndarray
    deviance: float
    pearson_chi2: float
    df_resid: int
    n_obs: int
    converged: bool
    n_iter: int
    alpha: float | None = None  # NB2 dispersion (Var = mu + alpha mu^2)
    loglik: float | None = None

    @property
    def dispersion(self) -> float:
        """Pearson chi^2 / df (quasi-likelihood scale), floored at 1."""
        if self.df_resid <= 0:
            return 1.0
        return max(1.0, self.pearson_chi2 / self.df_resid)

    def cov(self, quasi: bool = True) -> np.ndarray:
        return self.cov_unscaled * (self.dispersion if quasi else 1.0)

    def se(self, quasi: bool = True) -> np.ndarray:
        return np.sqrt(np.diag(self.cov(quasi)))

    def eta(self, X: np.ndarray, offset: np.ndarray | None = None) -> np.ndarray:
        e = X @ self.beta
        return e + offset if offset is not None else e


def _poisson_dev(y, mu):
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(y > 0, y * np.log(y / mu), 0.0)
    return 2.0 * np.sum(t - (y - mu))


def poisson_deviance_terms(y, mu):
    mu = np.maximum(mu, EPS)
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(y > 0, y * np.log(y / mu), 0.0)
    return 2.0 * (t - (y - mu))


def _binom_dev(y, n, mu):
    with np.errstate(divide="ignore", invalid="ignore"):
        a = np.where(y > 0, y * np.log(y / mu), 0.0)
        b = np.where(n - y > 0, (n - y) * np.log((n - y) / (n - mu)), 0.0)
    return 2.0 * np.sum(a + b)


def _nb_dev(y, mu, alpha):
    with np.errstate(divide="ignore", invalid="ignore"):
        a = np.where(y > 0, y * np.log(y / mu), 0.0)
    b = (y + 1.0 / alpha) * np.log((1.0 + alpha * y) / (1.0 + alpha * mu))
    return 2.0 * np.sum(a - b)


def nb_loglik(y, mu, alpha) -> float:
    r = 1.0 / alpha
    return float(np.sum(special.gammaln(y + r) - special.gammaln(r) - special.gammaln(y + 1) + r * np.log(r / (r + mu)) + y * np.log(mu / (r + mu))))


def fit_glm(
    X: np.ndarray,
    y: np.ndarray,
    family: str = "poisson",
    *,
    offset: np.ndarray | None = None,
    trials: np.ndarray | None = None,
    alpha: float | None = None,
    max_iter: int = 100,
    tol: float = 1e-9,
    ridge: float = 1e-8,
) -> GLMResult:
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    n, p = X.shape
    off = np.zeros(n) if offset is None else np.asarray(offset, dtype=float)
    if family == "binomial":
        if trials is None:
            raise ValueError("binomial family needs trials")
        nt = np.asarray(trials, dtype=float)
        if np.any(y < 0) or np.any(y > nt):
            raise ValueError("binomial successes must be within [0, trials]")
        eta = special.logit((y + 0.5) / (nt + 1.0))
    elif family in ("poisson", "negbin"):
        if np.any(y < 0):
            raise ValueError("counts must be non-negative")
        eta = np.log(y + 0.5)
        if family == "negbin" and alpha is None:
            return _fit_negbin_profile(X, y, off, max_iter, tol, ridge)
    else:
        raise ValueError(f"unknown family {family}")

    # initial beta from a weighted LS on the working response
    beta = np.linalg.lstsq(X, eta - off, rcond=None)[0]
    dev_old = np.inf
    converged = False
    it = 0
    for it in range(1, max_iter + 1):  # noqa: B007 (n_iter is reported after the loop)
        eta = X @ beta + off
        mu, w, z = _working(family, eta, y, off, trials, alpha)
        XtW = X.T * w
        A = XtW @ X + ridge * np.eye(p)
        beta_new = np.linalg.solve(A, XtW @ z)
        # step halving on deviance increase
        step = 1.0
        for _ in range(30):
            cand = beta + step * (beta_new - beta)
            dev = _deviance(family, y, _mu(family, X @ cand + off, trials), trials, alpha)
            if np.isfinite(dev) and dev <= dev_old + 1e-12 * max(1.0, abs(dev_old)):
                break
            step /= 2.0
        beta_next = cand
        if abs(dev_old - dev) <= tol * (abs(dev) + 0.1):
            beta = beta_next
            converged = True
            break
        beta, dev_old = beta_next, dev
    eta = X @ beta + off
    mu, w, _ = _working(family, eta, y, off, trials, alpha)
    A = (X.T * w) @ X + ridge * np.eye(p)
    cov = np.linalg.pinv(A)
    dev = _deviance(family, y, mu, trials, alpha)
    var = _variance(family, mu, trials, alpha)
    pearson = float(np.sum((y - mu) ** 2 / np.maximum(var, EPS)))
    ll = nb_loglik(y, np.maximum(mu, EPS), alpha) if family == "negbin" else None
    return GLMResult(family, beta, cov, float(dev), pearson, n - p, n, converged, it, alpha, ll)


def _fit_negbin_profile(X, y, off, max_iter, tol, ridge) -> GLMResult:
    def negll(log_a):
        r = fit_glm(X, y, "negbin", offset=off, alpha=float(np.exp(log_a)), max_iter=max_iter, tol=tol, ridge=ridge)
        mu = np.exp(np.clip(r.eta(X, off), -30, 30))
        return -nb_loglik(y, mu, float(np.exp(log_a)))

    res = optimize.minimize_scalar(negll, bounds=(np.log(1e-4), np.log(50.0)), method="bounded", options={"xatol": 1e-4})
    a = float(np.exp(res.x))
    return fit_glm(X, y, "negbin", offset=off, alpha=a, max_iter=max_iter, tol=tol, ridge=ridge)


def _mu(family, eta, trials):
    if family == "binomial":
        return trials * special.expit(eta)
    return np.exp(np.clip(eta, -30, 30))


def _variance(family, mu, trials, alpha):
    if family == "binomial":
        p = np.clip(mu / np.maximum(trials, EPS), EPS, 1 - EPS)
        return trials * p * (1 - p)
    if family == "negbin":
        return mu + alpha * mu**2
    return mu


def _deviance(family, y, mu, trials, alpha):
    mu = np.maximum(mu, EPS)
    if family == "binomial":
        return _binom_dev(y, trials, np.minimum(mu, trials - EPS))
    if family == "negbin":
        return _nb_dev(y, mu, alpha)
    return _poisson_dev(y, mu)


def _working(family, eta, y, off, trials, alpha):
    if family == "binomial":
        p = np.clip(special.expit(eta), EPS, 1 - EPS)
        mu = trials * p
        dmu = trials * p * (1 - p)
        var = dmu
    else:
        mu = np.exp(np.clip(eta, -30, 30))
        dmu = mu
        var = mu + (alpha * mu**2 if family == "negbin" else 0.0)
    w = dmu**2 / np.maximum(var, EPS)
    z = (eta - off) + (y - mu) / np.maximum(dmu, EPS)
    return mu, w, z


def harmonics(doy: np.ndarray, order: int, period: float = 365.25) -> np.ndarray:
    """Fourier seasonal basis [sin(2 pi k t / P), cos(2 pi k t / P)] for k = 1..order."""
    doy = np.asarray(doy, dtype=float)
    if order == 0:
        return np.empty((len(doy), 0))
    cols = []
    for k in range(1, order + 1):
        a = 2 * np.pi * k * doy / period
        cols += [np.sin(a), np.cos(a)]
    return np.column_stack(cols)
