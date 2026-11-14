"""Request-scoped database and authentication dependencies."""

import secrets
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated, cast

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from sentinel.persistence.database import session_factory
from sentinel.settings import Settings
from sentinel.storage.filesystem import FileStore

bearer = HTTPBearer(auto_error=False)


def settings_for(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def database_session(request: Request) -> Iterator[Session]:
    factory = session_factory(request.app.state.engine)
    with factory() as session:
        session.info["sentinel_storage"] = FileStore(Path(request.app.state.settings.data_dir))
        yield session


def storage_for(request: Request) -> FileStore:
    return cast(FileStore, request.app.state.storage)


def storage_for_session(session: Session) -> FileStore:
    return cast(FileStore, session.info["sentinel_storage"])


SessionDep = Annotated[Session, Depends(database_session)]
SettingsDep = Annotated[Settings, Depends(settings_for)]
CredentialsDep = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]


def require_api_key(
    credentials: CredentialsDep,
    settings: SettingsDep,
) -> None:
    if (
        credentials is None
        or credentials.scheme.lower() != "bearer"
        or not secrets.compare_digest(credentials.credentials, settings.api_key)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Valid bearer API key required",
            headers={"WWW-Authenticate": "Bearer"},
        )
