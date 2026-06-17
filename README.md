# job-scraper

Coleta vagas **100% remotas** de dev (junior/pleno/senior) e support engineer
(L1/L2/L3, technical support), filtra fora qualquer vaga que mencione
**mentoring / coaching / pairing** ou que **exija diploma**, e mostra tudo num
dashboard web. Roda sozinho 1x/dia no GitHub Actions — você só abre a página.

## Como funciona

```
fontes (APIs grátis) ──> filtros (config.json) ──> dedup ──> docs/jobs.json ──> dashboard (docs/index.html)
       coleta                  inclui/exclui      mesma vaga    GitHub Actions        GitHub Pages
```

- **Fontes:** RemoteOK, Remotive, Arbeitnow, We Work Remotely, Jobicy, Himalayas,
  Working Nomads (todas com API/RSS aberto). **Adzuna** opcional (API oficial,
  cobre vagas por país incl. `br`) — exige chave grátis; veja abaixo.
- **Filtro de título:** mantém só cargos técnicos (`include_title_keywords`) e
  descarta intern/manager/etc (`exclude_title_keywords`).
- **Filtro de texto:** descarta a vaga se título+descrição contém `mentor`,
  `coach`, `pairing`, `pair programming` ou exigência de diploma (`exclude_keywords`).
- **Região:** mantém `worldwide` / `brazil` / `latam` (e `unknown`, se ligado);
  descarta o resto.
- **Dedup:** a mesma vaga vem em vários boards. O bloco `dedup` mescla duplicatas
  quando o **título bate (por palavra)** e a empresa é a mesma (ou a descrição é
  >= 90% similar). A canônica guarda as outras fontes em `also_on`. Comparar
  título por palavra evita juntar papéis distintos da mesma empresa (ex.: Frontend
  Jr vs Backend Pleno) ou a mesma vaga em cidades diferentes.
- **Novas hoje:** cada vaga guarda `first_seen`; o dashboard marca como `NOVA`
  o que apareceu no dia.

Tudo é configurável em [`config.json`](config.json) — sem mexer em código.

## Deploy (uma vez só)

1. Crie um repositório no GitHub e suba este projeto:
   ```bash
   git add -A && git commit -m "job scraper inicial"
   git branch -M main
   git remote add origin https://github.com/<voce>/job-scraper.git
   git push -u origin main
   ```
2. No GitHub: **Settings → Pages → Build and deployment → Source: _Deploy from a branch_**,
   branch `main`, pasta **`/docs`**. Salve. Sua URL será `https://<voce>.github.io/job-scraper/`.
3. No GitHub: **Settings → Actions → General → Workflow permissions →** marque
   **_Read and write permissions_** (pro bot conseguir commitar o `jobs.json`).
4. Rode a primeira coleta na mão: aba **Actions → scrape-jobs → Run workflow**.
   Depois disso roda sozinho todo dia às 06:00 (BRT).

Pronto: abra a URL do Pages no PC ou no celular. Nada de terminal.

## Rodar localmente (opcional, pra testar)

```bash
pip install -r requirements.txt
python -m scraper.main          # gera docs/jobs.json
python -m http.server -d docs   # abre http://localhost:8000
```

## Ajustar os filtros

Edite [`config.json`](config.json):
- quer incluir outros cargos? adicione em `include_title_keywords`.
- a comparação é em minúsculas; tokens curtos (`l1`, `jr`) batem por palavra
  inteira, frases com espaço (`pair programming`) batem literalmente.
- `keep_unknown_region: false` esconde vagas sem região declarada.
- ligue/desligue fontes em `sources`.
- ajuste o dedup em `dedup` (`title_similarity`, `description_similarity`).

### Ligar o Adzuna (vagas por país, incl. Brasil)

1. Crie uma conta grátis em https://developer.adzuna.com/ e pegue `app_id` + `app_key`.
2. Exponha como variáveis de ambiente (local) ou **GitHub Secrets** (no Actions):
   `ADZUNA_APP_ID` e `ADZUNA_APP_KEY`.
3. Em `config.json`: troque `"adzuna": false` para `true` e ajuste
   `adzuna_countries` (ex.: `["br","us"]`), `adzuna_query` e `adzuna_pages`.

Sem as chaves, a fonte é simplesmente pulada — não quebra o run.

## Limitações honestas

- O filtro de mentoring é por palavra-chave: pega ~90% dos casos. Frases tipo
  "no formal mentoring" seriam excluídas por engano (raras). Dá pra refinar
  depois com um passo de classificação por LLM se incomodar.
- Só usa fontes com API aberta. LinkedIn/Indeed ficaram de fora de propósito
  (scraping frágil + contra os ToS).
