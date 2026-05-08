"""PatternClassifier — XGBoost classifier for entry pattern quality."""

import os
import pickle
import numpy as np
from datetime import datetime
from typing import List, Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODELS_DIR = os.path.join(_ROOT, "local_db", "ml_models")
MODEL_PATH = os.path.join(MODELS_DIR, "pattern_clf.pkl")

MIN_TRAIN_TRADES = 50
THRESHOLD = 0.65

# ── Encoding maps ─────────────────────────────────────────────────────────────

PAIR_CATEGORY = {
    # Metals
    "XAUUSD": 0, "GC=F": 0, "XAGUSD": 0, "SI=F": 0, "XPTUSD": 0,
    # Major forex
    "GBPUSD": 1, "EURUSD": 1, "USDJPY": 1, "USDCHF": 1,
    "AUDUSD": 1, "NZDUSD": 1, "USDCAD": 1,
    # Cross forex
    "EURJPY": 2, "GBPJPY": 2, "EURGBP": 2, "AUDCAD": 2, "GBPAUD": 2,
    # Crypto
    "BTCUSD": 3, "ETHUSD": 3, "BTC-USD": 3, "ETH-USD": 3,
    # Indices
    "NAS100": 4, "US30": 4, "GER40": 4, "SPX500": 4,
}

PATTERN_MAP = {
    "": 0, "none": 0,
    "engulfing": 1, "bullish_engulfing": 1, "bearish_engulfing": 1,
    "pin_bar": 2, "hammer": 2, "shooting_star": 2, "hanging_man": 2,
    "inside_bar": 3,
    "doji": 4,
    "marubozu": 5,
    "morning_star": 6, "evening_star": 6,
    "three_white_soldiers": 7, "three_black_crows": 7, "three_soldiers": 7,
}

REGIME_MAP = {
    "": 0, "neutral": 0, "unknown": 0,
    "trending": 1, "strong_trend": 1, "trending_bull": 1, "trending_bear": 1,
    "ranging": 2, "sideways": 2,
    "volatile": 3, "high_vol": 3,
    "quiet": 4, "low_vol": 4,
}

DIRECTION_MAP = {"long": 1, "buy": 1, "bullish": 1, "short": 0, "sell": 0, "bearish": 0}


def _encode_trade(trade, atr_ratio: float = 1.0, adx_value: float = 25.0) -> Optional[np.ndarray]:
    """Convert a WFTrade into a 10-feature vector. Returns None on failure."""
    try:
        confluence   = int(getattr(trade, "confluence", 0))
        rrr_target   = float(getattr(trade, "rrr_target", 1.5))
        pattern_raw  = str(getattr(trade, "pattern", "")).lower().strip()
        regime_raw   = str(getattr(trade, "regime", "")).lower().strip()
        direction_raw= str(getattr(trade, "direction", "long")).lower().strip()
        pair_raw     = str(getattr(trade, "pair", "")).upper().strip()
        hold_bars    = int(getattr(trade, "hold_bars", 10))
        cost_frac    = float(getattr(trade, "cost_fraction", 0.0005))

        pattern_enc   = PATTERN_MAP.get(pattern_raw, 0)
        regime_enc    = REGIME_MAP.get(regime_raw, 0)
        direction_enc = DIRECTION_MAP.get(direction_raw, 1)
        pair_cat      = PAIR_CATEGORY.get(pair_raw, 2)  # default: cross

        return np.array([
            confluence, rrr_target, pattern_enc, regime_enc,
            direction_enc, pair_cat, hold_bars, cost_frac,
            float(atr_ratio), float(adx_value),
        ], dtype=np.float32)
    except Exception:
        return None


class PatternClassifier:
    """XGBoost (fallback: GradientBoosting) classifier for entry pattern quality."""

    THRESHOLD = THRESHOLD

    def __init__(self):
        self.model = None
        self.last_trained: Optional[datetime] = None
        self._using_xgb = False
        os.makedirs(MODELS_DIR, exist_ok=True)
        self._load()

    # ── Serialisation ─────────────────────────────────────────────────────────

    def _save(self) -> None:
        payload = {
            "model": self.model,
            "last_trained": self.last_trained,
            "using_xgb": self._using_xgb,
        }
        with open(MODEL_PATH, "wb") as f:
            pickle.dump(payload, f)

    def _load(self) -> bool:
        if not os.path.exists(MODEL_PATH):
            return False
        try:
            with open(MODEL_PATH, "rb") as f:
                payload = pickle.load(f)
            self.model = payload.get("model")
            self.last_trained = payload.get("last_trained")
            self._using_xgb = payload.get("using_xgb", False)
            return True
        except Exception:
            return False

    # ── Training ──────────────────────────────────────────────────────────────

    def train(self, trades: list) -> bool:
        """Train on a list of WFTrade objects. Requires >= MIN_TRAIN_TRADES test trades."""
        test_trades = [t for t in trades if getattr(t, "split", "train") == "test"]
        if len(test_trades) < MIN_TRAIN_TRADES:
            return False

        X, y = [], []
        for t in test_trades:
            vec = _encode_trade(t)
            if vec is None:
                continue
            outcome = str(getattr(t, "realistic_outcome", "loss")).lower()
            y.append(1 if outcome == "win" else 0)
            X.append(vec)

        if len(X) < MIN_TRAIN_TRADES:
            return False

        X = np.array(X, dtype=np.float32)
        y = np.array(y, dtype=np.int32)

        try:
            import xgboost as xgb  # type: ignore
            self.model = xgb.XGBClassifier(
                n_estimators=200,
                max_depth=4,
                learning_rate=0.05,
                subsample=0.8,
                colsample_bytree=0.8,
                use_label_encoder=False,
                eval_metric="logloss",
                random_state=42,
                verbosity=0,
            )
            self._using_xgb = True
        except ImportError:
            from sklearn.ensemble import GradientBoostingClassifier  # type: ignore
            self.model = GradientBoostingClassifier(
                n_estimators=200,
                max_depth=4,
                learning_rate=0.05,
                subsample=0.8,
                random_state=42,
            )
            self._using_xgb = False

        self.model.fit(X, y)
        self.last_trained = datetime.utcnow()
        self._save()
        return True

    # ── Inference ─────────────────────────────────────────────────────────────

    def predict(self, trade, atr_ratio: float = 1.0, adx_value: float = 25.0) -> float:
        """Return win probability 0-1. Falls back to 0.5 if model unavailable."""
        if self.model is None:
            return 0.5
        vec = _encode_trade(trade, atr_ratio=atr_ratio, adx_value=adx_value)
        if vec is None:
            return 0.5
        try:
            proba = self.model.predict_proba(vec.reshape(1, -1))[0]
            # proba[1] = probability of class 1 (win)
            return float(proba[1]) if len(proba) > 1 else float(proba[0])
        except Exception:
            return 0.5

    def passes(self, trade, atr_ratio: float = 1.0, adx_value: float = 25.0) -> bool:
        """Return True if predicted win probability >= THRESHOLD."""
        return self.predict(trade, atr_ratio=atr_ratio, adx_value=adx_value) >= self.THRESHOLD

    # ── Retraining schedule ───────────────────────────────────────────────────

    def needs_retrain(self, hours: int = 168) -> bool:
        """True if model has never been trained or last training was > hours ago."""
        if self.model is None or self.last_trained is None:
            return True
        delta = datetime.utcnow() - self.last_trained
        return delta.total_seconds() > hours * 3600

    # ── Diagnostics ───────────────────────────────────────────────────────────

    def summary(self) -> dict:
        return {
            "trained": self.model is not None,
            "last_trained": self.last_trained.isoformat() if self.last_trained else None,
            "backend": "xgboost" if self._using_xgb else "gradient_boosting",
            "threshold": self.THRESHOLD,
            "model_path": MODEL_PATH,
        }
