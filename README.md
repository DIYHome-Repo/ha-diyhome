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

## Configurazione OAuth2

L'integrazione usa **OAuth2** per autenticarsi in modo sicuro con il tuo account DiyHome.

Al momento dell'aggiunta dell'integrazione, Home Assistant aprirà il browser su [diyhome.cloud](https://diyhome.cloud) per completare l'autorizzazione. Nessuna password viene memorizzata localmente.

> **Prima volta?** Assicurati di aver effettuato l'accesso su [diyhome.cloud](https://diyhome.cloud) prima di avviare la configurazione.

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
