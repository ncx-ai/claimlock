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
K1, B = 1.2, 0.75

# The relevance floor (§3.4, amended 2026-09-16) drops these before deciding
# whether a query has vocabulary in the store. A document-frequency-based
# floor (a term "discriminating" below some doc-frequency ceiling) was tried
# first and failed at real-corpus scale in three separate ways — see the
# spec's §3.4 table and the fix-round report in
# .superpowers/sdd/2026-09-16-claimlock-search-ranking/task-1-report.md:
# document frequency ranks a rare-but-generic word (e.g. "handling", 1
# claim) as more informative than a common-but-real topic word (e.g. "grpc",
# 39% of claims), deletes real answers, admits junk, and — since every term
# in a corpus of N<=3 trivially exceeds any fixed percentage ceiling —
# returns nothing at all for any query against a small store. Absence from
# the vocabulary, not rarity within it, is the signal this list supports.
STOP_WORDS = frozenset("""
    a an the is are was were be been being do does did done how what when where why
    which who whom this that these those it its of for to in on at by with from as and or but if then
    than so such can could should would may might will shall i you we they he she use used using get
    got make made support supports happens happen
""".split())

# "At least half absent" (spec §3.4): a query's content terms (post-fold,
# stop words dropped) with an absent-from-vocabulary fraction at or above
# this silences the query rather than answering off whatever content term
# happens to be present.
ABSENT_FRACTION_FLOOR = 0.5

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
    written. Folding itself has no notion of stop words — STOP_WORDS is
    applied afterward, only when computing the relevance floor (§3.4), so a
    stop word still folds like any other term (not that it usually matters:
    the fixed stop list is already inflected forms of themselves or close
    to it)."""
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
    claim, `field` one of FIELDS. Holds per-field postings and average field
    lengths, plus a whole-corpus term vocabulary for `fold` and the
    relevance floor. Immutable after construction; there is nothing here
    that a second call could invalidate, which is the point of rebuilding
    it fresh every time (spec §2)."""

    def __init__(self, docs: list):
        self.ids = [doc_id for doc_id, _ in docs]
        self.n = len(docs)
        self.vocab = set()
        # field -> doc_id -> token count in that field
        self.field_len = {f: {} for f in FIELDS}
        # field -> term -> {doc_id: tf}
        self.field_freq = {f: {} for f in FIELDS}

        for doc_id, doc_fields in docs:
            for f in FIELDS:
                text = (doc_fields or {}).get(f) or ""
                toks = tokens(text)
                self.field_len[f][doc_id] = len(toks)
                counts = {}
                for tok in toks:
                    counts[tok] = counts.get(tok, 0) + 1
                    self.vocab.add(tok)
                postings = self.field_freq[f]
                for tok, tf in counts.items():
                    postings.setdefault(tok, {})[doc_id] = tf

        self.avg_len = {
            f: (sum(self.field_len[f].values()) / self.n if self.n else 0.0)
            for f in FIELDS
        }

    def unknown(self, terms) -> list:
        """Terms (post-fold) absent from the corpus vocabulary entirely, in
        the order given — named in the CLI's no-match message so a silent
        miss becomes a useful one. The caller decides which terms to pass:
        the relevance floor below calls this with content terms only (stop
        words dropped), which is also the natural set for a no-match
        message to name."""
        return [t for t in terms if t not in self.vocab]


def search(index: Index, query: str, limit=None) -> list:
    """(score, claim id) pairs, best first, ties broken by claim id.

    The relevance floor (spec §3.4, amended): fold the query, drop stop
    words, and if at least half of the remaining content terms are absent
    from the corpus vocabulary entirely, return no hits — an honest "the
    store has no vocabulary for this" rather than a confident answer built
    from whatever term happens to be present. A query with no content terms
    at all (every term a stop word) has nothing to gate on and nothing
    meaningful to rank, so it is silenced the same way.

    Otherwise every term (stop words included — §3.3's scoring is
    unchanged) contributes to BM25 as usual, and every claim the score
    ends up nonzero for is returned: there is no per-document floor any
    more, only the query-level one above. `limit` bounds the result; None
    returns every hit.
    """
    raw_terms = tokens(query)
    if not raw_terms:
        return []
    terms = fold(raw_terms, index.vocab)

    content_terms = [t for t in terms if t not in STOP_WORDS]
    if not content_terms:
        return []
    absent = len(index.unknown(content_terms))
    if absent / len(content_terms) >= ABSENT_FRACTION_FLOOR:
        return []

    scores = {doc_id: 0.0 for doc_id in index.ids}

    # Σ_{t∈q} in spec §3.3 is over the query as written, not its distinct
    # terms — a repeated query term contributes its match more than once.
    for term in terms:
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

    hits = [(score, doc_id) for doc_id, score in scores.items() if score > 0]
    hits.sort(key=lambda pair: (-pair[0], pair[1]))
    if limit is not None:
        hits = hits[:limit]
    return hits
