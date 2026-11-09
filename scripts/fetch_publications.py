#!/usr/bin/env python3
"""Récupère les publications des membres de 3T depuis leurs profils ORCID.

Lit les identifiants ORCID dans data/members.yaml (champ `orcid:`), interroge l'API publique
ORCID, puis fusionne les doublons et écrit data/publications_all.json, que Hugo affiche
sur la page Publications.

Sans dépendance : Python 3.9+ suffit.

    python3 scripts/fetch_publications.py            # cas courant
    python3 scripts/fetch_publications.py -v         # détaille les fusions
    python3 scripts/fetch_publications.py --dry-run  # n'écrit rien, affiche le bilan
    python3 scripts/fetch_publications.py --since 0  # toutes les années

Comment les doublons sont traités (dans cet ordre) :
  1. DOI commun : un même article apparaissant chez plusieurs membres, ou une version
     preprint et la version publiée regroupées par ORCID, ne forment qu'une entrée.
  2. Titre identique après normalisation (casse, accents, ponctuation, balises, lettres
     grecques) avec des années compatibles (écart de 2 ans au plus).
  3. Titre quasi identique (similarité >= 0.94) avec les mêmes numéros (« Part I » et
     « Part II » ne sont jamais fusionnés). Les cas entre 0.85 et 0.94 ne sont pas
     fusionnés : ils sont écrits dans scripts/publications_review.txt pour relecture.
  Dans chaque groupe, la meilleure version est conservée (article de revue > conférence >
  chapitre > preprint > jeu de données) et complétée avec les champs manquants des autres.
  Les DOI des autres versions sont gardés dans `also`.
"""
from __future__ import annotations

import argparse
import datetime as dt
import difflib
import hashlib
import json
import os
import re
import sys
import time
import unicodedata
from pathlib import Path
from urllib import error, parse, request

ROOT = Path(__file__).resolve().parent.parent
MEMBERS = ROOT / "data" / "members.yaml"
CONFIG = Path(__file__).resolve().parent / "publications_config.json"
OUT = ROOT / "data" / "publications_all.json"
REVIEW = Path(__file__).resolve().parent / "publications_review.txt"
CACHE = Path(__file__).resolve().parent / ".cache"

ORCID_API = "https://pub.orcid.org/v3.0"
CROSSREF_API = "https://api.crossref.org/works/"
DATACITE_API = "https://api.datacite.org/dois/"

DEFAULT_CONFIG = {
    "since": 2019,
    "exclude_types": ["data-set"],
    "exclude_dois": [],
    "exclude_title_regex": [],
    "extra_orcids": [],
    "mailto": "",
    "fuzzy_merge": 0.94,
    "fuzzy_review": 0.85,
}

TYPE_RANK = {
    "journal-article": 5, "conference-paper": 4, "book-chapter": 3, "book": 3, "report": 2,
    "dissertation-thesis": 2, "patent": 2, "preprint": 1, "software": 1, "data-set": 0,
}
PREPRINT_PREFIXES = (
    "10.1101/", "10.48550/", "10.21203/", "10.20944/", "10.2139/", "10.31219/", "10.26434/",
    "10.22541/", "10.31234/", "10.31224/", "10.36227/",
)
PREPRINT_JOURNALS = {"biorxiv", "medrxiv", "arxiv", "research square", "ssrn", "chemrxiv",
                     "preprints.org", "authorea preprints", "techrxiv", "hal"}
GREEK = {"α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "κ": "kappa", "λ": "lambda",
         "μ": "mu", "µ": "mu", "σ": "sigma", "ω": "omega", "π": "pi"}
ROMAN = {"i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x"}
# Un erratum ressemble beaucoup à l'article qu'il corrige : on ne le fusionne jamais avec lui.
ERRATUM_RE = re.compile(r"^(erratum|corrigendum|correction|author correction|publisher correction|"
                        r"retraction|expression of concern)\b")


# ----------------------------------------------------------------------------- utilitaires
def norm_doi(value: str | None) -> str:
    if not value:
        return ""
    v = value.strip().lower()
    v = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:\s*)", "", v)
    return v.rstrip(".,;)").strip()


def norm_title(title: str | None) -> str:
    if not title:
        return ""
    s = re.sub(r"<[^>]+>", " ", title)                       # balises HTML / MathML
    for g, name in GREEK.items():
        s = s.replace(g, f" {name} ")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def number_tokens(norm: str) -> frozenset:
    return frozenset(t for t in norm.split() if t.isdigit() or t in ROMAN)


def initials(given: str) -> str:
    """« Jean-Luc Marie » -> « J.-L. M. »"""
    out = []
    for word in given.split():
        out.append("-".join(p[0].upper() + "." for p in word.split("-") if p))
    return " ".join(out)


def fmt_author(given: str, family: str, name: str = "") -> str:
    if family:
        return f"{initials(given)} {family}".strip() if given else family
    return name.strip()


def short_authors(authors: list[str]) -> str:
    if len(authors) <= 8:
        return ", ".join(authors)
    return ", ".join(authors[:3]) + ", … " + ", ".join(authors[-2:])


def good_title(title: str) -> bool:
    letters = [c for c in title if c.isalpha()]
    return not letters or sum(c.isupper() for c in letters) / len(letters) < 0.7


def is_preprint(rec: "Record") -> bool:
    if rec.type == "preprint":
        return True
    if any(rec.doi.startswith(p) for p in PREPRINT_PREFIXES):
        return True
    return (rec.journal or "").strip().lower() in PREPRINT_JOURNALS


# ----------------------------------------------------------------------------- réseau + cache
class Fetcher:
    def __init__(self, offline: bool, refresh: bool, ttl_hours: float, mailto: str, verbose: bool):
        self.offline, self.refresh, self.ttl, self.mailto, self.verbose = offline, refresh, ttl_hours, mailto, verbose
        self.ua = "3T-site-publications/1.0" + (f" (mailto:{mailto})" if mailto else "")
        CACHE.mkdir(exist_ok=True)

    def _path(self, key: str) -> Path:
        h = hashlib.md5(key.encode()).hexdigest()[:16]
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", key)[:60]
        return CACHE / f"{safe}-{h}.json"

    def get_json(self, url: str, accept: str = "application/json"):
        path = self._path(url)
        if path.exists():
            fresh = (time.time() - path.stat().st_mtime) < self.ttl * 3600
            if self.offline or (fresh and not self.refresh):
                return json.loads(path.read_text(encoding="utf-8"))
        if self.offline:
            return None
        data = self._http(url, accept)
        path.write_text(json.dumps(data), encoding="utf-8")
        return data

    def _http(self, url: str, accept: str):
        last = None
        for attempt in range(5):
            req = request.Request(url, headers={"Accept": accept, "User-Agent": self.ua})
            try:
                with request.urlopen(req, timeout=40) as resp:
                    return json.load(resp)
            except error.HTTPError as e:
                if e.code == 404:
                    return None
                if e.code in (429, 500, 502, 503, 504):
                    wait = float(e.headers.get("Retry-After", 0) or 0) or 2 ** attempt * 1.5
                    last = e
                    time.sleep(wait)
                    continue
                raise
            except (error.URLError, TimeoutError, ConnectionError) as e:
                last = e
                time.sleep(2 ** attempt)
        raise RuntimeError(f"Échec réseau pour {url} : {last}")


# ----------------------------------------------------------------------------- modèle
class Record:
    __slots__ = ("title", "norm", "year", "month", "day", "type", "doi", "dois", "journal",
                 "authors", "url", "members", "groups", "orcid_authors_ok")

    def __init__(self, title="", year=None, month=None, day=None, type="other", doi="", dois=None,
                 journal="", authors=None, url="", members=None, groups=None):
        self.title = title.strip()
        self.norm = norm_title(title)
        self.year, self.month, self.day = year, month, day
        self.type = type or "other"
        self.doi = norm_doi(doi)
        self.dois = {norm_doi(d) for d in (dois or []) if d} | ({self.doi} if self.doi else set())
        self.journal = (journal or "").strip()
        self.authors = list(authors or [])
        self.url = url or ""
        self.members = set(members or [])
        self.groups = set(groups or [])

    def score(self) -> tuple:
        return (TYPE_RANK.get(self.type, 0) - (1 if is_preprint(self) and self.type != "preprint" else 0),
                2 if self.doi else 0, 1 if self.journal else 0, 1 if len(self.authors) >= 2 else 0,
                len(self.authors))

    def sort_date(self) -> tuple:
        return (self.year or 0, self.month or 0, self.day or 0)


# ----------------------------------------------------------------------------- lecture des membres
def parse_groups(v) -> list[str]:
    """`xwi`, `[xwi, igs]` ou une vraie liste -> ["xwi", "igs"]."""
    items = v if isinstance(v, (list, tuple, set)) else re.split(r"[,\s]+", (v or "").strip().strip("[]"))
    return [x.strip("\"' ") for x in items if x.strip("\"' ")]


def read_members(path: Path) -> list[dict]:
    """Lecture minimaliste de data/members.yaml (pas de dépendance PyYAML)."""
    people, cur = [], None
    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.lstrip().startswith("#"):
            continue
        line = re.sub(r"\s+#.*$", "", raw).rstrip()
        m = re.match(r"^-\s+name:\s*(.+?)\s*$", line)
        if m:
            cur = {"name": m.group(1).strip("\"'"), "orcid": "", "group": []}
            people.append(cur)
            continue
        if cur is not None:
            m = re.match(r"^\s+(orcid|group):\s*(.+?)\s*$", line)
            if m:
                cur[m.group(1)] = parse_groups(m.group(2)) if m.group(1) == "group" else m.group(2).strip("\"'")
    return people


# ----------------------------------------------------------------------------- ORCID
def parse_orcid_work(w: dict, group_dois: set[str], member: dict) -> Record:
    t = w.get("title") or {}
    title = (t.get("title") or {}).get("value") or ""
    sub = (t.get("subtitle") or {}).get("value") if t.get("subtitle") else ""
    if sub:
        title = f"{title}: {sub}"
    pd = w.get("publication-date") or {}
    num = lambda k: int(pd[k]["value"]) if pd.get(k) and (pd[k] or {}).get("value", "").isdigit() else None  # noqa: E731
    dois, primary = set(group_dois), ""
    for e in (w.get("external-ids") or {}).get("external-id", []) or []:
        if (e.get("external-id-type") or "").lower() == "doi":
            d = norm_doi(e.get("external-id-value"))
            if d and e.get("external-id-relationship") != "part-of":
                dois.add(d)
                primary = primary or d
    contribs = []
    for c in (w.get("contributors") or {}).get("contributor", []) or []:
        n = ((c.get("credit-name") or {}).get("value") or "").strip()
        if n:
            contribs.append(n)
    return Record(title=title, year=num("year"), month=num("month"), day=num("day"),
                  type=(w.get("type") or "other").lower().replace("_", "-").replace(" ", "-"),
                  doi=primary or (sorted(dois)[0] if dois else ""), dois=dois,
                  journal=((w.get("journal-title") or {}).get("value") or ""),
                  authors=contribs, url=((w.get("url") or {}).get("value") or ""),
                  members={member["name"]}, groups=set(parse_groups(member.get("group"))))


def fetch_orcid(member: dict, fx: Fetcher) -> list[Record]:
    orcid = member["orcid"]
    summary = fx.get_json(f"{ORCID_API}/{orcid}/works")
    if not summary:
        print(f"  ! {member['name']} ({orcid}) : profil introuvable ou aucun accès", file=sys.stderr)
        return []
    put_codes, gdois = [], {}
    for g in summary.get("group", []):
        sums = g.get("work-summary") or []
        if not sums:
            continue
        pc = sums[0].get("put-code")            # ORCID range la version préférée en premier
        put_codes.append(pc)
        gdois[pc] = {norm_doi(e.get("external-id-value")) for e in
                     (g.get("external-ids") or {}).get("external-id", []) or []
                     if (e.get("external-id-type") or "").lower() == "doi"}
    records = []
    for i in range(0, len(put_codes), 100):
        chunk = put_codes[i:i + 100]
        bulk = fx.get_json(f"{ORCID_API}/{orcid}/works/{','.join(map(str, chunk))}")
        for item in (bulk or {}).get("bulk", []):
            w = item.get("work")
            if w:
                records.append(parse_orcid_work(w, gdois.get(w.get("put-code"), set()), member))
    return records


# ----------------------------------------------------------------------------- dédoublonnage
class UnionFind:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def years_compatible(a: Record, b: Record, gap: int = 2) -> bool:
    return a.year is None or b.year is None or abs(a.year - b.year) <= gap


def cluster(records: list[Record], fuzzy_merge: float, fuzzy_review: float):
    """Retourne (clusters, raisons, cas à relire). Un cluster = liste d'indices."""
    n = len(records)
    uf = UnionFind(n)
    why: dict[tuple, str] = {}
    by_doi: dict[str, int] = {}
    for i, r in enumerate(records):                                   # 1. DOI commun
        for d in r.dois:
            if d in by_doi:
                uf.union(by_doi[d], i)
                why[(by_doi[d], i)] = "DOI"
            else:
                by_doi[d] = i
    by_title: dict[str, int] = {}
    for i, r in enumerate(records):                                   # 2. titre identique
        if len(r.norm) < 12 or ERRATUM_RE.search(r.norm):
            continue
        j = by_title.get(r.norm)
        # titre court et deux DOI différents : trop risqué (« Editorial », « Introduction »…)
        short_but_distinct = len(r.norm) < 25 and j is not None and records[j].doi and r.doi
        if j is not None and not short_but_distinct and years_compatible(records[j], r) and uf.find(j) != uf.find(i):
            uf.union(j, i)
            why[(j, i)] = "titre identique"
        by_title.setdefault(r.norm, i)
    review = []
    words = [set(r.norm.split()) for r in records]
    toks = [number_tokens(r.norm) for r in records]
    for i in range(n):                                                # 3. titre quasi identique
        if len(records[i].norm) < 25 or ERRATUM_RE.search(records[i].norm):
            continue
        for j in range(i + 1, n):
            if uf.find(i) == uf.find(j) or len(records[j].norm) < 25 or ERRATUM_RE.search(records[j].norm):
                continue
            if not years_compatible(records[i], records[j]):
                continue
            inter = len(words[i] & words[j])
            if inter / max(1, len(words[i] | words[j])) < 0.6:
                continue
            sm = difflib.SequenceMatcher(None, records[i].norm, records[j].norm)
            if sm.real_quick_ratio() < fuzzy_review or sm.quick_ratio() < fuzzy_review:
                continue
            ratio = sm.ratio()
            if ratio >= fuzzy_merge and toks[i] == toks[j]:
                uf.union(i, j)
                why[(i, j)] = f"titre proche ({ratio:.2f})"
            elif ratio >= fuzzy_review and toks[i] == toks[j]:   # « Part I / Part II » : distincts, rien à relire
                review.append((ratio, i, j))
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(uf.find(i), []).append(i)
    return list(groups.values()), why, review


def merge_cluster(idx: list[int], records: list[Record]) -> Record:
    members = sorted((records[i] for i in idx), key=lambda r: r.score(), reverse=True)
    best = members[0]
    out = Record(title=best.title, year=best.year, month=best.month, day=best.day, type=best.type,
                 doi=best.doi, dois=set(), journal=best.journal, authors=best.authors, url=best.url)
    for r in members:
        out.dois |= r.dois
        out.members |= r.members
        out.groups |= r.groups
        for attr in ("journal", "url"):
            if not getattr(out, attr) and getattr(r, attr):
                setattr(out, attr, getattr(r, attr))
        if len(r.authors) > len(out.authors) and not is_preprint(r):
            out.authors = r.authors
        if not out.authors and r.authors:
            out.authors = r.authors
        if out.year is None and r.year is not None:
            out.year, out.month, out.day = r.year, r.month, r.day
    if not out.doi:
        out.doi = next(iter(sorted(out.dois)), "")
    if not good_title(out.title):                      # évite un titre TOUT EN MAJUSCULES
        for r in members:
            if good_title(r.title):
                out.title = r.title
                break
    return out


def dedupe(records: list[Record], fuzzy_merge=0.94, fuzzy_review=0.85):
    clusters, why, review = cluster(records, fuzzy_merge, fuzzy_review)
    merged = [merge_cluster(c, records) for c in clusters]
    return merged, clusters, why, review


# ----------------------------------------------------------------------------- enrichissement
def enrich(rec: Record, fx: Fetcher) -> bool:
    """Complète auteurs et revue via Crossref (puis DataCite). True si un appel a eu lieu."""
    if not rec.doi or (len(rec.authors) >= 2 and rec.journal):
        return False
    q = parse.quote(rec.doi, safe="/")
    cr = fx.get_json(CROSSREF_API + q + (f"?mailto={parse.quote(fx.mailto)}" if fx.mailto else ""))
    if cr and cr.get("message"):
        m = cr["message"]
        names = [fmt_author(a.get("given", ""), a.get("family", ""), a.get("name", "")) for a in m.get("author", [])]
        names = [n for n in names if n]
        if len(names) > len(rec.authors):
            rec.authors = names
        if not rec.journal and m.get("container-title"):
            rec.journal = m["container-title"][0]
        if m.get("subtype") == "preprint" or m.get("type") == "posted-content":
            rec.type = "preprint"
        if rec.year is None:
            parts = ((m.get("issued") or {}).get("date-parts") or [[None]])[0]
            rec.year = parts[0] if parts else None
        return True
    dc = fx.get_json(DATACITE_API + q)
    if not (dc and dc.get("data")):
        return False
    a = dc["data"].get("attributes", {})
    names = [fmt_author(c.get("givenName", ""), c.get("familyName", ""), c.get("name", "")) for c in a.get("creators", [])]
    names = [n for n in names if n]
    if len(names) > len(rec.authors):
        rec.authors = names
    if not rec.journal and a.get("publisher"):
        pub = a["publisher"]
        rec.journal = pub if isinstance(pub, str) else (pub or {}).get("name", "")
    if rec.year is None and a.get("publicationYear"):
        rec.year = int(a["publicationYear"])
    return True


# ----------------------------------------------------------------------------- sortie
def to_item(r: Record) -> dict:
    authors = list(r.authors)
    preprint = is_preprint(r)
    also = sorted(d for d in r.dois if d != r.doi)
    return {
        "title": r.title,
        "authors": ", ".join(authors),
        "authors_short": short_authors(authors),
        "journal": r.journal,
        "year": r.year,
        "type": "preprint" if preprint else r.type,
        "doi": r.doi,
        "url": f"https://doi.org/{r.doi}" if r.doi else r.url,
        "also": also,
        "groups": sorted(r.groups),
        "members": sorted(r.members),
    }


def load_config(path: Path) -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if path.exists():
        cfg.update(json.loads(path.read_text(encoding="utf-8")))
    return cfg


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--since", type=int, help="année minimale (0 = toutes)")
    ap.add_argument("--include-datasets", action="store_true", help="garder les jeux de données")
    ap.add_argument("--no-enrich", action="store_true", help="ne pas interroger Crossref/DataCite")
    ap.add_argument("--offline", action="store_true", help="utiliser uniquement le cache local")
    ap.add_argument("--refresh", action="store_true", help="ignorer le cache")
    ap.add_argument("--ttl-hours", type=float, default=24, help="durée de validité du cache")
    ap.add_argument("--dry-run", action="store_true", help="n'écrit aucun fichier")
    ap.add_argument("--members", type=Path, default=MEMBERS)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)

    cfg = load_config(CONFIG)
    since = cfg["since"] if a.since is None else a.since
    exclude_types = set() if a.include_datasets else set(cfg["exclude_types"])
    mailto = os.environ.get("CROSSREF_MAILTO") or cfg["mailto"]
    fx = Fetcher(a.offline, a.refresh, a.ttl_hours, mailto, a.verbose)

    people = [p for p in read_members(a.members) if p["orcid"]]
    seen = {p["orcid"] for p in people}
    people += [{"name": o, "orcid": o, "group": []} for o in cfg["extra_orcids"] if o not in seen]
    if not people:
        print("Aucun ORCID trouvé dans data/members.yaml (champ `orcid:`).", file=sys.stderr)
        return 1

    print(f"{len(people)} profils ORCID")
    raw: list[Record] = []
    for p in people:
        recs = fetch_orcid(p, fx)
        print(f"  {p['name']:<28} {len(recs):>4} travaux")
        raw.extend(recs)
        if not a.offline:
            time.sleep(0.2)
    if not raw:
        print("Aucune donnée récupérée : rien n'est écrit.", file=sys.stderr)
        return 1

    merged, clusters, why, review = dedupe(raw, cfg["fuzzy_merge"], cfg["fuzzy_review"])
    print(f"\n{len(raw)} travaux bruts → {len(merged)} après fusion des doublons "
          f"({len(raw) - len(merged)} doublons supprimés)")
    if a.verbose:
        for c in clusters:
            if len(c) > 1:
                reasons = {why.get((i, j)) or why.get((j, i)) for i in c for j in c if i != j} - {None}
                print(f"  fusion [{', '.join(sorted(reasons))}] :")
                for i in c:
                    r = raw[i]
                    print(f"      - {r.year} {r.type:<16} {r.doi or '(sans DOI)':<34} {r.title[:70]}")

    excl_dois = {norm_doi(d) for d in cfg["exclude_dois"]}
    excl_re = [re.compile(x, re.I) for x in cfg["exclude_title_regex"]]
    kept, dropped = [], {"année": 0, "type": 0, "exclusion": 0, "erratum": 0, "sans titre": 0}
    for r in merged:
        if not r.title:
            dropped["sans titre"] += 1
        elif ERRATUM_RE.search(r.norm) and not cfg.get("keep_errata"):
            dropped["erratum"] += 1
        elif r.type in exclude_types:
            dropped["type"] += 1
        elif r.dois & excl_dois or any(x.search(r.title) for x in excl_re):
            dropped["exclusion"] += 1
        elif since and (r.year is None or r.year < since):
            dropped["année"] += 1
        else:
            kept.append(r)
    print("Écartés : " + ", ".join(f"{v} {k}" for k, v in dropped.items() if v) if any(dropped.values()) else "Rien d'écarté")

    if not a.no_enrich:
        calls = 0
        for k, r in enumerate(kept, 1):
            try:
                calls += enrich(r, fx)
            except Exception as e:                      # noqa: BLE001 - l'enrichissement est facultatif
                print(f"  ! enrichissement impossible pour {r.doi}: {e}", file=sys.stderr)
            if not a.offline:
                time.sleep(0.05)
        print(f"Enrichissement Crossref/DataCite : {calls} notices consultées")
        # l'enrichissement peut révéler un preprint ou un type différent
        kept = [r for r in kept if r.type not in exclude_types]

    kept.sort(key=lambda r: (r.sort_date(), r.title), reverse=True)
    by_year: dict[int, list] = {}
    for r in kept:
        by_year.setdefault(r.year or 0, []).append(to_item(r))
    years = [{"year": y, "items": by_year[y]} for y in sorted(by_year, reverse=True)]
    payload = {
        "generated": dt.date.today().isoformat(),
        "count": len(kept),
        "orcids": sorted({p["orcid"] for p in people}),
        "years": years,
    }

    suspects = []
    for ratio, i, j in sorted(review, reverse=True):
        suspects.append(f"{ratio:.2f}\n  A [{raw[i].year}] {raw[i].doi or '-'}  {raw[i].title}\n"
                        f"  B [{raw[j].year}] {raw[j].doi or '-'}  {raw[j].title}\n")
    if suspects:
        print(f"{len(suspects)} paires proches mais non fusionnées, à relire : scripts/publications_review.txt")

    if a.dry_run:
        print("\n--dry-run : aucun fichier écrit.")
        return 0
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    REVIEW.write_text("\n".join(suspects) if suspects else "Aucun cas ambigu.\n", encoding="utf-8")
    print(f"\n{len(kept)} publications écrites dans {a.out.relative_to(ROOT) if a.out.is_relative_to(ROOT) else a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
