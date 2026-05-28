# DiyHome — Home Assistant Integration

<p align="center">
  <img src="https://raw.githubusercontent.com/DIYHome-Repo/ha-diyhome/main/icon.png" alt="DiyHome" width="120" />
</p>

<p align="center">
  <strong>Controllo nativo dei dispositivi DiyHome direttamente da Home Assistant</strong>
</p>

<p align="center">
  <a href="https://github.com/hacs/integration"><img src="https://img.shields.io/badge/HACS-Custom-orange.svg" alt="HACS Custom" /></a>
  <a href="https://diyhome.cloud"><img src="https://img.shields.io/badge/diyhome.cloud-blue.svg" alt="DiyHome Cloud" /></a>
  <a href="https://github.com/DIYHome-Repo/ha-diyhome/releases"><img src="https://img.shields.io/github/v/release/DIYHome-Repo/ha-diyhome" alt="Release" /></a>
</p>

---

## Cos'è DiyHome?

**DiyHome** è una piattaforma IoT cloud-first per la gestione smart dell'irrigazione e dei sensori idrici. I dispositivi DiyHome si connettono al cloud tramite MQTT sicuro e ora possono essere controllati direttamente da Home Assistant.

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
