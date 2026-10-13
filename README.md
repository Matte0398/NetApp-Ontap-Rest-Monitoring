# NetApp ONTAP REST Monitoring

[check_netapp_ontap_rest.py](check_netapp_ontap_rest.py) monitors NetApp ONTAP through its REST API. It provides Nagios-compatible checks, Checkmk local-check output and JSON results, using only the Python standard library.

The script is designed for ASA r2 and also includes data sources for AFF, FAS and ASA environments. Available checks depend on the endpoints and fields exposed by the target system. It only sends GET requests and does not modify cluster configuration.

## Contents

- [Features](#features)
- [Repository structure](#repository-structure)
- [Quick start](#quick-start)
- [Compatibility and limitations](#compatibility-and-limitations)
- [Example outputs](#example-outputs)
- [Credentials](#credentials)
- [Checks](#checks)
- [API requests and execution](#api-requests-and-execution)
- [REST API endpoints](#rest-api-endpoints)
- [Thresholds](#thresholds)
- [Capacity sources and root aggregates](#capacity-sources-and-root-aggregates)
- [Volumes and snapshots](#volumes-and-snapshots)
- [Item filters and EMS exclusions](#item-filters-and-ems-exclusions)
- [Discovery](#discovery)
- [Output formats and exit codes](#output-formats-and-exit-codes)
- [Nagios integration](#nagios-integration)
- [Checkmk integration](#checkmk-integration)
- [CLI reference](#cli-reference)
- [Troubleshooting](#troubleshooting)
- [License](#license)

## Features

- Checks covering cluster/node health, hardware, capacity, volumes, snapshots, storage units, replication, networking, EMS events and cluster performance.
- Availability-zone monitoring and separate selection of data aggregates, root aggregates or both. Aggregate type is identified by UUID, independently of its name.
- Configurable warning/critical thresholds for space usage, snapshot age, replication lag and cluster latency, plus a minimum spare-disk count.
- Item discovery and regex filters for names, snapshots and EMS event exclusions.
- Nagios-compatible status and performance data, Checkmk local-check services, and JSON output with optional raw REST responses. Output format does not change the selected checks.
- Credential files, interactive/standard-input passwords and Checkmk Password Store integration. TLS certificate verification is enabled by default.
- Python standard-library implementation with GET-only REST access; platform-specific endpoint availability determines which checks can run.

## Repository structure

```text
NetApp-Ontap-Rest-Monitoring/
|-- examples/
|   |-- nagios/
|   |-- output/
|   `-- credentials.env.example
|-- .gitignore
|-- check_netapp_ontap_rest.py
|-- LICENSE
`-- README.md
```

## Quick start

Requirements:

- Python 3.6 or later on the monitoring server.
- HTTPS access to the cluster management endpoint (default port 443).
- A dedicated monitoring account with read access to the required REST resources and an http application login.

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

For a complete collection, use `--check all`.

`--check all` selects all check types; it does not select every capacity source. To include both data and root aggregates, add `--capacity-source aggregates --aggregate-type all`. Without these options, capacity defaults to availability zones when present.

TLS certificate verification is enabled by default. Ensure the certificate issuer is trusted by the monitoring server; add `--no-cert-check` when verification must be disabled for testing.

## Compatibility and limitations

The intended platforms are ASA r2, AFF, FAS and ASA. Available REST resources, returned fields and account permissions vary by platform and ONTAP release; the platform list does not imply that every check has been verified on every model.

All aggregate modes (data, root and all) require system_aggregate.uuid from `/api/cluster/nodes` for every returned node. This field is documented from ONTAP 9.14 onward; its availability on the target platform must still be checked. The requirement also applies when `--capacity-source auto` falls back to aggregates. Missing node references cause capacity to return UNKNOWN, including in data mode, because root exclusion depends on those UUIDs.

Aggregate selection requires read access to `/api/cluster/nodes`, to `/api/storage/aggregates` for data and all, and to individual `/api/storage/aggregates/<AGGREGATE_UUID>` resources for root retrieval.

## Example outputs

These are the files currently available under examples/output:

| File                                                                                 | Contents                                                                             |
| ------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------ |
| [check_all.txt](examples/output/check_all.txt)                                       | Grouped report covering multiple check types and an overall state.                   |
| [check_all_checkmk.txt](examples/output/check_all_checkmk.txt)                       | Collection rendered as a `<<<local>>>` section with individual services and metrics. |
| [check_ems_exclude.txt](examples/output/check_ems_exclude.txt)                       | EMS name exclusion returning OK when all relevant events are excluded.               |
| [check_perf.json](examples/output/check_perf.json)                                   | Structured cluster performance results and metrics.                                  |
| [check_volumes.txt](examples/output/check_volumes.txt)                               | Volume usage warning, snapshot/inode metrics and Nagios performance data.            |
| [discover_capacity_aggregates.txt](examples/output/discover_capacity_aggregates.txt) | Aggregate names returned by capacity discovery.                                      |

The retained files are illustrative samples. Their identifiers and commands must be adapted to your environment.

## Credentials

Select exactly one credential source:

| Method                 | Arguments                                                      | Notes                                                                                 |
| ---------------------- | -------------------------------------------------------------- | ------------------------------------------------------------------------------------- |
| Credentials file       | --credentials-file '<CREDENTIALS_FILE_PATH>'                   | Reads username and password from a .env-style file.                                   |
| Standard input         | --user '<MONITORING_USER>' --password-stdin                    | Prompts without echo on an interactive terminal; otherwise reads one line from stdin. |
| Checkmk Password Store | --user '<MONITORING_USER>' --password-id '<PASSWORD_STORE_ID>' | Requires Checkmk libraries and access to the site password store.                     |

The credentials file must have no group or other-user permissions (0600 or stricter) and be readable by the account running the plugin. ONTAP_USER and ONTAP_PASSWORD are also accepted. `--user` overrides the file username.

Optional surrounding quotes are removed from file values. Password characters are read literally, without shell expansion.

Interactive example:

```sh
./check_netapp_ontap_rest.py \
  --host '<CLUSTER_HOST_OR_IP>' \
  --user '<MONITORING_USER>' \
  --password-stdin --check system
```

The Password Store reference can include an explicit file: `--password-id '<PASSWORD_STORE_ID>:<PASSWORD_STORE_FILE_PATH>'`. Otherwise the plugin resolves the current site store through Checkmk libraries or OMD_ROOT. Run in the site's Python environment.

## Checks

Select one check with `--check '<CHECK_NAME>'`, or all checks with `--check all`. API data sources are reused where possible; a failed check is reported as UNKNOWN, and a complete collection continues with the remaining checks.

| Check        | Purpose / item format                                                                                  |
| ------------ | ------------------------------------------------------------------------------------------------------ |
| system       | REST reachability, cluster name and ONTAP version; summary result.                                     |
| nodes        | Node state, HA takeover/giveback and uptime; `<NODE_NAME>`.                                            |
| temperature  | Controller and shelf temperature sensors; `controller <NODE_NAME>` / `shelf <SHELF_ID>`.               |
| voltage      | Controller and shelf voltage/current sensors.                                                          |
| fans         | Controller and shelf fan health.                                                                       |
| psu          | Controller and shelf power supply health.                                                              |
| nvram        | NVRAM battery state and sensors.                                                                       |
| disks        | Disk state, assignment and optional minimum spare count; `<DISK_ID>` / spares.                         |
| shelves      | Shelf I/O module health; `<SHELF_ID>`.                                                                 |
| capacity     | Availability-zone or aggregate occupancy; `<ZONE_NAME>` / `<AGGREGATE_NAME>`.                          |
| volumes      | Volume state, occupancy, snapshot bytes and inode metrics; `<SVM_NAME>:<VOLUME_NAME>`.                 |
| snapshots    | Matching snapshot count and latest snapshot age per volume; `<SVM_NAME>:<VOLUME_NAME>`.                |
| luns         | LUN and NVMe namespace state and occupancy; `<STORAGE_UNIT_PATH>`.                                     |
| snapmirror   | Replication relationship health and optional lag thresholds; destination path.                         |
| clusterlinks | Cluster ports/LIFs and HA interconnect.                                                                |
| ports        | Physical enabled Ethernet ports in a broadcast domain and enabled FC ports; `<NODE_NAME>:<PORT_NAME>`. |
| ifgrps       | Interface groups and active members; `<NODE_NAME>:<IFGRP_NAME>`.                                       |
| interfaces   | Data/management IP and FC LIFs, state and home placement; `<SVM_NAME>:<LIF_NAME>`.                     |
| ems          | Emergency, alert and error events in the selected time window; `<NODE_NAME>:<EVENT_INDEX>`.            |
| perf         | Cluster latency, IOPS and throughput; summary result.                                                  |

Hardware checks divide responsibilities: shelf fans are evaluated by fans, while shelf I/O modules are evaluated by shelves. Cluster network objects belong to clusterlinks. Capacity alerts can overlap between aggregates, volumes and storage units.

Controller sensor data comes from /api/cluster/sensors; some evaluators fall back to node summary fields when sensor data is unavailable. Optional endpoint failures with HTTP 400 or 404 can be skipped, but a check that lacks necessary evaluation data can still produce UNKNOWN.

## API requests and execution

`--check all` executes multiple HTTP GET requests sequentially. It does not combine all checks into one API request. A single execution caches named data sources so that checks can share node, sensor, shelf and network collections. The cache is discarded when the process exits; separate invocations collect their own data.

Some reads of the same endpoint remain separate because they request different fields: cluster information and performance, node health and system-aggregate references, and volume usage and the volume inventory used by snapshot monitoring.

Request count also depends on the selected objects:

- Snapshot monitoring reads the volume inventory and then the snapshot collection of each volume selected by the item filters.
- Aggregate modes root and all retrieve each required root aggregate missing from the collected aggregates through its UUID.
- Collection reads follow pagination links, issuing an additional request for each subsequent page.

`--timeout` (default 20 seconds) is passed to each HTTP request; it is not an overall execution deadline. Total runtime depends on request count and response times and can exceed this setting. Allow for that runtime in the monitoring scheduler's timeout and interval. The output format, including Checkmk, does not change collection behavior.

Use `--debug` to inspect DEBUG: GET ... messages on stderr and see the requests actually sent.

## REST API endpoints

All requests use GET. The table lists endpoint paths without query strings; {uuid} represents the UUID of the requested aggregate or volume.

| Endpoint                              | Checks / use                                                                                                 |
| ------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| /api/cluster                          | system and perf, using separate requests with different fields.                                              |
| /api/cluster/nodes                    | nodes, temperature, fans, psu, nvram and clusterlinks; capacity also reads node system-aggregate references. |
| /api/cluster/sensors                  | temperature, voltage, fans and nvram.                                                                        |
| /api/storage/shelves                  | shelves, temperature, voltage, fans and psu.                                                                 |
| /api/storage/disks                    | disks.                                                                                                       |
| /api/storage/availability-zones       | capacity when the source is zones or auto.                                                                   |
| /api/storage/aggregates               | capacity when collecting data aggregates or all aggregates.                                                  |
| /api/storage/aggregates/{uuid}        | capacity when retrieving individual root aggregates.                                                         |
| /api/storage/volumes                  | volumes and the volume inventory used by snapshots.                                                          |
| /api/storage/volumes/{uuid}/snapshots | snapshots, for each selected volume.                                                                         |
| /api/storage/luns                     | luns.                                                                                                        |
| /api/storage/namespaces               | luns, for NVMe namespaces.                                                                                   |
| /api/snapmirror/relationships         | snapmirror.                                                                                                  |
| /api/network/ethernet/ports           | ports, ifgrps and clusterlinks.                                                                              |
| /api/network/fc/ports                 | ports.                                                                                                       |
| /api/network/ip/interfaces            | interfaces and clusterlinks.                                                                                 |
| /api/network/fc/interfaces            | interfaces.                                                                                                  |
| /api/support/ems/events               | ems.                                                                                                         |

Use `--debug` to see the actual paths and query strings sent to the cluster. Endpoint availability and read permissions must match the selected checks; see [Compatibility and limitations](#compatibility-and-limitations).

## Thresholds

| Checks                  | Options                                  | Defaults / units                                                        |
| ----------------------- | ---------------------------------------- | ----------------------------------------------------------------------- |
| capacity, volumes, luns | --warn, --crit                           | 80, 90 percent used.                                                    |
| snapshots               | --snapshot-age-warn, --snapshot-age-crit | 24, 48 whole hours.                                                     |
| perf                    | --latency-warn, --latency-crit           | Unset; milliseconds.                                                    |
| snapmirror              | --lag-warn, --lag-crit                   | Unset; minutes.                                                         |
| disks                   | --min-spares                             | Unset; minimum spare count, producing WARN below the configured number. |

Space thresholds must satisfy `0 <= warn < crit <= 100`. Snapshot thresholds must satisfy `0 <= warn < crit`. When both latency or lag thresholds are supplied, warning must be lower than critical. Threshold equality triggers the corresponding state. Object health can produce an alert independently of usage thresholds.

With `--check all`, the same `--warn` and `--crit` values apply to capacity, volumes and LUN/NVMe namespace usage. They also apply to every selected aggregate, including roots. Run separate checks/services when those groups require different space thresholds; there are no per-object threshold overrides within one invocation. Snapshot-age, latency and replication-lag options remain independent of space thresholds.

## Capacity sources and root aggregates

`--capacity-source` applies to checks and discovery:

- auto (default): queries availability zones first and falls back to aggregates when no zones are returned.
- zones: explicitly selects availability zones.
- aggregates: explicitly selects aggregates, even when zones exist.

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

`--aggregate-type` selects aggregates by their UUIDs, independently of their names:

| Value          | Selection                                                                       |
| -------------- | ------------------------------------------------------------------------------- |
| data (default) | Aggregates from the ordinary collection, excluding node system-aggregate UUIDs. |
| root           | Only node system aggregates, retrieved directly by UUID.                        |
| all            | Both collections, deduplicated by UUID.                                         |

When explicitly supplied, this option requires `--capacity-source aggregates` and `--check capacity`, `--check all` or `--discover capacity`. Without an explicit type, aggregate collection defaults to data; `--capacity-source auto` still prefers availability zones.

Discover only root aggregates:

```sh
./check_netapp_ontap_rest.py \
  --host 'EXAMPLE_HOST' \
  --credentials-file '<CREDENTIALS_FILE_PATH>' \
  --discover capacity --capacity-source aggregates --aggregate-type root
```

Monitor root aggregates with their own thresholds:

```sh
./check_netapp_ontap_rest.py \
  --host '<CLUSTER_HOST_OR_IP>' \
  --credentials-file '<CREDENTIALS_FILE_PATH>' \
  --check capacity --capacity-source aggregates --aggregate-type root \
  --warn 80 --crit 90
```

Use `--aggregate-type data` for non-root aggregates or `--aggregate-type all` for both. The existing `--include` and `--exclude` regex options filter item names after type selection; an aggregate's name does not determine its type.

## Volumes and snapshots

The volumes check evaluates known non-online states and space usage. Occupancy is `space.used / space.size * 100`, representing ONTAP volume accounting rather than guest filesystem free space.

Monitor one volume with custom space thresholds:

```sh
./check_netapp_ontap_rest.py \
  --host 'EXAMPLE_HOST' \
  --credentials-file '<CREDENTIALS_FILE_PATH>' \
  --check volumes \
  --include '^<SVM_NAME_REGEX>:<VOLUME_NAME_REGEX>$' \
  --warn 85 --crit 95
```

Use `--discover volumes` to obtain the item names before constructing the regex. Omit `--include` to monitor all returned volumes.

The snapshots check reads `/api/storage/volumes/<VOLUME_UUID>/snapshots` for each selected volume and uses name and create_time to find the newest matching snapshot.

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

| Condition                                                               | State   |
| ----------------------------------------------------------------------- | ------- |
| Latest matching snapshot below warning age                              | OK      |
| Latest matching snapshot at warning age but below critical age          | WARN    |
| Latest matching snapshot at critical age, or no matching snapshots      | CRIT    |
| No volumes selected, request error, missing fields or invalid timestamp | UNKNOWN |

`--check all` includes snapshot requests for selected volumes. Use separate services when volumes require different snapshot policies. Snapshot discovery lists volume items and still collects snapshot data.

## Item filters and EMS exclusions

`--include` and `--exclude` are case-sensitive regular expressions applied to item names. Matches are partial unless anchored with `^` and `$`. Summary results without an item remain visible. Escape regex metacharacters in actual names when an exact match is intended.

With `--check all`, these filters apply to every check's item names. Use separate invocations when only aggregates, volumes or another check should be filtered.

```sh
# Select one volume
--check volumes --include '^<SVM_NAME_REGEX>:<VOLUME_NAME_REGEX>$'

# Exclude a deliberately unused port
--check ports --exclude '^<NODE_NAME_REGEX>:<PORT_NAME_REGEX>$'

# Select controller sensors
--check temperature --include '^controller '
```

If an item filter removes every result, the check returns UNKNOWN with No objects match the requested filters.

EMS collects emergency, alert and error events in the last `--ems-minutes` minutes (default 60, minimum 1), reporting unexcluded events as CRIT. All pages in the window are read before filtering.

`--ems-exclude-name` matches the event's message.name, rather than the message text or node:index item. No names are excluded by default. If all relevant events are excluded by name, the check returns OK with the excluded count. Raw JSON retains excluded events.

```sh
./check_netapp_ontap_rest.py \
  --host '<CLUSTER_HOST_OR_IP>' \
  --credentials-file '<CREDENTIALS_FILE_PATH>' \
  --check ems --ems-minutes 60 \
  --ems-exclude-name '^<EMS_EVENT_NAME_REGEX>$'
```

Choose a lookback window that covers the collection interval and expected scheduling delays; overlapping windows can report the same event more than once.

## Discovery

`--discover` is mutually exclusive with `--check`. Supported checks are nodes, disks, shelves, capacity, volumes, snapshots, luns, snapmirror, clusterlinks, ports, ifgrps and interfaces.

```sh
./check_netapp_ontap_rest.py \
  --host '<CLUSTER_HOST_OR_IP>' \
  --credentials-file '<CREDENTIALS_FILE_PATH>' \
  --discover volumes
```

Default output lists item names, one per line. `--output json` returns an array with item and data fields. Capacity source selection and item filters also apply to discovery. Successful discovery exits 0 independently of individual object health; a summary-level UNKNOWN prevents discovery and returns 3.

## Output formats and exit codes

| Format           | Contents                                                                                                          |
| ---------------- | ----------------------------------------------------------------------------------------------------------------- |
| nagios (default) | Single-check counters, object summaries with problems first, and performance data. all produces a grouped report. |
| text             | Legacy alias for nagios.                                                                                          |
| json             | host, overall_state and results; each result includes state, summary, details and metrics.                        |
| checkmk          | `<<<local>>>` section with individual object services and available metrics.                                      |

`--include-raw` adds the collected REST data to JSON check output. For capacity, only queried sources appear. Result details may already contain environment identifiers without this flag.

```sh
./check_netapp_ontap_rest.py \
  --host '<CLUSTER_HOST_OR_IP>' \
  --credentials-file '<CREDENTIALS_FILE_PATH>' \
  --check perf --output json --include-raw
```

Nagios/text and JSON checks return 0 (OK), 1 (WARN), 2 (CRIT) or 3 (UNKNOWN). Overall status priority is CRIT > WARN > UNKNOWN > OK, rather than numeric order.

Checkmk output normally exits 0, carrying state in service lines. Handled startup errors produce an UNKNOWN NetApp Collector service. Argument parsing or validation errors can return a nonzero code before local-check output is generated.

## Nagios integration

Copy the script in the monitoring plugin directory:

```sh
cp check_netapp_ontap_rest.py '<PLUGIN_DIRECTORY>/check_netapp_ontap_rest.py'
```

Reference templates are available in [commands.cfg](examples/nagios/commands.cfg) and [services.cfg](examples/nagios/services.cfg). Adapt their host names, credentials paths and TLS options before use.

Command template:

```text
define command{
    command_name    check_netapp_ontap_rest
    command_line    $USER1$/check_netapp_ontap_rest.py --host $HOSTADDRESS$ --credentials-file $ARG1$ --no-cert-check --check $ARG2$ $ARG3$
}
```

Service template:

```text
define service{
    use                     <SERVICE_TEMPLATE>
    host_name               <MONITORED_HOST_NAME>
    service_description     NetApp Capacity
    servicegroups           netapp-storage
    check_command           check_netapp_ontap_rest!<CREDENTIALS_FILE_PATH>!capacity!--capacity-source aggregates --warn 80 --crit 90
}
```

`$ARG3$` in the command-line supplies optional flags.

## Checkmk integration

Copy the script under the site's custom plugin directory:

```sh
cp check_netapp_ontap_rest.py '/omd/sites/<SITE>/local/lib/nagios/plugins/check_netapp_ontap_rest.py'
```

```sh
'/omd/sites/<SITE>/local/lib/nagios/plugins/check_netapp_ontap_rest.py' \
  --host '<CLUSTER_HOST_OR_IP>' \
  --user '<MONITORING_USER>' \
  --password-id '<PASSWORD_STORE_ID>' \
  --check all \
  --capacity-source aggregates --aggregate-type all \
  --output checkmk
```

`--output checkmk` changes formatting only: each selected aggregate produces a `NetApp Capacity <AGGREGATE_NAME>` service with its state and space metrics. Use `--aggregate-type root` or `--aggregate-type data` to restrict the aggregate selection. To collect only capacity services, replace `--check all` with `--check capacity`.

Execute in the site environment for Password Store access. In distributed monitoring, copy the script and provide credentials on the site that runs the checks. The script does not create monitoring rules automatically.

Repeat Checkmk service discovery after changing the capacity source or aggregate type, and review newly discovered or vanished services before accepting the changes.

## CLI reference

| Option                                                | Default / purpose                                                                       |
| ----------------------------------------------------- | --------------------------------------------------------------------------------------- |
| --host                                                | Required cluster hostname or IP, without URL scheme or API path.                        |
| --port / --timeout                                    | 443 / 20 seconds per request.                                                           |
| --check / --discover                                  | Select exactly one operation.                                                           |
| --credentials-file / --password-stdin / --password-id | Select exactly one credential source.                                                   |
| --user                                                | Required for stdin and Password Store; overrides file username.                         |
| --capacity-source                                     | auto (default), zones or aggregates.                                                    |
| --aggregate-type                                      | data (default), root or all; explicit use requires aggregate capacity checks/discovery. |
| --warn / --crit                                       | Space thresholds, 80 / 90 percent.                                                      |
| --latency-warn / --latency-crit                       | Optional performance thresholds in milliseconds.                                        |
| --lag-warn / --lag-crit                               | Optional SnapMirror lag thresholds in minutes.                                          |
| --min-spares                                          | Optional minimum spare count.                                                           |
| --snapshot-age-warn / --snapshot-age-crit             | 24 / 48 hours, integer arguments.                                                       |
| --snapshot-name                                       | Optional snapshot-name regex.                                                           |
| --ems-minutes / --ems-exclude-name                    | 60 minutes / optional event-name regex.                                                 |
| --include / --exclude                                 | Optional item-name regex filters.                                                       |
| --output / --include-raw                              | nagios / add collected payload to JSON, off by default.                                 |
| --no-cert-check                                       | Disable TLS verification, off by default.                                               |
| --debug                                               | Write REST request diagnostics to stderr.                                               |
| --help / --version                                    | Display usage / plugin version.                                                         |

## Troubleshooting

Start with `./check_netapp_ontap_rest.py --help`, then test one check. Add `--debug` and `--output json --include-raw` to inspect requests and data. Review and anonymize results before sharing them.

| Symptom                                                  | What to verify                                                                                                                              |
| -------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| HTTP 401                                                 | Credentials, account status and an http application login.                                                                                  |
| HTTP 403                                                 | Check the monitoring account's read permissions for the failing endpoint, including node and aggregate resources when selecting aggregates. |
| Unsafe credentials-file permissions                      | Remove group/other permissions with chmod 600.                                                                                              |
| Invalid .env syntax                                      | Use KEY=value lines with both username and password.                                                                                        |
| Certificate verification failure                         | Certificate validity, hostname and issuer trust; use `--no-cert-check` only when needed.                                                    |
| Missing endpoint or required fields                      | Platform support, account permissions and returned schema.                                                                                  |
| No objects match the requested filters                   | Discover exact names and check regex escaping and capacity source.                                                                          |
| Checkmk shows an availability zone instead of aggregates | Add `--capacity-source aggregates --aggregate-type all` to the data source command, then repeat service discovery.                          |
| Checkmk output contains only item names                  | Use `--check capacity` or `--check all`; `--discover` lists inventory items.                                                                |
| `--include-root` rejected                                | The option was removed; replace it with `--aggregate-type all`.                                                                             |
| Aggregate selection rejected                             | Use `--capacity-source aggregates` with `--check capacity/all` or `--discover capacity`.                                                    |
| Aggregate collection fails                               | Verify node system_aggregate.uuid fields (ONTAP 9.14+) and read access to the required node/aggregate endpoints.                            |
| No matching snapshots                                    | Selected volumes and snapshot-name filter; this condition is CRIT.                                                                          |
| snapshots is not an accepted choice                      | Verify that the current script is deployed.                                                                                                 |

A direct reachability/authentication test can prompt for the password without putting it in the command:

```sh
curl --silent --show-error \
  --user '<MONITORING_USER>' \
  'https://<CLUSTER_HOST_OR_IP>:<HTTPS_PORT>/api/cluster?fields=name'
```

## License

Released under the MIT License. See [LICENSE](LICENSE).
