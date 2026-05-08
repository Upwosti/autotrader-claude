"""
ML Trainer — trains a RandomForest on trade features to predict win probability.
"""

import os
import json
import pickle
import numpy as np
from loguru import logger
from config import MODELS_DIR

try:
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import accuracy_score
    SKLEARN_OK = True
except ImportError:
    SKLEARN_OK = False
    logger.warning("scikit-learn not available — ML training disabled")


FEATURE_COLS = [
    "confidence", "rrr", "session_london", "session_ny",
    "direction_buy", "htf_aligned", "fvg_present", "bos_confirmed",
]

MODEL_PATH = os.path.join(MODELS_DIR, "trade_predictor.pkl")
STATS_PATH = os.path.join(MODELS_DIR, "ml_stats.json")


def _trade_to_features(trade: dict) -> list:
    session = trade.get("session", "")
    direction = trade.get("direction", "")
    return [
        float(trade.get("confidence", 0)),
        float(trade.get("rrr", 0)),
        1.0 if "london" in session.lower() else 0.0,
        1.0 if "ny" in session.lower() or "new_york" in session.lower() else 0.0,
        1.0 if direction == "buy" else 0.0,
        float(trade.get("htf_aligned", 0)),
        float(trade.get("fvg_present", 1)),
        float(trade.get("bos_confirmed", 1)),
    ]


def train(trades: list) -> dict:
    """Train model on list of trade dicts. Returns stats dict."""
    if not SKLEARN_OK:
        return {"error": "sklearn unavailable"}

    wins  = [t for t in trades if t.get("outcome") == "win"]
    total = len(trades)
    if total < 20:
        logger.warning(f"Only {total} trades — need at least 20 to train ML")
        return {"error": f"insufficient data ({total} trades)"}

    X = [_trade_to_features(t) for t in trades]
    y = [1 if t.get("outcome") == "win" else 0 for t in trades]

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42
    )

    model = RandomForestClassifier(n_estimators=100, max_depth=5, random_state=42)
    model.fit(X_train, y_train)
    preds = model.predict(X_test)
    acc   = accuracy_score(y_test, preds)

    os.makedirs(MODELS_DIR, exist_ok=True)
    with open(MODEL_PATH, "wb") as f:
        pickle.dump(model, f)

    stats = {
        "accuracy": round(acc, 4),
        "train_samples": len(X_train),
        "test_samples": len(X_test),
        "win_rate_train": round(sum(y_train) / len(y_train), 4),
        "feature_importances": dict(zip(FEATURE_COLS,
                                        model.feature_importances_.round(3).tolist())),
    }
    with open(STATS_PATH, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2)

    logger.info(f"ML model trained — accuracy: {acc:.1%} on {total} trades")
    return stats


def predict_win_prob(trade_features: dict) -> float:
    """Returns win probability 0.0-1.0 using saved model."""
    if not SKLEARN_OK or not os.path.exists(MODEL_PATH):
        return 0.5
    try:
        with open(MODEL_PATH, "rb") as f:
            model = pickle.load(f)
        X = [_trade_to_features(trade_features)]
        prob = model.predict_proba(X)[0][1]
        return round(prob, 3)
    except Exception as e:
        logger.error(f"ML predict error: {e}")
        return 0.5
