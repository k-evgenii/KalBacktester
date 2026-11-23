from __future__ import annotations
import re

import numpy as np
import numpy.typing as npt



Array = npt.NDArray[np.float64]
IntArray = npt.NDArray[np.int_]

#This script is essentially a payoff geometry
class MarketStructure:
    """Finite-state market for one event."""

    def __init__(
        self,
        market_id: str,
        state_labels: list[str],
        contract_labels: list[str],

    ) -> None:
        
        #market id
        self.market_id: str = market_id  # Use the parameter, not self.market_id

        # state labels
        self.state_labels = state_labels

        #contract labels
        self.contract_labels = contract_labels

        #compute dimensions from labels:
        self.n_states = len(state_labels)
        self.n_contracts = len(contract_labels)

        # state space Ω
        self.Omega: IntArray = np.arange(self.n_states, dtype=int)

        # realized state ω (set later)
        self.omega: int | None = None

        # ones vector
        self.one = np.ones(self.n_states, dtype=float)

        # YES payoff matrix
        self.A_yes = self.build_A_yes_from_labels(state_labels, contract_labels)

        if self.A_yes.shape != (self.n_states, self.n_contracts):
            raise ValueError("A_yes has wrong shape")
        
        self.A_no = np.ones((self.n_states, self.n_contracts), dtype=np.float64) - self.A_yes
        
        if self.A_no.shape != (self.n_states, self.n_contracts):
            raise ValueError("A_no has wrong shape")
        
        #Example
        #market = MarketStructure("NYC_TEMP_2026_03_11", 6)
    
    """
    Since state labels look like this: 
        state_labels = [
            "48_or_below",
            "49_50",
            "51_52",
            "53_54",
            "55_or_above",
        ]
    """
    def parse_state_upper_bound(self, label: str) -> float:
            if label.endswith("_or_below"):
                return float(label.split("_")[0])

            if label.endswith("_or_above"):
                return np.inf

            m = re.fullmatch(r"(\d+)_(\d+)", label)
            if m:
                return float(m.group(2))

            raise ValueError(f"Unsupported state label: {label}")

    """
    Contract labels can use two conventions:
      "T49"  — legacy / test convention (T-prefix, upper-tail implied)
      "LT71" — lower-tail convention produced by event_structure_builder
               (LT-prefix makes the lower-tail interpretation explicit)
    Both are supported by parse_contract_strike.
    """

    def parse_contract_strike(self, label: str) -> float:
        if label.startswith("LT"):
            # Lower-tail contract: "LT71" → strike = 71.0
            # Produced by event_structure_builder for replay (lower-tail convention).
            return float(label[2:])

        if label.startswith("T"):
            # Legacy / test convention: "T49" → strike = 49.0
            return float(label[1:])

        if label.startswith("<"):
            return float(label[1:])
        
        if label.startswith("B"):
            # Between (bin) contract — use bin center as approximate lower-tail strike
            # Payoff is approximate; settlement PnL uses Kalshi result via settle_trades()
            return float(label[1:])

        raise ValueError(f"Unsupported contract label: {label}")

    # Building a Yes-payoff matrix from the state_labels & contract_labels
    def build_A_yes_from_labels(
        self,
        state_labels: list[str],
        contract_labels: list[str]   
    ) -> Array:
        
        A = np.zeros((self.n_states, self.n_contracts), dtype=np.float64)
        
        for s_idx, s_label in enumerate(state_labels):
            state_high = self.parse_state_upper_bound(s_label)

            for j_idx, j_label in enumerate(contract_labels):
                strike = self.parse_contract_strike(j_label)

                if state_high < strike:
                    A[s_idx, j_idx] = np.float64(1.0)

        return A


    def yes_payoff_matrix(self) -> Array:
        """YES payoff matrix: rows=states, cols=contracts."""
        return self.A_yes


    def no_payoff_matrix(self) -> Array:
        """NO payoff matrix: complement of YES."""
        return self.A_no


    def state_basis(self, s: int) -> Array:
        """Standard basis vector for state s, not for listed contracts."""
        if not 0 <= s < self.n_states:
            raise IndexError(f"State index out of range: {s}")
        
        vec = np.zeros(self.n_states, dtype=np.float64)
        vec[s] = np.float64(1.0)
        return vec


    def yes_contract(self, i: int) -> Array:
        """Payoff vector for listed YES contract i across states."""
        return self.A_yes[:, i]


    def no_contract(self, i: int) -> Array:
        """Payoff vector for listed NO contract i across states."""
        return self.A_no[:, i]


    def payoff_yes(self, i: int, omega: int) -> np.float64:
        """Realized scalar payoff of YES_i in state omega."""
        return np.float64(self.A_yes[omega, i])


    def payoff_no(self, i: int, omega: int) -> np.float64:
        """Realized scalar payoff of NO_i in state omega."""
        return np.float64(self.A_no[omega, i])

    
#    #similar to yes_contract(i) but instead of asnwering
#    #what is the payoff vector of the YES claim on bin i?
#    #It answers: what is the payoff-vector change from 
#    executing action a in bin i?
    def u(self, i: int, action: str) -> Array:
        if action == 'BY':
            return self.yes_contract(i)
        if action == 'SY':
            return -self.yes_contract(i)
        if action == 'BN':
            return self.no_contract(i)
        if action == 'SN':
            return -self.no_contract(i)
        raise ValueError(f"Unknown action: {action}")
    
    def U(self) -> Array:
        actions = ['BY','SY','BN','SN']
        cols = [
            self.u(i,action)
            for action in actions
            for i in range (self.n_contracts)
        ]
        return  np.column_stack(cols)

    def U_column_labels(self) -> list[tuple[str, str]]:
        actions = ["BY", "SY", "BN", "SN"]
        return [
            (action, contract_label) 
            for action in actions
            for contract_label in self.contract_labels
        ]
    



if __name__ == "__main__":
    market = MarketStructure(
        market_id="TEST",
        state_labels=[
            "48_or_below",
            "49_50",
            "51_52",
            "53_54",
            "55_or_above",
        ],
        contract_labels=["T49", "T51", "T53", "T55"],
    )

    print(market.A_yes)
    print(market.state_basis(2))
    print(market.U())