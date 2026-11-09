"""Tests du dédoublonnage (aucun accès réseau).   python3 scripts/test_fetch_publications.py"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_publications as fp  # noqa: E402


def R(title, year=2023, doi="", type="journal-article", journal="J", authors=None, member="A", group="xwi", dois=None):
    return fp.Record(title=title, year=year, doi=doi, dois=dois, type=type, journal=journal,
                     authors=authors or [], members={member}, groups={group})


class Normalisation(unittest.TestCase):
    def test_doi(self):
        self.assertEqual(fp.norm_doi("https://doi.org/10.1000/ABC.1."), "10.1000/abc.1")
        self.assertEqual(fp.norm_doi("doi: 10.1000/X"), "10.1000/x")

    def test_title(self):
        a = fp.norm_title("Why are amyloid-β plaques detected by X-ray phase-contrast imaging?")
        b = fp.norm_title("Why Are Amyloid-Beta Plaques Detected by X‑Ray Phase‑Contrast Imaging")
        self.assertEqual(a, b)
        self.assertEqual(fp.norm_title("Étude <i>in vivo</i> du poumon"), "etude in vivo du poumon")

    def test_authors(self):
        self.assertEqual(fp.fmt_author("Jean-Luc Marie", "Dupont"), "J.-L. M. Dupont")
        self.assertEqual(fp.short_authors([str(i) for i in range(30)]), "0, 1, 2, … 28, 29")
        self.assertEqual(fp.short_authors(["A", "B"]), "A, B")


class Dedoublonnage(unittest.TestCase):
    def merged(self, recs, **kw):
        out, clusters, why, review = fp.dedupe(recs, **kw)
        return out, review

    def test_meme_doi_chez_deux_membres(self):
        out, _ = self.merged([R("Paper one on X-ray dark-field imaging", doi="10.1/a", member="A"),
                              R("Paper one on X-ray dark-field imaging", doi="10.1/A", member="B")])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].members, {"A", "B"})

    def test_preprint_et_publie_gardent_la_revue(self):
        pre = R("Single-shot phase retrieval with a random mask", year=2022, doi="10.1101/2022.01.01.1",
                type="preprint", journal="bioRxiv", member="A")
        pub = R("Single-shot phase retrieval with a random mask", year=2023, doi="10.1364/optica.1",
                type="journal-article", journal="Optica", authors=["A. B", "C. D"], member="B")
        out, _ = self.merged([pre, pub])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].doi, "10.1364/optica.1")
        self.assertEqual(out[0].journal, "Optica")
        self.assertIn("10.1101/2022.01.01.1", out[0].dois)
        self.assertEqual(fp.to_item(out[0])["also"], ["10.1101/2022.01.01.1"])

    def test_doi_de_groupe_orcid_relie_preprint_et_article(self):
        a = R("Phase-contrast CT of the human jaw at high energy", doi="10.1/pub", dois={"10.1/pub", "10.1/pre"})
        b = R("Phase contrast CT of the human jaw at high energy (preprint)", doi="10.1/pre", type="preprint")
        out, _ = self.merged([a, b])
        self.assertEqual(len(out), 1)

    def test_meme_titre_sans_doi(self):
        out, _ = self.merged([R("Quantitative elemental imaging of lung tissue by LIBS", doi=""),
                              R("Quantitative Elemental Imaging of Lung Tissue by LIBS.", doi="10.2/x", member="B")])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].doi, "10.2/x")

    def test_titre_proche_fusionne(self):
        out, _ = self.merged([R("Fractal analysis reveals functional unit of ventilation in the lung", doi="10.3/a"),
                              R("Fractal analysis reveals the functional unit of ventilation in the lung", doi="10.3/b", member="B")])
        self.assertEqual(len(out), 1)

    def test_parties_numerotees_jamais_fusionnees(self):
        out, _ = self.merged([R("Modulation-based X-ray imaging with a random membrane: Part I", doi="10.4/a"),
                              R("Modulation-based X-ray imaging with a random membrane: Part II", doi="10.4/b")])
        self.assertEqual(len(out), 2)

    def test_meme_titre_annees_trop_eloignees(self):
        out, _ = self.merged([R("Annual report of the beamline activity and instrumentation", year=2015),
                              R("Annual report of the beamline activity and instrumentation", year=2023, doi="10.5/z")])
        self.assertEqual(len(out), 2)

    def test_titres_courts_non_fusionnes_hors_identique(self):
        out, _ = self.merged([R("Lung imaging", doi="10.6/a"), R("Lung imaging.", doi="10.6/b")])
        self.assertEqual(len(out), 2)   # trop court pour être sûr : deux DOI distincts

    def test_titre_court_sans_doi_fusionne_avec_la_version_a_doi(self):
        out, _ = self.merged([R("Lung imaging review", doi=""), R("Lung imaging review", doi="10.6/c")])
        self.assertEqual(len(out), 1)

    def test_erratum_jamais_fusionne_avec_l_article(self):
        art = R("Water-soluble aza-BODIPYs: biocompatible organic dyes for high contrast in vivo NIR-II imaging", doi="10.9/a")
        err = R("Correction to: Water-soluble aza-BODIPYs: biocompatible organic dyes for high contrast in vivo NIR-II imaging",
                doi="10.9/b")
        out, _ = self.merged([art, err])
        self.assertEqual(len(out), 2)

    def test_cas_ambigu_signale_pour_relecture(self):
        out, review = self.merged([
            R("Virtual histology of human fetal organs using synchrotron phase-contrast tomography", doi="10.7/a"),
            R("Virtual histology of the human fetal heart using synchrotron phase-contrast tomography", doi="10.7/b")])
        self.assertEqual(len(out), 2)
        self.assertEqual(len(review), 1)

    def test_titre_en_majuscules_non_retenu(self):
        a = R("WHY ARE AMYLOID-BETA PLAQUES DETECTED BY X-RAY PHASE-CONTRAST IMAGING", doi="10.1/m",
              authors=["A. B", "C. D", "E. F"])
        b = R("Why are amyloid-beta plaques detected by X-ray phase-contrast imaging?", doi="10.1/m", member="B")
        out, _ = self.merged([a, b])
        self.assertEqual(out[0].title, "Why are amyloid-beta plaques detected by X-ray phase-contrast imaging?")
        self.assertEqual(len(out[0].authors), 3)

    def test_completion_des_champs_manquants(self):
        a = R("Elemental mapping of metals in single cells", doi="10.8/a", journal="", authors=[])
        b = R("Elemental mapping of metals in single cells", doi="10.8/a", journal="Metallomics", authors=["X. Y", "Z. W"], member="B")
        out, _ = self.merged([a, b])
        self.assertEqual(out[0].journal, "Metallomics")
        self.assertEqual(out[0].authors, ["X. Y", "Z. W"])


class Membres(unittest.TestCase):
    def test_lecture_yaml(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "m.yaml"
            p.write_text("# commentaire\n- name: Ada Lovelace\n  group: xwi\n  orcid: 0000-0001-0000-0001  # note\n"
                         "- name: Bob\n  group: igs\n", encoding="utf-8")
            people = fp.read_members(p)
            self.assertEqual(people[0], {"name": "Ada Lovelace", "orcid": "0000-0001-0000-0001", "group": "xwi"})
            self.assertEqual(people[1]["orcid"], "")


class OrcidParsing(unittest.TestCase):
    def test_parse_work(self):
        w = {"put-code": 1, "type": "JOURNAL_ARTICLE",
             "title": {"title": {"value": "Titre"}, "subtitle": {"value": "sous-titre"}},
             "journal-title": {"value": "Optica"},
             "publication-date": {"year": {"value": "2021"}, "month": {"value": "11"}, "day": None},
             "external-ids": {"external-id": [
                 {"external-id-type": "doi", "external-id-value": "10.1364/OPTICA.434954", "external-id-relationship": "self"},
                 {"external-id-type": "doi", "external-id-value": "10.9999/proceedings", "external-id-relationship": "part-of"},
                 {"external-id-type": "eid", "external-id-value": "2-s2.0-1"}]},
             "contributors": {"contributor": [{"credit-name": {"value": "L. Quénot"}}, {"credit-name": None}]}}
        r = fp.parse_orcid_work(w, {"10.1101/xyz"}, {"name": "M", "group": "xwi"})
        self.assertEqual((r.title, r.year, r.month, r.day), ("Titre: sous-titre", 2021, 11, None))
        self.assertEqual(r.type, "journal-article")
        self.assertEqual(r.doi, "10.1364/optica.434954")
        self.assertEqual(r.dois, {"10.1364/optica.434954", "10.1101/xyz"})
        self.assertEqual(r.authors, ["L. Quénot"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
