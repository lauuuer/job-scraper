"""Deduplicação de vagas iguais que aparecem em fontes diferentes.

A mesma vaga costuma ser publicada em vários boards (RemoteOK, WWR, Jobicy...).
O dedup por `id` do main.py só pega duplicata exata dentro do run; aqui pegamos
"a mesma vaga em fontes diferentes" por similaridade.

Regra: o título PRECISA ser similar (>= title_similarity) — é o que separa
"mesma vaga" de "vagas diferentes da mesma empresa". Dado isso, basta um de:
  1. Mesma empresa (normalizada, sem sufixo Inc/Ltd/...).
  2. Descrições >= description_similarity (ponte p/ quando o nome da empresa vem
     escrito diferente entre as fontes).

Por que o título é obrigatório: o texto institucional da empresa ("Sobre a
Acme...") domina a descrição e faz papéis distintos (Frontend Jr vs Backend Pleno
da mesma empresa) baterem 90%+ na descrição. Sem checar o título, isso vira
falso positivo. Com ele, só mescla o que é de fato o mesmo posting.

Para não comparar todo-mundo-com-todo-mundo (O(n²) caro), agrupamos em baldes
por empresa e por prefixo do título; só comparamos pares dentro do mesmo balde.
Pares duplicados viram um grupo (union-find); de cada grupo mantemos UMA vaga
canônica (o first_seen mais antigo, desempate pela descrição mais completa) e
registramos as outras fontes em `also_on`.
"""

from __future__ import annotations

import re
from collections import defaultdict
from difflib import SequenceMatcher

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_COMPANY_SUFFIX = re.compile(
    r"\b(inc|llc|ltd|limited|gmbh|co|corp|corporation|company|sc|sa|srl|"
    r"bv|ab|oy|plc|pvt|technologies|tech|labs|solutions|software)\b"
)
_DESC_CAP = 2000  # comparar só o começo da descrição já basta e é mais rápido


def _norm(s: str | None) -> str:
    return _NON_ALNUM.sub(" ", (s or "").lower()).strip()


def _norm_company(s: str | None) -> str:
    s = _COMPANY_SUFFIX.sub(" ", _norm(s))
    return re.sub(r"\s+", " ", s).strip()


def _norm_desc(s: str | None) -> str:
    return _NON_ALNUM.sub(" ", (s or "").lower()).strip()[:_DESC_CAP]


def _ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _title_ratio(a: str, b: str) -> float:
    """Similaridade de título POR PALAVRA (não por caractere).

    Comparar caractere a caractere engana: 'backend pleno' e 'frontend pleno'
    batem >0.9 porque só muda uma palavra dentro de um título longo. Comparando
    listas de tokens, trocar uma palavra que importa derruba a similaridade.
    """
    ta, tb = a.split(), b.split()
    if not ta or not tb:
        return 0.0
    return SequenceMatcher(None, ta, tb).ratio()


def dedupe_jobs(jobs: list[dict], config: dict) -> tuple[list[dict], int]:
    """Retorna (vagas_sem_duplicata, qtd_removida)."""
    cfg = config.get("dedup", {})
    if not cfg.get("enabled", True) or len(jobs) < 2:
        return jobs, 0

    title_thr = cfg.get("title_similarity", 0.9)
    desc_thr = cfg.get("description_similarity", 0.9)
    min_desc = cfg.get("min_description_chars", 300)

    n = len(jobs)
    titles = [_norm(j.get("title")) for j in jobs]
    companies = [_norm_company(j.get("company")) for j in jobs]
    descs = [_norm_desc(j.get("description")) for j in jobs]

    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    # Baldes: por empresa e por prefixo de título. Pares candidatos = quem cai
    # no mesmo balde em qualquer um dos dois critérios.
    by_company: dict[str, list[int]] = defaultdict(list)
    by_title: dict[str, list[int]] = defaultdict(list)
    for i in range(n):
        if companies[i]:
            by_company[companies[i]].append(i)
        prefix = " ".join(titles[i].split()[:5])
        if prefix:
            by_title[prefix].append(i)

    candidates: set[tuple[int, int]] = set()
    for buckets in (by_company, by_title):
        for idxs in buckets.values():
            if len(idxs) < 2:
                continue
            for a in range(len(idxs)):
                for b in range(a + 1, len(idxs)):
                    i, j = idxs[a], idxs[b]
                    candidates.add((i, j) if i < j else (j, i))

    for i, j in candidates:
        # Título precisa bater (por palavra): títulos diferentes => vagas diferentes.
        if _title_ratio(titles[i], titles[j]) < title_thr:
            continue
        same_company = bool(companies[i]) and companies[i] == companies[j]
        if same_company:
            union(i, j)
        elif len(descs[i]) >= min_desc and len(descs[j]) >= min_desc:
            if _ratio(descs[i], descs[j]) >= desc_thr:
                union(i, j)

    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(i)

    result: list[dict] = []
    removed = 0
    for members in groups.values():
        if len(members) == 1:
            result.append(jobs[members[0]])
            continue
        # Canônica: first_seen mais antigo; desempate pela descrição mais longa.
        members.sort(key=lambda m: (
            jobs[m].get("first_seen") or "9999-99-99",
            -len(jobs[m].get("description") or ""),
        ))
        canon = jobs[members[0]]
        others = members[1:]
        # Preserva o histórico: first_seen do grupo = o mais antigo de todos.
        seens = [jobs[m].get("first_seen") for m in members if jobs[m].get("first_seen")]
        if seens:
            canon["first_seen"] = min(seens)
        canon["also_on"] = sorted({
            jobs[m].get("source", "") for m in others if jobs[m].get("source")
        })
        canon["dup_count"] = len(others)
        removed += len(others)
        result.append(canon)

    return result, removed
