"""Ranked search: pure BM25 over the claim store's fields, with a relevance
floor that keeps an honest zero from becoming a confident wrong answer.

No I/O, no imports of `claims`/`gitio`/`project` — this module is built and
queried fresh on every invocation (spec §2), so it must be constructible from
plain in-memory data and safe to call from a benchmark with no store at all.
See docs/specs/2026-09-16-claimlock-search-ranking-design.md §3.1-3.4.
"""
import math
import re

FIELDS = ("id", "head", "area", "src", "body", "ev")
WEIGHTS = {"id": 3.0, "head": 2.0, "area": 1.5, "src": 1.0, "body": 1.0, "ev": 0.5}
K1, B, DISCRIMINATING = 1.2, 0.75, 0.25

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokens(text: str) -> list[str]:
    """Lowercase, split on runs of non-alphanumerics, drop empties."""
    if not text:
        return []
    return _TOKEN_RE.findall(text.lower())


def _candidates(term: str) -> list[str]:
    """Naive suffix-stripped stems for `term`, in the fixed try-order of
    spec §3.2: -s, -es, -ed, -ing→e, -ing. No linguistics (no doubled-letter
    or silent-e repair) — a candidate is used only when it happens to already
    be a term in this corpus."""
    out = []
    if term.endswith("s"):
        out.append(term[:-1])
    if term.endswith("es"):
        out.append(term[:-2])
    if term.endswith("ed"):
        out.append(term[:-2])
    if term.endswith("ing"):
        out.append(term[:-3] + "e")
        out.append(term[:-3])
    return out


def fold(terms: list[str], vocab: set) -> list[str]:
    """Corpus-aware suffix folding (spec §3.2). A term already in `vocab` is
    never touched; otherwise the first candidate stem (in the fixed order
    above) that is itself in `vocab` replaces it, else the term is left as
    written. No stop-word list: BM25's IDF already drives ubiquitous terms
    toward zero, and the relevance floor (§3.4) keeps them from ever being
    the sole basis of a hit."""
    out = []
    for t in terms:
        if t in vocab:
            out.append(t)
            continue
        folded = t
        for cand in _candidates(t):
            if cand in vocab:
                folded = cand
                break
        out.append(folded)
    return out


class Index:
    """Built once from `docs = [(claim_id, {field: text})]`, one entry per
    claim, `field` one of FIELDS. Holds per-field postings, doc frequencies
    and average field lengths, plus a whole-corpus term vocabulary for
    `fold`. Immutable after construction; there is nothing here that a
    second call could invalidate, which is the point of rebuilding it fresh
    every time (spec §2)."""

    def __init__(self, docs: list):
        self.ids = [doc_id for doc_id, _ in docs]
        self.n = len(docs)
        self.vocab = set()
        # field -> doc_id -> token count in that field
        self.field_len = {f: {} for f in FIELDS}
        # field -> term -> {doc_id: tf}
        self.field_freq = {f: {} for f in FIELDS}
        # term -> number of docs containing it in ANY field — the corpus-wide
        # document frequency the relevance floor (§3.4) gates on, distinct
        # from the per-field n_f(t) the BM25 IDF below uses.
        self._doc_freq = {}

        for doc_id, doc_fields in docs:
            doc_terms = set()
            for f in FIELDS:
                text = (doc_fields or {}).get(f) or ""
                toks = tokens(text)
                self.field_len[f][doc_id] = len(toks)
                counts = {}
                for tok in toks:
                    counts[tok] = counts.get(tok, 0) + 1
                    self.vocab.add(tok)
                    doc_terms.add(tok)
                postings = self.field_freq[f]
                for tok, tf in counts.items():
                    postings.setdefault(tok, {})[doc_id] = tf
            for tok in doc_terms:
                self._doc_freq[tok] = self._doc_freq.get(tok, 0) + 1

        self.avg_len = {
            f: (sum(self.field_len[f].values()) / self.n if self.n else 0.0)
            for f in FIELDS
        }

    def doc_freq(self, term: str) -> int:
        """Number of claims mentioning `term` in any field."""
        return self._doc_freq.get(term, 0)

    def is_discriminating(self, term: str) -> bool:
        """spec §3.4: a term is discriminating when its document frequency is
        at most 25% of the corpus. A term absent from the corpus entirely
        (doc_freq 0) is trivially discriminating, but that never matters —
        it has no postings, so it can never make a claim qualify."""
        if self.n == 0:
            return False
        return self.doc_freq(term) / self.n <= DISCRIMINATING

    def unknown(self, terms) -> list:
        """Query terms (post-fold) absent from the corpus vocabulary
        entirely, in the order given — named in the CLI's no-match message
        so a silent miss becomes a useful one."""
        return [t for t in terms if t not in self.vocab]


def search(index: Index, query: str, limit=None) -> list:
    """(score, claim id) pairs, best first, ties broken by claim id, for
    claims that match at least one discriminating query term (spec §3.4).
    `limit` bounds the result; None returns every qualifying hit."""
    raw_terms = tokens(query)
    if not raw_terms:
        return []
    terms = fold(raw_terms, index.vocab)

    scores = {doc_id: 0.0 for doc_id in index.ids}
    qualifies = set()

    # Σ_{t∈q} in spec §3.3 is over the query as written, not its distinct
    # terms — a repeated query term contributes its match more than once.
    for term in terms:
        discriminating = index.is_discriminating(term)
        for f in FIELDS:
            postings = index.field_freq[f].get(term)
            if not postings:
                continue
            n_f = len(postings)
            idf = math.log(1 + (index.n - n_f + 0.5) / (n_f + 0.5))
            avgdl = index.avg_len[f]
            w = WEIGHTS[f]
            for doc_id, tf in postings.items():
                dl = index.field_len[f][doc_id]
                norm = (1 - B + B * (dl / avgdl)) if avgdl else 1.0
                denom = tf + K1 * norm
                scores[doc_id] += w * idf * (tf * (K1 + 1)) / denom
                if discriminating:
                    qualifies.add(doc_id)

    hits = [(scores[doc_id], doc_id) for doc_id in index.ids if doc_id in qualifies]
    hits.sort(key=lambda pair: (-pair[0], pair[1]))
    if limit is not None:
        hits = hits[:limit]
    return hits
