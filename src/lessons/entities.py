"""Entity-resolution decisions + audit — the release gate before any public bundle.

The extraction model produces node-level noise a grammar can't fix: a variant or
misspelled person ('Gustav Leban' for 'Gustave Le Bon'), a non-entity node
('Austrian fellow' typed as an era), the podcast host captured as a subject
('Wilson'). Auto-deleting these is dangerous — a wrong merge corrupts every
downstream query invisibly — so this module does NOT guess. It:

  1. AUDITS the graph and flags SUSPECT nodes for human review (no mutation);
  2. lets the maintainer record a durable DECISION per suspect
     (rename / drop / merge / keep), keyed by (type, normalized label) so it
     survives re-extraction;
  3. APPLIES recorded decisions deterministically;
  4. backs the release gate: publish --strict refuses while un-decided suspects
     remain, so noise never ships to a public bundle.

Detection is intentionally HIGH-RECALL and advisory: a flag means "a human should
look", never "this will be deleted". 'keep' is a first-class decision so a
legitimately-odd node can be marked reviewed and clear the gate.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from lessons.store import Store

VALID_ACTIONS = {"rename", "drop", "merge", "keep"}

# Heuristics for SUSPECT detection (advisory only). Tuned to the real noise seen
# in extraction, deliberately erring toward flagging (a human clears it).
_HOST_WORDS = {"host", "narrator", "podcast", "interviewer", "welcome"}
# A person label that is a single bare token is a weak signal it's a host handle
# or a partial name worth a look (e.g. 'Wilson'), not a full 'First Last'.
_SINGLE_TOKEN = re.compile(r"^\S+$")


def _norm(label: str) -> str:
    return label.strip().lower()


@dataclass
class Suspect:
    node_id: str
    node_type: str
    label: str
    reasons: list[str]
    decision: str | None = None  # the recorded action, if any

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "type": self.node_type,
            "label": self.label,
            "reasons": self.reasons,
            "decision": self.decision,
        }


def _flag_reasons(node_type: str, label: str) -> list[str]:
    """Return the reasons this node looks suspect, or [] if it looks fine."""
    reasons: list[str] = []
    low = label.lower()
    # whole-word match only — 'hostile'/'hostage' must NOT trip the 'host' rule
    words = set(re.findall(r"[a-z]+", low))
    if words & _HOST_WORDS:
        reasons.append("label contains a host/narrator word")
    if node_type == "person":
        if _SINGLE_TOKEN.match(label.strip()):
            reasons.append("person label is a single token (partial name or host?)")
        if not label[:1].isupper():
            reasons.append("person label is not capitalized")
    if node_type in ("era", "situation") and len(label.split()) <= 1:
        reasons.append(f"{node_type} label is a single word (likely mis-typed)")
    # a label that reads like a description, not an entity
    if node_type in ("person", "company") and (" of " in low or "'s " in low):
        reasons.append("label reads like a description, not a proper name")
    return reasons


def recorded_decisions(store: Store) -> dict[tuple[str, str], dict]:
    """All recorded decisions keyed by (node_type, norm_label)."""
    out: dict[tuple[str, str], dict] = {}
    for r in store.conn.execute(
        "SELECT node_type, norm_label, action, target_label, target_type, reason "
        "FROM node_decisions"
    ).fetchall():
        out[(r["node_type"], r["norm_label"])] = dict(r)
    return out


def audit(store: Store) -> list[Suspect]:
    """Scan all nodes and return suspects, annotated with any recorded decision.
    Also flags duplicate-label candidates (same normalized label across types)."""
    decisions = recorded_decisions(store)
    rows = store.conn.execute("SELECT id, type, label FROM nodes").fetchall()

    # duplicate-label detection: same normalized label seen under >1 node id
    by_norm: dict[str, list] = {}
    for r in rows:
        by_norm.setdefault(_norm(r["label"]), []).append(r)

    suspects: list[Suspect] = []
    for r in rows:
        reasons = _flag_reasons(r["type"], r["label"])
        dupes = by_norm.get(_norm(r["label"]), [])
        if len(dupes) > 1:
            reasons.append(f"duplicate label across {len(dupes)} nodes")
        if not reasons:
            continue
        dec = decisions.get((r["type"], _norm(r["label"])))
        suspects.append(
            Suspect(
                node_id=r["id"],
                node_type=r["type"],
                label=r["label"],
                reasons=reasons,
                decision=dec["action"] if dec else None,
            )
        )
    return suspects


def undecided_suspects(store: Store) -> list[Suspect]:
    """Suspects with NO recorded decision — what the release gate blocks on."""
    return [s for s in audit(store) if s.decision is None]


def record_decision(
    store: Store,
    node_type: str,
    label: str,
    action: str,
    *,
    target_label: str | None = None,
    target_type: str | None = None,
    reason: str | None = None,
) -> None:
    """Record (or replace) a durable decision for a (type, label) entity shape."""
    if action not in VALID_ACTIONS:
        raise ValueError(f"unknown action {action!r}; allowed: {sorted(VALID_ACTIONS)}")
    if action == "rename" and not target_label:
        raise ValueError("rename requires target_label")
    if action == "merge" and not target_label:
        raise ValueError("merge requires target_label")
    store.conn.execute(
        """INSERT OR REPLACE INTO node_decisions
           (id, node_type, norm_label, action, target_label, target_type, reason, decided_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            uuid.uuid4().hex, node_type, _norm(label), action,
            target_label, target_type, reason, datetime.now(UTC).isoformat(),
        ),
    )
    store.conn.commit()


@dataclass
class ApplyResult:
    renamed: int = 0
    dropped: int = 0
    merged: int = 0
    kept: int = 0
    missing: int = 0  # decisions whose target node no longer exists


def apply_decisions(store: Store) -> ApplyResult:
    """Apply every recorded decision to the graph, transactionally.

    rename: change the node's label in place.
    drop:   delete the node and its edges + mentions (child-first, no dangling).
    merge:  repoint the node's edges + mentions onto the target node, then delete.
    keep:   no-op (the node was reviewed and judged fine).

    A single transaction: any failure rolls the whole pass back, so a partial
    clean never ships."""
    res = ApplyResult()
    c = store.conn
    try:
        c.execute("BEGIN")
        for d in c.execute(
            "SELECT node_type, norm_label, action, target_label, target_type FROM node_decisions"
        ).fetchall():
            row = c.execute(
                "SELECT id FROM nodes WHERE type=? AND lower(trim(label))=?",
                (d["node_type"], d["norm_label"]),
            ).fetchone()
            if row is None:
                res.missing += 1
                continue
            nid = row["id"]
            action = d["action"]
            if action == "keep":
                res.kept += 1
            elif action == "rename":
                c.execute("UPDATE nodes SET label=? WHERE id=?", (d["target_label"], nid))
                res.renamed += 1
            elif action == "drop":
                c.execute("DELETE FROM edges WHERE src_node_id=? OR dst_node_id=?", (nid, nid))
                c.execute("DELETE FROM mentions WHERE node_id=?", (nid,))
                c.execute("DELETE FROM bios WHERE person_node_id=?", (nid,))
                c.execute("DELETE FROM nodes WHERE id=?", (nid,))
                res.dropped += 1
            elif action == "merge":
                tgt_type = d["target_type"] or d["node_type"]
                tgt = c.execute(
                    "SELECT id FROM nodes WHERE type=? AND lower(trim(label))=?",
                    (tgt_type, _norm(d["target_label"])),
                ).fetchone()
                if tgt is None:
                    res.missing += 1
                    continue
                tgt_id = tgt["id"]
                c.execute("UPDATE edges SET src_node_id=? WHERE src_node_id=?", (tgt_id, nid))
                c.execute("UPDATE edges SET dst_node_id=? WHERE dst_node_id=?", (tgt_id, nid))
                c.execute("UPDATE mentions SET node_id=? WHERE node_id=?", (tgt_id, nid))
                c.execute("UPDATE bios SET person_node_id=? WHERE person_node_id=?", (tgt_id, nid))
                c.execute("DELETE FROM nodes WHERE id=?", (nid,))
                res.merged += 1
        c.execute("COMMIT")
    except Exception:
        c.execute("ROLLBACK")
        raise
    return res
