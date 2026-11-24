# DIYHome — Home Assistant Integration

<p align="center">
  <img src="https://raw.githubusercontent.com/DIYHome-Repo/ha-diyhome/main/icon.png" alt="DiyHome" width="120" />
</p>

<p align="center">
  <strong>Controllo nativo dei dispositivi DIYHome direttamente da Home Assistant</strong>
</p>

<p align="center">
  <a href="https://github.com/hacs/integration"><img src="https://img.shields.io/badge/HACS-Custom-orange.svg" alt="HACS Custom" /></a>
  <a href="https://diyhome.cloud"><img src="https://img.shields.io/badge/diyhome.cloud-blue.svg" alt="DiyHome Cloud" /></a>
  <a href="https://github.com/DIYHome-Repo/ha-diyhome/releases"><img src="https://img.shields.io/github/v/release/DIYHome-Repo/ha-diyhome" alt="Release" /></a>
</p>

---

## Cos'è DIYHome?

**DIYHome** è una piattaforma IoT cloud-first per la gestione smart dell'irrigazione e dei sensori idrici. I dispositivi DIYHome si connettono al cloud tramite MQTT sicuro e ora possono essere controllati direttamente da Home Assistant.

Con questa integrazione puoi:
- Controllare valvole di irrigazione come switch in HA
- Monitorare il livello della cisterna in tempo reale
- Leggere temperatura e portata dal sensore integrato
- Automatizzare l'irrigazione con le potenti automazioni di HA

---

## Funzionalità

| Entità | Tipo | Descrizione |
|--------|------|-------------|
| `switch.diyhome_*_valve` | Switch | Valvola principale (apertura/chiusura) |
| `switch.diyhome_*_valve2` | Switch | Valvola secondaria |
| `sensor.diyhome_*_tank_level` | Sensor | Livello cisterna in percentuale (%) |
| `sensor.diyhome_*_tank_liters` | Sensor | Volume cisterna in litri (L) |
| `sensor.diyhome_*_temperature` | Sensor | Temperatura sensore integrato (°C) |
| `binary_sensor.diyhome_*_online` | Binary Sensor | Stato connessione cloud |

---

## Requisiti

- Home Assistant **2024.1.0** o superiore
- Account attivo su [diyhome.cloud](https://diyhome.cloud)
- Almeno un dispositivo DiyHome registrato e connesso

---

## Installazione via HACS

1. Apri **HACS** → **Integrazioni**
2. Menu (⋮) in alto a destra → **Custom repositories**
3. Inserisci `https://github.com/DIYHome-Repo/ha-diyhome` — Categoria: **Integration**
4. Cerca **DiyHome** e clicca **Scarica**
5. Riavvia Home Assistant
6. Vai su **Impostazioni → Dispositivi e servizi → Aggiungi integrazione**
7. Cerca **DiyHome** e avvia la configurazione

---

## Accesso all'account

La configurazione chiede **email e password** dell'account DiyHome (non apre il
browser). Se l'account usa la verifica in due passaggi, al passo successivo
chiede il codice a 6 cifre dell'app di autenticazione (o un codice di
recupero). La password non viene salvata: Home Assistant conserva solo il token
di accesso emesso dal cloud.

Se il token viene revocato o scade, Home Assistant mostra **«Accedi di nuovo»**
nella pagina dell'integrazione: basta reinserire la password (ed eventualmente
il codice), senza rimuovere e riaggiungere l'integrazione.

---

## Supporto

- 📖 [Documentazione ufficiale](https://diyhome.cloud/docs/integrations/home-assistant)
- 🐛 [Segnala un problema](https://github.com/DIYHome-Repo/ha-diyhome/issues)
- 🌐 [Sito ufficiale](https://diyhome.cloud)

## Qualificazione dello stato valvola — R21

V1/V2 con `state_qualified: false`, o senza un booleano `is_open`, espongono
stato sconosciuto (`is_on=None`). Un comando non lo trasforma in OFF/ON;
CLOSE resta inviabile. Il consumer MQTT conserva la qualificazione di ciascuna
valvola, anche se un report legacy successivo la omette. Un booleano valido con
qualificazione null/assente conserva la compatibilita' storica.

Questa correzione e' nel monorepo DIYHome; non attesta una release HACS o
l'aggiornamento delle installazioni. Distribuzione tramite i workflow esistenti
HACS Sync → PR, revisione/merge su ha-diyhome e HACS Release Tag. Nessun tag
creato da questo delta. L'ottimismo generale su dati qualificati (INT-004)
e i problemi separati dei trasporti restano da trattare.

## Comandi valvola: anteprima limitata e canali — 23/11/2026

Dopo ON/OFF su V1/V2 l'entità mostra subito il target, ma lo ritira se il
comando fallisce o viene rifiutato (errore visibile in HA) e, senza conferma
del dispositivo entro 15 secondi, torna allo stato riportato. I comandi
passano da LAN HTTP (con esito del dispositivo) o dal cloud; il broker MQTT
locale, se configurato, serve solo a leggere lo stato: il topic che il
firmware ascolta in locale usa un identificativo diverso dall'uid cloud
(INT-003). Zone e pompa conservano ancora l'anteprima senza scadenza.

## Versione 2.7.1 — 23/11/2026

Prima release HACS dopo la 2.7.0 pubblicata su `ha-diyhome`. Contiene, rispetto
a quella copia:

- stato valvola sconosciuto (R21): V1/V2 non qualificate o senza dato mostrano
  «sconosciuto», non ON/OFF; lo shadow MQTT aggiorna `valve1`/`valve2`;
- solo dispositivi WT-1 (`productTypeId` `wt1-v1`) da cloud e LAN;
- anteprima valvola ritirata su errore/rifiuto e scaduta dopo 15 s; comandi non
  più inviati sul broker MQTT locale (INT-004, INT-003; PR #544).

Distribuzione: workflow `HACS Sync → PR su ha-diyhome` (confirm `SYNC-HACS`),
revisione e merge della PR su `ha-diyhome`, poi `HACS Release Tag` con
version `2.7.1` e confirm `RELEASE-HACS`. I workflow li avvia il proprietario.

## Versione 2.7.2 — 23/11/2026

Chiude i residui della revisione di sicurezza dell'integrazione:

- **Zone e pompa** (INT-004): stessa regola della valvola. L'anteprima del
  comando sparisce se il comando fallisce o viene rifiutato (errore visibile
  in HA) e scade dopo 15 secondi senza conferma del dispositivo.
- **Più WT-1 sullo stesso account** (INT-011): l'indirizzo LAN serve un solo
  dispositivo, quello che risponde a quell'indirizzo. Gli altri restano
  visibili e comandabili tramite cloud; prima sparivano o ricevevano lo stato
  e i comandi del dispositivo LAN.
- **Chiave LAN** (INT-019): il JWT LAN viene inviato solo all'host che risponde
  con l'uid per cui è stato emesso. Un annuncio mDNS non cambia più in
  silenzio l'indirizzo salvato; per cambiarlo si usa **Configura**, che ora
  ha la precedenza sull'indirizzo trovato in automatico.
- **Accesso** (INT-013): verifica in due passaggi supportata, nuovo accesso
  guidato quando il token viene rifiutato, lista dispositivi letta nel
  formato del server. Rimosso il rinnovo automatico del token, che il server
  rifiutava sempre.

Limiti noti: la LAN resta in HTTP senza TLS e lo stato LAN non è autenticato,
quindi un host ostile in rete che dichiara lo stesso uid non viene escluso dal
controllo (serve un intervento sul firmware). Con più WT-1 solo uno usa la LAN
diretta.

Distribuzione: come per la 2.7.1 (`HACS Sync` con confirm `SYNC-HACS`, merge
della PR su `ha-diyhome`, `HACS Release Tag` con version `2.7.2` e confirm
`RELEASE-HACS`). I workflow li avvia il proprietario.
