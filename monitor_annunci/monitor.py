#!/usr/bin/env python3
"""Monitor di annunci di lavoro (uso personale).

Legge le fonti elencate in fonti.json (feed RSS/Atom o pagine HTML con elenchi
di link), filtra gli annunci con filtri.json, ricorda quelli gia' visti
(seen.json) e avvisa dei nuovi via email e/o notifica ntfy.

Solo libreria standard. Credenziali SOLO da variabili d'ambiente (mai nei file):
  SMTP_HOST, SMTP_PORT (587 o 465), SMTP_USER, SMTP_PASSWORD, MAIL_TO, MAIL_FROM
  NTFY_TOPIC, NTFY_SERVER (default https://ntfy.sh)

Uso:
  python monitor.py              controllo normale
  python monitor.py --verifica   esito per ogni fonte, senza salvare ne' avvisare
  python monitor.py --prova      come normale ma senza salvare ne' avvisare
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import smtplib
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import xml.etree.ElementTree as ET
from email.message import EmailMessage
from html.parser import HTMLParser

BASE = os.path.dirname(os.path.abspath(__file__))
UA = "MonitorAnnunciPersonale/1.0 (uso personale; lettura di pagine pubbliche)"
TIMEOUT = 20
MAX_BYTES = 5_000_000
MAX_VISTI = 5000
SOGLIA_ERRORI = 3
MAX_IN_AVVISO = 50
ETICHETTE_ZONA = {1: "Lucca e provincia", 2: "Pisa/Versilia", 3: "Firenze", 9: "altra zona / non indicata"}


# ---------- utilita' ----------

def carica(percorso, default):
    if not os.path.exists(percorso):
        return default
    with open(percorso, encoding="utf-8") as f:
        return json.load(f)


def salva(percorso, dati):
    with open(percorso, "w", encoding="utf-8") as f:
        json.dump(dati, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")


def norm(testo):
    return re.sub(r"\s+", " ", (testo or "").lower()).strip()


def pulisci_url(url):
    """Toglie il frammento e scarta i redirect di Google Alerts."""
    p = urllib.parse.urlsplit(url)
    if p.netloc.lower().startswith("www.google.") and p.path == "/url":
        reale = urllib.parse.parse_qs(p.query).get("url")
        if reale:
            return pulisci_url(reale[0])
    return urllib.parse.urlunsplit((p.scheme, p.netloc, p.path, p.query, ""))


def contiene(parola, testo):
    """Corrispondenza a inizio parola; con '$' finale anche a fine parola."""
    esatta = parola.endswith("$")
    parola = parola.rstrip("$").lower()
    modello = r"(?<!\w)" + re.escape(parola) + (r"(?!\w)" if esatta else "")
    return re.search(modello, testo) is not None


# ---------- rete ----------

_robots = {}


def consentito(url):
    parti = urllib.parse.urlsplit(url)
    if parti.scheme not in ("http", "https"):
        return True
    radice = f"{parti.scheme}://{parti.netloc}"
    if radice not in _robots:
        rp = urllib.robotparser.RobotFileParser()
        try:
            req = urllib.request.Request(radice + "/robots.txt", headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                rp.parse(r.read(500_000).decode("utf-8", errors="replace").splitlines())
        except urllib.error.HTTPError:
            rp.parse([])  # robots.txt assente o non accessibile: nessuna restrizione
        except Exception:
            rp = None  # rete non disponibile: ci pensera' il download a fallire
        _robots[radice] = rp
    rp = _robots[radice]
    return True if rp is None else rp.can_fetch(UA, url)


def scarica(url):
    if not consentito(url):
        raise RuntimeError("vietato da robots.txt")
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "it,en;q=0.5"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        grezzo = r.read(MAX_BYTES)
        codifica = r.headers.get_content_charset() or "utf-8"
    return grezzo, codifica


# ---------- lettura delle fonti ----------

class _Link(HTMLParser):
    def __init__(self, base):
        super().__init__()
        self.base, self.links, self._href, self._txt = base, [], None, []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href, self._txt = dict(attrs).get("href"), []

    def handle_data(self, dati):
        if self._href is not None:
            self._txt.append(dati)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            titolo = " ".join("".join(self._txt).split())
            if titolo and not self._href.startswith(("#", "javascript:", "mailto:", "tel:")):
                self.links.append((titolo, urllib.parse.urljoin(self.base, self._href)))
            self._href = None


def _testo(el, nome):
    f = el.find(nome)
    return (f.text or "").strip() if f is not None and f.text else ""


def leggi_feed(grezzo):
    radice = ET.fromstring(grezzo)
    voci = []
    for item in radice.iter("item"):  # RSS
        voci.append({"titolo": _testo(item, "title"), "url": _testo(item, "link"),
                     "testo": re.sub(r"<[^>]+>", " ", _testo(item, "description"))})
    atom = "{http://www.w3.org/2005/Atom}"
    for e in radice.iter(atom + "entry"):  # Atom
        link = e.find(atom + "link")
        url = link.get("href", "") if link is not None else ""
        riass = e.find(atom + "summary")
        voci.append({"titolo": re.sub(r"<[^>]+>", "", _testo(e, atom + "title")), "url": url,
                     "testo": re.sub(r"<[^>]+>", " ", (riass.text or "") if riass is not None else "")})
    return voci


def raccogli(fonte):
    grezzo, codifica = scarica(fonte["url"])
    if fonte.get("tipo", "html") in ("rss", "atom", "feed"):
        voci = leggi_feed(grezzo)
    else:
        parser = _Link(fonte["url"])
        parser.feed(grezzo.decode(codifica, errors="replace"))
        voci = [{"titolo": t, "url": u, "testo": ""} for t, u in parser.links]
    return [v for v in voci if v["titolo"] and v["url"]]


# ---------- filtri ----------

def valuta(voce, fonte, filtri):
    """Ritorna un dict con l'annuncio arricchito, o None se non interessa."""
    testo = norm(voce["titolo"] + " " + voce.get("testo", ""))
    if any(contiene(p, testo) for p in filtri.get("escludi", [])):
        return None
    if fonte.get("tutti"):
        gruppi = ["tutte le novita'"]
    elif fonte.get("parole"):
        gruppi = ["pagina lavoro"] if any(contiene(p, testo) for p in fonte["parole"]) else []
    else:
        gruppi = [nome for nome, g in filtri.get("gruppi", {}).items()
                  if g.get("attivo", True) and any(contiene(p, testo) for p in g["parole"])]
    if not gruppi:
        return None
    priorita = fonte.get("priorita_zona", 9)
    for livello in ("1", "2", "3"):
        if any(contiene(l, testo) for l in filtri.get("luoghi", {}).get(livello, [])):
            priorita = min(priorita, int(livello))
            break
    return {"titolo": voce["titolo"], "url": pulisci_url(voce["url"]), "fonte": fonte["nome"],
            "gruppi": gruppi, "priorita": priorita}


# ---------- avvisi ----------

def costruisci_testo(nuovi, problemi, primo_avvio):
    righe = []
    if primo_avvio:
        righe.append("Primo avvio: questi sono gli annunci gia' presenti nelle fonti.\n")
    ordinati = sorted(nuovi, key=lambda r: (r["priorita"], r["titolo"].lower()))
    for i, r in enumerate(ordinati[:MAX_IN_AVVISO], 1):
        righe.append(f"{i}. {r['titolo']}\n   {r['fonte']} | {ETICHETTE_ZONA[r['priorita']]} | "
                     f"{', '.join(r['gruppi'])}\n   {r['url']}\n")
    if len(ordinati) > MAX_IN_AVVISO:
        righe.append(f"... e altri {len(ordinati) - MAX_IN_AVVISO}: elenco completo in annunci_nuovi.md\n")
    if problemi:
        righe.append("Fonti da controllare:")
        righe += [f" - {p}" for p in problemi]
    righe.append("\nDati da verificare alla fonte prima di candidarsi (scadenze e requisiti).")
    return "\n".join(righe)


def invia_email(oggetto, corpo):
    host, utente = os.environ.get("SMTP_HOST"), os.environ.get("SMTP_USER")
    pwd, a = os.environ.get("SMTP_PASSWORD"), os.environ.get("MAIL_TO")
    if not (host and utente and pwd and a):
        return None
    porta = int(os.environ.get("SMTP_PORT") or 587)
    msg = EmailMessage()
    msg["Subject"], msg["To"] = oggetto, a
    msg["From"] = os.environ.get("MAIL_FROM") or utente
    msg.set_content(corpo)
    ctx = ssl.create_default_context()
    try:
        if porta == 465:
            with smtplib.SMTP_SSL(host, porta, context=ctx, timeout=30) as s:
                s.login(utente, pwd)
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, porta, timeout=30) as s:
                s.starttls(context=ctx)
                s.login(utente, pwd)
                s.send_message(msg)
        return True
    except Exception as e:
        print(f"[email] errore: {type(e).__name__}", file=sys.stderr)
        return False


def invia_ntfy(nuovi, problemi):
    argomento = os.environ.get("NTFY_TOPIC")
    if not argomento:
        return None
    server = (os.environ.get("NTFY_SERVER") or "https://ntfy.sh").rstrip("/")
    ordinati = sorted(nuovi, key=lambda r: (r["priorita"], r["titolo"].lower()))
    corpo = "\n".join(f"- {r['titolo']} ({r['fonte']})" for r in ordinati[:5])
    if len(ordinati) > 5:
        corpo += f"\n... e altri {len(ordinati) - 5}"
    if problemi:
        corpo += "\nFonti da controllare: " + "; ".join(problemi)
    intest = {"User-Agent": UA, "Title": f"Nuovi annunci: {len(nuovi)}", "Tags": "briefcase",
              "Priority": "high" if any(r["priorita"] == 1 for r in nuovi) else "default"}
    if ordinati:
        intest["Click"] = urllib.parse.quote(ordinati[0]["url"], safe=":/?&=%#@+,;~")
    try:
        req = urllib.request.Request(f"{server}/{urllib.parse.quote(argomento)}",
                                     data=corpo.encode("utf-8"), headers=intest, method="POST")
        urllib.request.urlopen(req, timeout=20).close()
        return True
    except Exception as e:
        print(f"[ntfy] errore: {type(e).__name__}", file=sys.stderr)
        return False


def aggiorna_file_md(percorso, nuovi, primo_avvio):
    adesso = dt.datetime.now(dt.timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    sezione = [f"## {adesso}" + (" (primo avvio)" if primo_avvio else ""), ""]
    for r in sorted(nuovi, key=lambda r: (r["priorita"], r["titolo"].lower())):
        sezione.append(f"- [{r['titolo']}]({r['url']}) - {r['fonte']} - "
                       f"{ETICHETTE_ZONA[r['priorita']]} - {', '.join(r['gruppi'])}")
    sezione.append("")
    intestazione = ("# Annunci nuovi\n\nGenerato dal monitor. Tutti i dati sono **da verificare alla fonte** "
                    "(data della verifica: quella dell'elenco).\n\n")
    vecchio = ""
    if os.path.exists(percorso):
        with open(percorso, encoding="utf-8") as f:
            contenuto = f.read()
        inizio = contenuto.find("## ")
        vecchio = contenuto[inizio:] if inizio >= 0 else ""
    corpo = "\n".join(sezione) + "\n" + vecchio
    righe = corpo.split("\n")[:3000]
    with open(percorso, "w", encoding="utf-8") as f:
        f.write(intestazione + "\n".join(righe))


# ---------- principale ----------

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prova", action="store_true", help="non salva lo stato e non invia avvisi")
    ap.add_argument("--verifica", action="store_true", help="mostra l'esito per fonte (implica --prova)")
    ap.add_argument("--fonti", default=os.path.join(BASE, "fonti.json"))
    ap.add_argument("--filtri", default=os.path.join(BASE, "filtri.json"))
    ap.add_argument("--stato", default=os.path.join(BASE, "seen.json"))
    ap.add_argument("--uscita", default=os.path.join(BASE, "annunci_nuovi.md"))
    args = ap.parse_args()
    prova = args.prova or args.verifica

    fonti = carica(args.fonti, {}).get("fonti", [])
    filtri = carica(args.filtri, {})
    stato = carica(args.stato, {})
    stato.setdefault("visti", {})
    stato.setdefault("fonti_inizializzate", [])
    stato.setdefault("errori", {})
    primo_avvio = not stato["visti"] and not stato["fonti_inizializzate"]

    nuovi, problemi = [], []
    for f in fonti:
        nome = f["nome"]
        if not f.get("attiva", True):
            if args.verifica:
                print(f"[--] {nome}: disattivata")
            continue
        try:
            voci = raccogli(f)
            stato["errori"][nome] = 0
        except Exception as e:
            n = stato["errori"].get(nome, 0) + 1
            stato["errori"][nome] = n
            print(f"[KO] {nome}: {type(e).__name__}: {e}", file=sys.stderr)
            if n == SOGLIA_ERRORI:
                problemi.append(f"{nome}: non raggiungibile da {n} controlli ({f['url']})")
            continue
        silenziosa = f.get("tutti") and nome not in stato["fonti_inizializzate"]
        trovati = 0
        for v in voci:
            r = valuta(v, f, filtri)
            if not r:
                continue
            idv = hashlib.sha1(r["url"].encode("utf-8")).hexdigest()[:16]
            if idv in stato["visti"]:
                continue
            stato["visti"][idv] = {"titolo": r["titolo"], "url": r["url"], "fonte": nome,
                                   "data": dt.date.today().isoformat()}
            if not silenziosa:
                nuovi.append(r)
                trovati += 1
        if nome not in stato["fonti_inizializzate"]:
            stato["fonti_inizializzate"].append(nome)
        if args.verifica:
            print(f"[OK] {nome}: {len(voci)} voci lette, {trovati} nuove pertinenti"
                  + (" (riferimento iniziale silenzioso)" if silenziosa else ""))
            for r in nuovi[-trovati:] if trovati else []:
                print(f"       - {r['titolo']} -> {r['url']}")

    print(f"Nuovi annunci: {len(nuovi)}; fonti da controllare: {len(problemi)}")
    if prova:
        for r in nuovi if not args.verifica else []:
            print(f" * {r['titolo']} | {r['fonte']} | {r['url']}")
        return 0

    if nuovi or problemi:
        corpo = costruisci_testo(nuovi, problemi, primo_avvio)
        oggetto = f"[Annunci] {len(nuovi)} nuovi" + (
            f" ({sum(1 for r in nuovi if r['priorita'] == 1)} in zona Lucca)" if nuovi else " - fonti da controllare")
        esiti = [invia_email(oggetto, corpo), invia_ntfy(nuovi, problemi)]
        configurati = [e for e in esiti if e is not None]
        if configurati and not any(configurati):
            print("Nessun avviso consegnato: stato non salvato, riprovo al prossimo giro.", file=sys.stderr)
            return 1
        if not configurati:
            print("Nessun canale di avviso configurato: vedi README.", file=sys.stderr)
        if nuovi:
            aggiorna_file_md(args.uscita, nuovi, primo_avvio)

    visti = stato["visti"]
    if len(visti) > MAX_VISTI:
        for k in sorted(visti, key=lambda k: visti[k]["data"])[:len(visti) - MAX_VISTI]:
            del visti[k]
    stato["ultimo_controllo"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    salva(args.stato, stato)
    return 0


if __name__ == "__main__":
    sys.exit(main())
