"""
SkillBuilder — persistent skills library.

Best params per pair, regime, and session saved to local_db/skills.json.
Loaded at every startup so the system never forgets what it has learned.
Skills are only ever updated upward (better WR/score), never overwritten downward.
"""

import json
import os
from datetime import datetime
from typing import Dict, List, Optional
from loguru import logger

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS_PATH = os.path.join(_ROOT, "local_db", "skills.json")

_EMPTY = {
    "per_pair": {},
    "regime_skills": {
        "trending_market":  None,
        "ranging_market":   None,
        "high_volatility":  None,
    },
    "session_skills": {
        "london_session": None,
        "ny_session":     None,
    },
    # New skill dimensions
    "ml_regime_skills": {
        "trending": None,
        "ranging":  None,
        "volatile": None,
        "quiet":    None,
    },
    "volatility_skills": {
        "low_vol":    None,
        "normal_vol": None,
        "high_vol":   None,
    },
    "day_of_week_skills": {
        "Monday": None, "Tuesday": None, "Wednesday": None,
        "Thursday": None, "Friday": None,
    },
    "month_skills": {
        "January": None, "February": None, "March": None, "April": None,
        "May": None, "June": None, "July": None, "August": None,
        "September": None, "October": None, "November": None, "December": None,
    },
    "cross_pair_skills": {},   # {pair1_pair2: best_params_when_both_active}
    "news_event_skills": {
        "pre_nfp":  None,
        "pre_fomc": None,
        "pre_cpi":  None,
        "quiet_week": None,
    },
    "global_best":          None,
    "total_skills_learned": 0,
    "last_updated":         None,
}


class SkillBuilder:
    """
    Maintains a library of best-known parameter sets organised by:
      - pair (XAUUSD, GBPUSD, …)
      - market regime (trending / ranging / volatile)
      - session (London / NY)
      - global (single best overall)

    Skills are persisted to skills.json immediately on every update.
    Rules:
      - A skill is only written when it strictly improves the stored one
      - Skills are never deleted or overwritten with worse values
      - total_skills_learned is a monotonically increasing counter
    """

    def __init__(self):
        os.makedirs(os.path.dirname(SKILLS_PATH), exist_ok=True)
        self.skills: Dict = self._load()

    # ── Persistence ──────────────────────────────────────────────────────────

    def _load(self) -> Dict:
        if os.path.exists(SKILLS_PATH):
            try:
                with open(SKILLS_PATH, encoding="utf-8") as f:
                    data = json.load(f)
                n_pair = len(data.get("per_pair", {}))
                n_regime = sum(1 for v in data.get("regime_skills", {}).values() if v)
                logger.info(
                    f"Skills loaded: {n_pair} pair | {n_regime} regime | "
                    f"total={data.get('total_skills_learned', 0)}"
                )
                return data
            except Exception as e:
                logger.warning(f"Skills load failed ({e}) — starting fresh library")
        import copy
        return copy.deepcopy(_EMPTY)

    def _save(self):
        try:
            with open(SKILLS_PATH, "w", encoding="utf-8") as f:
                json.dump(self.skills, f, indent=2, default=str)
        except Exception as e:
            logger.warning(f"Skills save failed: {e}")

    # ── Update helpers ────────────────────────────────────────────────────────

    def _record(self, params_dict: Dict, wr: float, rrr: float, score: float) -> Dict:
        return {
            "params":      params_dict,
            "wr":          round(wr, 4),
            "rrr":         round(rrr, 4),
            "score":       round(score, 4),
            "learned_at":  datetime.utcnow().isoformat(),
        }

    def update_pair_skill(
        self, pair: str, params_dict: Dict,
        wr: float, rrr: float, score: float,
    ) -> bool:
        """Update pair skill only if wr improves. Returns True if updated."""
        current = self.skills["per_pair"].get(pair) or {}
        if wr <= current.get("wr", 0):
            return False
        self.skills["per_pair"][pair] = self._record(params_dict, wr, rrr, score)
        self.skills["total_skills_learned"] = self.skills.get("total_skills_learned", 0) + 1
        self.skills["last_updated"] = datetime.utcnow().isoformat()
        self._save()
        logger.info(f"Skill updated: {pair} WR={wr:.1%} RRR={rrr:.2f} score={score:.4f}")
        return True

    def update_regime_skill(
        self, regime: str, params_dict: Dict,
        wr: float, rrr: float,
    ) -> bool:
        current = self.skills["regime_skills"].get(regime) or {}
        if wr <= current.get("wr", 0):
            return False
        self.skills["regime_skills"][regime] = self._record(params_dict, wr, rrr, wr * rrr)
        self.skills["last_updated"] = datetime.utcnow().isoformat()
        self._save()
        logger.info(f"Regime skill updated: {regime} WR={wr:.1%}")
        return True

    def update_global_best(
        self, params_dict: Dict,
        wr: float, rrr: float, score: float,
    ) -> bool:
        current = self.skills.get("global_best") or {}
        if score <= current.get("score", 0):
            return False
        self.skills["global_best"] = self._record(params_dict, wr, rrr, score)
        self.skills["last_updated"] = datetime.utcnow().isoformat()
        self._save()
        logger.info(f"Global best updated: WR={wr:.1%} RRR={rrr:.2f} score={score:.4f}")
        return True

    def update_ml_regime_skill(
        self, ml_regime: str, params_dict: Dict,
        wr: float, rrr: float,
    ) -> bool:
        """Update skill for a specific ML-detected regime."""
        if ml_regime not in self.skills.get("ml_regime_skills", {}):
            if "ml_regime_skills" not in self.skills:
                self.skills["ml_regime_skills"] = {}
            self.skills["ml_regime_skills"][ml_regime] = None
        current = self.skills["ml_regime_skills"].get(ml_regime) or {}
        if wr <= current.get("wr", 0):
            return False
        self.skills["ml_regime_skills"][ml_regime] = self._record(params_dict, wr, rrr, wr * rrr)
        self.skills["last_updated"] = datetime.utcnow().isoformat()
        self._save()
        logger.info(f"ML regime skill updated: {ml_regime} WR={wr:.1%}")
        return True

    def update_volatility_skill(
        self, vol_level: str, params_dict: Dict,
        wr: float, rrr: float,
    ) -> bool:
        """Update skill for a volatility level (low_vol/normal_vol/high_vol)."""
        if "volatility_skills" not in self.skills:
            self.skills["volatility_skills"] = {}
        current = self.skills["volatility_skills"].get(vol_level) or {}
        if wr <= current.get("wr", 0):
            return False
        self.skills["volatility_skills"][vol_level] = self._record(params_dict, wr, rrr, wr * rrr)
        self.skills["last_updated"] = datetime.utcnow().isoformat()
        self._save()
        logger.info(f"Volatility skill updated: {vol_level} WR={wr:.1%}")
        return True

    def update_day_of_week_skill(
        self, day: str, params_dict: Dict,
        wr: float, rrr: float,
    ) -> bool:
        """Update skill for a day of week."""
        if "day_of_week_skills" not in self.skills:
            self.skills["day_of_week_skills"] = {}
        current = self.skills["day_of_week_skills"].get(day) or {}
        if wr <= current.get("wr", 0):
            return False
        self.skills["day_of_week_skills"][day] = self._record(params_dict, wr, rrr, wr * rrr)
        self.skills["last_updated"] = datetime.utcnow().isoformat()
        self._save()
        return True

    def update_month_skill(
        self, month: str, params_dict: Dict,
        wr: float, rrr: float,
    ) -> bool:
        """Update skill for a calendar month."""
        if "month_skills" not in self.skills:
            self.skills["month_skills"] = {}
        current = self.skills["month_skills"].get(month) or {}
        if wr <= current.get("wr", 0):
            return False
        self.skills["month_skills"][month] = self._record(params_dict, wr, rrr, wr * rrr)
        self.skills["last_updated"] = datetime.utcnow().isoformat()
        self._save()
        return True

    def try_skill_inheritance(
        self, source_pair: str, target_pair: str,
        params_dict: Dict, target_wr: float, target_rrr: float,
    ) -> bool:
        """
        Skill inheritance: apply source_pair's best skill to target_pair.
        Keeps only if target_wr improves over stored skill for target_pair.
        E.g. XAUUSD trending skill → test on XAGUSD trending.
        """
        current_target = self.skills["per_pair"].get(target_pair) or {}
        if target_wr <= current_target.get("wr", 0):
            return False
        score = target_wr * target_rrr
        self.skills["per_pair"][target_pair] = self._record(params_dict, target_wr, target_rrr, score)
        self.skills["total_skills_learned"] = self.skills.get("total_skills_learned", 0) + 1
        self.skills["last_updated"] = datetime.utcnow().isoformat()
        self._save()
        logger.info(f"Skill inherited: {source_pair}→{target_pair} WR={target_wr:.1%}")
        return True

    def get_best_params_for_ml_regime(self, ml_regime: str) -> Optional[Dict]:
        skill = self.skills.get("ml_regime_skills", {}).get(ml_regime)
        return skill["params"] if skill else None

    def get_best_params_for_volatility(self, vol_level: str) -> Optional[Dict]:
        skill = self.skills.get("volatility_skills", {}).get(vol_level)
        return skill["params"] if skill else None

    def update_from_best_result(self, best_params, best_result: Dict):
        """
        Convenience: update global best + all per-pair skills from a backtest result.
        Also updates ML regime, volatility, day-of-week, month skills.
        Call whenever a new best is found in the evolution loop.
        """
        if best_params is None or not best_result:
            return
        params_dict = best_params.to_dict()
        wr  = best_result.get("xauusd_win_rate_realistic",
                              best_result.get("xauusd_win_rate", 0))
        rrr = best_result.get("aggregate_avg_rrr", 1.0)
        score = best_result.get("aggregate_score", 0)

        self.update_global_best(params_dict, wr, rrr, score)

        for pair_name, pstats in best_result.get("per_pair", {}).items():
            pair_wr  = pstats.get("test_win_rate_realistic",
                                  pstats.get("test_win_rate", 0))
            pair_rrr = pstats.get("test_avg_rrr_realistic",
                                  pstats.get("test_avg_rrr", 0))
            pair_sc  = pstats.get("composite_score", 0)
            if pair_wr > 0:
                self.update_pair_skill(pair_name, params_dict, pair_wr, pair_rrr, pair_sc)

        # Update ML regime skill based on dominant regime in result
        ml_regime = best_result.get("dominant_regime", "")
        if ml_regime:
            self.update_ml_regime_skill(ml_regime, params_dict, wr, rrr)

        # Update volatility skill
        atr_ratio = best_result.get("avg_atr_ratio", 1.0)
        if atr_ratio < 0.7:
            self.update_volatility_skill("low_vol", params_dict, wr, rrr)
        elif atr_ratio > 1.5:
            self.update_volatility_skill("high_vol", params_dict, wr, rrr)
        else:
            self.update_volatility_skill("normal_vol", params_dict, wr, rrr)

        # Skill inheritance: try to apply XAUUSD best to correlated XAGUSD
        xau_skill = self.skills["per_pair"].get("XAUUSD")
        if xau_skill and xau_skill.get("wr", 0) > 0:
            xag_stats = best_result.get("per_pair", {}).get("XAGUSD", {})
            xag_wr = xag_stats.get("test_win_rate_realistic", 0)
            if xag_wr > 0:
                self.try_skill_inheritance(
                    "XAUUSD", "XAGUSD", xau_skill["params"],
                    xag_wr, xag_stats.get("test_avg_rrr_realistic", 1.0)
                )

    # ── Retrieval ─────────────────────────────────────────────────────────────

    def get_best_params_for_pair(self, pair: str) -> Optional[Dict]:
        skill = self.skills["per_pair"].get(pair)
        return skill["params"] if skill else None

    def get_best_params_global(self) -> Optional[Dict]:
        skill = self.skills.get("global_best")
        return skill["params"] if skill else None

    def get_best_wr_for_pair(self, pair: str) -> float:
        skill = self.skills["per_pair"].get(pair)
        return skill["wr"] if skill else 0.0

    def summary_lines(self) -> List[str]:
        lines = [f"Skills library: {self.total_skills} skills learned"]
        for pair_name, sk in sorted(
            self.skills["per_pair"].items(),
            key=lambda x: x[1]["wr"],
            reverse=True,
        )[:5]:
            lines.append(f"  {pair_name:8}: WR={sk['wr']:.1%} RRR={sk['rrr']:.2f}")
        return lines

    @property
    def total_skills(self) -> int:
        return self.skills.get("total_skills_learned", 0)
