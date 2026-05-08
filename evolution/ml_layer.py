"""
MLLayer — ensemble meta-learner for trade probability scoring.

Trains on WFTrade objects produced by the walk-forward backtester.
Features extracted from trade setup attributes (not outcomes):
  confluence, rrr_target, pattern, regime, direction, pair_category

Models (auto-skip if library not installed):
  1. RandomForest   — sklearn (always available)
  2. XGBoost        — optional, skip if missing
  3. LightGBM       — optional, skip if missing

Ensemble: soft voting (average predict_proba across available models)
Threshold: ML_SCORE_THRESHOLD = 0.55 for "high confidence" label

Currently used as a SOFT FILTER (reporting only).
Hard filter mode: set ML_HARD_FILTER = True — only execute trades >= threshold.
MT5_PENDING: apply hard filter in live execution when MT5 is connected.
"""

import json
import os
import pickle
from datetime import datetime
from typing import Dict, List, Optional, Tuple
import numpy as np
from loguru import logger

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODELS_DIR = os.path.join(_ROOT, "local_db", "ml_models")
SCORES_PATH = os.path.join(_ROOT, "local_db", "ml_scores.json")

ML_SCORE_THRESHOLD = 0.55   # above this → high-confidence setup
ML_HARD_FILTER     = False  # set True to block trades below threshold
MIN_TRAIN_TRADES   = 50     # minimum trades required to train

# ── Feature encoding maps ────────────────────────────────────────────────────

_PATTERN_MAP = {
    "": 0, "none": 0,
    "engulfing": 1, "bullish_engulfing": 1, "bearish_engulfing": 1,
    "pin_bar": 2, "hammer": 2, "shooting_star": 2, "hanging_man": 2,
    "inside_bar": 3,
    "doji": 4,
    "marubozu": 5,
    "morning_star": 6, "evening_star": 6,
    "three_white_soldiers": 7, "three_black_crows": 7,
}

_REGIME_MAP = {
    "": 0, "neutral": 0, "unknown": 0,
    "trending": 1, "strong_trend": 1,
    "ranging": 2, "sideways": 2,
    "volatile": 3, "high_vol": 3,
    "low_vol": 4,
}

_PAIR_CATEGORY = {
    # Metals
    "XAUUSD": 0, "GC=F": 0, "XAGUSD": 0, "SI=F": 0, "XPTUSD": 0,
    # Major forex
    "GBPUSD": 1, "EURUSD": 1, "USDJPY": 1, "USDCHF": 1,
    "AUDUSD": 1, "NZDUSD": 1, "USDCAD": 1,
    # Cross forex
    "EURJPY": 2, "GBPJPY": 2,
    # Crypto
    "BTCUSD": 3, "ETHUSD": 3, "BTC-USD": 3, "ETH-USD": 3,
    # Indices
    "NAS100": 4, "US30": 4, "GER40": 4,
}


def _encode_trade(trade) -> Optional[np.ndarray]:
    """
    Convert a WFTrade into a fixed-length feature vector.
    Returns None if the trade can't be encoded (e.g. missing fields).
    """
    try:
        confluence   = int(getattr(trade, "confluence", 0))
        rrr_target   = float(getattr(trade, "rrr_target", 1.5))
        pattern_raw  = str(getattr(trade, "pattern", "")).lower().strip()
        regime_raw   = str(getattr(trade, "regime",  "")).lower().strip()
        direction    = 1 if getattr(trade, "direction", "long") == "long" else -1
        pair_raw     = str(getattr(trade, "pair", "")).upper()
        hold_bars    = int(getattr(trade, "hold_bars", 1))
        cost_frac    = float(getattr(trade, "cost_fraction", 0.0))

        pattern_enc = _PATTERN_MAP.get(pattern_raw, 0)
        regime_enc  = _REGIME_MAP.get(regime_raw,   0)
        pair_enc    = _PAIR_CATEGORY.get(pair_raw,  1)

        return np.array([
            confluence,         # 0: signal quality  0-5
            rrr_target,         # 1: target RRR      0.5-3.0
            pattern_enc,        # 2: candle pattern  0-7
            regime_enc,         # 3: market regime   0-4
            direction,          # 4: long=1, short=-1
            pair_enc,           # 5: asset category  0-4
            min(hold_bars, 20), # 6: bars held       1-20
            cost_frac,          # 7: cost fraction   0-0.4
        ], dtype=float)
    except Exception:
        return None


class MLLayer:
    """
    Ensemble meta-learner. Train on WFTrade lists, score new setups.
    Models persist to local_db/ml_models/ and reload automatically.
    """

    def __init__(self):
        os.makedirs(MODELS_DIR, exist_ok=True)
        self.models: Dict = {}           # name → fitted estimator
        self.is_trained = False
        self.n_train_trades = 0
        self.last_trained: Optional[str] = None
        self.feature_importance: Dict = {}
        self._load_models()

    # ── Training ──────────────────────────────────────────────────────────────

    def train(self, trades: list, pair_filter: Optional[str] = None) -> bool:
        """
        Train ensemble on WFTrade list.
        Returns True if training succeeded.
        pair_filter: if given, only train on this pair's trades.
        """
        if pair_filter:
            trades = [t for t in trades if getattr(t, "pair", "") == pair_filter]

        # Only use test-set trades with realistic outcome
        valid = [t for t in trades
                 if getattr(t, "split", "test") == "test"
                 and getattr(t, "realistic_outcome", "") in ("win", "loss")]

        if len(valid) < MIN_TRAIN_TRADES:
            logger.debug(
                f"ML: only {len(valid)} valid trades — need {MIN_TRAIN_TRADES} to train"
            )
            return False

        X_list, y_list = [], []
        for t in valid:
            feat = _encode_trade(t)
            if feat is not None:
                X_list.append(feat)
                y_list.append(1 if t.realistic_outcome == "win" else 0)

        if len(X_list) < MIN_TRAIN_TRADES:
            return False

        X = np.array(X_list)
        y = np.array(y_list)

        trained_any = False
        new_models: Dict = {}

        # 1. RandomForest (always available)
        try:
            from sklearn.ensemble import RandomForestClassifier
            from sklearn.calibration import CalibratedClassifierCV
            rf = RandomForestClassifier(
                n_estimators=100, max_depth=5,
                min_samples_leaf=5, random_state=42, n_jobs=-1,
            )
            rf.fit(X, y)
            new_models["rf"] = rf
            trained_any = True
            # Feature importance
            self.feature_importance["rf"] = rf.feature_importances_.tolist()
        except Exception as e:
            logger.debug(f"RF training failed: {e}")

        # 2. XGBoost (optional)
        try:
            import xgboost as xgb
            xgb_model = xgb.XGBClassifier(
                n_estimators=100, max_depth=4, learning_rate=0.1,
                eval_metric="logloss", random_state=42,
                use_label_encoder=False,
            )
            xgb_model.fit(X, y)
            new_models["xgb"] = xgb_model
            trained_any = True
        except ImportError:
            logger.debug("XGBoost not installed — skipping")
        except Exception as e:
            logger.debug(f"XGBoost training failed: {e}")

        # 3. LightGBM (optional)
        try:
            import lightgbm as lgb
            lgb_model = lgb.LGBMClassifier(
                n_estimators=100, max_depth=4, learning_rate=0.1,
                random_state=42, verbose=-1,
            )
            lgb_model.fit(X, y)
            new_models["lgb"] = lgb_model
            trained_any = True
        except ImportError:
            logger.debug("LightGBM not installed — skipping")
        except Exception as e:
            logger.debug(f"LightGBM training failed: {e}")

        # 4. GradientBoosting (sklearn fallback when xgb/lgb missing)
        if "xgb" not in new_models and "lgb" not in new_models:
            try:
                from sklearn.ensemble import GradientBoostingClassifier
                gb = GradientBoostingClassifier(
                    n_estimators=80, max_depth=3, learning_rate=0.1,
                    random_state=42,
                )
                gb.fit(X, y)
                new_models["gb"] = gb
                trained_any = True
            except Exception as e:
                logger.debug(f"GradientBoosting training failed: {e}")

        if trained_any:
            self.models = new_models
            self.is_trained = True
            self.n_train_trades = len(X_list)
            self.last_trained = datetime.utcnow().isoformat()
            self._save_models()
            win_rate = float(np.mean(y))
            logger.info(
                f"ML trained: {len(new_models)} models | {len(X_list)} trades | "
                f"base WR={win_rate:.1%} | models={list(new_models.keys())}"
            )

        return trained_any

    # ── Scoring ───────────────────────────────────────────────────────────────

    def score(self, trade) -> float:
        """Return ensemble win-probability for a trade (0.0 – 1.0)."""
        if not self.is_trained or not self.models:
            return 0.5   # neutral when untrained

        feat = _encode_trade(trade)
        if feat is None:
            return 0.5

        X = feat.reshape(1, -1)
        probs = []
        for name, model in self.models.items():
            try:
                p = model.predict_proba(X)[0][1]
                probs.append(float(p))
            except Exception:
                pass

        return float(np.mean(probs)) if probs else 0.5

    def score_features(self, features: np.ndarray) -> float:
        """Score raw feature vector directly."""
        if not self.is_trained or not self.models:
            return 0.5
        X = features.reshape(1, -1)
        probs = []
        for model in self.models.values():
            try:
                probs.append(float(model.predict_proba(X)[0][1]))
            except Exception:
                pass
        return float(np.mean(probs)) if probs else 0.5

    def passes_threshold(self, trade) -> bool:
        return self.score(trade) >= ML_SCORE_THRESHOLD

    def score_trades_batch(self, trades: list) -> List[float]:
        """Score a list of trades, return list of probabilities."""
        return [self.score(t) for t in trades]

    # ── Persistence ───────────────────────────────────────────────────────────

    def _save_models(self):
        try:
            for name, model in self.models.items():
                path = os.path.join(MODELS_DIR, f"{name}.pkl")
                with open(path, "wb") as f:
                    pickle.dump(model, f)
            meta = {
                "models":         list(self.models.keys()),
                "n_train_trades": self.n_train_trades,
                "last_trained":   self.last_trained,
                "threshold":      ML_SCORE_THRESHOLD,
                "feature_importance": self.feature_importance,
            }
            with open(os.path.join(MODELS_DIR, "meta.json"), "w") as f:
                json.dump(meta, f, indent=2)
        except Exception as e:
            logger.warning(f"ML model save failed: {e}")

    def _load_models(self):
        meta_path = os.path.join(MODELS_DIR, "meta.json")
        if not os.path.exists(meta_path):
            return
        try:
            with open(meta_path) as f:
                meta = json.load(f)
            loaded = {}
            for name in meta.get("models", []):
                pkl = os.path.join(MODELS_DIR, f"{name}.pkl")
                if os.path.exists(pkl):
                    with open(pkl, "rb") as f:
                        loaded[name] = pickle.load(f)
            if loaded:
                self.models = loaded
                self.is_trained = True
                self.n_train_trades = meta.get("n_train_trades", 0)
                self.last_trained   = meta.get("last_trained")
                self.feature_importance = meta.get("feature_importance", {})
                logger.info(
                    f"ML models loaded: {list(loaded.keys())} | "
                    f"trained on {self.n_train_trades} trades | "
                    f"last={self.last_trained}"
                )
        except Exception as e:
            logger.debug(f"ML model load failed: {e}")

    # ── Reporting ─────────────────────────────────────────────────────────────

    def summary(self) -> Dict:
        return {
            "trained":       self.is_trained,
            "models":        list(self.models.keys()),
            "n_train":       self.n_train_trades,
            "last_trained":  self.last_trained,
            "threshold":     ML_SCORE_THRESHOLD,
            "hard_filter":   ML_HARD_FILTER,
        }

    def evaluate_trades(self, trades: list) -> Dict:
        """
        Score a batch and compute calibration metrics.
        Returns accuracy, mean score of winners vs losers.
        """
        if not self.is_trained:
            return {"trained": False}

        scored = [
            (self.score(t), t.realistic_outcome)
            for t in trades
            if getattr(t, "realistic_outcome", "") in ("win", "loss")
        ]
        if not scored:
            return {"trained": True, "n_scored": 0}

        high_conf_wins  = sum(1 for s, o in scored if s >= ML_SCORE_THRESHOLD and o == "win")
        high_conf_total = sum(1 for s, _ in scored if s >= ML_SCORE_THRESHOLD)
        hc_wr = high_conf_wins / high_conf_total if high_conf_total else 0

        low_conf_wins   = sum(1 for s, o in scored if s < ML_SCORE_THRESHOLD and o == "win")
        low_conf_total  = sum(1 for s, _ in scored if s < ML_SCORE_THRESHOLD)
        lc_wr = low_conf_wins / low_conf_total if low_conf_total else 0

        return {
            "trained":          True,
            "n_scored":         len(scored),
            "high_conf_wr":     round(hc_wr, 4),
            "high_conf_trades": high_conf_total,
            "low_conf_wr":      round(lc_wr, 4),
            "low_conf_trades":  low_conf_total,
            "lift":             round(hc_wr - lc_wr, 4),
        }

    # MT5_PENDING: apply hard filter in live signal generation when MT5 connected
    # MT5_PENDING: retrain daily using live execution results (not just backtest)
    # ML_PENDING:  add LSTM for sequential pattern recognition
    # ML_PENDING:  add RL agent for entry/exit timing optimization
