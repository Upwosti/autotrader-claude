"""RegimeDetector — LightGBM market regime classifier."""

import os
import pickle
import numpy as np
from datetime import datetime
from typing import Dict, List, Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODELS_DIR = os.path.join(_ROOT, "local_db", "ml_models")
MODEL_PATH = os.path.join(MODELS_DIR, "regime_det.pkl")

REGIME_LABELS = ["trending", "ranging", "volatile", "quiet"]
REGIME_IDX    = {r: i for i, r in enumerate(REGIME_LABELS)}
LOOKBACK      = 20   # bars used for feature calculation


# ── Manual ADX calculation ─────────────────────────────────────────────────────

def _ema(values: np.ndarray, period: int) -> np.ndarray:
    """Exponential moving average via pandas-style recursive formula."""
    result = np.empty_like(values, dtype=float)
    alpha = 1.0 / period
    result[0] = values[0]
    for i in range(1, len(values)):
        result[i] = alpha * values[i] + (1.0 - alpha) * result[i - 1]
    return result


def _calc_adx(high: np.ndarray, low: np.ndarray, close: np.ndarray,
              period: int = 14) -> float:
    """Return the final ADX value (0-100) for the given arrays."""
    n = len(close)
    if n < period + 2:
        return 20.0  # neutral fallback

    tr_arr    = np.zeros(n)
    plus_dm   = np.zeros(n)
    minus_dm  = np.zeros(n)

    for i in range(1, n):
        h_l   = high[i]  - low[i]
        h_pc  = abs(high[i]  - close[i - 1])
        l_pc  = abs(low[i]   - close[i - 1])
        tr_arr[i] = max(h_l, h_pc, l_pc)

        up_move   = high[i]  - high[i - 1]
        down_move = low[i - 1] - low[i]

        plus_dm[i]  = up_move   if (up_move   > down_move and up_move   > 0) else 0.0
        minus_dm[i] = down_move if (down_move > up_move   and down_move > 0) else 0.0

    # Wilder smoothing (RMA)
    def wilder_smooth(arr: np.ndarray, p: int) -> np.ndarray:
        s = np.zeros_like(arr)
        s[p] = arr[1:p + 1].sum()
        for i in range(p + 1, len(arr)):
            s[i] = s[i - 1] - (s[i - 1] / p) + arr[i]
        return s

    tr_smooth  = wilder_smooth(tr_arr, period)
    pdm_smooth = wilder_smooth(plus_dm, period)
    mdm_smooth = wilder_smooth(minus_dm, period)

    eps = 1e-9
    pdi = 100.0 * pdm_smooth / (tr_smooth + eps)
    mdi = 100.0 * mdm_smooth / (tr_smooth + eps)

    dx = 100.0 * np.abs(pdi - mdi) / (pdi + mdi + eps)

    # Smooth DX into ADX
    adx_arr = wilder_smooth(dx, period)
    return float(adx_arr[-1])


# ── Feature extraction ────────────────────────────────────────────────────────

def _extract_features(df, lookback: int = LOOKBACK) -> Optional[np.ndarray]:
    """
    Extract 6 regime features from the last `lookback` bars of a DataFrame.
    Expected columns (case-insensitive): open/high/low/close/volume (optional).
    Returns None if df is too short.
    """
    try:
        cols = {c.lower(): c for c in df.columns}
        df_local = df.copy()
        df_local.columns = [c.lower() for c in df_local.columns]

        if len(df_local) < lookback + 2:
            return None

        window = df_local.iloc[-lookback:]
        high   = window["high"].values.astype(float)
        low    = window["low"].values.astype(float)
        close  = window["close"].values.astype(float)

        # Volume (optional)
        if "volume" in df_local.columns:
            vol = window["volume"].values.astype(float)
            vol_mean = vol.mean()
            volume_ratio = (vol[-1] / vol_mean) if vol_mean > 0 else 1.0
        else:
            volume_ratio = 1.0

        # ADX
        adx_14 = _calc_adx(high, low, close, period=14)

        # ATR ratio: current ATR / 20-bar mean ATR
        atr = np.array([
            max(high[i] - low[i],
                abs(high[i] - close[i - 1]),
                abs(low[i]  - close[i - 1]))
            for i in range(1, len(close))
        ])
        atr_mean = atr.mean() if len(atr) > 0 else 1e-9
        atr_ratio = (atr[-1] / atr_mean) if atr_mean > 0 else 1.0

        # Bollinger Band width
        bb_mean  = close.mean()
        bb_std   = close.std()
        bb_upper = bb_mean + 2 * bb_std
        bb_lower = bb_mean - 2 * bb_std
        bb_width = (bb_upper - bb_lower) / (bb_mean + 1e-9)

        # Trend slope (linear regression, normalised by std)
        x = np.arange(len(close), dtype=float)
        x -= x.mean()
        close_norm = close - close.mean()
        denom = (x * x).sum()
        trend_slope = float((x * close_norm).sum() / denom) / (close.std() + 1e-9) if denom > 0 else 0.0

        # Volatility ratio: std(last 5) / std(last 20)
        std_5  = close[-5:].std()  if len(close) >= 5  else close.std()
        std_20 = close.std()
        volatility_ratio = (std_5 / (std_20 + 1e-9))

        return np.array([
            adx_14, atr_ratio, bb_width, trend_slope, volatility_ratio, volume_ratio
        ], dtype=np.float32)

    except Exception:
        return None


def _rule_based_regime(features: np.ndarray) -> str:
    """Fallback rule-based classification from 6 features."""
    adx_14, atr_ratio, bb_width, trend_slope, volatility_ratio, _ = features
    if atr_ratio > 1.5 or volatility_ratio > 1.8:
        return "volatile"
    if adx_14 > 25:
        return "trending"
    if adx_14 < 20:
        return "ranging"
    if bb_width < 0.02:
        return "quiet"
    return "ranging"


# ── RegimeDetector class ───────────────────────────────────────────────────────

class RegimeDetector:
    """LightGBM (fallback: RandomForest) market regime classifier."""

    def __init__(self):
        self.model = None
        self.last_trained: Optional[datetime] = None
        self._using_lgb = False
        os.makedirs(MODELS_DIR, exist_ok=True)
        self._load()

    # ── Serialisation ─────────────────────────────────────────────────────────

    def _save(self) -> None:
        payload = {
            "model": self.model,
            "last_trained": self.last_trained,
            "using_lgb": self._using_lgb,
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
            self._using_lgb = payload.get("using_lgb", False)
            return True
        except Exception:
            return False

    # ── Label generation ──────────────────────────────────────────────────────

    @staticmethod
    def _label_window(features: np.ndarray) -> int:
        """Assign integer regime label from feature array."""
        adx_14, atr_ratio, bb_width, trend_slope, volatility_ratio, _ = features
        if atr_ratio > 1.5 or volatility_ratio > 1.8:
            return REGIME_IDX["volatile"]
        if adx_14 > 25:
            return REGIME_IDX["trending"]
        if adx_14 < 20 or bb_width < 0.02:
            return REGIME_IDX["quiet"] if bb_width < 0.01 else REGIME_IDX["ranging"]
        return REGIME_IDX["ranging"]

    # ── Training ──────────────────────────────────────────────────────────────

    def train(self, df_list: List) -> bool:
        """Train on multiple DataFrames. Each is windowed with sliding windows."""
        X, y = [], []
        for df in df_list:
            if df is None or len(df) < LOOKBACK + 2:
                continue
            # Slide over the DataFrame in LOOKBACK-bar windows
            for end in range(LOOKBACK, len(df) + 1):
                window_df = df.iloc[max(0, end - LOOKBACK * 2):end]
                feats = _extract_features(window_df, lookback=LOOKBACK)
                if feats is None:
                    continue
                label = self._label_window(feats)
                X.append(feats)
                y.append(label)

        if len(X) < 20:
            return False

        X = np.array(X, dtype=np.float32)
        y = np.array(y, dtype=np.int32)

        try:
            import lightgbm as lgb  # type: ignore
            self.model = lgb.LGBMClassifier(
                n_estimators=200,
                max_depth=5,
                learning_rate=0.05,
                num_leaves=31,
                random_state=42,
                verbose=-1,
            )
            self._using_lgb = True
        except ImportError:
            from sklearn.ensemble import RandomForestClassifier  # type: ignore
            self.model = RandomForestClassifier(
                n_estimators=200,
                max_depth=6,
                random_state=42,
                n_jobs=-1,
            )
            self._using_lgb = False

        self.model.fit(X, y)
        self.last_trained = datetime.utcnow()
        self._save()
        return True

    # ── Inference ─────────────────────────────────────────────────────────────

    def detect(self, df) -> str:
        """Return regime string for the tail of df."""
        feats = _extract_features(df)
        if feats is None:
            return "ranging"
        if self.model is None:
            return _rule_based_regime(feats)
        try:
            pred = int(self.model.predict(feats.reshape(1, -1))[0])
            return REGIME_LABELS[pred] if 0 <= pred < len(REGIME_LABELS) else "ranging"
        except Exception:
            return _rule_based_regime(feats)

    def detect_proba(self, df) -> Dict[str, float]:
        """Return probability dict for all 4 regimes."""
        feats = _extract_features(df)
        if feats is None:
            return {r: 0.25 for r in REGIME_LABELS}
        if self.model is None:
            regime = _rule_based_regime(feats)
            return {r: (0.7 if r == regime else 0.1) for r in REGIME_LABELS}
        try:
            proba = self.model.predict_proba(feats.reshape(1, -1))[0]
            # Align to REGIME_LABELS order via model classes_
            classes = list(self.model.classes_)
            result = {r: 0.0 for r in REGIME_LABELS}
            for cls_idx, cls_val in enumerate(classes):
                if 0 <= cls_val < len(REGIME_LABELS):
                    result[REGIME_LABELS[cls_val]] = float(proba[cls_idx])
            return result
        except Exception:
            regime = _rule_based_regime(feats)
            return {r: (0.7 if r == regime else 0.1) for r in REGIME_LABELS}

    # ── Retraining schedule ───────────────────────────────────────────────────

    def needs_retrain(self, hours: int = 24) -> bool:
        if self.model is None or self.last_trained is None:
            return True
        delta = datetime.utcnow() - self.last_trained
        return delta.total_seconds() > hours * 3600

    # ── Diagnostics ───────────────────────────────────────────────────────────

    def summary(self) -> dict:
        return {
            "trained": self.model is not None,
            "last_trained": self.last_trained.isoformat() if self.last_trained else None,
            "backend": "lightgbm" if self._using_lgb else "random_forest",
            "model_path": MODEL_PATH,
            "regimes": REGIME_LABELS,
        }
