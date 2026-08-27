# NetBox Topology do Zabbix Map Sync

Jazyk:
[![English](https://img.shields.io/badge/English-switch-0a7ea4)](README.md)
[![Cesky](https://img.shields.io/badge/Cesky-active-cf2e2e)](README.cs.md)

Python CLI aplikace a volitelna webhook sluzba, ktera nacita topologii z NetBoxu a vytvori nebo aktualizuje mapu v Zabbixu.

## Funkce

- Import topologie z NetBoxu (vcetne XML exportu z pluginu topology-views).
- Mapovani popisku zarizeni z NetBoxu na hosty v Zabbixu.
- Vytvoreni nebo aktualizace jedne mapy v Zabbixu se uzly a linkami.
- Automaticke rozmisteni uzlu pomoci silove orientovaneho algoritmu Fruchterman-Reingold.
- Podpora dry-run rezimu pro bezpecne otestovani.
- Podpora webhook/manual sync pomoci jednoducheho web serveru.
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

3. Otestujte bez zapisu zmen:

```bash
zbx-map-sync --dry-run
```

4. Provedte synchronizaci:

```bash
zbx-map-sync
```

## Docker

GitHub workflow publikuje image do Docker Hub pod nazvem:

```text
<dockerhub-username>/netbox-topology-zabbix-map
```

Pull a spusteni:

```bash
docker run --rm --env-file .env <dockerhub-username>/netbox-topology-zabbix-map:latest
```

Dry-run v Dockeru:

```bash
docker run --rm --env-file .env <dockerhub-username>/netbox-topology-zabbix-map:latest --dry-run
```

Lokalni build:

```bash
docker build -t netbox-topology-zabbix-map:local .
```

## Web rezim

Spusteni HTTP rezimu:

```bash
zbx-map-sync --serve --host 0.0.0.0 --port 8080
```

Endpointy:

- GET / : jednoducha stranka s odkazem na manualni synchronizaci
- GET /sync : manualni spusteni synchronizace
- POST /webhook : webhook trigger synchronizace

## Konfigurace

Povinne promenne:

- NETBOX_URL
- NETBOX_TOKEN
- ZABBIX_URL
- ZABBIX_USER a ZABBIX_PASSWORD, nebo ZABBIX_TOKEN

Volitelne promenne:

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
