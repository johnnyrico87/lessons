# NOTICE — Intended use, content, and attribution

**`lessons` is a personal research tool.** It is designed to be run locally, by
an individual, to help *that person* learn from podcasts they already listen to.
It is not a service, not a content host, and not a redistribution platform.

## What the tool does and does not keep

The product of this tool is a **derived knowledge graph** — structured notes
*about* what was said, not a copy of the source material.

| Data | Kept? |
|---|---|
| Podcast **audio (MP3)** | **No.** Downloaded only transiently to transcribe locally, then **deleted.** Never stored long-term, never published. |
| **Full transcript** | **Not by default.** Held only if you opt in (as a cache to re-run extraction), and never published or committed to this repository. |
| **Derived graph** | Yes — nodes, edges, and **short quotations** with a **deep link back to the source** at the exact timestamp. |

In other words: the graph does **not** hold the actual MP3 or full transcript
data. It holds short, attributed excerpts that point you back to the original.

## The graph is notes

The right way to think about the shipped graph is as **one person's notes** from
listening to podcasts. Listening to an episode and writing down "this leader did
X — the lesson was Y," with a pointer to the minute you heard it, is exactly what
a person does with a notebook. The graph is those notes: short quotes, the
timestamp they came from, and the connections the listener drew between people
and ideas. A user who downloads this app receives the maintainer's notes as a
starting point, and extends them with notes of their own. That is categorically
different from redistributing the podcast itself.

## Source material belongs to its creators

- Podcast audio, video, and transcripts are the property of their respective
  creators and rights holders. This tool does **not** claim any rights to them.
- Every item in the graph **cites its source** (show, episode, timestamp) and
  **links back** to the original so you consume the real thing from the creator.
- The short quotations retained are intended to function as citations/pointers,
  in the spirit of fair use for personal study — not as a substitute for the
  work.

## Your responsibility as a user

- You run ingestion against feeds **you choose**, on **your own machine**, for
  **your own personal use**.
- Respect the terms of the sources you point it at and the rights of their
  creators. Do not use this tool to redistribute transcripts, audio, or bulk
  copies of anyone's work.
- Prefer sources published for consumption (e.g. a podcast's public **RSS
  feed**, whose audio enclosures are meant to be fetched by podcast clients)
  over scraping platforms whose terms prohibit automated access.

## No legal advice

This notice describes the project's **design intent**; it is not legal advice.
If you intend to operate this at scale, publicly, or commercially, consult a
qualified attorney first — the personal-use, no-redistribution, cite-and-link
posture above is what keeps the footprint small, and changing any of those
assumptions changes the analysis.

## Attribution for bundled/external data

- External biographies (optional `enrich` pass) come from **Wikipedia**
  (CC BY-SA) and **Wikidata** (CC0), fetched locally and attributed in-app with
  their source and license. Bio text is never committed to this repository.
