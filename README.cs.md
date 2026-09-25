# NetBox Topology do Zabbix Map Sync

Jazyk:
[![English](https://img.shields.io/badge/English-switch-0a7ea4)](README.md)
[![Cesky](https://img.shields.io/badge/Cesky-active-cf2e2e)](README.cs.md)

Webova sluzba (FastAPI + htmx), ktera nacita topologii z NetBoxu a vytvari nebo aktualizuje mapy v Zabbixu.

## Funkce

- Import topologie z NetBoxu (vcetne XML exportu z pluginu topology-views).
- Mapovani popisku zarizeni z NetBoxu na hosty v Zabbixu.
- Vytvareni a aktualizace vice map v Zabbixu z weboveho formulare, kazda s vlastnim filtrem NetBox topologie.
- Automaticke rozmisteni uzlu pomoci silove orientovaneho algoritmu Fruchterman-Reingold.
- Nahled mapy (dry-run) pred zapisem do Zabbixu.
- Opakovana synchronizace vsech ulozenych map pres webhook nebo rucne.
- Podpora indikatoru linek z trigger mapovani v custom fieldu kabelu v NetBoxu.
- Volitelne vynechani patch panelu z topologie s prepojenim kabelu, ktere skrz ne prochazi.

## Pozadavky

- Python 3.10+
- NetBox API token
- Zabbix API prihlaseni nebo token

## Rychly start

1. Instalace:

```bash
pip install .
```

2. Nastavte povinne promenne prostredi:

```bash
export NETBOX_URL="https://netbox.example.com"
export NETBOX_TOKEN="your-netbox-token"
export ZABBIX_URL="https://zabbix.example.com/api_jsonrpc.php"
export ZABBIX_USER="Admin"
export ZABBIX_PASSWORD="zabbix"
```

3. Spustte web server:

```bash
zbx-map-sync --host 0.0.0.0 --port 8080
```

4. Otevrete `http://<host>:8080/`, vyplnte formular mapy (predvyplneny z promennych prostredi) a pouzijte
   **Preview (dry-run)** nebo **Save & sync map**.

## Docker

GitHub workflow publikuje image do Docker Hub pod nazvem:

```text
<dockerhub-username>/netbox-topology-zabbix-map
```

Pull a spusteni (mapy vytvorene v UI se ukladaji do `/data/maps.json`, uchovejte je na volume):

```bash
docker run -d -p 7010:7010 --env-file .env -v zbx-map-sync-data:/data <dockerhub-username>/netbox-topology-zabbix-map:latest
```

Lokalni build:

```bash
docker build -t netbox-topology-zabbix-map:local .
```

## Webove UI a endpointy

`zbx-map-sync` vzdy spousti web server (`--host`, `--port`, `--log-level`).

Uvodni stranka zobrazuje pripojeni k NetBoxu/Zabbixu (tajne udaje se nikdy nezobrazuji), formular pro novou
mapu s vychozimi hodnotami z promennych prostredi nize a seznam ulozenych map s akcemi Sync / Edit / Delete.
Filtr topologie se predava do exportu pluginu topology-views, takze kazdy filtr, ktery plugin podporuje
(napr. `site_id=1&role_id=3&tag=core`), vytvori samostatnou mapu.

- GET / : webove UI
- POST /maps : ulozeni definice mapy a jeji synchronizace do Zabbixu
- POST /maps/preview : dry-run definice mapy (nic se neulozi ani nezapise do Zabbixu)
- POST /maps/sync : synchronizace jedne ulozene mapy (pole formulare `name`) nebo vsech
- POST /maps/delete : odstraneni ulozene definice mapy (mapa v Zabbixu zustane)
- GET /sync : synchronizace vsech map, vysledek v JSON
- POST /webhook : webhook trigger, synchronizuje vsechny mapy, vysledek v JSON
- GET/POST /cables/<cable_id>/triggers : vyber triggeru linky pro kabel

### Ulozene mapy

Definice map se ukladaji do JSON souboru dle `ZABBIX_MAPS_CONFIG` (vychozi `maps.json` v pracovnim
adresari, v Dockeru `/data/maps.json`):

```json
{"maps": [{"name": "Core", "topology_query": "site_id=1", "ignored_device_roles": ["patchpanel"]}]}
```

Chybejici pole se doplni z vychozich hodnot prostredi. `/sync` a `/webhook` synchronizuji vsechny ulozene
mapy; dokud neni ulozena zadna mapa, synchronizuji jednu mapu definovanou promennymi prostredi.

## Konfigurace

Povinne promenne:

- NETBOX_URL
- NETBOX_TOKEN
- ZABBIX_URL
- ZABBIX_USER a ZABBIX_PASSWORD, nebo ZABBIX_TOKEN

Volitelne promenne (promenne mapy jsou vychozi hodnoty weboveho formulare):

- NETBOX_TOPOLOGY_PATH (vychozi: /api/plugins/netbox_topology_views/xml-export/)
- NETBOX_TOPOLOGY_QUERY (vychozi: show_unconnected=True&show_cables=True&limit=0)
- NETBOX_REQUIRED_TAG
- NETBOX_IGNORED_DEVICE_ROLES (carkou oddelene nazvy/slugs)
- ZABBIX_TOKEN (Bearer token autentizace)
- ZABBIX_MAP_NAME (vychozi: NetBox Topology)
- ZABBIX_MAP_WIDTH (vychozi: 1920)
- ZABBIX_MAP_HEIGHT (vychozi: 1200)
- ZABBIX_LAYOUT_GRID_X (vychozi: 40)
- ZABBIX_LAYOUT_GRID_Y (vychozi: 40)
- ZABBIX_SKIPPED_NODE_MODE (vychozi: skip; jedna z hodnot skip, image)
- ZABBIX_SKIPPED_NODE_ICON_ID (ID ikony pro uzly v rezimu image; vychozi je vestavena ikona hostu)
- ZABBIX_MAPS_CONFIG (vychozi: maps.json; soubor s ulozenymi definicemi map)
- LOG_LEVEL (vychozi: DEBUG)

### Rezim vynechanych uzlu

Uzly topologie bez odpovidajiciho hostu v Zabbixu se ve vychozim nastaveni na mape nezobrazi
(`skip`). Nastavenim `ZABBIX_SKIPPED_NODE_MODE=image` se misto toho vykresli jako obrazkove
prvky s popiskem podle nazvu zarizeni v NetBoxu, volitelne s ikonou dle `ZABBIX_SKIPPED_NODE_ICON_ID`.

### Vynechavani patch panelu

Zarizeni rozpoznana jako patch panely podle nazvu v NetBoxu (napr. obsahujici "patch panel" nebo
token "PP") se z topologie odstrani pouze pokud je `patchpanel` uvedeno v
`NETBOX_IGNORED_DEVICE_ROLES`. Pokud je tato volba zapnuta, kazdy patch panel se z topologie
vyjme a jeho dva kabely se propoji primo mezi zarizenimi na obou stranach, takze mapa zobrazuje
vysledne spojeni misto prostredniho panelu.

## Mapovani triggeru linek

Mapovani triggeru se cte z NetBox cable custom fieldu s nazvem zabbix_triggers.

Priklad hodnoty:

```json
[{"triggers": ["trigger1", "trigger2"]}]
```

Chovani:

- Kazdy nazev triggeru se hleda na obou hostech pripojenych k danemu kabelu.
- Odpovidajici triggery se pridaji jako indikatory linek v Zabbixu.
- Nenalezene triggery jsou reportovane jako unresolved a nezastavi synchronizaci.
