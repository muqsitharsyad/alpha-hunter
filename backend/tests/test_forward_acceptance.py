"""Forward cohorts must use information and prices available at detection."""
import datetime as dt
from uuid import uuid4

from app.db import session_scope
from app.models import SignalQuality, Token, Wallet
from app.providers.base import SwapEvent


def test_forward_cohort_excludes_unqualified_wallets_and_uses_detection_price(pipeline):
    address = str(uuid4())
    with session_scope() as db:
        token = Token(chain="solana", address=address, symbol="FORWARD")
        qualified = Wallet(chain="solana", address=str(uuid4()))
        rejected = Wallet(chain="solana", address=str(uuid4()))
        db.add_all([token, qualified, rejected])
        db.flush()
        token_id, qa, ra = token.id, qualified.address, rejected.address
    def swap(wallet):
        return SwapEvent(chain="solana", tx_hash=str(uuid4()), wallet=wallet,
                         pool_address="pool", token_address=address, symbol="FORWARD",
                         side="buy", base_amount=2000, quote_amount=1000,
                         price_usd=0.5, value_usd=1000,
                         block_time=dt.datetime.now(dt.timezone.utc))
    pipeline._record_signal_quality("solana", address, 1.0, [swap(qa), swap(ra)],
                                    {("solana", qa): "A", ("solana", ra): "C"})
    with session_scope() as db:
        rows = db.query(SignalQuality).filter_by(token_id=token_id).all()
        assert len(rows) == 1
        assert rows[0].entry_price == 1.0
        # The legacy suite shares one DB and inspects all discovered wallets.
        db.query(SignalQuality).filter_by(token_id=token_id).delete()
        db.query(Wallet).filter(Wallet.address.in_([qa, ra])).delete(synchronize_session=False)
        db.query(Token).filter_by(id=token_id).delete()
