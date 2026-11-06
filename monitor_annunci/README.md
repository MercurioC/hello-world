# Monitor annunci di lavoro

Controlla ogni 2 ore (circa 06:00-20:00 ora italiana) le fonti in `fonti.json`, tiene solo gli annunci pertinenti alle tue esperienze (`filtri.json`) e ti avvisa dei **nuovi** via email e/o notifica sul telefono. Gira su GitHub Actions, anche a PC spento. Gli annunci nuovi si accumulano anche in `annunci_nuovi.md`.

Ogni dato (bandi, scadenze, requisiti) e' **da verificare alla fonte** prima di candidarsi.

## Attivazione, una tantum

1. **Unisci (merge) la pull request** sul ramo `master`: GitHub esegue i flussi pianificati solo dal ramo principale.
2. **Imposta gli avvisi** in GitHub: Settings > Secrets and variables > Actions > New repository secret.
   Mai scrivere password nei file.

   | Canale | Secret da creare |
   |---|---|
   | Email | `SMTP_HOST`, `SMTP_PORT` (587 o 465), `SMTP_USER`, `SMTP_PASSWORD`, `MAIL_TO` (facoltativo `MAIL_FROM`) |
   | Telefono (ntfy) | `NTFY_TOPIC` (facoltativo `NTFY_SERVER`) |

   - **Gmail:** `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`, `SMTP_USER` = il tuo indirizzo, `SMTP_PASSWORD` = una **password per app** (serve la verifica in due passaggi), non quella normale. Altri provider: cerca "SMTP" sul loro sito.
   - **ntfy:** installa l'app ntfy, inventa un nome lungo e casuale (es. `lavoro-x7k2q9m4p8`: chi lo conosce puo' leggere gli avvisi), iscriviti in app e metti lo stesso nome in `NTFY_TOPIC`. Gli avvisi passano da ntfy.sh (servizio esterno): contengono solo titoli e link di annunci pubblici.
3. **Prova gli avvisi:** Actions > "Monitor annunci" > Run workflow > spunta *prova_avvisi*. Ricevi una mail intitolata "[Annunci] PROVA - avvisi funzionanti" e/o una notifica sul telefono. Nel log vedi per ogni canale "inviato", "ERRORE" o "non configurato". Non tocca l'elenco degli annunci.
4. **Prova le fonti:** scheda Actions > "Monitor annunci" > Run workflow > spunta *verifica*. Nel log vedi per ogni fonte se risponde e cosa trova. Le fonti segnate `[KO]` vanno corrette o disattivate.
5. **Primo avvio vero:** lancia di nuovo senza la spunta. Ricevi un primo elenco di quello che e' gia' presente, poi solo le novita'.

Se nessun avviso va a buon fine, il flusso risulta "fallito" (GitHub te lo segnala) e riprova al giro dopo senza perdere gli annunci.

## Personalizzazione

- **Cosa cercare:** `filtri.json`. Gruppi di parole per tema (accoglienza/musei, reception, creativo/3D, back office), ognuno con `"attivo": true/false`. Una parola trova anche le sue forme (`museal` trova museale, museali); con `$` finale solo la parola intera (`iat$`).
- **Cosa escludere:** `escludi` (ora: notturno, night, cucina, sala bar).
- **Zone:** `luoghi` ordina i risultati: 1 Lucca e provincia, 2 Pisa/Versilia, 3 Firenze. Gli altri compaiono in coda, non vengono esclusi.
- **Fonti:** `fonti.json`. Copia un blocco, cambia `url` e metti `"attiva": true`.

## Fonti: cosa funziona e cosa no

Il programma legge solo feed RSS e pagine pubbliche con elenchi di link, rispettando `robots.txt`.

| Fonte | Come coprirla |
|---|---|
| **LinkedIn, Indeed, InfoJobs** | I loro termini vietano la lettura automatica: usa i loro **alert via email** (parole: museo, biglietteria, accoglienza, front office, info point, receptionist; luoghi: Lucca, Pisa, Viareggio, Firenze; frequenza giornaliera) |
| **Google Alerts** | Crea gli alert con consegna **Feed RSS** e incolla l'indirizzo in `fonti.json`: copre molti siti piccoli senza sapere dove sono |
| **inPA, ToscanaLavoro** | Pagine dinamiche, spesso non leggibili: usa le loro notifiche/ricerche salvate con registrazione |
| **Musei, ville, cooperative, IAT, Comuni** | Pagina "Lavora con noi" o bandi come fonte `html`; le "sonde" gia' presenti segnalano una volta i link dove cercare |
| **Gare d'appalto** | Una ricerca su START Toscana e albi dei Comuni, con le stesse parole, va fatta a mano o via alert del portale |

Alternative agli avvisi: Telegram (si puo' aggiungere), un lettore RSS sul feed di Google Alerts, o solo `annunci_nuovi.md` nel repository.

## Limiti

- Non ho potuto provare le fonti reali ne' l'invio email/ntfy dal mio ambiente (rete bloccata): il collaudo e' stato fatto con pagine e feed di prova. Il punto 3 serve a questo.
- Un sito che cambia struttura o usa JavaScript puo' smettere di funzionare: dopo 3 controlli falliti arriva un avviso "fonte da controllare".
- GitHub puo' ritardare i flussi pianificati di alcuni minuti; il tempo di GitHub Actions su repository privato ha un limite mensile gratuito (usiamo circa 8 minuti al giorno).
- Il monitor non invia candidature e non contatta nessun ente.

## File

`monitor.py` (programma), `fonti.json`, `filtri.json`, `seen.json` (annunci gia' visti, aggiornato in automatico), `annunci_nuovi.md` (elenco), `../.github/workflows/monitor-annunci.yml` (pianificazione).
