# NetApp ONTAP REST Monitoring

`check_netapp_ontap_rest.py` monitors NetApp ONTAP through its REST API. It provides Nagios-compatible checks, Checkmk local-check output and JSON results, using only the Python standard library.

The script is designed for ASA r2 and also includes data sources for AFF, FAS and ASA environments. Available checks depend on the endpoints and fields exposed by the target system. It only sends `GET` requests and does not modify cluster configuration.

## Quick start

Requirements:

- Python 3.6 or later on the monitoring server.
- HTTPS access to the cluster management endpoint (default port `443`).
- A dedicated monitoring account with read access to the required REST resources and an `http` application login.

Run commands from the project directory. Replace every `<PLACEHOLDER>` with your own setting; placeholders are quoted in shell commands. Numeric defaults and status codes describe the plugin, rather than a specific environment.

```sh
chmod 755 check_netapp_ontap_rest.py
cp examples/credentials.env.example '<CREDENTIALS_FILE_PATH>'
chmod 600 '<CREDENTIALS_FILE_PATH>'
```

Edit the copied file:

```dotenv
NETAPP_USER=<MONITORING_USER>
NETAPP_PASSWORD=<MONITORING_PASSWORD>
```

Run a single check:

```sh
./check_netapp_ontap_rest.py \
  --host '<CLUSTER_HOST_OR_IP>' \
  --credentials-file '<CREDENTIALS_FILE_PATH>' \
  --check nodes
```

For a complete collection, use `--check all`. This includes snapshot freshness checks and may report `CRIT` for volumes without matching snapshots.

TLS certificate verification is enabled by default. Ensure the certificate issuer is trusted by the monitoring server; add `--no-cert-check` when verification must be disabled for testing.

## Repository layout and example outputs

```text
NetApp-Ontap-Rest-Monitoring/
|-- check_netapp_ontap_rest.py
|-- README.md
|-- LICENSE
|-- .gitignore
`-- examples/
    |-- credentials.env.example
    |-- nagios/
    |   |-- commands.cfg
    |   `-- services.cfg
    `-- output/
        |-- check_all.txt
        |-- check_all_checkmk.txt
        |-- check_ems_exclude.txt
        |-- check_perf.json
        |-- check_volumes.txt
        `-- discover_capacity_aggregates.txt
```

These are the six files currently available under `examples/output`:

| File | Contents |
| --- | --- |
| [check_all.txt](examples/output/check_all.txt) | Grouped report covering multiple check types and an overall state. |
| [check_all_checkmk.txt](examples/output/check_all_checkmk.txt) | Collection rendered as a `<<<local>>>` section with individual services and metrics. |
| [check_ems_exclude.txt](examples/output/check_ems_exclude.txt) | EMS name exclusion returning `OK` when all relevant events are excluded. |
| [check_perf.json](examples/output/check_perf.json) | Structured cluster performance results and metrics. |
| [check_volumes.txt](examples/output/check_volumes.txt) | Volume usage warning, snapshot/inode metrics and Nagios performance data. |
| [discover_capacity_aggregates.txt](examples/output/discover_capacity_aggregates.txt) | Aggregate names returned by capacity discovery. |

The retained files are illustrative samples. Their identifiers and commands must be adapted to your environment, and their coverage may predate checks added to the current script. The check list below describes the current implementation. This README uses placeholders for environment-specific names, addresses, paths, credentials and output values.

## Credentials

Select exactly one credential source:

| Method | Arguments | Notes |
| --- | --- | --- |
| Credentials file | `--credentials-file '<CREDENTIALS_FILE_PATH>'` | Reads username and password from a `.env`-style file. |
| Standard input | `--user '<MONITORING_USER>' --password-stdin` | Prompts without echo on an interactive terminal; otherwise reads one line from stdin. |
| Checkmk Password Store | `--user '<MONITORING_USER>' --password-id '<PASSWORD_STORE_ID>'` | Requires Checkmk libraries and access to the site password store. |

The credentials file must have no group or other-user permissions (`0600` or stricter) and be readable by the account running the plugin. `ONTAP_USER` and `ONTAP_PASSWORD` are also accepted. `--user` overrides the file username.

Optional surrounding quotes are removed from file values. Password characters are read literally, without shell expansion. UTF-8 BOM and Windows line endings are handled by the loader.

Interactive example:

```sh
./check_netapp_ontap_rest.py \
  --host '<CLUSTER_HOST_OR_IP>' \
  --user '<MONITORING_USER>' \
  --password-stdin --check system
```

The Password Store reference can include an explicit file: `--password-id '<PASSWORD_STORE_ID>:<PASSWORD_STORE_FILE_PATH>'`. Otherwise the plugin resolves the current site store through Checkmk libraries or `OMD_ROOT`. Run in the site's Python environment.

## Checks

Select one check with `--check '<CHECK_NAME>'`, or all twenty with `--check all`. API data sources are reused where possible; a failed check is reported as `UNKNOWN`, and a complete collection continues with the remaining checks.

| Check | Purpose / item format |
| --- | --- |
| `system` | REST reachability, cluster name and ONTAP version; summary result. |
| `nodes` | Node state, HA takeover/giveback and uptime; `<NODE_NAME>`. |
| `temperature` | Controller and shelf temperature sensors; `controller <NODE_NAME>` / `shelf <SHELF_ID>`. |
| `voltage` | Controller and shelf voltage/current sensors. |
| `fans` | Controller and shelf fan health. |
| `psu` | Controller and shelf power supply health. |
| `nvram` | NVRAM battery state and sensors. |
| `disks` | Disk state, assignment and optional minimum spare count; `<DISK_ID>` / `spares`. |
| `shelves` | Shelf I/O module health; `<SHELF_ID>`. |
| `capacity` | Availability-zone or aggregate occupancy; `<ZONE_NAME>` / `<AGGREGATE_NAME>`. |
| `volumes` | Volume state, occupancy, snapshot bytes and inode metrics; `<SVM_NAME>:<VOLUME_NAME>`. |
| `snapshots` | Matching snapshot count and latest snapshot age per volume; `<SVM_NAME>:<VOLUME_NAME>`. |
| `luns` | LUN and NVMe namespace state and occupancy; `<STORAGE_UNIT_PATH>`. |
| `snapmirror` | Replication relationship health and optional lag thresholds; destination path. |
| `clusterlinks` | Cluster ports/LIFs and HA interconnect. |
| `ports` | Physical enabled Ethernet ports in a broadcast domain and enabled FC ports; `<NODE_NAME>:<PORT_NAME>`. |
| `ifgrps` | Interface groups and active members; `<NODE_NAME>:<IFGRP_NAME>`. |
| `interfaces` | Data/management IP and FC LIFs, state and home placement; `<SVM_NAME>:<LIF_NAME>`. |
| `ems` | Emergency, alert and error events in the selected time window; `<NODE_NAME>:<EVENT_INDEX>`. |
| `perf` | Cluster latency, IOPS and throughput; summary result. |

Hardware checks divide responsibilities: shelf fans are evaluated by `fans`, while shelf I/O modules are evaluated by `shelves`. Cluster network objects belong to `clusterlinks`. Capacity alerts can overlap between aggregates, volumes and storage units.

Controller sensor data comes from `/api/cluster/sensors`; some evaluators fall back to node summary fields when sensor data is unavailable. Optional endpoint failures with HTTP `400` or `404` can be skipped, but a check that lacks necessary evaluation data can still produce `UNKNOWN`.

## Thresholds

| Checks | Options | Defaults / units |
| --- | --- | --- |
| `capacity`, `volumes`, `luns` | `--warn`, `--crit` | `80`, `90` percent used. |
| `snapshots` | `--snapshot-age-warn`, `--snapshot-age-crit` | `24`, `48` whole hours. |
| `perf` | `--latency-warn`, `--latency-crit` | Unset; milliseconds. |
| `snapmirror` | `--lag-warn`, `--lag-crit` | Unset; minutes. |
| `disks` | `--min-spares` | Unset; minimum spare count, producing `WARN` below the configured number. |

Space thresholds must satisfy `0 <= warn < crit <= 100`. Snapshot thresholds must satisfy `0 <= warn < crit`. When both latency or lag thresholds are supplied, warning must be lower than critical. Threshold equality triggers the corresponding state. Object health can produce an alert independently of usage thresholds.

## Capacity sources and root aggregates

`--capacity-source` applies to checks and discovery:

- `auto` (default): queries availability zones first and falls back to aggregates when no zones are returned.
- `zones`: explicitly selects availability zones.
- `aggregates`: explicitly selects aggregates, even when zones exist.

Discover names before selecting an aggregate:

```sh
./check_netapp_ontap_rest.py \
  --host '<CLUSTER_HOST_OR_IP>' \
  --credentials-file '<CREDENTIALS_FILE_PATH>' \
  --discover capacity --capacity-source aggregates
```

Monitor the discovered item with its own thresholds:

```sh
./check_netapp_ontap_rest.py \
  --host '<CLUSTER_HOST_OR_IP>' \
  --credentials-file '<CREDENTIALS_FILE_PATH>' \
  --check capacity --capacity-source aggregates \
  --include '^<AGGREGATE_NAME_REGEX>$' --warn 85 --crit 95
```

`--include-root` requires `--capacity-source aggregates`. It obtains aggregate UUIDs through `GET /api/private/cli/storage/aggregate?fields=uuid`, then reads `/api/storage/aggregates/<AGGREGATE_UUID>` for entries missing from the ordinary collection. It requires platform support and read permissions for CLI passthrough. A name filter alone does not add root aggregates to the collection.

```sh
./check_netapp_ontap_rest.py \
  --host '<CLUSTER_HOST_OR_IP>' \
  --credentials-file '<CREDENTIALS_FILE_PATH>' \
  --check capacity --capacity-source aggregates --include-root \
  --include '^<ROOT_AGGREGATE_NAME_REGEX>$'
```

## Volumes and snapshots

`volumes` evaluates known non-online states and space usage. Occupancy is `space.used / space.size * 100`, representing ONTAP volume accounting rather than guest filesystem free space. Snapshot bytes and inode usage are additional metrics without separate alert thresholds. FlexGroup constituents are excluded. An empty volume collection is `OK`; unavailable required data is `UNKNOWN`.

`snapshots` reads `/api/storage/volumes/<VOLUME_UUID>/snapshots` for each selected volume and uses `name` and `create_time` to find the newest matching snapshot.

```sh
./check_netapp_ontap_rest.py \
  --host '<CLUSTER_HOST_OR_IP>' \
  --credentials-file '<CREDENTIALS_FILE_PATH>' \
  --check snapshots \
  --include '^<SVM_NAME_REGEX>:<VOLUME_NAME_REGEX>$' \
  --snapshot-name '<SNAPSHOT_NAME_REGEX>' \
  --snapshot-age-warn 24 --snapshot-age-crit 48
```

Volume filters apply before snapshot requests. Snapshot-name matching is case-sensitive; without `--snapshot-name`, all names are considered.

| Condition | State |
| --- | --- |
| Latest matching snapshot below warning age | `OK` |
| Latest matching snapshot at warning age but below critical age | `WARN` |
| Latest matching snapshot at critical age, or no matching snapshots | `CRIT` |
| No volumes selected, request error, missing fields or invalid timestamp | `UNKNOWN` |

A snapshot timestamp more than five minutes in the future produces `UNKNOWN`; smaller future offsets are treated as age zero. Metrics include `snapshot_count` and `snapshot_age` in seconds. This check measures freshness, not restore validity or policy compliance.

`--check all` includes snapshot requests for selected volumes. Use separate services when volumes require different snapshot policies. Snapshot discovery lists volume items and still collects snapshot data.

## Item filters and EMS exclusions

`--include` and `--exclude` are case-sensitive regular expressions applied to item names. Matches are partial unless anchored with `^` and `$`. Summary results without an item remain visible. Escape regex metacharacters in actual names when an exact match is intended.

```sh
# Select one volume
--check volumes --include '^<SVM_NAME_REGEX>:<VOLUME_NAME_REGEX>$'

# Exclude a deliberately unused port
--check ports --exclude '^<NODE_NAME_REGEX>:<PORT_NAME_REGEX>$'

# Select controller sensors
--check temperature --include '^controller '
```

If an item filter removes every result, the check returns `UNKNOWN` with `No objects match the requested filters`.

EMS collects `emergency`, `alert` and `error` events in the last `--ems-minutes` minutes (default `60`, minimum `1`), reporting unexcluded events as `CRIT`. All pages in the window are read before filtering.

`--ems-exclude-name` matches the event's `message.name`, rather than the message text or `node:index` item. No names are excluded by default. If all relevant events are excluded by name, the check returns `OK` with the excluded count. Raw JSON retains excluded events.

```sh
./check_netapp_ontap_rest.py \
  --host '<CLUSTER_HOST_OR_IP>' \
  --credentials-file '<CREDENTIALS_FILE_PATH>' \
  --check ems --ems-minutes 60 \
  --ems-exclude-name '^<EMS_EVENT_NAME_REGEX>$'
```

Choose a lookback window that covers the collection interval and expected scheduling delays; overlapping windows can report the same event more than once.

## Discovery

`--discover` is mutually exclusive with `--check`. Supported checks are `nodes`, `disks`, `shelves`, `capacity`, `volumes`, `snapshots`, `luns`, `snapmirror`, `clusterlinks`, `ports`, `ifgrps` and `interfaces`.

```sh
./check_netapp_ontap_rest.py \
  --host '<CLUSTER_HOST_OR_IP>' \
  --credentials-file '<CREDENTIALS_FILE_PATH>' \
  --discover volumes
```

Default output lists item names, one per line. `--output json` returns an array with `item` and `data` fields. Capacity source selection and item filters also apply to discovery. Successful discovery exits `0` independently of individual object health; a summary-level `UNKNOWN` prevents discovery and returns `3`.

## Output formats and exit codes

| Format | Contents |
| --- | --- |
| `nagios` (default) | Single-check counters, object summaries with problems first, and performance data. `all` produces a grouped report. |
| `text` | Legacy alias for `nagios`. |
| `json` | `host`, `overall_state` and `results`; each result includes state, summary, details and metrics. |
| `checkmk` | `<<<local>>>` section with individual object services and available metrics. |

`--include-raw` adds the collected REST data to JSON check output. For capacity, only queried sources appear. Result details may already contain environment identifiers without this flag.

```sh
./check_netapp_ontap_rest.py \
  --host '<CLUSTER_HOST_OR_IP>' \
  --credentials-file '<CREDENTIALS_FILE_PATH>' \
  --check perf --output json --include-raw
```

Output template (placeholders represent runtime values):

```text
<<<local>>>
0 "NetApp System" - REST API reachable - cluster <CLUSTER_NAME> (ONTAP <ONTAP_VERSION>, location=<LOCATION>)
2 "NetApp Port <NODE_NAME>:<PORT_NAME>" - Ethernet, state=down, speed=<SPEED_MBPS> Mb/s, broadcast_domain=<BROADCAST_DOMAIN>
```

Nagios/text and JSON checks return `0` (`OK`), `1` (`WARN`), `2` (`CRIT`) or `3` (`UNKNOWN`). Aggregate priority is **CRIT > WARN > UNKNOWN > OK**, rather than numeric order.

Checkmk output normally exits `0`, carrying state in service lines. Handled startup errors produce an `UNKNOWN` `NetApp Collector` service. Argument parsing or validation errors can return a nonzero code before local-check output is generated.

## Nagios integration

Install the script in the monitoring plugin directory:

```sh
install -m 755 check_netapp_ontap_rest.py '<PLUGIN_DIRECTORY>/check_netapp_ontap_rest.py'
```

Reference templates are available in [commands.cfg](examples/nagios/commands.cfg) and [services.cfg](examples/nagios/services.cfg). Adapt their host names, credentials paths and TLS options before use.

Command template:

```text
define command{
    command_name    check_netapp_ontap_rest
    command_line    $USER1$/check_netapp_ontap_rest.py --host "$HOSTADDRESS$" --credentials-file "$ARG1$" --check $ARG2$ $ARG3$
}
```

Service template:

```text
define service{
    use                     <SERVICE_TEMPLATE>
    host_name               <MONITORED_HOST_NAME>
    service_description     NetApp Capacity
    check_command           check_netapp_ontap_rest!<CREDENTIALS_FILE_PATH>!capacity!--capacity-source aggregates --warn 80 --crit 90
}
```

`$ARG3$` supplies optional flags. Nagios uses `!` as an argument separator and `$` for macros; a literal regex end anchor in configuration must be written as `$$`. Use separate services for object groups that need different thresholds. Test execution as the monitoring account before activating configuration.

## Checkmk integration

Install under the site's custom plugin directory:

```sh
install -m 755 check_netapp_ontap_rest.py '/omd/sites/<SITE>/local/lib/nagios/plugins/check_netapp_ontap_rest.py'
```

For an active check, execute one check in Nagios format. For multiple object services, configure a data source program that consumes Checkmk output:

```sh
'/omd/sites/<SITE>/local/lib/nagios/plugins/check_netapp_ontap_rest.py' \
  --host '<CLUSTER_HOST_OR_IP>' \
  --user '<MONITORING_USER>' \
  --password-id '<PASSWORD_STORE_ID>' \
  --check all --output checkmk
```

Run service discovery after configuring the data source. Execute in the site environment for Password Store access. In distributed monitoring, install the script and provide credentials on the site that runs the checks. The script does not create monitoring rules automatically.

## CLI reference

| Option | Default / purpose |
| --- | --- |
| `--host` | Required cluster hostname or IP, without URL scheme or API path. |
| `--port` / `--timeout` | `443` / `20` seconds per request. |
| `--check` / `--discover` | Select exactly one operation. |
| `--credentials-file` / `--password-stdin` / `--password-id` | Select exactly one credential source. |
| `--user` | Required for stdin and Password Store; overrides file username. |
| `--capacity-source` / `--include-root` | `auto`, `zones`, `aggregates` / root inclusion, off by default. |
| `--warn` / `--crit` | Space thresholds, `80` / `90` percent. |
| `--latency-warn` / `--latency-crit` | Optional performance thresholds in milliseconds. |
| `--lag-warn` / `--lag-crit` | Optional SnapMirror lag thresholds in minutes. |
| `--min-spares` | Optional minimum spare count. |
| `--snapshot-age-warn` / `--snapshot-age-crit` | `24` / `48` hours, integer arguments. |
| `--snapshot-name` | Optional snapshot-name regex. |
| `--ems-minutes` / `--ems-exclude-name` | `60` minutes / optional event-name regex. |
| `--include` / `--exclude` | Optional item-name regex filters. |
| `--output` / `--include-raw` | `nagios` / add collected payload to JSON, off by default. |
| `--no-cert-check` | Disable TLS verification, off by default. |
| `--debug` | Write REST request diagnostics to stderr. |
| `--help` / `--version` | Display usage / plugin version. |

## Troubleshooting

Start with `./check_netapp_ontap_rest.py --help`, then test one check. Add `--debug` and `--output json --include-raw` to inspect requests and data. Review and anonymize results before sharing them.

| Symptom | What to verify |
| --- | --- |
| HTTP `401` | Credentials, account status and an `http` application login. |
| Unsafe credentials-file permissions | Remove group/other permissions with `chmod 600`. |
| Invalid `.env` syntax | Use `KEY=value` lines with both username and password. |
| Certificate verification failure | Certificate validity, hostname and issuer trust; use `--no-cert-check` only when needed. |
| Missing endpoint or required fields | Platform support, account permissions and returned schema. |
| No objects match the requested filters | Discover exact names and check regex escaping and capacity source. |
| `--include-root` rejected | Select `--capacity-source aggregates`. |
| Root aggregate collection fails | CLI passthrough support and permissions for the aggregate command. |
| No matching snapshots | Selected volumes and snapshot-name filter; this condition is `CRIT`. |
| `snapshots` is not an accepted choice | Verify that the current script is deployed. |

A direct reachability/authentication test can prompt for the password without putting it in the command:

```sh
curl --silent --show-error \
  --user '<MONITORING_USER>' \
  'https://<CLUSTER_HOST_OR_IP>:<HTTPS_PORT>/api/cluster?fields=name'
```

## License

Released under the MIT License. See [LICENSE](LICENSE).
