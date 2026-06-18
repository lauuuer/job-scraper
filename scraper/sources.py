"""Coletores de vagas. Cada fonte tem uma API/feed aberto e gratuito.

Cada função retorna uma lista de dicts já normalizados no formato comum:
    {id, title, company, location, url, source, description, date}

Onde:
    - description é texto puro (sem HTML), usado pelos filtros
    - date é uma string ISO (YYYY-MM-DD) quando disponível, senão ""

Toda função é tolerante a falha: se a fonte cair ou mudar o formato,
ela loga e retorna [] em vez de derrubar o run inteiro.
"""

from __future__ import annotations

import html
import os
import re
import sys
from datetime import datetime, timezone

import requests

try:
    import feedparser
except ImportError:  # feedparser só é necessário para fontes RSS
    feedparser = None

HEADERS = {
    "User-Agent": "job-scraper/1.0 (+https://github.com) Python-requests",
    "Accept": "application/json",
}
TIMEOUT = 30


def _log(msg: str) -> None:
    print(f"[sources] {msg}", file=sys.stderr)


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def strip_html(text: str | None) -> str:
    """Remove tags HTML e normaliza espaços/entidades."""
    if not text:
        return ""
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    return _WS_RE.sub(" ", text).strip()


def _iso_date(value) -> str:
    """Tenta converter vários formatos de data em YYYY-MM-DD."""
    if not value:
        return ""
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc).strftime("%Y-%m-%d")
        except (OverflowError, OSError, ValueError):
            return ""
    s = str(value).strip()
    # ISO com hora / timezone
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:len(fmt) + 6], fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    # epoch como string
    if s.isdigit():
        return _iso_date(int(s))
    return s[:10] if len(s) >= 10 else ""


def _get_json(url: str, params: dict | None = None):
    resp = requests.get(url, headers=HEADERS, params=params, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


# --------------------------------------------------------------------------- #
# RemoteOK — https://remoteok.com/api  (array JSON; 1º item é aviso legal)
# --------------------------------------------------------------------------- #
def fetch_remoteok() -> list[dict]:
    try:
        data = _get_json("https://remoteok.com/api")
    except Exception as e:  # noqa: BLE001
        _log(f"remoteok falhou: {e}")
        return []

    jobs = []
    for item in data:
        if not isinstance(item, dict) or not item.get("position"):
            continue  # pula o item de aviso legal / entradas inválidas
        # location vazio -> deixa vazio (vira "unknown" no filtro, não "other").
        # Não usamos tags como localização: tags são skills, não região.
        loc = (item.get("location") or "").strip()
        jobs.append({
            "id": f"remoteok-{item.get('id') or item.get('slug') or item.get('url')}",
            "title": item.get("position", "").strip(),
            "company": (item.get("company") or "").strip(),
            "location": loc,
            "url": item.get("url") or item.get("apply_url") or "",
            "source": "RemoteOK",
            "description": strip_html(item.get("description")),
            "date": _iso_date(item.get("date") or item.get("epoch")),
        })
    _log(f"remoteok: {len(jobs)} vagas")
    return jobs


# --------------------------------------------------------------------------- #
# Remotive — https://remotive.com/api/remote-jobs  ({"jobs": [...]})
# --------------------------------------------------------------------------- #
def fetch_remotive() -> list[dict]:
    try:
        data = _get_json("https://remotive.com/api/remote-jobs", params={"limit": 500})
    except Exception as e:  # noqa: BLE001
        _log(f"remotive falhou: {e}")
        return []

    jobs = []
    for item in data.get("jobs", []):
        jobs.append({
            "id": f"remotive-{item.get('id')}",
            "title": (item.get("title") or "").strip(),
            "company": (item.get("company_name") or "").strip(),
            "location": (item.get("candidate_required_location") or "").strip(),
            "url": item.get("url") or "",
            "source": "Remotive",
            "description": strip_html(item.get("description")),
            "date": _iso_date(item.get("publication_date")),
        })
    _log(f"remotive: {len(jobs)} vagas")
    return jobs


# --------------------------------------------------------------------------- #
# Arbeitnow — https://www.arbeitnow.com/api/job-board-api  ({"data":[...], links})
# --------------------------------------------------------------------------- #
def fetch_arbeitnow(max_pages: int = 5) -> list[dict]:
    jobs = []
    url = "https://www.arbeitnow.com/api/job-board-api"
    try:
        for _ in range(max_pages):
            data = _get_json(url)
            for item in data.get("data", []):
                if item.get("remote") is False:
                    continue
                jobs.append({
                    "id": f"arbeitnow-{item.get('slug')}",
                    "title": (item.get("title") or "").strip(),
                    "company": (item.get("company_name") or "").strip(),
                    "location": (item.get("location") or "").strip(),
                    "url": item.get("url") or "",
                    "source": "Arbeitnow",
                    "description": strip_html(item.get("description")),
                    "date": _iso_date(item.get("created_at")),
                })
            nxt = (data.get("links") or {}).get("next")
            if not nxt or nxt == url:
                break
            url = nxt
    except Exception as e:  # noqa: BLE001
        _log(f"arbeitnow falhou: {e}")
    _log(f"arbeitnow: {len(jobs)} vagas")
    return jobs


# --------------------------------------------------------------------------- #
# We Work Remotely — feeds RSS por categoria
# --------------------------------------------------------------------------- #
_WWR_FEEDS = [
    "https://weworkremotely.com/categories/remote-programming-jobs.rss",
    "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss",
    "https://weworkremotely.com/categories/remote-customer-support-jobs.rss",
    "https://weworkremotely.com/categories/remote-full-stack-programming-jobs.rss",
    "https://weworkremotely.com/categories/remote-back-end-programming-jobs.rss",
    "https://weworkremotely.com/categories/remote-front-end-programming-jobs.rss",
]


def fetch_weworkremotely() -> list[dict]:
    if feedparser is None:
        _log("weworkremotely pulado: feedparser não instalado")
        return []
    jobs = []
    for feed_url in _WWR_FEEDS:
        try:
            parsed = feedparser.parse(feed_url, request_headers=HEADERS)
        except Exception as e:  # noqa: BLE001
            _log(f"wwr feed {feed_url} falhou: {e}")
            continue
        for entry in parsed.entries:
            # título costuma vir "Empresa: Cargo"
            raw_title = entry.get("title", "")
            company, _, title = raw_title.partition(":")
            if not title:
                title, company = company, ""
            region = (entry.get("region") or "").strip()
            jobs.append({
                "id": f"wwr-{entry.get('id') or entry.get('link')}",
                "title": title.strip(),
                "company": company.strip(),
                "location": region or "Remote",
                "url": entry.get("link", ""),
                "source": "WeWorkRemotely",
                "description": strip_html(entry.get("summary")),
                "date": _iso_date(entry.get("published")),
            })
    _log(f"weworkremotely: {len(jobs)} vagas")
    return jobs


# --------------------------------------------------------------------------- #
# Jobicy — https://jobicy.com/api/v2/remote-jobs  ({"jobs": [...]})
# jobGeo é string ("USA", "Anywhere", ...); jobDescription vem em HTML.
# --------------------------------------------------------------------------- #
def fetch_jobicy() -> list[dict]:
    try:
        data = _get_json("https://jobicy.com/api/v2/remote-jobs", params={"count": 100})
    except Exception as e:  # noqa: BLE001
        _log(f"jobicy falhou: {e}")
        return []

    jobs = []
    for item in data.get("jobs", []):
        jobs.append({
            "id": f"jobicy-{item.get('id') or item.get('jobSlug') or item.get('url')}",
            "title": (item.get("jobTitle") or "").strip(),
            "company": (item.get("companyName") or "").strip(),
            "location": (item.get("jobGeo") or "").strip(),
            "url": item.get("url") or "",
            "source": "Jobicy",
            "description": strip_html(item.get("jobDescription") or item.get("jobExcerpt")),
            "date": _iso_date(item.get("pubDate")),
        })
    _log(f"jobicy: {len(jobs)} vagas")
    return jobs


# --------------------------------------------------------------------------- #
# Himalayas — https://himalayas.app/jobs/api  ({"jobs": [...], "totalCount"})
# locationRestrictions é lista (["United States"] etc.); vazia = sem restrição.
# pubDate é epoch (segundos); guid é a URL canônica.
# --------------------------------------------------------------------------- #
def fetch_himalayas(max_jobs: int = 200) -> list[dict]:
    # A API devolve no máx. 20 por request; paginamos via offset até max_jobs.
    jobs = []
    offset = 0
    page_size = 20
    try:
        while len(jobs) < max_jobs:
            data = _get_json(
                "https://himalayas.app/jobs/api",
                params={"limit": page_size, "offset": offset},
            )
            batch = data.get("jobs", [])
            if not batch:
                break
            for item in batch:
                restrictions = item.get("locationRestrictions") or []
                location = ", ".join(r for r in restrictions if r).strip()
                jobs.append({
                    "id": f"himalayas-{item.get('guid') or item.get('applicationLink')}",
                    "title": (item.get("title") or "").strip(),
                    "company": (item.get("companyName") or "").strip(),
                    "location": location,
                    "url": item.get("applicationLink") or item.get("guid") or "",
                    "source": "Himalayas",
                    "description": strip_html(item.get("description") or item.get("excerpt")),
                    "date": _iso_date(item.get("pubDate")),
                })
            offset += page_size
    except Exception as e:  # noqa: BLE001
        _log(f"himalayas falhou (offset {offset}): {e}")
    _log(f"himalayas: {len(jobs)} vagas")
    return jobs


# --------------------------------------------------------------------------- #
# Working Nomads — https://www.workingnomads.com/api/exposed_jobs/  (array JSON)
# Não traz id estável; derivamos da URL. location é texto ("Latin America"...).
# --------------------------------------------------------------------------- #
def fetch_workingnomads() -> list[dict]:
    try:
        data = _get_json("https://www.workingnomads.com/api/exposed_jobs/")
    except Exception as e:  # noqa: BLE001
        _log(f"workingnomads falhou: {e}")
        return []

    jobs = []
    for item in data if isinstance(data, list) else []:
        url = item.get("url") or ""
        jobs.append({
            "id": f"workingnomads-{url}",
            "title": (item.get("title") or "").strip(),
            "company": (item.get("company_name") or "").strip(),
            "location": (item.get("location") or "").strip(),
            "url": url,
            "source": "WorkingNomads",
            "description": strip_html(item.get("description")),
            "date": _iso_date(item.get("pub_date")),
        })
    _log(f"workingnomads: {len(jobs)} vagas")
    return jobs


# --------------------------------------------------------------------------- #
# Adzuna — https://api.adzuna.com  (API oficial; exige chave grátis)
# Cobre vagas POR PAÍS (inclui "br"), o que os boards remote-first não pegam.
# Configure ADZUNA_APP_ID / ADZUNA_APP_KEY no ambiente (ou nos GitHub Secrets).
# Sem chave, a função apenas loga e devolve [] — não quebra o run.
#
# IMPORTANTE — remoto: a API da Adzuna NÃO tem flag de "remote" e a descrição
# que ela devolve vem TRUNCADA, então filtrar remoto no nosso lado é furado.
# Solução: fazemos o Adzuna filtrar no servidor dele (onde ele vê o texto
# completo) buscando o termo de remoto via `what_phrase` (frase exata). Assim só
# voltam vagas remotas; o filtro de TÍTULO do pipeline cuida do cargo.
#
# config.json (opcional):
#   "adzuna_countries": ["br"]                      # códigos ISO (br, us, gb...)
#   "adzuna_remote_terms": ["remoto","remote","home office"]  # frases de remoto
#   "adzuna_pages": 2                               # páginas por termo/país (50/pág)
# --------------------------------------------------------------------------- #
_ADZUNA_COUNTRY_NAME = {
    "br": "Brasil", "us": "United States", "gb": "United Kingdom", "ca": "Canada",
    "de": "Germany", "fr": "France", "es": "Spain", "it": "Italy", "pt": "Portugal",
    "nl": "Netherlands", "au": "Australia", "mx": "Mexico", "in": "India",
}
_ADZUNA_DEFAULT_REMOTE_TERMS = ["remoto", "remote", "home office"]


def fetch_adzuna(countries=None, remote_terms=None,
                 pages: int = 2, max_days_old: int = 30) -> list[dict]:
    app_id = os.environ.get("ADZUNA_APP_ID", "").strip()
    app_key = os.environ.get("ADZUNA_APP_KEY", "").strip()
    if not app_id or not app_key:
        _log("adzuna pulado: defina ADZUNA_APP_ID e ADZUNA_APP_KEY")
        return []

    countries = countries or ["br"]
    remote_terms = remote_terms or _ADZUNA_DEFAULT_REMOTE_TERMS
    seen: set[str] = set()
    jobs = []
    for country in countries:
        country = country.lower().strip()
        cname = _ADZUNA_COUNTRY_NAME.get(country, country.upper())
        for term in remote_terms:
            for page in range(1, pages + 1):
                try:
                    data = _get_json(
                        f"https://api.adzuna.com/v1/api/jobs/{country}/search/{page}",
                        params={
                            "app_id": app_id,
                            "app_key": app_key,
                            "results_per_page": 50,
                            # what_phrase = frase exata no texto completo (server-side):
                            # garante que a vaga é de fato remota.
                            "what_phrase": term,
                            "max_days_old": max_days_old,
                            "content-type": "application/json",
                        },
                    )
                except Exception as e:  # noqa: BLE001
                    _log(f"adzuna {country}/{term} p{page} falhou: {e}")
                    break
                results = data.get("results", [])
                if not results:
                    break
                for item in results:
                    jid = f"adzuna-{item.get('id')}"
                    if jid in seen:
                        continue  # mesma vaga pode bater em mais de um termo de remoto
                    seen.add(jid)
                    loc = ((item.get("location") or {}).get("display_name") or "").strip()
                    # anexa país (p/ o classificador de região) + "Remoto" (rastreável)
                    location = f"{loc}, {cname} (Remoto)" if loc else f"{cname} (Remoto)"
                    jobs.append({
                        "id": jid,
                        "title": (item.get("title") or "").strip(),
                        "company": ((item.get("company") or {}).get("display_name") or "").strip(),
                        "location": location,
                        "url": item.get("redirect_url") or "",
                        "source": "Adzuna",
                        "description": strip_html(item.get("description")),
                        "date": _iso_date(item.get("created")),
                    })
    _log(f"adzuna: {len(jobs)} vagas")
    return jobs


FETCHERS = {
    "remoteok": fetch_remoteok,
    "remotive": fetch_remotive,
    "arbeitnow": fetch_arbeitnow,
    "weworkremotely": fetch_weworkremotely,
    "jobicy": fetch_jobicy,
    "himalayas": fetch_himalayas,
    "workingnomads": fetch_workingnomads,
    "adzuna": fetch_adzuna,
}
