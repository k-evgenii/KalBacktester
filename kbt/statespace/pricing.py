from __future__ import annotations

import numpy as np
import numpy.typing as npt

from .market import MarketStructure

Array = npt.NDArray[np.float64]


def normalize_state_probs(q: Array) -> Array:
    """
    Normalize a latent state-probability vector so it sums to 1.
    """
    s = np.float64(q.sum())
    if s <= 0:
        raise ValueError("State-probability vector must have positive sum")

    return np.asarray(q / s, dtype=np.float64)


def validate_state_probs(
    q: Array,
    tol: np.float64 = np.float64(1e-10),
) -> None:
    """
    Validate a latent state-probability vector.
    """
    if np.any(q < -tol):
        raise ValueError("State probabilities must be nonnegative")

    if not np.isclose(q.sum(), np.float64(1.0), atol=tol):
        raise ValueError(f"State probabilities must sum to 1, got {q.sum()}")


def yes_prices_from_state_probs(
    market: MarketStructure,
    q_state: Array,
) -> Array:
    """
    Model-implied YES prices from latent state probabilities:

        p_yes = A_yes^T q_state
    """
    if q_state.shape != (market.n_states,):
        raise ValueError(
            f"q_state must have shape ({market.n_states},), got {q_state.shape}"
        )

    validate_state_probs(q_state)

    return np.asarray(
        market.yes_payoff_matrix().T @ q_state,
        dtype=np.float64,
    )


def no_prices_from_yes_prices(p_yes: Array) -> Array:
    """
    NO prices from YES prices:

        p_no = 1 - p_yes
    """
    if np.any(p_yes < 0) or np.any(p_yes > 1):
        raise ValueError("YES prices must lie in [0, 1]")

    return np.asarray(
        np.ones(p_yes.shape, dtype=np.float64) - p_yes,
        dtype=np.float64,
    )


def no_prices_from_state_probs(
    market: MarketStructure,
    q_state: Array,
) -> Array:
    """
    Model-implied NO prices from latent state probabilities.
    """
    p_yes = yes_prices_from_state_probs(market, q_state)
    return no_prices_from_yes_prices(p_yes)


def implied_state_probs_from_less_yes_prices(
    p_yes: Array,
) -> Array:
    """
    Recover disjoint state/bin probabilities from a monotone 'less-than' YES ladder.

    Assumes:
    - contracts are ordered like: <K1, <K2, ..., <Km
    - resulting state probabilities are:
        q0 = p0
        q1 = p1 - p0
        ...
        q_{m-1} = p_{m-1} - p_{m-2}
        q_m = 1 - p_{m-1}

    Output length is len(p_yes) + 1.
    """
    if p_yes.ndim != 1:
        raise ValueError("p_yes must be a 1D vector")

    if np.any(p_yes < 0) or np.any(p_yes > 1):
        raise ValueError("YES prices must lie in [0, 1]")

    if np.any(np.diff(p_yes) < -np.float64(1e-10)):
        raise ValueError("Lower-tail YES ladder must be nondecreasing")

    n_contracts = p_yes.shape[0]
    q = np.zeros(n_contracts + 1, dtype=np.float64)

    q[0] = np.float64(p_yes[0])

    for j in range(1, n_contracts):
        q[j] = np.float64(p_yes[j] - p_yes[j - 1])

    q[-1] = np.float64(np.float64(1.0) - p_yes[-1])

    return q


def discrepancy(model_vec: Array, market_vec: Array) -> Array:
    """
    Elementwise model-minus-market discrepancy.
    Inputs must live in the same space.
    """
    if model_vec.shape != market_vec.shape:
        raise ValueError(
            f"Shape mismatch: {model_vec.shape} vs {market_vec.shape}"
        )

    return np.asarray(model_vec - market_vec, dtype=np.float64)