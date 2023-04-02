import logging
from abc import ABC, abstractmethod
from datetime import datetime
from os import environ
from pathlib import Path

import sqlalchemy as db
from sqlalchemy import String
from sqlalchemy.orm import Mapped, Session, declarative_base, mapped_column

from ipget.errors import ConfigurationError

log = logging.getLogger(__name__)

Base = declarative_base()

TABLE_NAME = "public_ip_address"


class IPInfo(Base):
    __tablename__ = TABLE_NAME

    ID: Mapped[int] = mapped_column(
        primary_key=True, nullable=False, autoincrement=True
    )
    time: Mapped[datetime] = mapped_column(nullable=False)
    ip_address: Mapped[str] = mapped_column(String(80), nullable=True)


class AlchemyDB(ABC):
    def __init__(self) -> None:
        self.table_name: str = TABLE_NAME
        self.database_name: str
        self.created_new_table: bool = False
        self.engine: db.Engine = self.create_engine()
        self.create_table()

    @abstractmethod
    def create_engine(self) -> db.Engine:
        ...

    def write_data(self, datetime: datetime, ip: str) -> int:
        values = IPInfo(time=datetime, ip_address=ip)
        log.info(f"Adding row to table '{self.table_name}' in '{self.database_name}'")
        return self.commit_row(values)

    def create_table(self):
        if not db.inspect(self.engine).has_table(self.table_name):
            log.info(f"Table '{self.table_name}' does not exist, creating")
            self.created_new_table = True
            Base.metadata.create_all(self.engine)

    def commit_row(self, values: IPInfo) -> int:
        log.debug("Creating session")
        with Session(self.engine) as session:
            with session.begin():
                log.debug("Session started, adding data")
                session.add(values)
                log.debug("Committing changes")
                session.commit()
            session.refresh(values)
            new_row_ID = values.ID
            log.info(f"Committed new row to database with ID {new_row_ID}")
        return new_row_ID

    def get_last(self) -> tuple[int, datetime, str] | None:
        log.debug("Retrieving most recent IP from database")
        with Session(self.engine) as session:
            with session.begin():
                log.debug("Session started, fetching data")
                q = db.select(IPInfo).order_by(IPInfo.time.desc()).limit(1)
                result = session.scalars(q).first()
                return (
                    None
                    if result is None
                    else (result.ID, result.time, result.ip_address)
                )


class MySQL(AlchemyDB):
    def __init__(self) -> None:
        self._load_config()
        super().__init__()
        self.database_name = f"{self._database} on {self._host}:{self._port}"

    def _load_config(self):
        self._username: str | None = environ.get("IPGET_MYSQL_USERNAME")
        self._password: str | None = environ.get("IPGET_MYSQL_PASSWORD")
        self._host: str | None = environ.get("IPGET_MYSQL_HOST")
        self._port: int | None = (
            int(port) if (port := environ.get("IPGET_MYSQL_PORT")) else None
        )
        self._database: str | None = environ.get("IPGET_MYSQL_DATABASE")
        required_settings = [
            (self._username, "IPGET_MYSQL_USERNAME"),
            (self._password, "IPGET_MYSQL_PASSWORD"),
            (self._host, "IPGET_MYSQL_HOST"),
            (self._port, "IPGET_MYSQL_PORT"),
            (self._database, "IPGET_MYSQL_DATABASE"),
        ]
        if missing_settings := [e for k, e in required_settings if not k]:
            raise ConfigurationError(", ".join(missing_settings))

    def create_engine(self) -> db.Engine:
        log.debug("Creating database engine")
        dialect = "mysql+pymysql"
        user_pass = f"{self._username}:{self._password}"
        host = f"{self._host}:{self._port}"
        database = self._database
        url = f"{dialect}://{user_pass}@{host}/{database}"
        log.debug(f"SQLAlchemy url: '{url}'")
        return db.create_engine(url)


class SQLite(AlchemyDB):
    def __init__(self) -> None:
        self._load_config()
        super().__init__()
        self.database_name = f"{self._path.name}"

    def _load_config(self):
        self._path: Path = Path(
            environ.get("IPGET_SQLITE_DATABASE", "/app/public_ip.db")
        )

    def create_engine(self) -> db.Engine:
        log.debug("Creating database engine")
        log.debug(f"CWD: {Path.cwd()}")
        dialect = "sqlite"
        database = str(self._path)
        url = f"{dialect}:///{database}"
        log.debug(f"SQLAlchemy url: '{url}'")
        return db.create_engine(url)


def get_database(type: str = environ.get("IPGET_DB_TYPE", "")) -> AlchemyDB:
    log.debug(f"Requested database type is '{type.lower()}'")
    try:
        match type.lower():
            case "mysql":
                return MySQL()
            case "sqlite":
                return SQLite()
            case _:
                raise ConfigurationError("IPGET_DB_TYPE")
    except ConfigurationError as e:
        log.exception(e)
        raise e