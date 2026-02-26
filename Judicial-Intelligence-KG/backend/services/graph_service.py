import os
from typing import Any
import logging

from neo4j import GraphDatabase
from neo4j.exceptions import Neo4jError
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


class GraphUnavailableError(RuntimeError):
    pass


class GraphService:
    def __init__(self) -> None:
        self.uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
        self.username = os.getenv("NEO4J_USERNAME", "neo4j")
        self.password = os.getenv("NEO4J_PASSWORD", "password")
        self.database = os.getenv("NEO4J_DATABASE", "neo4j")
        self.driver = None
        self.last_error: str | None = None

    def connect(self) -> None:
        try:
            self.driver = GraphDatabase.driver(
                self.uri, auth=(self.username, self.password)
            )
            self.driver.verify_connectivity()
            self.last_error = None
        except Exception as exc:
            self.last_error = str(exc)
            logger.warning("Neo4j connection unavailable at startup: %s", exc)
            if self.driver is not None:
                self.driver.close()
            self.driver = None

    def close(self) -> None:
        if self.driver is not None:
            self.driver.close()
            self.driver = None

    def health_check(self) -> bool:
        if self.driver is None:
            return False

        try:
            with self.driver.session(database=self.database) as session:
                session.run("RETURN 1 AS ok").single()
            return True
        except Neo4jError:
            return False

    def run_query(
        self, query: str, parameters: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        if self.driver is None:
            message = (
                f"Neo4j is not connected. Start Neo4j at {self.uri} and retry."
            )
            if self.last_error:
                message += f" Details: {self.last_error}"
            raise GraphUnavailableError(message)

        params = parameters or {}
        with self.driver.session(database=self.database) as session:
            result = session.run(query, params)
            return [self._serialize_value(record.data()) for record in result]

    def get_nodes_by_label(self, label: str, limit: int = 10) -> list[dict[str, Any]]:
        safe_label = label.replace("`", "")
        query = (
            f"MATCH (n:`{safe_label}`) "
            "RETURN properties(n) AS node "
            "LIMIT $limit"
        )
        return self.run_query(query, {"limit": limit})

    def _serialize_value(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: self._serialize_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._serialize_value(item) for item in value]

        # Neo4j temporal values expose iso_format(); convert them to JSON-safe strings.
        iso_format = getattr(value, "iso_format", None)
        if callable(iso_format):
            return iso_format()

        return value
