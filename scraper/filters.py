"""Lógica de filtragem orientada por config.json.

Regras:
  - INCLUI a vaga se o TÍTULO bate em algum include_title_keywords
    e NÃO bate em nenhum exclude_title_keywords.
  - EXCLUI a vaga se TÍTULO+DESCRIÇÃO contém algum exclude_keywords
    (mentoring / coaching / pairing / exigência de diploma).
  - REGIÃO: classifica em worldwide / brazil / latam / unknown / other.
    Mantém worldwide, brazil, latam, e (opcional) unknown. Descarta other.

Matching de keyword: usa fronteira de palavra à esquerda (\\b + termo),
então "mentor" pega "mentoring/mentorship", "pair" não pega "repair",
e "l1" não pega "url1". Frases com espaço batem literalmente.
"""

from __future__ import annotations

import re


def _compile(keywords: list[str]) -> list[re.Pattern]:
    """Compila cada keyword como regex com fronteira de palavra à esquerda."""
    patterns = []
    for kw in keywords:
        kw = kw.strip().lower()
        if not kw:
            continue
        patterns.append(re.compile(r"\b" + re.escape(kw)))
    return patterns


class JobFilter:
    def __init__(self, config: dict):
        self.include_title = _compile(config.get("include_title_keywords", []))
        self.exclude_title = _compile(config.get("exclude_title_keywords", []))
        self.exclude_text = _compile(config.get("exclude_keywords", []))
        self.region_block = _compile(config.get("region_block_keywords", []))
        self.regions = {
            label: [t.lower() for t in terms]
            for label, terms in config.get("region_keywords", {}).items()
        }
        self.keep_unknown = config.get("keep_unknown_region", True)

    # -- região ----------------------------------------------------------- #
    def classify_region(self, location: str) -> str:
        loc = (location or "").lower().strip()
        if not loc:
            return "unknown"
        for label, terms in self.regions.items():
            if any(t in loc for t in terms):
                return label
        return "other"

    # -- decisão ---------------------------------------------------------- #
    def matches(self, job: dict) -> tuple[bool, str]:
        """Retorna (passou?, motivo_da_rejeição). Motivo vazio se passou."""
        title = (job.get("title") or "").lower()
        text = title + " " + (job.get("description") or "").lower()

        if not any(p.search(title) for p in self.include_title):
            return False, "título fora do escopo"
        if any(p.search(title) for p in self.exclude_title):
            return False, "título excluído (intern/manager/etc)"
        for p in self.exclude_text:
            if p.search(text):
                return False, f"contém termo proibido: {p.pattern}"

        # Geo-trava ("USA only" etc.) aparece no título ou na localização mesmo
        # quando a fonte marca a região como "Anywhere". Bloqueia, exceto Brasil.
        geo = title + " " + (job.get("location") or "").lower()
        if any(p.search(geo) for p in self.region_block):
            return False, "vaga geo-travada (não worldwide/brazil)"

        region = self.classify_region(job.get("location", ""))
        if region == "other":
            return False, f"região não aceita: {job.get('location')!r}"
        if region == "unknown" and not self.keep_unknown:
            return False, "região desconhecida"

        job["region"] = region
        return True, ""
