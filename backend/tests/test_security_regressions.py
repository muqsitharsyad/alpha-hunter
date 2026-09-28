"""Security regressions for the single-operator paper service."""
import pytest
from fastapi.testclient import TestClient
from app.config import Settings
from app.main import app


def test_production_rejects_demo_and_weak_secret():
    with pytest.raises(ValueError):
        Settings(app_env="production", demo_mode=True, secret_key="weak",
                 public_origin="https://example.test", _env_file=None)


def test_public_signup_is_closed_by_default():
    with TestClient(app) as client:
        response = client.post("/auth/register", json={"email": "a@example.test", "password": "not-a-real-pass"})
    assert response.status_code == 403


def test_cookie_post_requires_same_origin():
    with TestClient(app) as client:
        response = client.post("/app/ui/bot/start", cookies={"ah_token": "invalid"},
                               headers={"Origin": "https://evil.example"})
    assert response.status_code == 403


def test_dashboard_api_rejects_missing_bearer():
    with TestClient(app) as client:
        assert client.get("/dashboard/summary").status_code == 401


def test_bootstrap_rejects_database_containing_demo_account():
    from app.seed import bootstrap_operator
    with TestClient(app):
        with pytest.raises(RuntimeError, match="demo account"):
            bootstrap_operator("operator@example.test", "a-secure-bootstrap-passphrase")


def test_learning_get_never_mutates_tournament():
    from app.auth import create_token
    from app.db import session_scope
    from app.models import TournamentRun, User
    with TestClient(app) as client:
        with session_scope() as db:
            uid = db.query(User).filter_by(email="demo@alphahunter.local").one().id
            before = db.query(TournamentRun).count()
        response = client.get("/app/learning?run=1", cookies={"ah_token": create_token(uid)})
        assert response.status_code == 200
        with session_scope() as db:
            assert db.query(TournamentRun).count() == before


def test_ui_close_all_rejects_stale_mark_and_keeps_position_open():
    import datetime as dt
    from uuid import uuid4
    from app.auth import create_token
    from app.db import session_scope
    from app.execution import EmergencyLayer
    from app.models import PaperAccount, PaperPosition, Token, User
    from app.seed import seed_demo_user

    seed_demo_user()
    with session_scope() as db:
        user = User(email=f"ui-close-{uuid4().hex}@example.test", password_hash="disabled")
        token = Token(chain="solana", address=uuid4().hex)
        db.add_all([user, token])
        db.flush()
        account = PaperAccount(user_id=user.id)
        db.add(account)
        db.flush()
        pos = PaperPosition(account_id=account.id, token_id=token.id, chain="solana",
                            qty=1, qty_open=1, avg_entry=100, notional_usd=100,
                            last_price=110,
                            last_price_ts=dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1))
        db.add(pos)
        db.flush()
        uid, account_id, pid = user.id, account.id, pos.id
    EmergencyLayer().set_state("running", account_id)
    from app.auth import require_operator
    assert require_operator(uid) == uid
    with TestClient(app) as client:
        response = client.post("/app/ui/bot/close-all", cookies={"ah_token": create_token(uid)},
                               headers={"Origin": "http://testserver"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/app/settings?error=")
    with session_scope() as db:
        assert db.get(PaperPosition, pid).status == "open"
    assert EmergencyLayer().get_state()["status"] == "paused"


def test_opportunities_detail_renders_legacy_feasibility_without_slippage_key():
    from uuid import uuid4
    from app.auth import create_token
    from app.db import session_scope
    from app.execution import EmergencyLayer
    from app.models import Opportunity, PaperAccount, Token
    from app.seed import seed_demo_user

    uid = seed_demo_user()
    with session_scope() as db:
        account = db.query(PaperAccount).filter_by(user_id=uid).first()
        account_id = account.id
        token = Token(chain="solana", address=uuid4().hex, symbol="LEGACY", name="Legacy Token")
        db.add(token)
        db.flush()
        opportunity = Opportunity(
            token_id=token.id, chain="solana", symbol="LEGACY", scanner="listed",
            score=72.0, decision="WATCH", explanation="legacy opportunity fixture",
            components={"composite": 72.0, "consensus": 60.0},
            gate_results={"passed": False, "failures": ["copy feasibility failed"]},
            feasibility={"our_size": 125.0}, features={"pool": "legacy-pool"},
            regime="sideways", strategy_version="test@1.0.0",
            feature_hash="legacy1234567890",
        )
        db.add(opportunity)
        db.flush()
        opp_id = opportunity.id
    EmergencyLayer().set_state("idle", account_id)

    with TestClient(app) as client:
        response = client.get(
            f"/app/opportunities?opp={opp_id}",
            cookies={"ah_token": create_token(uid)},
        )
    assert response.status_code == 200
    assert "LEGACY" in response.text
    assert "slippage" in response.text

    with TestClient(app) as client:
        filtered = client.get(
            "/app/opportunities?decision=WATCH",
            cookies={"ah_token": create_token(uid)},
        )
        missing = client.get(
            "/app/opportunities?opp=999999999",
            cookies={"ah_token": create_token(uid)},
        )
    assert filtered.status_code == 200
    assert "LEGACY" in filtered.text
    assert "WATCH" in filtered.text
    assert missing.status_code == 200
    assert "Evaluated candidates" in missing.text


@pytest.mark.parametrize(
    ("feasibility", "expected"),
    [
        ({}, "slippage - bps"),
        ({"slippage_bps": None, "our_size": None}, "slippage - bps"),
        ({"slippage_bps": 0, "our_size": 0}, "slippage 0 bps"),
    ],
)
def test_opportunities_preserves_unknown_and_real_zero_values(feasibility, expected):
    from uuid import uuid4
    from app.auth import create_token
    from app.db import session_scope
    from app.execution import EmergencyLayer
    from app.models import Opportunity, PaperAccount, Token
    from app.seed import seed_demo_user

    uid = seed_demo_user()
    with session_scope() as db:
        account = db.query(PaperAccount).filter_by(user_id=uid).first()
        token = Token(chain="solana", address=uuid4().hex, symbol="SEMANTIC", name="Semantic Token")
        db.add(token)
        db.flush()
        opportunity = Opportunity(
            token_id=token.id, chain="solana", symbol="SEMANTIC", scanner="listed",
            score=72.0, decision="WATCH", explanation="semantic fixture",
            components={"composite": 72.0, "consensus": 60.0},
            gate_results={"passed": False, "failures": ["copy feasibility failed"]},
            feasibility=feasibility, features={"pool": "semantic-pool"},
            regime="sideways", strategy_version="test@1.0.0",
            feature_hash="semantic1234567890",
        )
        db.add(opportunity)
        db.flush()
        opp_id = opportunity.id
    EmergencyLayer().set_state("idle", account.id)

    with TestClient(app) as client:
        response = client.get(f"/app/opportunities?opp={opp_id}", cookies={"ah_token": create_token(uid)})
    assert response.status_code == 200
    assert expected in response.text


def test_operator_validation_rejects_demo_only_database():
    from app.seed import validate_operator
    with TestClient(app):
        with pytest.raises(RuntimeError, match="operator"):
            validate_operator()


def test_manual_close_does_not_fabricate_fill_from_stale_last_mark():
    import datetime as dt
    from uuid import uuid4
    from fastapi import HTTPException
    from app.db import session_scope
    from app.main import bot_close_all
    from app.models import PaperAccount, PaperPosition, Token, User
    with session_scope() as db:
        user = User(email=f"close-{uuid4().hex}@example.test", password_hash="disabled")
        token = Token(chain="solana", address=uuid4().hex)
        db.add_all([user, token])
        db.flush()
        account = PaperAccount(user_id=user.id)
        db.add(account)
        db.flush()
        pos = PaperPosition(account_id=account.id, token_id=token.id, chain="solana",
                            qty=1, qty_open=1, avg_entry=100, notional_usd=100,
                            last_price=110,
                            last_price_ts=dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1))
        db.add(pos)
        db.flush()
        uid, pid = user.id, pos.id
    with pytest.raises(HTTPException) as err:
        bot_close_all(uid=uid)
    assert err.value.status_code == 409
    with session_scope() as db:
        assert db.get(PaperPosition, pid).status == "open"
