"""Alembic migration environment."""

from alembic import context
from sqlalchemy import engine_from_config, pool

from sentinel.config import database_url
from sentinel.incidents import models as incident_models  # noqa: F401
from sentinel.persistence.models import Base
from sentinel.quality import models as quality_models  # noqa: F401
from sentinel.reliability import models as reliability_models  # noqa: F401

config = context.config
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = database_url()
    connectable = engine_from_config(configuration, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
