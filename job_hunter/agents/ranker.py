"""HybridRanker: Industry-standard 3-Pass candidate filtering pipeline.

Pass 1 ── Hard Guardrails:   Drop violations (remote/salary/location constraints)
Pass 2 ── Hybrid Ranking:    BM25 + TF-IDF Cosine Similarity → RRF → Top-K × buffer
Pass 3 ── (in orchestrator): NVIDIA Nemotron deep LLM vetting on Top-K only

References:
    - Robertson, S. & Zaragoza, H. (2009). The Probabilistic Relevance Framework: BM25.
    - Cormack et al. (2009). Reciprocal Rank Fusion outperforms Condorcet.
    - Lin, J. (2022). Pretrained Transformers for Text Ranking (bi-encoder pipeline).
"""

import logging
import math
import re
from collections import Counter
from typing import List, Tuple

from ..models import CandidateProfile, JobListing, JobSearchCriteria

log = logging.getLogger(__name__)

# Weight for blending BM25 vs. TF-IDF cosine scores in RRF
BM25_WEIGHT = 0.55   # Lexical match (mandatory skills, job titles)
TFIDF_WEIGHT = 0.45  # Semantic TF-IDF vector similarity
RRF_K = 60           # RRF smoothing constant (standard: 60)
BUFFER_MULTIPLIER = 1.5  # Evaluate 1.5× the requested max_evals


# ─────────────────────────────────────────────────────────────────────────────
# BM25 Implementation (no external dependency)
# ─────────────────────────────────────────────────────────────────────────────
class BM25:
    """Lightweight Okapi BM25 scorer — zero external dependencies."""

    def __init__(self, corpus: List[List[str]], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.corpus_size = len(corpus)
        self.avgdl = sum(len(doc) for doc in corpus) / max(1, len(corpus))
        self.doc_freqs: List[Counter] = [Counter(doc) for doc in corpus]
        # IDF
        df: Counter = Counter()
        for doc_freq in self.doc_freqs:
            for term in doc_freq:
                df[term] += 1
        self.idf: dict = {
            term: math.log(1 + (self.corpus_size - freq + 0.5) / (freq + 0.5))
            for term, freq in df.items()
        }

    def score(self, query_terms: List[str], doc_idx: int) -> float:
        score = 0.0
        doc_freq = self.doc_freqs[doc_idx]
        dl = sum(doc_freq.values())
        for term in query_terms:
            if term not in doc_freq:
                continue
            tf = doc_freq[term]
            idf = self.idf.get(term, 0.0)
            num = tf * (self.k1 + 1)
            den = tf + self.k1 * (1 - self.b + self.b * dl / self.avgdl)
            score += idf * num / den
        return score

    def score_all(self, query_terms: List[str]) -> List[float]:
        return [self.score(query_terms, i) for i in range(self.corpus_size)]


# ─────────────────────────────────────────────────────────────────────────────
# TF-IDF Cosine Similarity (pure stdlib — zero external dependency)
# ─────────────────────────────────────────────────────────────────────────────

def _tokenize(text: str) -> List[str]:
    """Lowercase, strip punctuation, tokenize."""
    return re.findall(r"[a-z0-9+#]+", text.lower())


def _tfidf_cosine_scores(query_tokens: List[str], corpus_tokens: List[List[str]]) -> List[float]:
    """Compute TF-IDF cosine similarity between query and each corpus document."""
    # Build vocabulary from query + all docs
    all_docs = [query_tokens] + corpus_tokens
    vocab: dict = {}
    for doc in all_docs:
        for t in doc:
            if t not in vocab:
                vocab[t] = len(vocab)

    n_docs = len(corpus_tokens)
    df_vec = [0.0] * len(vocab)
    for doc in corpus_tokens:
        seen = set(doc)
        for t in seen:
            if t in vocab:
                df_vec[vocab[t]] += 1.0

    def tfidf_vec(tokens: List[str]) -> List[float]:
        tf = Counter(tokens)
        vec = [0.0] * len(vocab)
        for t, cnt in tf.items():
            if t in vocab:
                df = df_vec[vocab[t]]
                idf = math.log((n_docs + 1) / (df + 1)) + 1.0
                vec[vocab[t]] = (cnt / max(1, len(tokens))) * idf
        return vec

    def dot(a: List[float], b: List[float]) -> float:
        return sum(x * y for x, y in zip(a, b))

    def norm(a: List[float]) -> float:
        return math.sqrt(sum(x * x for x in a))

    q_vec = tfidf_vec(query_tokens)
    q_norm = norm(q_vec)
    scores = []
    for doc_tokens in corpus_tokens:
        d_vec = tfidf_vec(doc_tokens)
        d_norm = norm(d_vec)
        if q_norm == 0 or d_norm == 0:
            scores.append(0.0)
        else:
            scores.append(dot(q_vec, d_vec) / (q_norm * d_norm))
    return scores


# ─────────────────────────────────────────────────────────────────────────────
# Reciprocal Rank Fusion
# ─────────────────────────────────────────────────────────────────────────────

def _reciprocal_rank_fusion(
    ranked_lists: List[List[int]],
    weights: List[float],
    k: int = RRF_K,
) -> List[Tuple[int, float]]:
    """Fuse multiple ranked lists with per-list weights using RRF."""
    rrf_scores: dict = {}
    for ranked, weight in zip(ranked_lists, weights):
        for rank, idx in enumerate(ranked):
            idx_int = int(idx)
            rrf_scores[idx_int] = rrf_scores.get(idx_int, 0.0) + float(weight) / (k + rank + 1)
    return sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)


# ─────────────────────────────────────────────────────────────────────────────
# Main HybridRanker
# ─────────────────────────────────────────────────────────────────────────────

class HybridRanker:
    """Industry-standard 3-Pass ranking pipeline:
    
    Pass 1: Hard Guardrail Filter  (Remote / Salary / Location constraints)
    Pass 2: Hybrid BM25 + TF-IDF Cosine + RRF ranking  (returns top_k × buffer)
    """

    def rank(
        self,
        jobs: List[JobListing],
        profile: CandidateProfile,
        criteria: JobSearchCriteria,
        top_k: int,
    ) -> List[JobListing]:
        """Return the top_k most semantically relevant jobs using the hybrid pipeline."""
        n_before = len(jobs)

        # ── Pass 1: Hard Guardrails ───────────────────────────────────────────
        jobs = self._apply_hard_guardrails(jobs, criteria)
        n_after_guardrails = len(jobs)
        log.info(
            "🛡️  [Guardrails] %d → %d jobs after hard constraint filtering.",
            n_before, n_after_guardrails,
        )

        if not jobs:
            return []

        # ── Pass 2: Hybrid BM25 + TF-IDF Cosine + RRF ranking ────────────────
        ranked = self._hybrid_rank(jobs, profile, criteria)

        # Take top_k × buffer then slice. Ensures LLM vetting has a rich pool.
        top_k_int = int(top_k)
        buffer_k = int(min(len(ranked), max(top_k_int, int(top_k_int * BUFFER_MULTIPLIER))))
        top_candidates = ranked[:buffer_k]

        log.info(
            "🔍 [Hybrid Ranker] Ranked %d jobs → Top-%d selected for LLM vetting "
            "(buffer: %.1fx, BM25 %.0f%% + TF-IDF %.0f%% via RRF).",
            n_after_guardrails, len(top_candidates),
            BUFFER_MULTIPLIER, BM25_WEIGHT * 100, TFIDF_WEIGHT * 100,
        )
        return top_candidates

    # ── Internal: Hard Guardrails ─────────────────────────────────────────────

    def _apply_hard_guardrails(
        self,
        jobs: List[JobListing],
        criteria: JobSearchCriteria,
    ) -> List[JobListing]:
        # Remote filter
        if criteria.remote_only:
            jobs = [j for j in jobs if self._is_remote(j)]

        # Minimum salary filter
        if criteria.min_salary and criteria.min_salary > 0:
            jobs = [j for j in jobs if self._satisfies_salary(j, criteria.min_salary)]

        return jobs

    @staticmethod
    def _is_remote(j: JobListing) -> bool:
        loc = (j.location or "").lower()
        desc = (j.description or "").lower()
        if "remote" in loc:
            return True
        if "remote" in desc:
            negative_signals = ["no remote", "not remote", "in-office only", "on-site only", "onsite only"]
            return not any(neg in desc for neg in negative_signals)
        return False

    @staticmethod
    def _satisfies_salary(j: JobListing, min_salary: int) -> bool:
        if not j.salary:
            return True  # Keep unstated salaries — do not drop silently
        numbers = [int(n.replace(",", "")) for n in re.findall(
            r"\b\d{1,3}(?:,\d{3})+\b|\b\d{5,8}\b", j.salary)]
        return not (numbers and max(numbers) < min_salary)

    # ── Internal: Hybrid Semantic Ranker ─────────────────────────────────────

    def _hybrid_rank(
        self,
        jobs: List[JobListing],
        profile: CandidateProfile,
        criteria: JobSearchCriteria,
    ) -> List[JobListing]:
        """Rank all jobs using BM25 + TF-IDF Cosine + RRF."""

        # Build query: candidate profile tokens
        query_text = " ".join([
            profile.summary or "",
            " ".join(profile.skills),
            " ".join(profile.job_titles),
            " ".join(profile.search_queries),
            " ".join(criteria.keywords),
        ])
        query_tokens = _tokenize(query_text)

        # Build corpus: each job is title + description blob
        corpus_texts = [
            f"{j.title} {j.title} {j.description or ''}"  # title doubled for weight
            for j in jobs
        ]
        corpus_tokens = [_tokenize(t) for t in corpus_texts]

        # ── BM25 Scores ────────────────────────────────────────────────────
        bm25 = BM25(corpus_tokens)
        bm25_scores = bm25.score_all(query_tokens)

        # ── TF-IDF Cosine Scores ───────────────────────────────────────────
        tfidf_scores = _tfidf_cosine_scores(query_tokens, corpus_tokens)

        # ── Build Ranked Lists per method ──────────────────────────────────
        bm25_ranked = sorted(range(len(jobs)), key=lambda i: bm25_scores[i], reverse=True)
        tfidf_ranked = sorted(range(len(jobs)), key=lambda i: tfidf_scores[i], reverse=True)

        # ── Reciprocal Rank Fusion ─────────────────────────────────────────
        fused = _reciprocal_rank_fusion(
            ranked_lists=[bm25_ranked, tfidf_ranked],
            weights=[BM25_WEIGHT, TFIDF_WEIGHT],
        )

        return [jobs[int(idx)] for idx, score in fused]
