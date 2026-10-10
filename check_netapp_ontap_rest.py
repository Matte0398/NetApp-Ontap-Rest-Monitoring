#!/usr/bin/env python3

########################################################################################
## Description: Script to monitor NetApp Ontap / ASA r2 via REST API
##
## Designed for ASA r2 systems (A20/A30/A50/A70/A90/A1K, ONTAP 9.16+); also works on
## AFF/FAS/ASA systems running ONTAP 9.x. Uses only the Python 3 standard library.
##
## Author: Matteo Z.
########################################################################################

import argparse
import base64
import getpass
import json
import os
import re
import ssl
import stat
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

VERSION = '1.1.0'
OK, WARN, CRIT, UNKNOWN = 0, 1, 2, 3
STATE_NAMES = {0: 'OK', 1: 'WARN', 2: 'CRIT', 3: 'UNKNOWN'}
CHECKS = (
    'system', 'nodes', 'temperature', 'voltage', 'fans', 'psu', 'nvram', 'disks', 'shelves',
    'capacity', 'volumes', 'snapshots', 'luns', 'snapmirror',
    'clusterlinks', 'ports', 'ifgrps', 'interfaces',
    'ems', 'perf',
)
DISCOVERABLE = (
    'nodes', 'disks', 'shelves', 'capacity', 'volumes', 'snapshots', 'luns', 'snapmirror',
    'clusterlinks', 'ports', 'ifgrps', 'interfaces',
)

# REST data sources: name -> (path, query parameters, optional)
# Optional sources may not exist on every system/release (HTTP 400/404 -> None)
SOURCES = {
    'cluster': ('/api/cluster', {'fields': 'name,uuid,version,location,contact'}, False),
    'metric': ('/api/cluster', {'fields': 'name,metric'}, False),
    'nodes': ('/api/cluster/nodes',
              {'fields': 'name,uuid,state,model,serial_number,uptime,ha,controller,nvram'}, False),
    'sensors': ('/api/cluster/sensors',
                {'fields': 'node.name,name,type,value,value_units,threshold_state'}, True),
    'shelves': ('/api/storage/shelves',
                {'fields': 'name,id,model,state,modules,fans,psus,'
                           'temperature_sensors,voltage_sensors,current_sensors'}, True),
    'disks': ('/api/storage/disks',
              {'fields': 'name,state,container_type,node.name,model,serial_number,'
                         'firmware_version,type,class'}, False),
    'zones': ('/api/storage/availability-zones', {'fields': 'name,space'}, True),
    'aggregates': ('/api/storage/aggregates', {'fields': 'name,state,node.name,space.block_storage'}, False),
    'volumes': ('/api/storage/volumes',
                {'fields': 'uuid,name,svm.name,state,space.size,space.used,space.snapshot.used,files.maximum,files.used',
                 'is_constituent': 'false'}, True),
    'luns': ('/api/storage/luns', {'fields': 'name,svm.name,status.state,space.size,space.used'}, True),
    'namespaces': ('/api/storage/namespaces', {'fields': 'name,svm.name,status.state,space.size,space.used'}, True),
    'snapmirror': ('/api/snapmirror/relationships',
                   {'fields': 'source.path,destination.path,healthy,unhealthy_reason,lag_time,state'}, False),
    'eth_ports': ('/api/network/ethernet/ports',
                  {'fields': 'name,node.name,state,enabled,type,speed,lag,'
                             'broadcast_domain.name,broadcast_domain.ipspace.name'}, False),
    'fc_ports': ('/api/network/fc/ports', {'fields': 'name,node.name,state,enabled,wwpn,speed'}, True),
    'ip_lifs': ('/api/network/ip/interfaces',
                {'fields': 'name,svm.name,state,enabled,ip.address,ipspace.name,'
                           'location.is_home,location.node.name,location.port.name'}, False),
    'fc_lifs': ('/api/network/fc/interfaces', {'fields': 'name,svm.name,state,enabled,wwpn'}, True),
}

# Which sources each check needs
CHECK_SOURCES = {
    'system': ('cluster',),
    'nodes': ('nodes',),
    'temperature': ('sensors', 'nodes', 'shelves'),
    'voltage': ('sensors', 'shelves'),
    'fans': ('sensors', 'nodes', 'shelves'),
    'psu': ('nodes', 'shelves'),
    'nvram': ('nodes', 'sensors'),
    'disks': ('disks',),
    'shelves': ('shelves',),
    'capacity': ('zones', 'aggregates'),
    'volumes': ('volumes',),
    'luns': ('luns', 'namespaces'),
    'snapmirror': ('snapmirror',),
    'clusterlinks': ('eth_ports', 'ip_lifs', 'nodes'),
    'ports': ('eth_ports', 'fc_ports'),
    'ifgrps': ('eth_ports',),
    'interfaces': ('ip_lifs', 'fc_lifs'),
    'ems': ('ems',),
    'perf': ('metric',),
    'snapshots': ('snapshots',),
}

NODE_STATES = {
    'up': OK, 'booting': WARN, 'degraded': CRIT, 'taken_over': CRIT,
    'waiting_for_giveback': CRIT, 'down': CRIT, 'unknown': UNKNOWN,
}
TAKEOVER_STATES = {
    'not_attempted': OK, 'not_possible': WARN, 'in_progress': CRIT,
    'in_takeover': CRIT, 'failed': CRIT,
}
GIVEBACK_STATES = {
    'nothing_to_giveback': OK, 'not_attempted': OK, 'in_progress': WARN,
    'failed': CRIT, 'partially_failed': CRIT, 'partial_failed': CRIT,
}
BATTERY_STATES = {
    'battery_ok': OK, 'battery_fully_charged': OK,
    'battery_partially_discharged': WARN, 'battery_near_end_of_life': WARN,
    'battery_over_charged': WARN, 'battery_unknown': UNKNOWN,
    'battery_fully_discharged': CRIT, 'battery_not_present': CRIT, 'battery_at_end_of_life': CRIT,
}
DISK_STATES = {
    'present': OK, 'spare': OK, 'partner': OK, 'zeroing': OK, 'pending': OK,
    'copy': WARN, 'reconstructing': WARN, 'maintenance': WARN, 'unfail': WARN,
    'broken': CRIT, 'removed': CRIT,
}
COMPONENT_STATES = {'ok': OK, 'error': CRIT, 'unknown': OK}
EMS_SEVERITIES = {'emergency': CRIT, 'alert': CRIT, 'error': CRIT}

SERVICE_NAMES = {
    'system': 'NetApp System', 'nodes': 'NetApp Node', 'temperature': 'NetApp Temperature',
    'voltage': 'NetApp Voltage', 'fans': 'NetApp Fans', 'psu': 'NetApp PSU', 'nvram': 'NetApp NVRAM',
    'disks': 'NetApp Disk', 'shelves': 'NetApp Shelf', 'capacity': 'NetApp Capacity',
    'volumes': 'NetApp Volume',
    'luns': 'NetApp LUN', 'snapmirror': 'NetApp SnapMirror', 'clusterlinks': 'NetApp Cluster Link',
    'ports': 'NetApp Port', 'ifgrps': 'NetApp Ifgrp', 'interfaces': 'NetApp LIF',
    'ems': 'NetApp EMS', 'perf': 'NetApp Performance', 'snapshots': 'NetApp Snapshots',
}
ITEM_LABELS = {
    'nodes': 'Node', 'temperature': 'Temperature', 'voltage': 'Voltage', 'fans': 'Fans',
    'psu': 'PSU', 'nvram': 'NVRAM', 'disks': 'Disk', 'shelves': 'Shelf', 'capacity': 'Capacity',
    'volumes': 'Volume',
    'luns': 'LUN', 'snapmirror': 'SnapMirror', 'clusterlinks': 'Cluster link', 'ports': 'Port',
    'ifgrps': 'Ifgrp', 'interfaces': 'LIF', 'ems': 'Event', 'snapshots': 'Snapshots',
}


class MonitorError(Exception):
    pass


class ApiHTTPError(MonitorError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def worst(states):
    s = list(states)

    if CRIT in s:
        return CRIT
    
    if WARN in s:
        return WARN
    
    if UNKNOWN in s:
        return UNKNOWN
    
    return OK


def load_env(path):
    """Read NETAPP_USER / NETAPP_PASSWORD from a .env-style file (mode 0600 or stricter)."""
    p = Path(path).expanduser()

    if not p.is_file():
        raise MonitorError(f'Credentials file not found: {p}')
    
    mode = stat.S_IMODE(p.stat().st_mode)

    if mode & 0o077:
        raise MonitorError(f'Unsafe permissions on {p}: {oct(mode)}; expected 0600 or stricter')
    
    vals = {}

    for n, raw in enumerate(p.read_text(encoding='utf-8-sig').splitlines(), 1):
        line = raw.strip()

        if not line or line.startswith('#'):
            continue

        if line.startswith('export '):
            line = line[7:].strip()

        if '=' not in line:
            raise MonitorError(f'Invalid .env syntax at {p}:{n}')
        
        k, v = line.split('=', 1)
        k, v = k.strip(), v.strip()

        if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
            v = v[1:-1]

        vals[k] = v

    u = vals.get('NETAPP_USER') or vals.get('ONTAP_USER')
    pw = vals.get('NETAPP_PASSWORD') or vals.get('ONTAP_PASSWORD')

    if not u or not pw:
        raise MonitorError(f'NETAPP_USER/NETAPP_PASSWORD missing in {p}')
    
    return u, pw


def _default_store_file():
    omd_root = os.environ.get('OMD_ROOT')

    if not omd_root:
        raise MonitorError('OMD_ROOT is not set: run inside a Checkmk site or use --password-id ID:FILE')
    
    for name in ('passwords_merged', 'stored_passwords'):
        candidate = Path(omd_root, 'var', 'check_mk', name)

        if candidate.exists():
            return candidate
        
    raise MonitorError(f'No Checkmk password store file found in {omd_root}/var/check_mk')


def lookup_checkmk_password(reference):
    """Resolve 'ID' or 'ID:FILE' from the Checkmk password store (Checkmk 2.3+)."""
    pw_id, sep, store = reference.partition(':')
    errors = []
    store_file = Path(store) if sep else None

    try:
        import cmk.utils.password_store as cmk_password_store
    except Exception:
        cmk_password_store = None

    if store_file is None and cmk_password_store is not None:
        store_path = getattr(cmk_password_store, 'password_store_path', None)

        if store_path is not None:
            try:
                store_file = Path(store_path() if callable(store_path) else store_path)
            except Exception:
                store_file = None

    if store_file is None:
        store_file = _default_store_file()

    try:
        from cmk.password_store.v1_unstable import resolve_secret_option

        ns = argparse.Namespace(password=None, password_id=f'{pw_id}:{store_file}')
        return resolve_secret_option(ns, 'password').reveal()
    except Exception as exc:
        errors.append(f'v1_unstable: {exc}')

    if cmk_password_store is not None:
        try:
            return cmk_password_store.lookup(store_file, pw_id)
        except Exception as exc:
            errors.append(f'cmk.utils.password_store: {exc}')
    else:
        errors.append('cmk.utils.password_store not importable')

    raise MonitorError(
        f'Unable to resolve Checkmk password-id {pw_id!r} from {store_file} '
        f'({"; ".join(errors)}). Use --credentials-file outside Checkmk.')


def credentials(args):
    selected = sum(bool(x) for x in (args.credentials_file, args.password_stdin, args.password_id))

    if selected != 1:
        raise MonitorError('Choose exactly one: --credentials-file, --password-stdin or --password-id')
    
    if args.credentials_file:
        user, pw = load_env(args.credentials_file)
        return (args.user or user), pw
    
    if not args.user:
        raise MonitorError('--user is required with --password-stdin / --password-id')
    
    if args.password_stdin:
        if sys.stdin.isatty():
            pw = getpass.getpass('Password: ')
        else:
            pw = sys.stdin.readline().rstrip('\r\n')

        if not pw:
            raise MonitorError('No password received on stdin')
        
        return args.user, pw
    
    return args.user, lookup_checkmk_password(args.password_id)


class Client:
    def __init__(self, host, user, password, port=443, verify=True, timeout=20, debug=False):
        self.base = f'https://{host}:{port}'
        token = base64.b64encode(f'{user}:{password}'.encode()).decode()
        self.headers = {'Authorization': f'Basic {token}', 'Accept': 'application/json'}
        self.timeout = timeout
        self.debug = debug
        self.ctx = ssl.create_default_context() if verify else ssl._create_unverified_context()
        self.cache = {}

    def _dbg(self, msg):
        if self.debug:
            print('DEBUG:', msg, file=sys.stderr)

    def req(self, path_and_query):
        req = urllib.request.Request(self.base + path_and_query, headers=self.headers, method='GET')
        self._dbg(f'GET {path_and_query}')

        try:
            with urllib.request.urlopen(req, context=self.ctx, timeout=self.timeout) as r:
                raw = r.read().decode()
                self._dbg(f'GET {path_and_query.split("?")[0]} -> HTTP {r.status}')
                return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors='replace')

            try:
                detail = json.loads(body).get('error', {}).get('message', '') or body[:300]
            except ValueError:
                detail = body[:300]

            path = path_and_query.split('?')[0]

            if e.code == 401:
                detail = 'authentication failed (check user, password, http application and account lock)'

            raise ApiHTTPError(e.code, f'HTTP {e.code} calling {path}: {detail}') from None
        except urllib.error.URLError as e:
            raise MonitorError(f'Connection error calling {path_and_query.split("?")[0]}: {e.reason}') from None
        except json.JSONDecodeError:
            raise MonitorError(f'Invalid JSON returned by {path_and_query.split("?")[0]}') from None

    def get(self, path, params=None):
        query = ('?' + urllib.parse.urlencode(params, safe=',|*.')) if params else ''
        return self.req(path + query)

    def records(self, path, params=None):
        """Return all records of a collection, following ONTAP pagination (_links.next)."""
        params = dict(params or {})
        params.setdefault('max_records', 1000)
        data = self.get(path, params)

        if not isinstance(data, dict) or not isinstance(data.get('records'), list):
            raise MonitorError(f'{path}: unexpected response, missing "records" list')
        
        out = [x for x in data['records'] if isinstance(x, dict)]
        nxt = (data.get('_links') or {}).get('next', {}).get('href')

        while nxt:
            data = self.req(nxt)
            out.extend(x for x in data.get('records', []) if isinstance(x, dict))
            nxt = (data.get('_links') or {}).get('next', {}).get('href')

        return out

    def fetch(self, source, args):
        """Fetch a named data source once per run. Optional sources return None if unsupported."""
        if source in self.cache:
            return self.cache[source]
        
        if source == 'snapshots':
            value = fetch_snapshots(self, args)

        elif source == 'ems':
            since = (datetime.now(timezone.utc) - timedelta(minutes=args.ems_minutes))
            value = self.records('/api/support/ems/events', {
                'fields': 'index,time,node.name,message.name,message.severity,log_message',
                'message.severity': 'emergency|alert|error',
                'time': '>' + since.strftime('%Y-%m-%dT%H:%M:%S+00:00'),
                'order_by': 'time desc',
                'max_records': 200,
            })
        else:
            path, params, optional = SOURCES[source]

            try:
                if path == '/api/cluster':
                    value = self.get(path, params)
                else:
                    value = self.records(path, params)
            except ApiHTTPError as e:
                if optional and e.code in (400, 404):
                    value = None
                else:
                    raise

        if source == 'aggregates' and args.include_root:
            value = add_root_aggregates(self, value)

        self.cache[source] = value

        return value


def dig(obj, path, default=None):
    """Safely read a nested field, e.g. dig(node, 'ha.takeover.state')."""
    for key in path.split('.'):
        if not isinstance(obj, dict) or key not in obj:
            return default
        
        obj = obj[key]

    return obj


def metric(name, value, uom='', warn=None, crit=None, vmin=None, vmax=None):
    return {'name': name, 'value': value, 'uom': uom, 'warn': warn, 'crit': crit, 'min': vmin, 'max': vmax}


def result(check, item, state, summary, details=None, metrics=None):
    return {'check': check, 'item': item, 'state': state, 'state_name': STATE_NAMES[state],
            'summary': summary, 'details': details, 'metrics': metrics or []}


def guard_missing(obj, fields, context):
    """Return a clear guardrail message for missing required fields, else None."""
    if not isinstance(obj, dict):
        return f'{context}: expected JSON object, got {type(obj).__name__}'
    
    missing = [f for f in fields if dig(obj, f) is None]

    if missing:
        return (f'{context}: missing required field(s): {", ".join(missing)}. '
                f'Cannot evaluate health reliably; verify the REST response for this ONTAP release.')
    
    return None


def _pct(used, total):
    try:
        used, total = float(used), float(total)
        return None if total <= 0 else used / total * 100.0
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _threshold(value, warn, crit):
    if value is None:
        return UNKNOWN
    
    if crit is not None and value >= crit:
        return CRIT
    
    if warn is not None and value >= warn:
        return WARN
    
    return OK


def _fmt_bytes(value):
    try:
        n = float(value)
    except (TypeError, ValueError):
        return 'n/a'
    
    for unit in ('B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB'):
        if abs(n) < 1024 or unit == 'PiB':
            return f'{n:.2f} {unit}' if unit != 'B' else f'{n:.0f} B'
        
        n /= 1024

    return 'n/a'


def _fmt_duration(seconds):
    try:
        s = int(seconds)
    except (TypeError, ValueError):
        return 'n/a'
    
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, _ = divmod(s, 60)

    return f'{d}d {h}h {m}m' if d else f'{h}h {m}m'


def _iso_duration_seconds(text):
    """Convert an ISO 8601 duration (e.g. 'PT1H5M') to seconds."""
    m = re.fullmatch(r'P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?', text or '')

    if not m:
        return None
    
    d, h, mi, s = (float(x) if x else 0 for x in m.groups())

    return d * 86400 + h * 3600 + mi * 60 + s


def _sensor_state(threshold_state):
    ts = (threshold_state or '').lower()

    if 'critical' in ts or ts in ('failed', 'fault'):
        return CRIT
    
    if 'warning' in ts:
        return WARN
    
    return OK


def _is_cluster_port(port):
    return (dig(port, 'broadcast_domain.ipspace.name') == 'Cluster'
            or dig(port, 'broadcast_domain.name') == 'Cluster')


def _no_data(check, data, what):
    return [result(check, None, UNKNOWN, f'{check}: no {what} returned by the REST API', None)]


def _controller_sensor_results(check, sensors, types, label):
    """One result per node, aggregating its controller sensors of the given types."""
    by_node = {}

    for s in sensors:
        if s.get('type') in types:
            by_node.setdefault(dig(s, 'node.name', 'unknown'), []).append(s)

    out = []

    for node, rows in sorted(by_node.items()):
        bad = [s for s in rows if _sensor_state(s.get('threshold_state')) != OK]
        state = worst(_sensor_state(s.get('threshold_state')) for s in rows)
        values = [s.get('value') for s in rows if isinstance(s.get('value'), (int, float))]

        if bad:
            summary = ', '.join(f'{s.get("name")}={s.get("value")} {s.get("value_units") or ""} '
                                f'({s.get("threshold_state")})'.replace(' (', '(').strip() for s in bad)
            summary = f'{len(bad)}/{len(rows)} {label} sensors out of range: {summary}'
        else:
            summary = f'{len(rows)} {label} sensors normal'

        mets = []

        if check == 'temperature' and values:
            summary += f', max={max(values)} {rows[0].get("value_units") or ""}'.rstrip()
            mets.append(metric('temp_max', max(values)))

        out.append(result(check, f'controller {node}', state, summary, {'sensors': rows}, mets))

    return out


def _shelf_component_results(check, shelves, keys, label, value_key=None, metric_name=None, unit=''):
    """One result per shelf, aggregating the component lists named in keys."""
    out = []

    for shelf in shelves or []:
        parts = []

        for key in keys:
            parts += [p for p in (shelf.get(key) or []) if isinstance(p, dict)]

        if not parts:
            continue

        name = shelf.get('name') or shelf.get('id')
        states = [COMPONENT_STATES.get(p.get('state', 'unknown'), WARN) for p in parts]
        bad = [p for p in parts if COMPONENT_STATES.get(p.get('state', 'unknown'), WARN) != OK]
        summary = (f'{len(bad)}/{len(parts)} {label} not ok: '
                   + ', '.join(f'#{p.get("id")}={p.get("state")}' for p in bad)) if bad \
            else f'{len(parts)} {label} ok'
        mets = []

        if value_key:
            values = [dig(p, value_key) for p in parts if isinstance(dig(p, value_key), (int, float))]

            if values:
                summary += f', max={max(values)}{" " + unit if unit else ""}'

                if metric_name:
                    mets.append(metric(metric_name, max(values)))

        out.append(result(check, f'shelf {name}', worst(states), summary, {key: shelf.get(key) for key in keys}, mets))

    return out


def eval_system(data, args):
    c = data['cluster']
    err = guard_missing(c, ('name', 'version.full'), 'system (/api/cluster)')

    if err:
        return [result('system', None, UNKNOWN, err, c if isinstance(c, dict) else None)]
    
    extras = [f'ONTAP {dig(c, "version.full")}']

    if c.get('location'):
        extras.append(f'location={c["location"]}')

    return [result('system', None, OK, f'REST API reachable - cluster {c["name"]} ({", ".join(extras)})', c)]


def eval_nodes(data, args):
    nodes = data['nodes']

    if not nodes:
        return _no_data('nodes', nodes, 'nodes')
    
    out = []

    for idx, n in enumerate(nodes):
        err = guard_missing(n, ('name', 'state'), f'nodes record[{idx}]')

        if err:
            out.append(result('nodes', str(n.get('name') or idx), UNKNOWN, err, n))
            continue

        states = [NODE_STATES.get(n['state'], UNKNOWN)]
        parts = [f'state={n["state"]}', f'model={n.get("model", "n/a")}', f'serial={n.get("serial_number", "n/a")}']

        if dig(n, 'ha.enabled'):
            takeover = dig(n, 'ha.takeover.state', 'n/a')
            giveback = dig(n, 'ha.giveback.state', 'n/a')
            states += [TAKEOVER_STATES.get(takeover, OK), GIVEBACK_STATES.get(giveback, OK)]
            parts += [f'takeover={takeover}', f'giveback={giveback}']
        else:
            parts.append('HA=disabled')

        mets = []

        if n.get('uptime') is not None:
            parts.append(f'uptime={_fmt_duration(n["uptime"])}')
            mets.append(metric('uptime', n['uptime'], 's'))

        out.append(result('nodes', n['name'], worst(states), ', '.join(parts), n, mets))

    return out


def eval_temperature(data, args):
    out = []

    if data['sensors'] is not None:
        out += _controller_sensor_results('temperature', data['sensors'], ('thermal',), 'temperature')
    else:
        # Fallback for releases without /api/cluster/sensors
        for n in data['nodes'] or []:
            over = dig(n, 'controller.over_temperature')
            state = CRIT if over == 'over' else OK
            out.append(result('temperature', f'controller {n.get("name")}', state,
                              f'controller temperature {over or "n/a"}', n.get('controller')))
            
    out += _shelf_component_results('temperature', data['shelves'], ('temperature_sensors',),
                                    'temperature sensors', 'temperature', 'temp_max', 'C')
    
    return out or _no_data('temperature', None, 'temperature sensors')


def eval_voltage(data, args):
    out = []

    if data['sensors'] is not None:
        out += _controller_sensor_results('voltage', data['sensors'], ('voltage', 'current'), 'voltage/current')

    out += _shelf_component_results('voltage', data['shelves'], ('voltage_sensors', 'current_sensors'),
                                    'voltage/current sensors')
    
    return out or _no_data('voltage', None, 'voltage sensors')


def eval_fans(data, args):
    out = []

    if data['sensors'] is not None:
        out += _controller_sensor_results('fans', data['sensors'], ('fan', 'rpm'), 'fan')
    else:
        for n in data['nodes'] or []:
            failed = dig(n, 'controller.failed_fan.count', 0) or 0
            msg = dig(n, 'controller.failed_fan.message.message', '')
            out.append(result('fans', f'controller {n.get("name")}', CRIT if failed else OK,
                              f'{failed} failed fans {msg}'.strip() if failed else 'no failed fans',
                              dig(n, 'controller.failed_fan')))
            
    out += _shelf_component_results('fans', data['shelves'], ('fans',), 'fans', 'rpm', None, 'RPM')

    return out or _no_data('fans', None, 'fans')


def eval_psu(data, args):
    out = []

    for n in data['nodes'] or []:
        failed = dig(n, 'controller.failed_power_supply.count', 0) or 0
        msg = dig(n, 'controller.failed_power_supply.message.message', '')
        out.append(result('psu', f'controller {n.get("name")}', CRIT if failed else OK,
                          f'{failed} failed power supplies {msg}'.strip() if failed else 'no failed power supplies',
                          dig(n, 'controller.failed_power_supply')))
    out += _shelf_component_results('psu', data['shelves'], ('psus',), 'power supplies')

    return out or _no_data('psu', None, 'power supplies')


def eval_nvram(data, args):
    out = []
    sensors_by_node = {}

    for s in data['sensors'] or []:
        if s.get('type') in ('battery_life', 'nvmem'):
            sensors_by_node.setdefault(dig(s, 'node.name'), []).append(s)
            
    for n in data['nodes'] or []:
        name = n.get('name')
        battery = dig(n, 'nvram.battery_state')

        if not battery:
            out.append(result('nvram', name, UNKNOWN, 'NVRAM battery state not available', n.get('nvram')))
            continue

        states = [BATTERY_STATES.get(battery, UNKNOWN)]
        parts = [f'battery={battery}']
        bad = [s for s in sensors_by_node.get(name, []) if _sensor_state(s.get('threshold_state')) != OK]

        for s in bad:
            states.append(_sensor_state(s.get('threshold_state')))
            parts.append(f'{s.get("name")}={s.get("value")} ({s.get("threshold_state")})')

        out.append(result('nvram', name, worst(states), ', '.join(parts), n.get('nvram')))

    return out or _no_data('nvram', None, 'nodes')


def eval_disks(data, args):
    disks = data['disks']

    if not disks:
        return [result('disks', None, WARN, 'No disks returned')]
    
    out = []
    spares = 0

    for idx, d in enumerate(disks):
        err = guard_missing(d, ('name', 'state'), f'disks record[{idx}]')

        if err:
            out.append(result('disks', str(d.get('name') or idx), UNKNOWN, err, d))
            continue

        container = d.get('container_type', 'n/a')
        state = DISK_STATES.get(d['state'], UNKNOWN)

        if container == 'broken':
            state = CRIT
        elif container == 'unassigned':
            state = worst((state, WARN))
        elif container == 'spare':
            spares += 1

        model = ' '.join(x for x in (d.get('model'), d.get('type')) if x) or 'n/a'
        summary = (f'state={d["state"]}, container={container}, node={dig(d, "node.name", "n/a")}, '
                   f'model={model}, SN={d.get("serial_number", "n/a")}, FW={d.get("firmware_version", "n/a")}')
        out.append(result('disks', d['name'], state, summary, d))

    if args.min_spares is not None:
        state = WARN if spares < args.min_spares else OK
        out.append(result('disks', 'spares', state, f'{spares} spare disks (minimum {args.min_spares})',
                          None, [metric('spares', spares, '', args.min_spares)]))
        
    return out


def eval_shelves(data, args):
    # Fans, PSUs and sensors are covered by their own checks; here only the shelf I/O modules.
    shelves = data['shelves']

    if not shelves:
        return [result('shelves', None, OK, 'No shelves returned')]
    
    out = []

    for shelf in shelves:
        name = shelf.get('name') or shelf.get('id') or 'unknown'
        modules = [m for m in (shelf.get('modules') or []) if isinstance(m, dict)]
        states = [COMPONENT_STATES.get(m.get('state', 'unknown'), WARN) for m in modules]
        mods = ', '.join(f'IOM {m.get("id")}={m.get("state", "n/a")}' for m in modules) or 'no module data'
        out.append(result('shelves', name, worst(states) if states else OK,
                          f'model={shelf.get("model", "n/a")}, {mods}', {'modules': modules}))
        
    return out


def _space(obj):
    size = dig(obj, 'space.size') or dig(obj, 'space.block_storage.size')
    used = dig(obj, 'space.used')

    if used is None:
        used = dig(obj, 'space.block_storage.used')

    avail = dig(obj, 'space.available')
    
    if avail is None:
        avail = dig(obj, 'space.block_storage.available')

    if used is None and size is not None and avail is not None:
        used = size - avail

    return size, used


def eval_capacity(data, args):
    source = args.capacity_source

    if source == 'auto':
        source = 'zones' if data.get('zones') else 'aggregates'

    items = data.get(source)
    kind = 'availability zone' if source == 'zones' else 'aggregate'

    if not items:
        return _no_data('capacity', None, source)
    
    out = []

    for idx, it in enumerate(items):
        name = str(it.get('name') or idx)

        if it.get('state') and it['state'] != 'online':
            out.append(result('capacity', name, CRIT, f'{kind} state={it["state"]}', it))
            continue

        size, used = _space(it)
        pct = _pct(used, size)

        if pct is None:
            out.append(result('capacity', name, UNKNOWN,
                              f'{kind}: space fields missing (expected space.size/used or space.block_storage)', it))
            continue

        summary = (f'{kind}, used={_fmt_bytes(used)}/{_fmt_bytes(size)} '
                   f'({pct:.1f}%, warn={args.warn:g}%, crit={args.crit:g}%)')
        out.append(result('capacity', name, _threshold(pct, args.warn, args.crit), summary, it,
                          [metric('used_pct', round(pct, 2), '%', args.warn, args.crit, 0, 100),
                           metric('used', used, 'B', None, None, 0, size)]))
        
    return out


def eval_volumes(data, args):
    volumes = data['volumes']

    if volumes is None:
        return _no_data('volumes', None, 'volumes (endpoint unavailable)')
    
    if not volumes:
        return [result('volumes', None, OK, 'No volumes returned')]

    out = []

    for idx, v in enumerate(volumes):
        name = f'{dig(v, "svm.name", "unknown")}:{v.get("name") or v.get("uuid") or idx}'
        err = guard_missing(v, ('name', 'svm.name', 'state'), f'volumes record[{idx}]')

        if err:
            out.append(result('volumes', name, UNKNOWN, err, v))
            continue

        state = v['state']

        if state != 'online':
            out.append(result('volumes', name, UNKNOWN if state == 'unknown' else CRIT,
                              f'state={state}', v))
            continue

        size, used = dig(v, 'space.size'), dig(v, 'space.used')
        pct = _pct(used, size)

        if pct is None:
            out.append(result('volumes', name, UNKNOWN,
                              'state=online, capacity unavailable (expected space.size > 0 and space.used)', v))
            continue

        parts = [f'state=online, used={_fmt_bytes(used)}/{_fmt_bytes(size)} '
                 f'({pct:.1f}%, warn={args.warn:g}%, crit={args.crit:g}%)']
        mets = [metric('used_pct', round(pct, 2), '%', args.warn, args.crit, 0, 100),
                metric('used', used, 'B', None, None, 0, size)]
        snapshots = dig(v, 'space.snapshot.used')

        if snapshots is not None:
            parts.append(f'snapshots={_fmt_bytes(snapshots)}')
            mets.append(metric('snapshot_used', snapshots, 'B', None, None, 0))

        files_used, files_max = dig(v, 'files.used'), dig(v, 'files.maximum')
        inode_pct = _pct(files_used, files_max)

        if inode_pct is not None:
            parts.append(f'inodes={files_used}/{files_max} ({inode_pct:.1f}%)')
            mets += [metric('inodes_used', files_used, '', None, None, 0, files_max),
                     metric('inodes_used_pct', round(inode_pct, 2), '%', None, None, 0, 100)]
            
        out.append(result('volumes', name, _threshold(pct, args.warn, args.crit), ', '.join(parts), v, mets))

    return out


def eval_luns(data, args):
    # On ASA r2 every storage unit is a LUN or an NVMe namespace in its own volume.
    units = [('LUN', u) for u in (data['luns'] or [])] + [('namespace', u) for u in (data['namespaces'] or [])]

    if data['luns'] is None and data['namespaces'] is None:
        return _no_data('luns', None, 'LUNs or namespaces')
    
    if not units:
        return [result('luns', None, OK, 'No LUNs or NVMe namespaces configured')]
    
    out = []

    for kind, u in units:
        name = u.get('name') or 'unknown'
        status = dig(u, 'status.state', 'unknown')

        if status != 'online':
            out.append(result('luns', name, CRIT, f'{kind}, state={status}', u))
            continue

        size, used = dig(u, 'space.size'), dig(u, 'space.used')
        pct = _pct(used or 0, size)

        if pct is None:
            out.append(result('luns', name, OK, f'{kind}, state=online, size n/a', u))
            continue

        summary = (f'{kind}, svm={dig(u, "svm.name", "n/a")}, state=online, '
                   f'used={_fmt_bytes(used)}/{_fmt_bytes(size)} ({pct:.1f}%)')
        out.append(result('luns', name, _threshold(pct, args.warn, args.crit), summary, u,
                          [metric('used_pct', round(pct, 2), '%', args.warn, args.crit, 0, 100)]))
        
    return out


def eval_snapmirror(data, args):
    rels = data['snapmirror']

    if not rels:
        return [result('snapmirror', None, OK, 'No SnapMirror relationships configured')]
    
    out = []

    for idx, x in enumerate(rels):
        dst = dig(x, 'destination.path') or str(idx)
        parts = [f'source={dig(x, "source.path", "n/a")}', f'state={x.get("state", "n/a")}']
        states = []

        if x.get('healthy') is False:
            reasons = ', '.join(r.get('message', '') for r in (x.get('unhealthy_reason') or []) if isinstance(r, dict))
            states.append(CRIT)
            parts.append(f'unhealthy{": " + reasons if reasons else ""}')
        else:
            parts.append('healthy')

        mets = []
        lag = _iso_duration_seconds(x.get('lag_time'))

        if lag is not None:
            lag_min = lag / 60
            states.append(_threshold(lag_min, args.lag_warn, args.lag_crit))
            parts.append(f'lag={_fmt_duration(lag)}')
            mets.append(metric('lag', round(lag), 's',
                               args.lag_warn * 60 if args.lag_warn else None,
                               args.lag_crit * 60 if args.lag_crit else None, 0))
            
        out.append(result('snapmirror', dst, worst(states) if states else OK, ', '.join(parts), x, mets))

    return out


def eval_clusterlinks(data, args):
    out = []

    for p in data['eth_ports'] or []:
        if not _is_cluster_port(p):
            continue

        item = f'port {dig(p, "node.name")}:{p.get("name")}'
        state = OK if p.get('state') == 'up' else CRIT
        out.append(result('clusterlinks', item, state,
                          f'state={p.get("state", "n/a")}, speed={p.get("speed", "n/a")} Mb/s', p))
        
    for lif in data['ip_lifs'] or []:
        if dig(lif, 'ipspace.name') != 'Cluster':
            continue

        st = lif.get('state', 'n/a')
        state = CRIT if st != 'up' else (WARN if dig(lif, 'location.is_home') is False else OK)
        out.append(result('clusterlinks', f'lif {lif.get("name")}', state,
                          f'state={st}, home={dig(lif, "location.is_home")}, '
                          f'port={dig(lif, "location.node.name")}:{dig(lif, "location.port.name")}', lif))
        
    for n in data['nodes'] or []:
        if not dig(n, 'ha.enabled'):
            continue

        ic = dig(n, 'ha.interconnect.state')

        if ic is None:
            continue

        out.append(result('clusterlinks', f'ha {n.get("name")}', OK if ic == 'up' else CRIT,
                          f'HA interconnect {ic}', dig(n, 'ha.interconnect')))
        
    return out or [result('clusterlinks', None, UNKNOWN, 'No ports or LIFs found in the Cluster IPspace')]


def eval_ports(data, args):
    # Cluster ports -> clusterlinks; LAGs and their members -> ifgrps.
    out = []

    for p in data['eth_ports'] or []:
        if p.get('type') != 'physical' or not p.get('enabled') or not dig(p, 'broadcast_domain.name'):
            continue

        if _is_cluster_port(p):
            continue

        st = p.get('state', 'n/a')
        out.append(result('ports', f'{dig(p, "node.name")}:{p.get("name")}', OK if st == 'up' else CRIT,
                          f'Ethernet, state={st}, speed={p.get("speed", "n/a")} Mb/s, '
                          f'broadcast_domain={dig(p, "broadcast_domain.name")}', p))
        
    for p in data['fc_ports'] or []:
        if not p.get('enabled'):
            continue

        st = p.get('state', 'n/a')
        out.append(result('ports', f'{dig(p, "node.name")}:{p.get("name")}', OK if st == 'online' else CRIT,
                          f'FC, state={st}, speed={dig(p, "speed.negotiated", "n/a")} Gb/s, '
                          f'WWPN={p.get("wwpn", "n/a")}', p))
        
    return out or [result('ports', None, OK, 'No data ports in use')]


def eval_ifgrps(data, args):
    lags = [p for p in (data['eth_ports'] or []) if p.get('type') == 'lag']

    if not lags:
        return [result('ifgrps', None, OK, 'No interface groups configured')]
    
    out = []

    for p in lags:
        item = f'{dig(p, "node.name")}:{p.get("name")}'
        members = [m for m in (dig(p, 'lag.member_ports') or []) if isinstance(m, dict)]
        active = [m for m in (dig(p, 'lag.active_ports') or []) if isinstance(m, dict)]
        inactive = sorted({m.get('name') for m in members} - {a.get('name') for a in active})

        if p.get('state') != 'up':
            state = CRIT
        elif members and inactive:
            state = WARN
        else:
            state = OK

        summary = (f'state={p.get("state", "n/a")}, mode={dig(p, "lag.mode", "n/a")}, '
                   f'active={len(active)}/{len(members)}'
                   + (f', inactive={",".join(inactive)}' if inactive else ''))
        out.append(result('ifgrps', item, state, summary, p,
                          [metric('active_ports', len(active), '', None, None, 0, len(members) or None)]))
        
    return out


def eval_interfaces(data, args):
    # Cluster LIFs are covered by clusterlinks.
    out = []

    for lif in data['ip_lifs'] or []:
        if dig(lif, 'ipspace.name') == 'Cluster' or lif.get('enabled') is False:
            continue

        item = f'{dig(lif, "svm.name") or "cluster"}:{lif.get("name")}'
        st = lif.get('state', 'n/a')
        home = dig(lif, 'location.is_home')
        state = CRIT if st != 'up' else (WARN if home is False else OK)
        out.append(result('interfaces', item, state,
                          f'IP, state={st}, address={dig(lif, "ip.address", "n/a")}, home={home}, '
                          f'port={dig(lif, "location.node.name")}:{dig(lif, "location.port.name")}', lif))
        
    for lif in data['fc_lifs'] or []:
        if lif.get('enabled') is False:
            continue

        st = lif.get('state', 'n/a')
        out.append(result('interfaces', f'{dig(lif, "svm.name")}:{lif.get("name")}', OK if st == 'up' else CRIT,
                          f'FC, state={st}, WWPN={lif.get("wwpn", "n/a")}', lif))
        
    return out or [result('interfaces', None, OK, 'No data LIFs configured')]


def eval_ems(data, args):
    events = data['ems'] or []
    exclude_name = re.compile(args.ems_exclude_name) if args.ems_exclude_name else None
    excluded = 0

    if exclude_name:
        kept = [e for e in events if not exclude_name.search(dig(e, 'message.name') or '')]
        excluded = len(events) - len(kept)
        events = kept

    if not events:
        summary = f'No emergency/alert/error events in the last {args.ems_minutes} minutes'

        if excluded:
            summary = (f'No unexcluded emergency/alert/error events in the last {args.ems_minutes} minutes '
                       f'({excluded} excluded by name)')
            
        return [result('ems', None, OK, summary)]
    
    out = []

    for idx, e in enumerate(events):
        sev = dig(e, 'message.severity', 'error')
        item = f'{dig(e, "node.name", "cluster")}:{e.get("index", idx)}'
        msg = ' '.join(str(e.get('log_message') or 'No description').split())[:300]
        out.append(result('ems', item, EMS_SEVERITIES.get(sev, WARN),
                          f'{sev.upper()} - {dig(e, "message.name", "n/a")}: {msg} (time={e.get("time", "n/a")})', e))
        
    return out


def eval_perf(data, args):
    m = data['metric'].get('metric') if isinstance(data['metric'], dict) else None

    if not m:
        return [result('perf', None, UNKNOWN, 'perf: cluster metrics not available')]
    
    # ONTAP reports latency in microseconds
    lat = (dig(m, 'latency.total', 0) or 0) / 1000.0
    lat_r = (dig(m, 'latency.read', 0) or 0) / 1000.0
    lat_w = (dig(m, 'latency.write', 0) or 0) / 1000.0
    iops = dig(m, 'iops.total', 0) or 0
    tput = dig(m, 'throughput.total', 0) or 0
    summary = (f'latency={lat:.2f} ms (read={lat_r:.2f}, write={lat_w:.2f}), IOPS={iops}, '
               f'throughput={_fmt_bytes(tput)}/s, status={m.get("status", "n/a")}')
    mets = [
        metric('latency', round(lat, 3), 'ms', args.latency_warn, args.latency_crit, 0),
        metric('latency_read', round(lat_r, 3), 'ms'), metric('latency_write', round(lat_w, 3), 'ms'),
        metric('iops', iops), metric('iops_read', dig(m, 'iops.read', 0)), metric('iops_write', dig(m, 'iops.write', 0)),
        metric('throughput', tput, 'B'),
    ]

    return [result('perf', None, _threshold(lat, args.latency_warn, args.latency_crit), summary, m, mets)]


def add_root_aggregates(client, aggregates):
    rows = list(aggregates)
    known = {row.get('uuid') for row in rows}

    refs = client.records(
        '/api/private/cli/storage/aggregate',
        {'fields': 'uuid'}
    )

    if not refs:
        raise MonitorError('CLI passthrough returned no aggregate UUIDs')

    for ref in refs:
        uuid = ref.get('uuid')

        if not uuid:
            raise MonitorError('Aggregate UUID missing in CLI response')

        if uuid in known:
            continue

        row = client.get(
            '/api/storage/aggregates/'
            + urllib.parse.quote(uuid, safe=''),
            {
                'fields':
                    'uuid,name,state,node.name,space.block_storage'
            }
        )

        rows.append(row)
        known.add(uuid)

    return rows


def fetch_snapshots(client, args):
    volumes = client.records(
        '/api/storage/volumes',
        {
            'fields': 'uuid,name,svm.name',
            'is_constituent': 'false',
        }
    )

    keep = item_filter(args)
    rows = []

    for volume in volumes:
        err = guard_missing(
            volume,
            ('uuid', 'name', 'svm.name'),
            'snapshot volume'
        )

        if err:
            raise MonitorError(err)

        item = '{}:{}'.format(
            dig(volume, 'svm.name'),
            volume['name']
        )

        # Apply volume filters before requesting snapshots.
        if not keep({'item': item}):
            continue

        row = {'item': item}

        try:
            path = '/api/storage/volumes/{}/snapshots'.format(
                urllib.parse.quote(volume['uuid'], safe='')
            )

            row['snapshots'] = client.records(
                path,
                {'fields': 'name,create_time'}
            )
        except MonitorError as exc:
            row['error'] = str(exc)

        rows.append(row)

    return rows


def snapshot_time(value):
    text = str(value or '').strip()

    if text.endswith('Z'):
        text = text[:-1] + '+0000'

    text = re.sub(
        r'([+-]\d{2}):(\d{2})$',
        r'\1\2',
        text
    )

    formats = (
        '%Y-%m-%dT%H:%M:%S%z',
        '%Y-%m-%dT%H:%M:%S.%f%z',
        '%Y-%m-%d %H:%M:%S %z',
    )

    for fmt in formats:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass

    raise MonitorError('Invalid snapshot create_time: {!r}'.format(value))


def eval_snapshots(data, args):
    rows = data['snapshots']

    if not rows:
        return [
            result(
                'snapshots', None, UNKNOWN,
                'No volumes selected'
            )
        ]

    pattern = (
        re.compile(args.snapshot_name)
        if args.snapshot_name else None
    )

    now = datetime.now(timezone.utc)
    out = []

    for row in rows:
        item = row['item']

        try:
            if 'error' in row:
                raise MonitorError(row['error'])

            snapshots = row['snapshots']

            for snap in snapshots:
                err = guard_missing(
                    snap,
                    ('name', 'create_time'),
                    item
                )

                if err:
                    raise MonitorError(err)

            selected = [
                snap for snap in snapshots
                if not pattern or pattern.search(snap['name'])
            ]

            if not selected:
                out.append(result(
                    'snapshots',
                    item,
                    CRIT,
                    'No matching snapshots',
                    row,
                    [
                        metric(
                            'snapshot_count', 0,
                            '', None, None, 0
                        )
                    ]
                ))
                continue

            dated = [
                (snapshot_time(snap['create_time']), snap)
                for snap in selected
            ]

            stamp, latest = max(
                dated,
                key=lambda pair: pair[0]
            )

            age = (now - stamp).total_seconds()

            if age < -300:
                raise MonitorError(
                    'Snapshot timestamp is in the future; '
                    'check clocks'
                )

            age = max(0, age)
            warn = args.snapshot_age_warn * 3600
            crit = args.snapshot_age_crit * 3600

            out.append(result(
                'snapshots',
                item,
                _threshold(age, warn, crit),
                'count={}, latest={}, age={}'.format(
                    len(selected),
                    latest['name'],
                    _fmt_duration(age)
                ),
                row,
                [
                    metric(
                        'snapshot_count', len(selected),
                        '', None, None, 0
                    ),
                    metric(
                        'snapshot_age', round(age),
                        's', warn, crit, 0
                    ),
                ]
            ))
        except MonitorError as exc:
            out.append(result(
                'snapshots',
                item,
                UNKNOWN,
                str(exc),
                row
            ))

    return out


EVAL = {
    'system': eval_system, 'nodes': eval_nodes, 'temperature': eval_temperature, 'voltage': eval_voltage,
    'fans': eval_fans, 'psu': eval_psu, 'nvram': eval_nvram, 'disks': eval_disks, 'shelves': eval_shelves,
    'capacity': eval_capacity, 'volumes': eval_volumes, 'luns': eval_luns, 'snapmirror': eval_snapmirror,
    'clusterlinks': eval_clusterlinks, 'ports': eval_ports, 'ifgrps': eval_ifgrps, 'interfaces': eval_interfaces,
    'ems': eval_ems, 'perf': eval_perf, 'snapshots': eval_snapshots,
}


def item_filter(args):
    inc = re.compile(args.include) if args.include else None
    exc = re.compile(args.exclude) if args.exclude else None

    def keep(r):
        if r['item'] is None:
            return True
        
        if inc and not inc.search(r['item']):
            return False
        
        if exc and exc.search(r['item']):
            return False
        
        return True
    
    return keep


def collect(c, checks, cont, args):
    raw = {}
    res = []
    keep = item_filter(args)

    for ch in checks:
        try:
            if ch == 'capacity':
                # Fetch only the selected source. Auto falls back only when zones are absent.
                data = {}

                if args.capacity_source in ('auto', 'zones'):
                    data['zones'] = c.fetch('zones', args)

                if args.capacity_source == 'aggregates' or (args.capacity_source == 'auto' and not data['zones']):
                    data['aggregates'] = c.fetch('aggregates', args)
            else:
                data = {src: c.fetch(src, args) for src in CHECK_SOURCES[ch]}
                
            raw[ch] = data
            selected = [
                r for r in EVAL[ch](data, args)
                if keep(r)
            ]

            if not selected and (args.include or args.exclude):
                selected = [
                    result(
                        ch,
                        None,
                        UNKNOWN,
                        'No objects match the requested filters'
                    )
                ]

            res += selected
        except Exception as e:
            res.append(result(ch, None, UNKNOWN, str(e)))

            if not cont:
                break

    return raw, res


def svc(r):
    base = SERVICE_NAMES[r['check']]
    return f'{base} {r["item"]}' if r['item'] else base


def _short_value(r):
    if r['item'] is None:
        return r['summary']
    
    return f'{ITEM_LABELS.get(r["check"], r["check"])} "{r["item"]}", {r["summary"]}'


def _num(v):
    if v is None:
        return ''
    
    if isinstance(v, float):
        return f'{v:.3f}'.rstrip('0').rstrip('.')
    
    return str(v)


def _metric_name(name):
    return re.sub(r'[^A-Za-z0-9_.-]', '_', str(name))


def perfdata(res):
    out = []

    for r in res:
        for m in r['metrics']:
            label = f'{r["item"]}_{m["name"]}' if r['item'] else m['name']
            out.append(f"'{label}'={_num(m['value'])}{m['uom']};{_num(m['warn'])};{_num(m['crit'])};"
                       f"{_num(m['min'])};{_num(m['max'])}")
            
    return ' '.join(out)


def out_text(res, allmode):
    if allmode:
        grouped = {}

        for r in res:
            grouped.setdefault(r['check'], []).append(r)

        for idx, (check, rows) in enumerate(grouped.items()):
            if idx:
                print()

            st = worst(r['state'] for r in rows)
            print(f'[{check.upper()}] {STATE_NAMES[st]}')

            for r in rows:
                print(f'- {STATE_NAMES[r["state"]]}: {_short_value(r)}')

        print()
        print(f'Overall: {STATE_NAMES[worst(r["state"] for r in res)]}')
        return
    
    overall = worst(r['state'] for r in res)
    counts = {s: sum(r['state'] == s for r in res) for s in (OK, WARN, CRIT, UNKNOWN)}
    # Problems first, so they are visible even when the line gets truncated
    ordered = sorted(res, key=lambda r: {CRIT: 0, UNKNOWN: 1, WARN: 2, OK: 3}[r['state']])
    details = ' - '.join(_short_value(r) for r in ordered)
    perf = perfdata(res)
    print(f'{STATE_NAMES[overall]} - objects={len(res)}, ok={counts[OK]}, warn={counts[WARN]}, '
          f'crit={counts[CRIT]}, unknown={counts[UNKNOWN]}'
          + (f' - {details}' if details else '') + (f' | {perf}' if perf else ''))


def out_checkmk(res):
    print('<<<local>>>')

    for r in res:
        name = svc(r).replace('\\', '\\\\').replace('"', '\\"')
        summary = r['summary'].replace('\n', ' ').replace('\r', ' ')
        mets = '|'.join(f'{_metric_name(m["name"])}={_num(m["value"])};{_num(m["warn"])};{_num(m["crit"])};'
                        f'{_num(m["min"])};{_num(m["max"])}' for m in r['metrics']) or '-'
        print(f'{r["state"]} "{name}" {mets} {summary}')


def parser():
    p = argparse.ArgumentParser(description='NetApp ONTAP / ASA r2 REST API monitor')
    p.add_argument('--version', action='version', version=f'%(prog)s {VERSION}')
    p.add_argument('--host', required=True, help='cluster management IP or hostname')
    p.add_argument('--port', type=int, default=443)
    p.add_argument('--timeout', type=int, default=20)
    p.add_argument('--no-cert-check', action='store_true', help='do not verify the TLS certificate')
    p.add_argument('--debug', action='store_true')
    p.add_argument('--user')
    p.add_argument('--credentials-file', help='.env file with NETAPP_USER / NETAPP_PASSWORD (mode 0600)')
    p.add_argument('--password-stdin', action='store_true', help='read the password from stdin')
    p.add_argument('--password-id', metavar='ID[:FILE]',
                   help='ID of the password in the Checkmk password store (FILE defaults to the site store)')
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument('--check', choices=(*CHECKS, 'all'))
    g.add_argument('--discover', choices=DISCOVERABLE)
    p.add_argument('--capacity-source', choices=('auto', 'zones', 'aggregates'), default='auto',
                   help='capacity/discovery source: auto prefers zones, then aggregates (default: auto)')
    p.add_argument('--warn', type=float, default=80.0, metavar='PCT',
                   help='WARN threshold percentage for capacity, volumes and LUN checks (default: 80)')
    p.add_argument('--crit', type=float, default=90.0, metavar='PCT',
                   help='CRIT threshold percentage for capacity, volumes and LUN checks (default: 90)')
    p.add_argument('--latency-warn', type=float, metavar='MS', help='WARN threshold for cluster latency (perf)')
    p.add_argument('--latency-crit', type=float, metavar='MS', help='CRIT threshold for cluster latency (perf)')
    p.add_argument('--lag-warn', type=float, metavar='MIN', help='WARN threshold for SnapMirror lag in minutes')
    p.add_argument('--lag-crit', type=float, metavar='MIN', help='CRIT threshold for SnapMirror lag in minutes')
    p.add_argument('--min-spares', type=int, metavar='N', help='WARN if fewer spare disks are available')
    p.add_argument('--ems-minutes', type=int, default=60, metavar='N',
                   help='Look back N minutes for emergency/alert/error EMS events (default: 60)')
    p.add_argument('--ems-exclude-name', metavar='REGEX',
                   help='exclude EMS events whose message.name matches (not the node:index or log text)')
    p.add_argument('--include', metavar='REGEX', help='only report items whose name matches')
    p.add_argument('--exclude', metavar='REGEX', help='skip items whose name matches')
    p.add_argument('--output', choices=('nagios', 'json', 'checkmk', 'text'), default='nagios',
                   help='Output format; text is a legacy alias of nagios')
    p.add_argument('--include-raw', action='store_true', help='json output: include the raw REST data')
    p.add_argument('--include-root', action='store_true', help='include aggregates discovered through CLI passthrough')
    p.add_argument('--snapshot-age-warn', type=int, default=24, metavar='HOURS', help='WARN when the latest matching snapshot reaches this age')
    p.add_argument('--snapshot-age-crit', type=int, default=48, metavar='HOURS', help='CRIT when the latest matching snapshot reaches this age')
    p.add_argument('--snapshot-name', metavar='REGEX', help='only evaluate snapshots whose name matches')

    return p


def validate(a):
    if a.include_root and a.capacity_source != 'aggregates':
        return '--include-root requires --capacity-source aggregates'

    if not (0 <= a.snapshot_age_warn < a.snapshot_age_crit):
        return 'snapshot thresholds must satisfy 0 <= WARN < CRIT (hours)'
    
    if not (0 <= a.warn < a.crit <= 100):
        return 'thresholds must satisfy 0 <= WARN < CRIT <= 100'
    
    if a.ems_minutes < 1:
        return '--ems-minutes must be >= 1'
    
    for low, high, name in ((a.latency_warn, a.latency_crit, 'latency'), (a.lag_warn, a.lag_crit, 'lag')):
        if low is not None and high is not None and low >= high:
            return f'--{name}-warn must be lower than --{name}-crit'
        
    for regex in (a.include, a.exclude, a.ems_exclude_name, a.snapshot_name):
        if regex:
            try:
                re.compile(regex)
            except re.error as e:
                return f'invalid regular expression {regex!r}: {e}'
            
    return None


def main():
    a = parser().parse_args()
    err = validate(a)

    if err:
        print(f'UNKNOWN - {err}')
        return UNKNOWN
    try:
        u, pw = credentials(a)
        c = Client(a.host, u, pw, a.port, not a.no_cert_check, a.timeout, a.debug)

        if a.discover:
            raw, res = collect(c, [a.discover], False, a)

            if any(r['state'] == UNKNOWN and r['item'] is None for r in res):
                out_text(res, False)
                return UNKNOWN
            
            disc = [{'item': r['item'], 'data': r['details']} for r in res if r['item'] is not None]

            if a.output == 'json':
                print(json.dumps(disc, indent=2, sort_keys=True, default=str))
            else:
                for entry in disc:
                    print(entry['item'])

            return OK

        checks = list(CHECKS) if a.check == 'all' else [a.check]
        raw, res = collect(c, checks, a.check == 'all', a)

        if a.output == 'json':
            payload = {'host': a.host, 'overall_state': STATE_NAMES[worst(r['state'] for r in res)], 'results': res}

            if a.include_raw:
                payload['raw'] = raw

            print(json.dumps(payload, indent=2, sort_keys=True, default=str))
        elif a.output == 'checkmk':
            out_checkmk(res)
            return OK
        else:
            out_text(res, a.check == 'all')

        return worst(r['state'] for r in res)
    except MonitorError as e:
        if a.output == 'json':
            print(json.dumps({'overall_state': 'UNKNOWN', 'error': str(e)}, separators=(',', ':')))
            return UNKNOWN
        
        if a.output == 'checkmk':
            print(f'<<<local>>>\n3 "NetApp Collector" - {str(e).replace(chr(10), " ")}')
            return OK
        
        print('UNKNOWN -', e)
        return UNKNOWN


if __name__ == '__main__':
    sys.exit(main())