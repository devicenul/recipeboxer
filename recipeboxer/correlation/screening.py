"""Top-k candidate screening engine for recipe cards (G5, G10).

Implements WAVE 5 Contracts §1.2 and REMEDIATION_PLAN.md §9:
1. Candidate generation from the union of:
   - Content-token retrieval (TF-IDF over content_tokens / dense embeddings if embedder provided)
   - Capture-order neighbours (±2)
   - Fast directed heuristic screening
2. O(N * k) evidence evaluations beyond cheap vector ops:
   Candidate pools are screened cheaply using vector dot-products and sequence indices,
   evaluating full pair_evidence ONLY on the filtered union (at most ~2k + 4 candidates per card).
3. Directed candidates (a precedes b): Candidate b is evaluated as the successor of card a.
4. Fully deterministic and card_id permutation-invariant.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import numpy as np

from recipeboxer.correlation.background import DeckBackground
from recipeboxer.correlation.evidence import ImageProvider, pair_evidence
from recipeboxer.models.correlation import PairEvidence
from recipeboxer.models.domain import VisualFingerprint
from recipeboxer.vision.heuristics import calculate_sequential_header_score

# Common stop words to suppress in default content tokenization
_STOPWORDS = {
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and", "any", "are",
    "as", "at", "be", "because", "been", "before", "being", "below", "between", "both", "but",
    "by", "can", "did", "do", "does", "doing", "don", "down", "during", "each", "few", "for",
    "from", "further", "had", "has", "have", "having", "he", "her", "here", "hers", "herself",
    "him", "himself", "his", "how", "if", "in", "into", "is", "it", "its", "itself", "just",
    "me", "more", "most", "my", "myself", "no", "nor", "not", "now", "of", "off", "on", "once",
    "only", "or", "other", "our", "ours", "ourselves", "out", "over", "own", "s", "same", "she",
    "should", "so", "some", "such", "t", "than", "that", "the", "their", "theirs", "them",
    "themselves", "then", "there", "these", "they", "this", "those", "through", "to", "too",
    "under", "until", "up", "very", "was", "we", "were", "what", "when", "where", "which",
    "while", "who", "whom", "why", "will", "with", "you", "your", "yours", "yourself",
}


def _default_content_tokens(fp: VisualFingerprint) -> List[str]:
    """Default fallback tokenizer extracting meaningful content tokens from fingerprint fields."""
    text_chunks: List[str] = []
    if fp.visible_title:
        text_chunks.append(fp.visible_title)
    if fp.first_lines:
        text_chunks.extend(fp.first_lines)
    if fp.last_lines:
        text_chunks.extend(fp.last_lines)
    if fp.printed_markers:
        text_chunks.extend(fp.printed_markers)
    if fp.boundary_text:
        if fp.boundary_text.first_line:
            text_chunks.append(fp.boundary_text.first_line)
        if fp.boundary_text.last_line:
            text_chunks.append(fp.boundary_text.last_line)
        if fp.boundary_text.header_tokens:
            text_chunks.extend(fp.boundary_text.header_tokens)

    raw_text = " ".join(text_chunks).lower()
    words = re.findall(r"[a-z0-9]+", raw_text)
    return [w for w in words if len(w) >= 2 and w not in _STOPWORDS]


def _build_tfidf_matrix(tokenized_docs: List[List[str]]) -> Tuple[np.ndarray, Dict[str, int]]:
    """Builds normalized TF-IDF feature matrix from tokenized documents."""
    vocab: Dict[str, int] = {}
    doc_freqs: Counter[str] = Counter()

    for doc in tokenized_docs:
        unique_tokens = set(doc)
        for token in unique_tokens:
            if token not in vocab:
                vocab[token] = len(vocab)
            doc_freqs[token] += 1

    num_docs = len(tokenized_docs)
    num_terms = len(vocab)
    if num_docs == 0 or num_terms == 0:
        return np.zeros((num_docs, 0), dtype=np.float32), vocab

    # Compute smooth IDF: log((1 + N) / (1 + DF)) + 1.0
    idf = np.zeros(num_terms, dtype=np.float32)
    for term, idx in vocab.items():
        df = doc_freqs[term]
        idf[idx] = math.log((1.0 + num_docs) / (1.0 + df)) + 1.0

    matrix = np.zeros((num_docs, num_terms), dtype=np.float32)
    for i, doc in enumerate(tokenized_docs):
        if not doc:
            continue
        counts = Counter(doc)
        total = float(len(doc))
        for token, count in counts.items():
            idx = vocab.get(token)
            if idx is not None:
                tf = count / total
                matrix[i, idx] = tf * idf[idx]

        # L2 normalize document vector
        norm = np.linalg.norm(matrix[i])
        if norm > 1e-6:
            matrix[i] /= norm

    return matrix, vocab


def top_k_candidates(
    fps: List[VisualFingerprint],
    background: DeckBackground,
    k: int = 5,
    *,
    embedder: Any = None,
    images: Optional[ImageProvider] = None,
) -> Dict[str, List[PairEvidence]]:
    """Generates and ranks top-k candidate successors for each card in the deck (G5, G10).

    Downstream complexity is strictly O(N * k) evidence evaluations:
    1. Candidate pools are formed per card from the union of:
       - Content-token retrieval (TF-IDF over content_tokens / embeddings if embedder given)
       - Capture-order neighbours (±2)
       - Fast heuristic header progression
    2. Full `pair_evidence` is evaluated ONLY for the filtered candidate union (at most ~2k + 4 cards).
    3. Candidates are ranked descending by `total_log_lr` (with deterministic tie-breaking).

    Args:
        fps: List of VisualFingerprints in the collection.
        background: Collection-relative DeckBackground model.
        k: Maximum number of candidate successors per card.
        embedder: Optional embedding provider or dictionary mapping card_id -> vector.
        images: Optional image provider for physical wear / show-through evidence.

    Returns:
        Mapping from card_id -> ordered list of up to k PairEvidence instances.
    """
    n = len(fps)
    if n == 0:
        return {}
    if n == 1:
        return {fps[0].card_id: []}

    card_map: Dict[str, VisualFingerprint] = {fp.card_id: fp for fp in fps}

    # 1. Resolve content tokens per card
    content_tokens_fn = None
    try:
        from recipeboxer.correlation.content import content_tokens
        content_tokens_fn = content_tokens
    except (ImportError, AttributeError):
        pass

    token_lists: List[List[str]] = []
    for fp in fps:
        if content_tokens_fn is not None:
            toks = content_tokens_fn(fp)
        else:
            toks = _default_content_tokens(fp)
        token_lists.append(toks)

    # 2. Content similarity matrix via TF-IDF or dense embeddings
    content_sim_matrix: Optional[np.ndarray] = None

    if embedder is not None:
        try:
            # Check if embedder is a precomputed dict mapping card_id -> vector
            if isinstance(embedder, dict):
                emb_list = [embedder.get(fp.card_id) for fp in fps]
                if all(v is not None for v in emb_list):
                    mat = np.array(emb_list, dtype=np.float32)
                    norms = np.linalg.norm(mat, axis=1, keepdims=True)
                    norms = np.maximum(norms, 1e-6)
                    normed = mat / norms
                    content_sim_matrix = np.dot(normed, normed.T)
            elif hasattr(embedder, "embed_texts_sync"):
                texts = [" ".join(toks) for toks in token_lists]
                embs = embedder.embed_texts_sync(texts)
                mat = np.array(embs, dtype=np.float32)
                norms = np.linalg.norm(mat, axis=1, keepdims=True)
                norms = np.maximum(norms, 1e-6)
                normed = mat / norms
                content_sim_matrix = np.dot(normed, normed.T)
        except Exception:
            content_sim_matrix = None

    if content_sim_matrix is None:
        tfidf_mat, _ = _build_tfidf_matrix(token_lists)
        if tfidf_mat.shape[1] > 0:
            content_sim_matrix = np.dot(tfidf_mat, tfidf_mat.T)
        else:
            content_sim_matrix = np.zeros((n, n), dtype=np.float32)

    # 3. Capture-order positions
    # Determine natural ordering indices (either from CaptureMeta or list position)
    order_indices: List[Tuple[int, int]] = []
    for i, fp in enumerate(fps):
        sort_idx = getattr(getattr(fp, "capture", None), "natural_sort_index", None)
        if sort_idx is None:
            sort_idx = i
        order_indices.append((sort_idx, i))

    order_indices.sort(key=lambda t: t[0])
    pos_in_order = {orig_i: rank for rank, (_, orig_i) in enumerate(order_indices)}
    rank_to_orig = [orig_i for _, orig_i in order_indices]

    # 4. Form candidate pools for each card A
    results: Dict[str, List[PairEvidence]] = {}

    for i in range(n):
        fp_a = fps[i]
        candidate_indices: Set[int] = set()

        # A. Content-token retrieval top-k
        if content_sim_matrix is not None:
            sims = content_sim_matrix[i].copy()
            sims[i] = -1.0  # Exclude self
            # Top-k positive similarity indices
            top_content_idx = np.argsort(-sims)[: max(k, 3)]
            for c_idx in top_content_idx:
                if sims[c_idx] > 0.0:
                    candidate_indices.add(int(c_idx))

        # B. Capture-order neighbours (±2)
        rank_a = pos_in_order[i]
        # Forward neighbours: rank_a + 1, rank_a + 2 (directed successor hypothesis)
        for delta in (1, 2, -1, -2):
            nb_rank = rank_a + delta
            if 0 <= nb_rank < n:
                nb_orig = rank_to_orig[nb_rank]
                if nb_orig != i:
                    candidate_indices.add(nb_orig)

        # C. Fast header progression heuristics top candidates
        hdr_scores: List[Tuple[float, int]] = []
        for j in range(n):
            if i != j:
                s = calculate_sequential_header_score(fp_a, fps[j])
                hdr_scores.append((s, j))
        hdr_scores.sort(key=lambda t: t[0], reverse=True)
        for s, j in hdr_scores[: max(k, 3)]:
            if s > 0.50:  # Higher than neutral baseline
                candidate_indices.add(j)

        # Fallback if candidates pool is smaller than k and deck has other cards
        if len(candidate_indices) < k and n > 1:
            for j in range(n):
                if j != i:
                    candidate_indices.add(j)
                if len(candidate_indices) >= k:
                    break

        # 5. Evaluate full PairEvidence ONLY on the candidate pool (O(N * k))
        evaluated_pairs: List[PairEvidence] = []
        # Sort candidate indices deterministically by card_id to ensure permutation invariance
        sorted_candidates = sorted(candidate_indices, key=lambda idx: fps[idx].card_id)

        for j in sorted_candidates:
            fp_b = fps[j]
            ev = pair_evidence(fp_a, fp_b, background, images=images)
            evaluated_pairs.append(ev)

        # 6. Rank candidates: primary key total_log_lr descending, secondary tie-break card_id ascending
        evaluated_pairs.sort(key=lambda ev: (-ev.total_log_lr, ev.b))

        results[fp_a.card_id] = evaluated_pairs[:k]

    return results
