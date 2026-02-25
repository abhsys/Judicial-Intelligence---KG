import os

from services.graph_service import GraphService


class GraphExtractor:
    def __init__(self, graph_service: GraphService) -> None:
        self.graph_service = graph_service
        self.raw_label = os.getenv(
            "NEO4J_CASE_LABEL", "Thunderbit_2ecc9c_20260224_164033.csv"
        )

    def _safe_label(self, label: str) -> str:
        return label.replace("`", "")

    def ensure_constraints(self) -> None:
        statements = [
            (
                "CREATE CONSTRAINT case_key_unique IF NOT EXISTS "
                "FOR (c:Case) REQUIRE c.case_key IS UNIQUE"
            ),
            (
                "CREATE CONSTRAINT court_name_unique IF NOT EXISTS "
                "FOR (c:Court) REQUIRE c.name IS UNIQUE"
            ),
            (
                "CREATE CONSTRAINT party_name_unique IF NOT EXISTS "
                "FOR (p:Party) REQUIRE p.name IS UNIQUE"
            ),
            (
                "CREATE CONSTRAINT order_key_unique IF NOT EXISTS "
                "FOR (o:Order) REQUIRE o.order_key IS UNIQUE"
            ),
        ]
        for statement in statements:
            self.graph_service.run_query(statement)

    def build_case_graph(self) -> dict[str, int | str]:
        self.ensure_constraints()

        safe_raw_label = self._safe_label(self.raw_label)
        query = f"""
        MATCH (r:`{safe_raw_label}`)
        WITH r,
             trim(coalesce(toString(r.`Case Type/Case Number/Case Year`), "")) AS case_key,
             trim(coalesce(toString(r.`Court Complex`), "")) AS court_name,
             trim(coalesce(toString(r.`Petitioner Name versus Respondent Name`), "")) AS parties,
             r.`Order Date` AS order_date,
             trim(coalesce(toString(r.`Order Document URL`), "")) AS order_url
        WHERE case_key <> ""
        WITH r, case_key, court_name, parties, order_date, order_url,
             CASE
                WHEN parties CONTAINS " versus " THEN split(parties, " versus ")
                WHEN parties CONTAINS " vs " THEN split(parties, " vs ")
                ELSE [parties, ""]
             END AS party_parts
        WITH case_key, court_name, order_date, order_url,
             trim(coalesce(party_parts[0], "")) AS petitioner_name,
             trim(coalesce(party_parts[1], "")) AS respondent_name
        MERGE (c:Case {{case_key: case_key}})
        SET c.case_reference = case_key,
            c.order_date = order_date,
            c.order_document_url = CASE WHEN order_url = "" THEN null ELSE order_url END
        FOREACH (_ IN CASE WHEN court_name <> "" THEN [1] ELSE [] END |
            MERGE (ct:Court {{name: court_name}})
            MERGE (c)-[:FILED_IN]->(ct)
        )
        FOREACH (_ IN CASE WHEN petitioner_name <> "" THEN [1] ELSE [] END |
            MERGE (p:Party {{name: petitioner_name}})
            MERGE (c)-[:HAS_PETITIONER]->(p)
        )
        FOREACH (_ IN CASE WHEN respondent_name <> "" THEN [1] ELSE [] END |
            MERGE (p:Party {{name: respondent_name}})
            MERGE (c)-[:HAS_RESPONDENT]->(p)
        )
        WITH c, case_key, order_url, order_date
        WITH c, order_url, order_date,
             CASE
                WHEN order_url <> "" THEN order_url
                ELSE case_key + ":" + coalesce(toString(order_date), "")
             END AS order_key
        MERGE (o:Order {{order_key: order_key}})
        SET o.document_url = CASE WHEN order_url = "" THEN null ELSE order_url END,
            o.order_date = order_date
        MERGE (c)-[:HAS_ORDER]->(o)
        RETURN count(c) AS processed
        """
        result = self.graph_service.run_query(query)
        processed = int(result[0]["processed"]) if result else 0
        return {"raw_label": safe_raw_label, "processed": processed}

    def graph_summary(self) -> dict[str, int]:
        counts_query = """
        RETURN
          size([(c:Case) | c]) AS cases,
          size([(c:Court) | c]) AS courts,
          size([(p:Party) | p]) AS parties,
          size([(o:Order) | o]) AS orders
        """
        rel_query = """
        RETURN
          size([()-[:FILED_IN]->() | 1]) AS filed_in,
          size([()-[:HAS_PETITIONER]->() | 1]) AS has_petitioner,
          size([()-[:HAS_RESPONDENT]->() | 1]) AS has_respondent,
          size([()-[:HAS_ORDER]->() | 1]) AS has_order
        """
        counts = self.graph_service.run_query(counts_query)[0]
        rels = self.graph_service.run_query(rel_query)[0]
        return {
            "cases": int(counts["cases"]),
            "courts": int(counts["courts"]),
            "parties": int(counts["parties"]),
            "orders": int(counts["orders"]),
            "filed_in": int(rels["filed_in"]),
            "has_petitioner": int(rels["has_petitioner"]),
            "has_respondent": int(rels["has_respondent"]),
            "has_order": int(rels["has_order"]),
        }
