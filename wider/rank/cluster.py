"""k-means on the top-K ranked records, with split-half stability, to see the literatures the ranking pulls up."""
from __future__ import annotations

import json
import re
from collections import Counter

import numpy as np
from sklearn.cluster import KMeans
from sklearn.metrics import adjusted_rand_score

from ..core.project import Project
from .corpus import load_corpus_embeddings

STOP = set("""a an and are as at be by for from has in is it its of on or that the this to was were with we our study
studies results research effects effect between among than more less not no into using used use two one three
these those their there they them also may can which who whom how what when where while based paper article""".split())


def top_terms(texts: list[str], k: int = 8) -> list[str]:
    c = Counter()
    for t in texts:
        c.update({w for w in re.findall(r"[a-z]{3,}", t.lower()) if w not in STOP})
    return [w for w, _ in c.most_common(k)]


def cluster(project: Project, ranking_name: str | None = None, k: int | None = None, top: int | None = None, seed: int | None = None) -> dict:
    rp = project.config.protocol.ranking
    k = k or rp.cluster_k
    top = top or rp.cluster_top
    from .versions import resolve

    st = project.store
    name = resolve(project, ranking_name)
    r = st.one("SELECT * FROM rankings WHERE name=?", (name,))
    if r is None:
        raise KeyError("no ranking")
    seed = seed if seed is not None else project.config.seed
    rows = st.query("SELECT rr.record_id, rr.rank, m.title, m.abstract FROM ranking_rows rr JOIN records m ON m.record_id=rr.record_id "
                    "WHERE rr.ranking_id=? AND rr.rank<=? ORDER BY rr.rank", (r["ranking_id"], top))
    ids, E = load_corpus_embeddings(project, r["set_name"])
    pos = {int(i): j for j, i in enumerate(ids)}
    X = E[[pos[x["record_id"]] for x in rows]]
    km = KMeans(n_clusters=k, n_init=10, random_state=seed).fit(X)
    labels = km.labels_
    # split-half stability: cluster each half, compare on the half by assigning the other half's centroids
    rng = np.random.default_rng(seed)
    idx = rng.permutation(len(X))
    h1, h2 = idx[: len(X) // 2], idx[len(X) // 2:]
    km1 = KMeans(n_clusters=k, n_init=10, random_state=seed).fit(X[h1])
    km2 = KMeans(n_clusters=k, n_init=10, random_state=seed).fit(X[h2])
    ari = adjusted_rand_score(km1.predict(X[h2]), km2.labels_)
    out = []
    for c in range(k):
        members = [rows[i] for i in range(len(rows)) if labels[i] == c]
        d = np.linalg.norm(X[labels == c] - km.cluster_centers_[c], axis=1)
        medoid = members[int(np.argmin(d))] if members else None
        out.append({"cluster": c, "n": len(members), "median_rank": float(np.median([m["rank"] for m in members])) if members else None,
                    "terms": top_terms([f"{m['title']} {m['abstract'] or ''}" for m in members], k=rp.cluster_terms),
                    "medoid": {"record_id": medoid["record_id"], "title": medoid["title"], "rank": medoid["rank"]} if medoid else None})
    res = {"ranking": name, "k": k, "top": top, "n_clustered": len(rows), "split_half_ari": round(float(ari), 3), "clusters": out,
           "labels": {int(rows[i]["record_id"]): int(labels[i]) for i in range(len(rows))}}
    st.set_meta(f"clusters:{name}:{k}", json.dumps(res))
    return res
