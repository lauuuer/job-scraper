"""Orquestrador: coleta -> filtra -> faz merge com histórico -> grava docs/jobs.json.

Roda 1x/dia no GitHub Actions. Mantém o campo `first_seen` por vaga para o
dashboard conseguir destacar o que é "novo hoje". Vagas que sumiram das fontes
e ficaram velhas (> max_age_days) são descartadas para não acumular link morto.

Uso local:
    python -m scraper.main
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .dedup import dedupe_jobs
from .filters import JobFilter
from .sources import FETCHERS

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"
OUTPUT_PATH = ROOT / "docs" / "jobs.json"


def log(msg: str) -> None:
    print(f"[main] {msg}", file=sys.stderr)


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def load_previous() -> dict[str, dict]:
    """Mapa id -> vaga do último run (para preservar first_seen)."""
    if not OUTPUT_PATH.exists():
        return {}
    try:
        with open(OUTPUT_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return {j["id"]: j for j in data.get("jobs", [])}
    except (json.JSONDecodeError, KeyError, OSError) as e:
        log(f"não consegui ler jobs.json anterior: {e}")
        return {}


def collect(config: dict) -> list[dict]:
    jobs: list[dict] = []
    for name, enabled in config.get("sources", {}).items():
        if not enabled:
            continue
        fetcher = FETCHERS.get(name)
        if not fetcher:
            log(f"fonte desconhecida no config: {name}")
            continue
        if name == "adzuna":
            jobs.extend(fetcher(
                countries=config.get("adzuna_countries", ["br"]),
                remote_terms=config.get("adzuna_remote_terms"),
                pages=config.get("adzuna_pages", 2),
                max_days_old=config.get("max_age_days", 30),
            ))
        else:
            jobs.extend(fetcher())
    return jobs


def main() -> int:
    config = load_config()
    jf = JobFilter(config)
    previous = load_previous()
    today = date.today().isoformat()
    max_age = config.get("max_age_days", 30)

    raw = collect(config)
    log(f"total coletado: {len(raw)} vagas brutas")
    raw_ids = {j["id"] for j in raw if j.get("id")}

    matched: dict[str, dict] = {}
    rejected = 0
    for job in raw:
        if not job.get("title") or not job.get("url"):
            continue
        ok, _reason = jf.matches(job)
        if not ok:
            rejected += 1
            continue
        jid = job["id"]
        if jid in matched:
            continue  # dedupe dentro do run
        prev = previous.get(jid)
        job["first_seen"] = prev["first_seen"] if prev and prev.get("first_seen") else today
        job["is_new"] = job["first_seen"] == today
        matched[jid] = job

    log(f"aprovadas: {len(matched)} | rejeitadas: {rejected}")

    # Mantém vagas recentes que SUMIRAM das fontes (link ainda pode valer), até
    # max_age_days. Importante: só ressuscita o que não veio na coleta de hoje —
    # uma vaga que ainda está na fonte mas foi REJEITADA pelos filtros fica fora.
    cutoff = (date.today() - timedelta(days=max_age)).isoformat()
    for jid, prev in previous.items():
        if jid in matched or jid in raw_ids:
            continue
        if prev.get("first_seen", "0") < cutoff:
            continue
        # Re-aplica os filtros atuais: se o config mudou (ex.: novo exclude),
        # uma vaga antiga que agora não passa mais é descartada, não ressuscitada.
        ok, _reason = jf.matches(prev)
        if not ok:
            continue
        prev["is_new"] = False
        matched[jid] = prev

    # Dedup de "mesma vaga em fontes diferentes" (por empresa+título e descrição).
    deduped, removed = dedupe_jobs(list(matched.values()), config)
    log(f"dedup: {removed} duplicatas mescladas | {len(deduped)} únicas")
    # first_seen pode ter sido recuado p/ o mais antigo do grupo: recalcula is_new.
    for j in deduped:
        j["is_new"] = j.get("first_seen") == today

    jobs_out = sorted(
        deduped,
        key=lambda j: (j.get("first_seen", ""), j.get("date", "")),
        reverse=True,
    )

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "count": len(jobs_out),
        "new_today": sum(1 for j in jobs_out if j.get("is_new")),
        "sources": [k for k, v in config.get("sources", {}).items() if v],
        "jobs": jobs_out,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    log(f"gravado {OUTPUT_PATH} | {payload['count']} vagas ({payload['new_today']} novas hoje)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
