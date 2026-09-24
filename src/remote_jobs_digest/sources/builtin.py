"""Fuente Built In — sin API ni RSS, HTML server-renderizado (sin auth, sin JS).

builtin.com/jobs/remote?page=N devuelve las tarjetas ya en el HTML servido
(confirmado con curl: no hace falta navegador). Más frágil que una fuente con
API/RSS real: si Built In cambia las clases/`data-id` del marcado, esto deja
de traer ofertas en silencio (falla a 0, no lanza excepción — igual que
cualquier otra fuente si la extracción no encuentra nada).
"""

from __future__ import annotations

from bs4 import BeautifulSoup

from .base import Job, http_get, log

LIST_URL = "https://builtin.com/jobs/remote"
PAGES = 3  # más allá de esto la señal decae rápido frente al riesgo de 429


def _parse_page(html_text: str) -> list[Job]:
    soup = BeautifulSoup(html_text, "html.parser")
    out = []
    for card in soup.select("[data-id=job-card]"):
        title_a = card.select_one("[data-id=job-card-title]")
        company_span = card.select_one("[data-id=company-title] span")
        if not title_a:
            continue
        href = title_a.get("href", "")
        if not href:
            continue
        url = href if href.startswith("http") else f"https://builtin.com{href}"
        attrs = card.select_one(".bounded-attribute-section")
        # Texto combinado (ubicación + nivel + salario si lo hay): más señal
        # para el clasificador que intentar aislar cada campo por separado
        # de un marcado que puede cambiar entre despliegues de Built In.
        loc_text = attrs.get_text(" | ", strip=True) if attrs else ""
        out.append(Job(
            source="builtin",
            title=title_a.get_text(strip=True),
            company=company_span.get_text(strip=True) if company_span else "",
            url=url,
            location=loc_text or "Remote",
        ))
    return out


def fetch() -> list[Job]:
    jobs: list[Job] = []
    seen: set[str] = set()
    for page in range(1, PAGES + 1):
        try:
            r = http_get(LIST_URL, params={"page": page})
        except Exception as exc:  # noqa: BLE001
            log(f"  builtin page={page} falló: {exc}")
            continue
        got = _parse_page(r.text)
        if not got:
            break  # última página o el marcado cambió: parar, no insistir
        new = 0
        for j in got:
            if j.url in seen:
                continue
            seen.add(j.url)
            jobs.append(j)
            new += 1
        if new == 0:
            break  # misma página repetida: fin de la paginación real
    log(f"builtin: {len(jobs)} ofertas")
    return jobs
