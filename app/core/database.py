from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from .config import settings

# Création du moteur de base de données
# echo=False même en DEBUG : les requêtes SQL polluent les logs au quotidien.
# Pour activer ponctuellement, mettre SQLALCHEMY_ECHO=true en env.
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.SQLALCHEMY_ECHO,
    future=True,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20
)

# Session factory
async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False
)

# Base pour les modèles
Base = declarative_base()


async def get_db() -> AsyncSession:
    """Dependency pour obtenir une session de base de données"""
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db():
    """Initialiser la base de données"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db():
    """Fermer la connexion à la base de données"""
    await engine.dispose()
