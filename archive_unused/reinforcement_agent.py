"""ReinforcementAgent — Q-learning agent for entry/exit timing optimisation."""

import os
import json
import random
from datetime import datetime
from typing import Dict, Optional, Tuple

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODELS_DIR = os.path.join(_ROOT, "local_db", "ml_models")
Q_TABLE_PATH = os.path.join(MODELS_DIR, "q_agent.json")

# ── State space ───────────────────────────────────────────────────────────────
# Dimension 0: regime          0=trending | 1=ranging | 2=volatile | 3=quiet
# Dimension 1: confluence_bin  0=0-1 | 1=2 | 2=3 | 3=4 | 4=5
# Dimension 2: time_in_signal  0=0 | 1=1 | 2=2 | 3=3 | 4=4+ bars
# Dimension 3: trend_alignment 0=not aligned | 1=aligned

# ── Action space ──────────────────────────────────────────────────────────────
# 0=skip | 1=enter_now | 2=wait_1_bar | 3=enter_aggressive

ACTION_LABELS = {0: "skip", 1: "enter", 2: "wait", 3: "aggressive"}
ACTIONS       = list(ACTION_LABELS.keys())

_REGIME_MAP = {
    "trending": 0, "trending_bull": 0, "trending_bear": 0, "strong_trend": 0,
    "ranging": 1, "sideways": 1,
    "volatile": 2, "high_vol": 2,
    "quiet": 3, "low_vol": 3,
}


def _confluence_bin(conf: int) -> int:
    if conf <= 1:
        return 0
    if conf == 2:
        return 1
    if conf == 3:
        return 2
    if conf == 4:
        return 3
    return 4   # 5+


def _time_bin(bars: int) -> int:
    return min(bars, 4)


def _encode_state(regime: str, confluence: int,
                  time_since_signal: int, aligned: bool) -> Tuple[int, int, int, int]:
    return (
        _REGIME_MAP.get(str(regime).lower().strip(), 1),
        _confluence_bin(int(confluence)),
        _time_bin(int(time_since_signal)),
        int(bool(aligned)),
    )


class ReinforcementAgent:
    """
    Tabular Q-learning agent for entry timing optimisation.

    State  : (regime, confluence_bin, time_since_signal, trend_alignment)
    Actions: 0=skip | 1=enter_now | 2=wait_1_bar | 3=enter_aggressive
    """

    def __init__(self):
        self.alpha   = 0.1   # learning rate
        self.gamma   = 0.9   # discount factor
        self.epsilon = 0.3   # exploration rate

        # Q-table: {state_tuple_str -> {action_int_str -> q_value}}
        self.q_table: Dict[str, Dict[str, float]] = {}

        self.last_trained: Optional[datetime] = None
        os.makedirs(MODELS_DIR, exist_ok=True)
        self._load()

    # ── Q-table access helpers ────────────────────────────────────────────────

    def _state_key(self, state: tuple) -> str:
        return str(state)

    def _get_q(self, state: tuple, action: int) -> float:
        k = self._state_key(state)
        return self.q_table.get(k, {}).get(str(action), 0.0)

    def _set_q(self, state: tuple, action: int, value: float) -> None:
        k = self._state_key(state)
        if k not in self.q_table:
            self.q_table[k] = {}
        self.q_table[k][str(action)] = value

    def _best_action(self, state: tuple) -> int:
        k = self._state_key(state)
        if k not in self.q_table:
            return 1  # default: enter_now
        q_dict = self.q_table[k]
        return int(max(ACTIONS, key=lambda a: q_dict.get(str(a), 0.0)))

    # ── Policy ────────────────────────────────────────────────────────────────

    def get_action(self, state: tuple) -> int:
        """Epsilon-greedy action selection."""
        if random.random() < self.epsilon:
            return random.choice(ACTIONS)
        return self._best_action(state)

    # ── Q-update ──────────────────────────────────────────────────────────────

    def update(self, state: tuple, action: int, reward: float, next_state: tuple) -> None:
        """Standard Q-learning update."""
        current_q = self._get_q(state, action)
        max_next_q = max(self._get_q(next_state, a) for a in ACTIONS)
        new_q = current_q + self.alpha * (reward + self.gamma * max_next_q - current_q)
        self._set_q(state, action, new_q)

    # ── Training ──────────────────────────────────────────────────────────────

    def train_on_trades(self, trades: list) -> int:
        """
        Train Q-table on historical trades using 10 epochs.

        State is constructed from trade attributes.
        Reward:
          - action=skip  → 0.0
          - win trade    → rrr_achieved (or rrr_target as proxy)
          - loss trade   → -1.0

        Returns number of trades used.
        """
        valid = []
        for t in trades:
            try:
                regime       = str(getattr(t, "regime",    "ranging")).lower()
                confluence   = int(getattr(t, "confluence", 2))
                hold_bars    = int(getattr(t, "hold_bars",  0))
                direction    = str(getattr(t, "direction",  "long")).lower()
                outcome      = str(getattr(t, "realistic_outcome", "loss")).lower()
                rrr          = float(getattr(t, "rrr_target", 1.5))

                # trend_alignment: we proxy with direction vs regime
                aligned = (regime in ("trending", "trending_bull", "trending_bear"))
                state = _encode_state(regime, confluence, time_since_signal=0, aligned=aligned)
                next_state = _encode_state(regime, confluence, time_since_signal=1, aligned=aligned)

                reward_enter     = rrr if outcome == "win" else -1.0
                reward_skip      = 0.0
                reward_wait      = reward_enter * 0.8  # slight discount for waiting
                reward_aggressive= reward_enter * 1.1 if outcome == "win" else -1.3

                valid.append((state, next_state, reward_enter, reward_skip,
                              reward_wait, reward_aggressive))
            except Exception:
                continue

        if not valid:
            return 0

        for _ in range(10):
            random.shuffle(valid)
            for (state, next_state, r_enter, r_skip, r_wait, r_agg) in valid:
                self.update(state, 1, r_enter,     next_state)
                self.update(state, 0, r_skip,      next_state)
                self.update(state, 2, r_wait,      next_state)
                self.update(state, 3, r_agg,       next_state)

        self.last_trained = datetime.utcnow()
        self._save()
        return len(valid)

    # ── Public recommendation ─────────────────────────────────────────────────

    def get_entry_recommendation(
        self,
        confluence: int,
        regime: str,
        time_in_session: int,
        aligned: bool,
    ) -> str:
        """
        Returns 'skip' | 'enter' | 'wait' | 'aggressive'.

        Parameters
        ----------
        confluence      : 0-5 signal confluence count
        regime          : 'trending'|'ranging'|'volatile'|'quiet'
        time_in_session : bars since signal was generated
        aligned         : True if trade direction aligns with trend
        """
        state = _encode_state(regime, confluence, time_in_session, aligned)
        action = self._best_action(state)
        return ACTION_LABELS.get(action, "enter")

    # ── Diagnostics ───────────────────────────────────────────────────────────

    def q_table_size(self) -> int:
        """Number of unique states explored."""
        return len(self.q_table)

    def needs_training(self) -> bool:
        """True if fewer than 50 states have been explored."""
        return self.q_table_size() < 50

    def summary(self) -> Dict:
        return {
            "q_table_states":  self.q_table_size(),
            "needs_training":  self.needs_training(),
            "last_trained":    self.last_trained.isoformat() if self.last_trained else None,
            "alpha":           self.alpha,
            "gamma":           self.gamma,
            "epsilon":         self.epsilon,
            "model_path":      Q_TABLE_PATH,
        }

    # ── Persistence ───────────────────────────────────────────────────────────

    def _save(self) -> None:
        payload = {
            "q_table":     self.q_table,
            "last_trained": self.last_trained.isoformat() if self.last_trained else None,
            "alpha":        self.alpha,
            "gamma":        self.gamma,
            "epsilon":      self.epsilon,
        }
        with open(Q_TABLE_PATH, "w") as f:
            json.dump(payload, f, indent=2)

    def _load(self) -> bool:
        if not os.path.exists(Q_TABLE_PATH):
            return False
        try:
            with open(Q_TABLE_PATH, "r") as f:
                data = json.load(f)
            self.q_table = data.get("q_table", {})
            lt = data.get("last_trained")
            self.last_trained = datetime.fromisoformat(lt) if lt else None
            self.alpha   = float(data.get("alpha",   self.alpha))
            self.gamma   = float(data.get("gamma",   self.gamma))
            self.epsilon = float(data.get("epsilon", self.epsilon))
            return True
        except Exception:
            return False
