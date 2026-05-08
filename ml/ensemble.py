"""MLEnsemble — combines PatternClassifier + RegimeDetector + LSTMPredictor scores."""

import os
import json
from datetime import datetime
from typing import Dict, List, Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODELS_DIR = os.path.join(_ROOT, "local_db", "ml_models")
WEIGHTS_PATH = os.path.join(MODELS_DIR, "ensemble_weights.json")

THRESHOLD = 0.62

# Trending trade — reward if direction aligns with trending regime
_TREND_REGIMES = {"trending"}
_TREND_DIRECTIONS = {"long", "buy", "bullish", "short", "sell", "bearish"}  # both are trend-following


class MLEnsemble:
    """
    Weighted ensemble of PatternClassifier, RegimeDetector, and LSTMPredictor.

    Weights default: pattern=0.40, regime=0.20, lstm=0.15, base_ml=0.25
    Weights are persisted to disk and can be updated via update_weights().
    """

    THRESHOLD = THRESHOLD

    def __init__(self):
        from ml.pattern_classifier import PatternClassifier
        from ml.regime_detector import RegimeDetector
        from ml.lstm_predictor import LSTMPredictor

        self.pattern_clf = PatternClassifier()
        self.regime_det  = RegimeDetector()
        self.lstm_pred   = LSTMPredictor()

        # Mutable weights
        self.pattern_weight = 0.40
        self.regime_weight  = 0.20
        self.lstm_weight    = 0.15
        self.base_ml_weight = 0.25

        os.makedirs(MODELS_DIR, exist_ok=True)
        self._load_weights()

    # ── Weight persistence ────────────────────────────────────────────────────

    def _save_weights(self) -> None:
        payload = {
            "pattern_weight": self.pattern_weight,
            "regime_weight":  self.regime_weight,
            "lstm_weight":    self.lstm_weight,
            "base_ml_weight": self.base_ml_weight,
            "updated_at":     datetime.utcnow().isoformat(),
        }
        with open(WEIGHTS_PATH, "w") as f:
            json.dump(payload, f, indent=2)

    def _load_weights(self) -> bool:
        if not os.path.exists(WEIGHTS_PATH):
            return False
        try:
            with open(WEIGHTS_PATH, "r") as f:
                data = json.load(f)
            self.pattern_weight = float(data.get("pattern_weight", self.pattern_weight))
            self.regime_weight  = float(data.get("regime_weight",  self.regime_weight))
            self.lstm_weight    = float(data.get("lstm_weight",    self.lstm_weight))
            self.base_ml_weight = float(data.get("base_ml_weight", self.base_ml_weight))
            return True
        except Exception:
            return False

    def _normalise_weights(self) -> None:
        total = self.pattern_weight + self.regime_weight + self.lstm_weight + self.base_ml_weight
        if total > 0:
            self.pattern_weight /= total
            self.regime_weight  /= total
            self.lstm_weight    /= total
            self.base_ml_weight /= total

    # ── Regime score helper ───────────────────────────────────────────────────

    def _regime_score(self, trade, df) -> float:
        """
        Score regime-trade alignment:
          - trending regime + directional trade → 0.7
          - mismatched or unknown → 0.4
          - df=None → 0.5 (neutral)
        """
        if df is None:
            return 0.5
        try:
            regime = self.regime_det.detect(df)
            direction = str(getattr(trade, "direction", "long")).lower().strip()
            if regime == "trending":
                return 0.7   # any directional trade benefits from trending
            if regime == "ranging":
                # Mean-reversion setups work well in ranging
                return 0.6
            if regime == "volatile":
                return 0.35  # higher risk, penalise slightly
            if regime == "quiet":
                return 0.45
            return 0.5
        except Exception:
            return 0.5

    # ── Core scoring ──────────────────────────────────────────────────────────

    def score(self, trade, df=None, base_score: float = 0.5) -> float:
        """
        Compute weighted ensemble score for a trade.

        Parameters
        ----------
        trade       : WFTrade or object with trading attributes
        df          : Optional OHLCV DataFrame for regime + LSTM scoring
        base_score  : Score from existing MLLayer (0-1), default 0.5

        Returns
        -------
        float in [0, 1]
        """
        pattern_score = self.pattern_clf.predict(trade)
        regime_score  = self._regime_score(trade, df)
        lstm_score    = self.lstm_pred.predict(df) if df is not None else 0.5

        # If LSTM not available, redistribute its weight proportionally
        if not self.lstm_pred.available:
            avail_weights = self.pattern_weight + self.regime_weight + self.base_ml_weight
            if avail_weights <= 0:
                return 0.5
            w_pat = self.pattern_weight / avail_weights
            w_reg = self.regime_weight  / avail_weights
            w_base= self.base_ml_weight / avail_weights
            return float(
                pattern_score * w_pat
                + regime_score  * w_reg
                + base_score    * w_base
            )

        total_w = self.pattern_weight + self.regime_weight + self.lstm_weight + self.base_ml_weight
        if total_w <= 0:
            return 0.5

        weighted = (
            pattern_score * self.pattern_weight
            + regime_score  * self.regime_weight
            + lstm_score    * self.lstm_weight
            + base_score    * self.base_ml_weight
        )
        return float(weighted / total_w)

    def passes(self, trade, df=None, base_score: float = 0.5) -> bool:
        """Return True if ensemble score >= THRESHOLD."""
        return self.score(trade, df=df, base_score=base_score) >= self.THRESHOLD

    # ── Training ──────────────────────────────────────────────────────────────

    def train_all(self, trades: list, df_list: Optional[List] = None) -> Dict[str, bool]:
        """Train all sub-models. Returns dict of success flags."""
        results: Dict[str, bool] = {"pattern": False, "regime": False, "lstm": False}

        results["pattern"] = self.pattern_clf.train(trades)

        if df_list:
            results["regime"] = self.regime_det.train(df_list)
            results["lstm"]   = self.lstm_pred.train(df_list)

        return results

    # ── Evaluation ────────────────────────────────────────────────────────────

    def evaluate(self, trades: list) -> Dict:
        """
        Evaluate ensemble quality on a list of trades.

        Returns
        -------
        dict with keys:
            high_conf_wr  — win-rate for trades above THRESHOLD
            low_conf_wr   — win-rate for trades below THRESHOLD
            lift          — high_conf_wr - low_conf_wr
            n_high        — number of high-confidence trades
            n_low         — number of low-confidence trades
            threshold     — the THRESHOLD value used
        """
        high_wins = high_total = 0
        low_wins  = low_total  = 0

        for t in trades:
            s = self.score(t)
            outcome = str(getattr(t, "realistic_outcome", "loss")).lower()
            win = 1 if outcome == "win" else 0

            if s >= self.THRESHOLD:
                high_wins  += win
                high_total += 1
            else:
                low_wins  += win
                low_total += 1

        high_wr = (high_wins / high_total) if high_total > 0 else 0.0
        low_wr  = (low_wins  / low_total)  if low_total  > 0 else 0.0

        return {
            "high_conf_wr": round(high_wr, 4),
            "low_conf_wr":  round(low_wr, 4),
            "lift":         round(high_wr - low_wr, 4),
            "n_high":       high_total,
            "n_low":        low_total,
            "threshold":    self.THRESHOLD,
        }

    # ── Weight updates ────────────────────────────────────────────────────────

    def update_weights(self, eval_results: Dict) -> None:
        """
        Adjust weights based on evaluation results.
        If pattern lift > 0.05, increase pattern_weight by 0.05.
        Normalises all weights to sum to 1.0 and saves to disk.
        """
        lift = eval_results.get("lift", 0.0)
        if lift > 0.05:
            self.pattern_weight += 0.05
        self._normalise_weights()
        self._save_weights()

    # ── Summary ───────────────────────────────────────────────────────────────

    def summary(self) -> Dict:
        """Return status of all models and current weights."""
        clf_s  = self.pattern_clf.summary()
        reg_s  = self.regime_det.summary()
        lstm_s = self.lstm_pred.summary()

        return {
            "threshold": self.THRESHOLD,
            "weights": {
                "pattern": round(self.pattern_weight, 4),
                "regime":  round(self.regime_weight, 4),
                "lstm":    round(self.lstm_weight, 4),
                "base_ml": round(self.base_ml_weight, 4),
            },
            "pattern_classifier": {
                "trained":      clf_s["trained"],
                "last_trained": clf_s["last_trained"],
                "backend":      clf_s["backend"],
                "needs_retrain": self.pattern_clf.needs_retrain(),
            },
            "regime_detector": {
                "trained":      reg_s["trained"],
                "last_trained": reg_s["last_trained"],
                "backend":      reg_s["backend"],
                "needs_retrain": self.regime_det.needs_retrain(),
            },
            "lstm_predictor": {
                "available":    lstm_s["available"],
                "trained":      lstm_s["trained"],
                "last_trained": lstm_s["last_trained"],
                "needs_retrain": self.lstm_pred.needs_retrain(),
            },
        }
