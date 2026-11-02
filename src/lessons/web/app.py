"""Pass C — the read-only web app (requirements §4a–4c, §4b, §5; design §7).

FastAPI serving:
  * a situation-first search API (`/api/search`)
  * FTS transcript search (`/api/fts`)
  * splash leader elements (`/api/splash`)
  * node detail with citations (`/api/node/{id}`)
  * static splash + results UI

Read-only: no write endpoints. Bound to 127.0.0.1 by the CLI (never 0.0.0.0).
fastapi/uvicorn are optional extras; `build_app` is import-light so tests can
construct the app with a Store but no running server.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from lessons.search import Search
from lessons.store import Store

STATIC_DIR = Path(__file__).parent / "static"

# Seed set for the splash before extraction has produced person nodes (R§4b.3):
# the three channel hosts + a few well-known leaders, so the page is never empty.
SEED_SPLASH = [
    {
        "label": "David Senra",
        "kind": "host",
        "quote": "Obsessed with the history of entrepreneurship.",
    },
    {"label": "John D. Rockefeller", "kind": "leader", "quote": None},
    {"label": "Andrew Carnegie", "kind": "leader", "quote": None},
    {"label": "Estée Lauder", "kind": "leader", "quote": None},
    {"label": "Edwin Land", "kind": "leader", "quote": None},
]


def build_app(store: Store) -> FastAPI:
    app = FastAPI(title="lessons", docs_url=None, redoc_url=None)
    search = Search(store)

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True}

    @app.get("/api/search")
    def api_search(q: str = Query(..., min_length=1), limit: int = 10) -> dict:
        return search.search(q, limit=limit).to_dict()

    @app.get("/api/graph")
    def api_graph(
        node_type: str = Query("", description="Filter to one node type."),
        limit: int = 500,
    ) -> dict:
        """Nodes + edges in Cytoscape elements format, for the graph explorer.

        The canvas is ACTORS ONLY — people and companies. Concept nodes
        (lessons, principles, experiences, situations, eras) are NOT drawn as
        peers; they are details that belong to an actor and surface in the
        right-hand detail drawer (/api/node/{id}/full). An explicit node_type
        filter still lets you inspect one concept type on its own.
        """
        ACTOR_TYPES = ("person", "company")
        if node_type:
            where = "WHERE type=?"
            params: tuple = (node_type, limit)
        else:
            where = "WHERE type IN (?,?)"
            params = (*ACTOR_TYPES, limit)
        node_rows = store.conn.execute(
            f"SELECT id, type, label FROM nodes {where} LIMIT ?", params
        ).fetchall()
        ids = {r["id"] for r in node_rows}
        # One pass for avatar images (person nodes carry a bio image when
        # `enrich` has run); absent → the UI shows an initials fallback.
        img_by_id = {
            r["person_node_id"]: r["image_url"]
            for r in store.conn.execute(
                "SELECT person_node_id, image_url FROM bios WHERE image_url IS NOT NULL"
            ).fetchall()
        }
        elements = []
        for r in node_rows:
            elements.append(
                {
                    "data": {
                        "id": r["id"],
                        "label": r["label"],
                        "ntype": r["type"],
                        "image_url": img_by_id.get(r["id"]),
                    }
                }
            )
        # edges only between included (visible) nodes
        for e in store.conn.execute(
            "SELECT src_node_id, dst_node_id, relation FROM edges"
        ).fetchall():
            if e["src_node_id"] in ids and e["dst_node_id"] in ids:
                tension = e["relation"] in ("contradicts", "contrasts")
                elements.append(
                    {
                        "data": {
                            "source": e["src_node_id"],
                            "target": e["dst_node_id"],
                            "relation": e["relation"],
                            "tension": tension,
                        }
                    }
                )
        return {"elements": elements, "count": len(node_rows)}

    @app.get("/api/fts")
    def api_fts(q: str = Query(..., min_length=1), limit: int = 20) -> dict:
        rows = store.fts_search(q, limit=limit)
        return {"query": q, "hits": [dict(r) for r in rows]}

    @app.get("/api/splash")
    def api_splash(limit: int = 12) -> dict:
        """Real person nodes + a representative quote once extraction has run;
        the seed set beforehand (R§4b.3)."""
        rows = store.conn.execute(
            "SELECT id, label FROM nodes WHERE type='person' AND origin='podcast' LIMIT ?",
            (limit,),
        ).fetchall()
        elements = []
        for r in rows:
            quote_row = store.conn.execute(
                "SELECT quote FROM mentions WHERE node_id=? ORDER BY length(quote) DESC LIMIT 1",
                (r["id"],),
            ).fetchone()
            bio = store.bio_for(r["id"])
            elements.append(
                {
                    "label": r["label"],
                    "kind": "leader",
                    "quote": quote_row["quote"] if quote_row else None,
                    "image_url": bio["image_url"] if bio else None,
                }
            )
        if not elements:
            elements = SEED_SPLASH
        return {"elements": elements, "seeded": not rows}

    @app.get("/api/node/{node_id}")
    def api_node(node_id: str) -> dict:
        node = store.conn.execute(
            "SELECT id, type, label, summary, origin FROM nodes WHERE id=?", (node_id,)
        ).fetchone()
        if node is None:
            raise HTTPException(status_code=404, detail="node not found")
        out: dict = {
            "id": node["id"],
            "type": node["type"],
            "label": node["label"],
            "summary": node["summary"],
            "origin": node["origin"],
        }
        if node["origin"] == "podcast":
            try:
                out["citations"] = [c.to_dict() for c in store.cite_node(node_id)]
            except Exception:
                out["citations"] = []
        else:
            bio = store.cite_bio(node_id)
            out["citation"] = bio.to_dict() if bio else None

        # A person node (podcast-origin) may ALSO carry an external bio — the
        # "About" panel, always badged external and kept distinct from quotes.
        if node["type"] == "person":
            bio = store.bio_for(node_id)
            if bio:
                out["bio"] = {
                    "source": bio["source"],
                    "source_url": bio["source_url"],
                    "license": bio["license"],
                    "title": bio["title"],
                    "summary": bio["summary"],
                    "image_url": bio["image_url"],
                    "image_license": bio["image_license"],
                    "origin": "external",
                }
        return out

    @app.get("/api/node/{node_id}/full")
    def api_node_full(node_id: str) -> dict:
        """Everything for the leader detail card in one call:
        the base node detail (summary, citations, bio) PLUS the concept facts
        (lessons/principles/experiences/situations tied to this leader) and the
        connected leaders (other person nodes, direct + shared-experience)."""
        base = api_node(node_id)  # reuse the detail assembly (also 404s)
        base["facts"] = store.node_facts(node_id)
        base["connections"] = (
            store.leader_connections(node_id) if base["type"] == "person" else []
        )
        return base

    # --- static UI --------------------------------------------------------
    if STATIC_DIR.exists():
        @app.get("/")
        def index() -> FileResponse:
            return FileResponse(STATIC_DIR / "index.html")

        @app.get("/graph")
        def graph_page() -> FileResponse:
            return FileResponse(STATIC_DIR / "graph.html")

        @app.get("/manifest.webmanifest")
        def manifest() -> FileResponse:
            return FileResponse(
                STATIC_DIR / "manifest.webmanifest", media_type="application/manifest+json"
            )

        @app.get("/sw.js")
        def service_worker() -> FileResponse:
            # Served from the app root so its scope covers the whole origin.
            return FileResponse(STATIC_DIR / "sw.js", media_type="application/javascript")

        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    return app
