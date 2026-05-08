"""LSTMPredictor — directional probability from 50-bar OHLCV sequence."""

import os
import numpy as np
from datetime import datetime
from typing import List, Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODELS_DIR = os.path.join(_ROOT, "local_db", "ml_models")
MODEL_PATH = os.path.join(MODELS_DIR, "lstm_model.pt")

SEQ_LEN = 50
INPUT_SIZE = 5      # open, high, low, close, volume
HIDDEN_SIZE = 64
NUM_LAYERS = 2
DROPOUT = 0.2


# ── Try importing torch ───────────────────────────────────────────────────────

try:
    import torch                         # type: ignore
    import torch.nn as nn               # type: ignore
    _TORCH_AVAILABLE = True
except ImportError:
    _TORCH_AVAILABLE = False


# ── LSTM model definition ─────────────────────────────────────────────────────

if _TORCH_AVAILABLE:
    class _LSTMNet(nn.Module):
        def __init__(self):
            super().__init__()
            self.lstm = nn.LSTM(
                input_size=INPUT_SIZE,
                hidden_size=HIDDEN_SIZE,
                num_layers=NUM_LAYERS,
                batch_first=True,
                dropout=DROPOUT,
            )
            self.fc = nn.Linear(HIDDEN_SIZE, 1)
            self.sigmoid = nn.Sigmoid()

        def forward(self, x):
            # x: (batch, seq_len, input_size)
            out, _ = self.lstm(x)
            # Take last time-step output
            last = out[:, -1, :]
            return self.sigmoid(self.fc(last)).squeeze(-1)
else:
    class _LSTMNet:  # type: ignore
        """Stub when torch is not available."""
        pass


# ── Helper: prepare sequence from DataFrame ───────────────────────────────────

def _prepare_sequence(df, seq_len: int = SEQ_LEN) -> Optional[np.ndarray]:
    """
    Extract and normalise last seq_len bars from df.
    Returns ndarray of shape (seq_len, 5) or None.
    """
    try:
        df_local = df.copy()
        df_local.columns = [c.lower() for c in df_local.columns]

        needed = ["open", "high", "low", "close"]
        for col in needed:
            if col not in df_local.columns:
                return None

        if "volume" not in df_local.columns:
            df_local["volume"] = 1.0

        if len(df_local) < seq_len:
            return None

        window = df_local.iloc[-seq_len:][["open", "high", "low", "close", "volume"]].values.astype(np.float32)

        # Normalise each feature by its own std (avoid division by zero)
        stds = window.std(axis=0)
        stds[stds < 1e-9] = 1.0
        means = window.mean(axis=0)
        window = (window - means) / stds

        return window
    except Exception:
        return None


# ── LSTMPredictor class ───────────────────────────────────────────────────────

class LSTMPredictor:
    """Directional probability estimator using a 2-layer LSTM."""

    def __init__(self):
        self.available = _TORCH_AVAILABLE
        self.model: Optional[object] = None
        self.last_trained: Optional[datetime] = None
        os.makedirs(MODELS_DIR, exist_ok=True)
        if self.available:
            self._build_model()
            self._load()

    # ── Model lifecycle ───────────────────────────────────────────────────────

    def _build_model(self) -> None:
        if not self.available:
            return
        self.model = _LSTMNet()

    def _save(self) -> None:
        if not self.available or self.model is None:
            return
        import torch  # type: ignore
        torch.save({
            "state_dict": self.model.state_dict(),
            "last_trained": self.last_trained,
        }, MODEL_PATH)

    def _load(self) -> bool:
        if not self.available or not os.path.exists(MODEL_PATH):
            return False
        try:
            import torch  # type: ignore
            ckpt = torch.load(MODEL_PATH, map_location="cpu")
            self.model.load_state_dict(ckpt["state_dict"])
            self.model.eval()
            self.last_trained = ckpt.get("last_trained")
            return True
        except Exception:
            return False

    # ── Training ──────────────────────────────────────────────────────────────

    def train(self, df_list: List, epochs: int = 20) -> bool:
        """Train on list of OHLCV DataFrames. Returns False if torch not available."""
        if not self.available:
            return False

        import torch  # type: ignore
        import torch.nn as nn  # type: ignore

        X_list, y_list = [], []

        for df in df_list:
            if df is None:
                continue
            try:
                df_local = df.copy()
                df_local.columns = [c.lower() for c in df_local.columns]

                if "volume" not in df_local.columns:
                    df_local["volume"] = 1.0

                cols = ["open", "high", "low", "close", "volume"]
                if not all(c in df_local.columns for c in ["open", "high", "low", "close"]):
                    continue

                values = df_local[cols].values.astype(np.float32)
                closes = df_local["close"].values.astype(np.float32)

                for i in range(len(values) - SEQ_LEN - 5):
                    seq = values[i:i + SEQ_LEN].copy()
                    # Normalise
                    stds = seq.std(axis=0)
                    stds[stds < 1e-9] = 1.0
                    seq = (seq - seq.mean(axis=0)) / stds

                    # Label: close[t+5] > close[t] * 1.005
                    t_close = closes[i + SEQ_LEN - 1]
                    fut_close = closes[i + SEQ_LEN + 4]
                    label = 1.0 if fut_close > t_close * 1.005 else 0.0

                    X_list.append(seq)
                    y_list.append(label)
            except Exception:
                continue

        if len(X_list) < 10:
            return False

        X_tensor = torch.tensor(np.array(X_list), dtype=torch.float32)
        y_tensor = torch.tensor(np.array(y_list), dtype=torch.float32)

        self._build_model()
        self.model.train()

        optimiser = torch.optim.Adam(self.model.parameters(), lr=1e-3, weight_decay=1e-5)
        criterion = nn.BCELoss()

        batch_size = min(64, len(X_list))
        dataset = torch.utils.data.TensorDataset(X_tensor, y_tensor)
        loader  = torch.utils.data.DataLoader(dataset, batch_size=batch_size, shuffle=True)

        for _ in range(epochs):
            for xb, yb in loader:
                optimiser.zero_grad()
                pred = self.model(xb)
                loss = criterion(pred, yb)
                loss.backward()
                optimiser.step()

        self.model.eval()
        self.last_trained = datetime.utcnow()
        self._save()
        return True

    # ── Inference ─────────────────────────────────────────────────────────────

    def predict(self, df) -> float:
        """Return directional probability 0-1. Falls back to 0.5 on any error."""
        if not self.available or self.model is None:
            return 0.5
        try:
            seq = _prepare_sequence(df, seq_len=SEQ_LEN)
            if seq is None:
                return 0.5
            import torch  # type: ignore
            with torch.no_grad():
                x = torch.tensor(seq, dtype=torch.float32).unsqueeze(0)  # (1, 50, 5)
                prob = self.model(x).item()
            return float(np.clip(prob, 0.0, 1.0))
        except Exception:
            return 0.5

    # ── Retraining schedule ───────────────────────────────────────────────────

    def needs_retrain(self, hours: int = 168) -> bool:
        if self.model is None or self.last_trained is None:
            return True
        delta = datetime.utcnow() - self.last_trained
        return delta.total_seconds() > hours * 3600

    # ── Diagnostics ───────────────────────────────────────────────────────────

    def summary(self) -> dict:
        return {
            "available": self.available,
            "trained": self.model is not None and self.last_trained is not None,
            "last_trained": self.last_trained.isoformat() if self.last_trained else None,
            "seq_len": SEQ_LEN,
            "hidden_size": HIDDEN_SIZE,
            "num_layers": NUM_LAYERS,
            "model_path": MODEL_PATH,
        }
