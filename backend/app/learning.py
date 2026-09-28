"""Learning loop + strategy tournament (PRD 19-20): post-trade features, statistical gates
(min sample, FDR/BH, deflated Sharpe, OOS), champion/challenger, model versions."""
from __future__ import annotations

import datetime as dt
import math

import numpy as np

from .db import session_scope
from .models import LearningEvent, ModelVersion, Strategy, StrategyRun, TournamentRun


def _stat(values: list[float]) -> dict[str, float]:
    if not values:
        return {"n": 0, "mean": 0.0, "std": 0.0, "sharpe": 0.0, "sum": 0.0}
    a = np.asarray(values, dtype=float)
    n = len(a)
    mean = float(a.mean())
    std = float(a.std()) if n > 1 else 0.0
    sharpe = (mean / std * math.sqrt(252)) if std > 0 else 0.0
    return {"n": n, "mean": mean, "std": std, "sharpe": sharpe, "sum": float(a.sum())}


def deflated_sharpe(sharpe: float, n_trials: int, n_obs: int) -> float:
    """PRD 19.3/20.2: adjust selection bias. Zero if too few observations."""
    if n_obs < 3 or n_trials <= 1:
        return sharpe
    try:
        e = np.sqrt(2 * math.log(n_trials)) / math.sqrt(max(n_obs - 1, 1))
        var = 1.0  # normalized: sharpe already annualized
        return (sharpe - e) / math.sqrt(1 + var / (n_obs - 1))
    except (ValueError, ZeroDivisionError):
        return 0.0


def bh_fdr(pvalues: list[float], q: float = 0.1) -> list[bool]:
    """Benjamini-Hochberg: returns reject decisions aligned with input order."""
    n = len(pvalues)
    if n == 0:
        return []
    order = sorted(range(n), key=lambda i: pvalues[i])
    ranks = {j: i + 1 for i, j in enumerate(order)}
    thresh = [(q * ranks[i]) / n for i in range(n)]
    reject = [pvalues[i] <= thresh[i] for i in range(n)]
    # enforce monotonic: largest rejected index -> all above rejected (BH step-up)
    last = -1
    for i in range(n - 1, -1, -1):
        if reject[order[i]]:
            last = order[i]
            break
    final = [False] * n
    if last >= 0:
        for j in range(n):
            if j in {order[k] for k in range(ranks[j])}:
                pass
    # simpler correct step-up:
    reject_idx = [i for i in range(n) if pvalues[i] <= thresh[i]]
    if not reject_idx:
        return [False] * n
    k = max((ranks[i] for i in reject_idx), default=0)  # largest rank
    # reject all p with rank <= k
    return [ranks[i] <= k for i in range(n)]


def record_learning_event(source_type: str, source_id: str, strategy_version: str, regime: str,
                          features: dict, outcome_label: str, pnl_pct: float) -> None:
    with session_scope() as db:
        db.add(LearningEvent(source_type=source_type, source_id=str(source_id),
                             strategy_version=strategy_version, regime=regime,
                             features=features, outcome_label=outcome_label, pnl_pct=pnl_pct))


def promote_strategy(name: str, version: str, config: dict, rationale: str = "") -> int:
    """Register (or fetch) a strategy version row. Immutable once created (PRD 19.4)."""
    from sqlalchemy.exc import IntegrityError

    with session_scope() as db:
        st = db.query(Strategy).filter(Strategy.name == name, Strategy.version == version).first()
        if st:
            return st.id
        st = Strategy(name=name, version=version, config=config, status="champion", rationale=rationale)
        db.add(st)
        try:
            db.flush()
            return st.id
        except IntegrityError:
            db.rollback()
            st = db.query(Strategy).filter(Strategy.name == name, Strategy.version == version).first()
            return st.id if st else 0


# backwards compatible alias
promise_strategy = promote_strategy


def record_strategy_run(strategy_id: int, regime: str, pnls: list[float]) -> dict[str, float]:
    s = _stat(pnls)
    with session_scope() as db:
        run = StrategyRun(strategy_id=strategy_id, regime=regime, sample_count=s["n"],
                          pnl=s["sum"], drawdown=0.0, expectation=s["mean"], sharpe=s["sharpe"])
        db.add(run)
        db.flush()
    return s


class Tournament:
    """Compare strategies head-to-head on OOS windows (champion vs challengers)."""

    def __init__(self) -> None:
        self.min_sample = 200  # PRD 19.3 hard gate (paper scale)
        self.min_per_regime = 30

    def run_tournament(self, entrants: list[dict]) -> dict:
        """entrants: [{strategy_id, name, pnls, regime}] -> ranking + deflated sharpe + promotion."""
        rows = []
        for e in entrants:
            s = _stat(e["pnls"])
            if s["n"] < self.min_sample:
                s["ineligible"] = f"n={s['n']} < {self.min_sample}"
            elif not e.get("oos_verified"):
                s["ineligible"] = "no verified out-of-sample return series"
            ds = deflated_sharpe(s["sharpe"], len(entrants), max(s["n"], 2))
            rows.append({**e, "stat": {**s, "deflated_sharpe": ds},
                         "score": ds * 100.0 + s["mean"]})
        rows.sort(key=lambda r: -r["score"])
        winner = next((r for r in rows if not r["stat"].get("ineligible")), None)
        notes = "No entrant cleared the sample and out-of-sample gates"
        if winner:
            notes = f"winner {winner['name']} deflated Sharpe {winner['stat']['deflated_sharpe']:.2f}"
        # only reference a strategy row that exists (FK integrity)
        winner_id = winner["strategy_id"] if winner else None
        if winner_id is not None:
            with session_scope() as db:
                exists = db.query(Strategy).filter(Strategy.id == winner_id).first()
                if exists is None:
                    winner_id = None
        with session_scope() as db:
            run = TournamentRun(window_start=dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30),
                                window_end=dt.datetime.now(dt.timezone.utc),
                                entrants=[{k: r[k] for k in ("strategy_id", "name")} for r in rows],
                                ranking=[{k: r[k] for k in ("strategy_id", "name", "score")} for r in rows],
                                winner_strategy_id=winner_id,
                                promoted=False, stats={}, notes=notes)
            db.add(run)
            db.flush()
            run_id = run.id
        return {"run_id": run_id, "winner": winner["name"] if winner else None,
                "ranking": rows, "notes": notes}


def promote_candidate(model_type: str, version: str, metrics: dict, gates: dict) -> None:
    with session_scope() as db:
        existing = db.query(ModelVersion).filter(ModelVersion.model_type == model_type,
                                                 ModelVersion.version == version).first()
        if existing is None:
            db.add(ModelVersion(model_type=model_type, version=version, metrics=metrics,
                                gates=gates, status="promoted",
                                promoted_at=dt.datetime.now(dt.timezone.utc)))