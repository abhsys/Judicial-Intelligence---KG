import os
from typing import Any

from neo4j import GraphDatabase
from neo4j.exceptions import Neo4jError
from dotenv import load_dotenv

load_dotenv()


class GraphService:
    def __init__(self) -> None:
        self.uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
        self.username = os.getenv("NEO4J_USERNAME", "neo4j")
        self.password = os.getenv("NEO4J_PASSWORD", "password")
        self.database = os.getenv("NEO4J_DATABASE", "neo4j")
        self.driver = None

    def connect(self) -> None:
        self.driver = GraphDatabase.driver(
            self.uri, auth=(self.username, self.password)
        )
        # Fail fast at startup if DB config is wrong or DB is unreachable.
        self.driver.verify_connectivity()

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
            raise RuntimeError("Neo4j driver is not connected.")

        params = parameters or {}
        with self.driver.session(database=self.database) as session:
            result = session.run(query, params)
            return [record.data() for record in result]

    def get_nodes_by_label(self, label: str, limit: int = 10) -> list[dict[str, Any]]:
        safe_label = label.replace("`", "")
        query = (
            f"MATCH (n:`{safe_label}`) "
            "RETURN properties(n) AS node "
            "LIMIT $limit"
        )
        return self.run_query(query, {"limit": limit})
