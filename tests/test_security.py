"""Tests de sécurité — propriétés critiques du socle d'authentification.

Couvre la dépendance `get_current_user` (utilisée par TOUS les endpoints
protégés) et les générateurs cryptographiques. La blacklist Redis est stubée
(on teste la logique de type/état de token, pas Redis).
"""
import os
import re
import uuid

import pytest
import pytest_asyncio

TEST_URL = os.environ.get("DATABASE_TEST_URL", "sqlite+aiosqlite:////tmp/sonaiyaa_test_security.db")


@pytest_asyncio.fixture
async def session():
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.core.database import Base
    import app.models  # noqa: F401

    engine = create_async_engine(TEST_URL, future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture(autouse=True)
def _stub_blacklist(monkeypatch):
    """get_current_user consulte la blacklist Redis — ici aucun token n'est révoqué."""
    import app.utils.dependencies as deps

    async def _never_blacklisted(_jti):
        return False

    monkeypatch.setattr(deps, "is_token_blacklisted", _never_blacklisted)


async def _creer_user(session, active=True):
    from app.models.user import User, UserRole
    user = User(
        id=uuid.uuid4(),
        phone=f"+224600{uuid.uuid4().int % 1000000:06d}",
        role=UserRole.EXPEDITEUR,
        is_verified=True,
        is_active=active,
    )
    session.add(user)
    await session.commit()
    return user


def _creds(token):
    from fastapi.security import HTTPAuthorizationCredentials
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


# ── Générateurs crypto ────────────────────────────────────────────────────────

class TestGenerateurs:
    def test_otp_est_6_chiffres(self):
        from app.core.security import generate_otp
        for _ in range(200):
            code = generate_otp()
            assert re.fullmatch(r"\d{6}", code), code
            assert 100_000 <= int(code) <= 999_999

    def test_code_livraison_est_4_chiffres(self):
        from app.core.security import generate_delivery_code
        for _ in range(200):
            code = generate_delivery_code()
            assert re.fullmatch(r"\d{4}", code), code
            assert 1_000 <= int(code) <= 9_999

    def test_otp_varie(self):
        # Un générateur figé (bug) sortirait toujours la même valeur.
        from app.core.security import generate_otp
        assert len({generate_otp() for _ in range(50)}) > 1


# ── get_current_user : type de token ─────────────────────────────────────────

class TestTokenType:
    async def test_refresh_token_refuse(self, session):
        """Un refresh token (valable 30 j) ne DOIT jamais authentifier une requête API."""
        from fastapi import HTTPException
        from app.core.security import create_refresh_token
        from app.utils.dependencies import get_current_user
        user = await _creer_user(session)
        token = create_refresh_token({"sub": str(user.id)})
        with pytest.raises(HTTPException) as exc:
            await get_current_user(credentials=_creds(token), db=session)
        assert exc.value.status_code == 401

    async def test_access_token_accepte(self, session):
        from app.core.security import create_access_token
        from app.utils.dependencies import get_current_user
        user = await _creer_user(session)
        token = create_access_token({"sub": str(user.id)})
        got = await get_current_user(credentials=_creds(token), db=session)
        assert got.id == user.id

    async def test_token_illisible_refuse(self, session):
        from fastapi import HTTPException
        from app.utils.dependencies import get_current_user
        with pytest.raises(HTTPException) as exc:
            await get_current_user(credentials=_creds("pas-un-jwt"), db=session)
        assert exc.value.status_code == 401


# ── get_current_user : état de l'utilisateur ─────────────────────────────────

class TestEtatUser:
    async def test_utilisateur_inconnu_refuse(self, session):
        from fastapi import HTTPException
        from app.core.security import create_access_token
        from app.utils.dependencies import get_current_user
        token = create_access_token({"sub": str(uuid.uuid4())})  # sub sans user en base
        with pytest.raises(HTTPException) as exc:
            await get_current_user(credentials=_creds(token), db=session)
        assert exc.value.status_code == 401

    async def test_compte_desactive_refuse(self, session):
        from fastapi import HTTPException
        from app.core.security import create_access_token
        from app.utils.dependencies import get_current_user
        user = await _creer_user(session, active=False)
        token = create_access_token({"sub": str(user.id)})
        with pytest.raises(HTTPException) as exc:
            await get_current_user(credentials=_creds(token), db=session)
        assert exc.value.status_code == 403
