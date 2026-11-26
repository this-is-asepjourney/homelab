import ipaddress
import os
import platform
import socket
import struct
import time
import psutil
from typing import Dict, Any, List, Optional, Tuple
from apps.agent.app.docker.client import docker_manager

_last_net_totals: Optional[Tuple[int, int, int, int]] = None
_last_net_time: Optional[float] = None
_last_pernic_bytes: Dict[str, Tuple[int, int]] = {}
_last_pernic_time: Optional[float] = None
_last_disk_io = None
_last_disk_time: Optional[float] = None

# Accurate service directory for Homelab & standard Linux ports
KNOWN_SERVICES: Dict[int, str] = {
    22: "SSH Server (OpenSSH)",
    53: "CoreDNS / Local DNS (.homelab)",
    80: "Homelab Nginx Ingress Router",
    443: "HTTPS Ingress / TLS",
    1883: "Mosquitto MQTT Broker (IoT)",
    3000: "Grafana Observability",
    3001: "Uptime Kuma Monitor",
    3005: "PSM Web Console",
    3306: "MySQL 8.4 Database",
    5432: "PostgreSQL 16 Database",
    6379: "Redis 7 Cache",
    8000: "PAP Core API Gateway",
    8001: "PSM Control Plane API",
    8080: "Nextcloud Collaboration",
    8081: "cAdvisor Container Metrics",
    9000: "MinIO / Silo S3 API",
    9001: "MinIO Internal Console",
    9002: "MinIO S3 Web Console",
    9090: "Prometheus TSDB",
    9100: "Node Exporter (Host Metrics)",
    9273: "Telegraf MQTT Exporter",
}

_TCP_STATES: Dict[str, str] = {
    "01": "ESTABLISHED",
    "02": "SYN_SENT",
    "03": "SYN_RECV",
    "04": "FIN_WAIT1",
    "05": "FIN_WAIT2",
    "06": "TIME_WAIT",
    "07": "CLOSE",
    "08": "CLOSE_WAIT",
    "09": "LAST_ACK",
    "0A": "LISTEN",
    "0B": "CLOSING",
}

_TAILSCALE_NET = ipaddress.ip_network("100.64.0.0/10")
_DOCKER_NET = ipaddress.ip_network("172.16.0.0/12")


def get_system_info() -> Dict[str, Any]:
    uname = platform.uname()
    return {
        "hostname": uname.node,
        "os_name": uname.system,
        "os_version": uname.release,
        "architecture": uname.machine,
        "processor": uname.processor,
        "python_version": platform.python_version(),
    }


def _classify_interface_type(iface_name: str) -> str:
    lower_name = iface_name.lower()
    if lower_name == "lo" or "loopback" in lower_name:
        return "loopback"
    if "tailscale" in lower_name or lower_name.startswith(("ts", "tun", "wg", "utun")):
        return "vpn"
    if "docker" in lower_name or lower_name.startswith(("br-", "veth", "cni")):
        return "docker"
    if lower_name.startswith(("wl", "wlan", "wifi")):
        return "wireless"
    return "ethernet"


def classify_ip_zone(ip_str: str) -> str:
    """Classify an IPv4/IPv6 address into Homelab network zones for clear flow visibility."""
    if not ip_str or ip_str in ("0.0.0.0", "::", "*", ""):
        return "any"
    clean = ip_str.split("%")[0].strip()
    if clean.startswith("::ffff:"):
        clean = clean[7:]
    try:
        ip_obj = ipaddress.ip_address(clean)
        if ip_obj.is_loopback:
            return "loopback"
        if isinstance(ip_obj, ipaddress.IPv4Address):
            if ip_obj in _TAILSCALE_NET:
                return "tailscale"
            if ip_obj in _DOCKER_NET:
                return "docker"
            if ip_obj.is_private:
                return "lan"
        elif ip_obj.is_private or ip_obj.is_link_local:
            return "lan"
        return "internet"
    except ValueError:
        return "unknown"


def _resolve_host_proc_net_dir() -> Optional[str]:
    """
    Locate host PID 1 `/proc/1/net` when running on Linux with `pid: host` (or explicit mount)
    so containerized `psm-agent` inspects the real Ubuntu host network namespace in <1ms.
    """
    if platform.system() != "Linux":
        return None
    for candidate in (
        os.getenv("HOST_PROC_NET_DIR", ""),
        "/host/proc/1/net",
        "/proc/1/net",
        "/proc/net",
    ):
        if candidate and os.path.isfile(os.path.join(candidate, "dev")):
            return candidate
    return None


def _read_sys_net_value(sys_dir: str, iface: str, attr: str, default: str = "") -> str:
    path = os.path.join(sys_dir, iface, attr)
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read().strip()
    except Exception:
        return default


def _parse_proc_fib_ipv4(proc_net_dir: str) -> Dict[str, Tuple[str, str]]:
    """
    Extract primary IPv4 address per interface from `/proc/1/net/fib_trie` or `route`
    when `psutil.net_if_addrs()` only sees the container's own network namespace.
    """
    result: Dict[str, Tuple[str, str]] = {}
    # 1. Read interface -> subnet mask hints from /proc/1/net/route
    iface_Subnets: List[Tuple[str, int, int]] = []
    route_path = os.path.join(proc_net_dir, "route")
    try:
        with open(route_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.read().splitlines()[1:]
        for ln in lines:
            parts = ln.split()
            if len(parts) >= 8:
                ifname = parts[0]
                dest_int = int(parts[1], 16)
                mask_int = int(parts[7], 16)
                if mask_int != 0:
                    iface_Subnets.append((ifname, dest_int, mask_int))
    except Exception:
        pass

    # 2. Extract LOCAL host IPv4 addresses from /proc/1/net/fib_trie
    fib_path = os.path.join(proc_net_dir, "fib_trie")
    local_ips: List[str] = []
    try:
        with open(fib_path, "r", encoding="utf-8", errors="ignore") as f:
            prev_ip = ""
            for raw in f:
                s = raw.strip()
                if s.startswith("|-- ") or s.startswith("+-- "):
                    cand = s[4:].strip()
                    if cand.count(".") == 3 and "/" not in cand:
                        prev_ip = cand
                elif "/32 host LOCAL" in s and prev_ip:
                    if not prev_ip.endswith(".255") and not prev_ip.endswith(".0"):
                        if prev_ip not in local_ips:
                            local_ips.append(prev_ip)
    except Exception:
        pass

    for ip_s in local_ips:
        if ip_s.startswith("127."):
            result.setdefault("lo", (ip_s, "255.0.0.0"))
            continue
        try:
            packed = struct.unpack("<I", socket.inet_aton(ip_s))[0]
            matched_if = None
            matched_mask = ""
            best_mask_bits = -1
            for ifname, dest_int, mask_int in iface_Subnets:
                if (packed & mask_int) == dest_int:
                    bits = bin(mask_int).count("1")
                    if bits > best_mask_bits:
                        best_mask_bits = bits
                        matched_if = ifname
                        matched_mask = socket.inet_ntoa(struct.pack("<I", mask_int))
            if matched_if and matched_if not in result:
                result[matched_if] = (ip_s, matched_mask)
        except Exception:
            continue

    return result


def parse_proc_net_dev(content: str) -> Dict[str, Dict[str, int]]:
    """Parse Linux `/proc/net/dev` text into per-interface byte/packet/error/drop counters."""
    stats: Dict[str, Dict[str, int]] = {}
    for line in content.splitlines()[2:]:
        if ":" not in line:
            continue
        iface_part, data_part = line.split(":", 1)
        iface = iface_part.strip()
        cols = data_part.split()
        if len(cols) < 16:
            continue
        try:
            stats[iface] = {
                "rx_bytes": int(cols[0]),
                "rx_packets": int(cols[1]),
                "err_in": int(cols[2]),
                "drop_in": int(cols[3]),
                "tx_bytes": int(cols[8]),
                "tx_packets": int(cols[9]),
                "err_out": int(cols[10]),
                "drop_out": int(cols[11]),
            }
        except ValueError:
            continue
    return stats


def _decode_proc_ip_port(hex_addr: str, is_ipv6: bool = False) -> Tuple[str, int]:
    host_hex, port_hex = hex_addr.split(":")
    port = int(port_hex, 16)
    if not is_ipv6:
        raw_bytes = struct.pack("<I", int(host_hex, 16))
        return socket.inet_ntoa(raw_bytes), port

    if len(host_hex) == 32:
        words = [struct.pack("<I", int(host_hex[i : i + 8], 16)) for i in range(0, 32, 8)]
        addr6 = socket.inet_ntop(socket.AF_INET6, b"".join(words))
        if addr6.startswith("::ffff:"):
            return addr6[7:], port
        return addr6, port
    return "::", port


def parse_proc_net_sockets(
    proc_entries: List[Tuple[str, bool, str]],
    service_Lookup: Optional[Dict[int, str]] = None,
) -> Dict[str, Any]:
    """
    Parse `/proc/net/{tcp,tcp6,udp,udp6}` contents into:
      - `listening_ports`: deduplicated active bound services
      - `connection_stats`: socket state breakdown
      - `active_flows`: aggregated real-time traffic flows with direction, zone, and kernel queue bytes
      - `flow_summary`: zone & direction traffic flow summary
    """
    svc_map = service_Lookup or KNOWN_SERVICES
    listening_ports: List[Dict[str, Any]] = []
    seen_listen: set = set()
    listen_port_nums: set = set()
    conn_counts = {
        "established": 0,
        "listen": 0,
        "time_wait": 0,
        "close_wait": 0,
        "syn_active": 0,
        "total": 0,
    }

    raw_connections: List[Dict[str, Any]] = []
    seen_socket_quads: set = set()

    for proto_label, is_ipv6, text in proc_entries:
        lines = text.splitlines()
        if len(lines) <= 1:
            continue
        for ln in lines[1:]:
            cols = ln.split()
            if len(cols) < 5:
                continue
            try:
                local_ip, local_port = _decode_proc_ip_port(cols[1], is_ipv6=is_ipv6)
                remote_ip, remote_port = _decode_proc_ip_port(cols[2], is_ipv6=is_ipv6)
                state_hex = cols[3].upper()
                is_udp = proto_label.startswith("UDP")
                if is_udp:
                    state = "LISTEN" if remote_port == 0 and state_hex in ("07", "0A") else "ESTABLISHED"
                else:
                    state = _TCP_STATES.get(state_hex, "OTHER")

                tx_q, rx_q = 0, 0
                if ":" in cols[4]:
                    tq_s, rq_s = cols[4].split(":", 1)
                    tx_q = int(tq_s, 16)
                    rx_q = int(rq_s, 16)
            except Exception:
                continue

            quad = (proto_label[:3], local_ip, local_port, remote_ip, remote_port, state)
            if quad in seen_socket_quads:
                continue
            seen_socket_quads.add(quad)

            conn_counts["total"] += 1
            if state == "ESTABLISHED":
                conn_counts["established"] += 1
            elif state == "LISTEN":
                conn_counts["listen"] += 1
            elif state == "TIME_WAIT":
                conn_counts["time_wait"] += 1
            elif state == "CLOSE_WAIT":
                conn_counts["close_wait"] += 1
            elif state in ("SYN_SENT", "SYN_RECV"):
                conn_counts["syn_active"] += 1

            if state == "LISTEN":
                listen_port_nums.add(local_port)
                lkey = f"{proto_label[:3]}:{local_port}"
                if lkey not in seen_listen:
                    seen_listen.add(lkey)
                    svc_name = svc_map.get(local_port) or (
                        f"Homelab App (:{local_port})" if 3100 <= local_port <= 3999 else "container/service"
                    )
                    listening_ports.append({
                        "port": local_port,
                        "protocol": proto_label[:3],
                        "bind_address": local_ip,
                        "pid": 0,
                        "service": svc_name,
                        "status": "LISTEN",
                    })
            elif remote_port > 0 and state in ("ESTABLISHED", "SYN_SENT", "SYN_RECV", "CLOSE_WAIT", "TIME_WAIT"):
                raw_connections.append({
                    "protocol": proto_label[:3],
                    "local_ip": local_ip,
                    "local_port": local_port,
                    "remote_ip": remote_ip,
                    "remote_port": remote_port,
                    "state": state,
                    "tx_queue": tx_q,
                    "rx_queue": rx_q,
                })

    # Aggregate active connections into clean, readable Network Flows
    flow_buckets: Dict[Tuple[str, str, int, str, str], Dict[str, Any]] = {}
    zone_counts = {"lan": 0, "tailscale": 0, "docker": 0, "internet": 0, "loopback": 0}
    direction_counts = {"inbound": 0, "internal": 0, "outbound": 0}

    for c in raw_connections:
        lp = c["local_port"]
        rp = c["remote_port"]
        rip = c["remote_ip"]
        lip = c["local_ip"]
        proto = c["protocol"]
        zone = classify_ip_zone(rip)
        if zone in zone_counts:
            zone_counts[zone] += 1

        is_inbound = lp in listen_port_nums or lp in svc_map or (3100 <= lp <= 3999 and rp > 3999)
        if zone in ("docker", "loopback"):
            direction = "internal"
        elif is_inbound:
            direction = "inbound"
        else:
            direction = "outbound"

        direction_counts[direction] += 1

        if is_inbound:
            anchor_port = lp
            svc = svc_map.get(lp) or (f"Deployed App (:{lp})" if 3100 <= lp <= 3999 else f"Port :{lp}")
            peer_endpoint = rip
        else:
            anchor_port = rp
            svc = svc_map.get(rp) or (
                "HTTPS / TLS Upstream" if rp == 443 else ("DNS Resolver" if rp == 53 else f"Remote :{rp}")
            )
            peer_endpoint = f"{rip}:{rp}"

        bkey = (proto, direction, anchor_port, peer_endpoint, c["state"])
        bucket = flow_buckets.get(bkey)
        if not bucket:
            flow_buckets[bkey] = {
                "protocol": proto,
                "direction": direction,
                "zone": zone,
                "local_ip": lip,
                "local_port": lp if is_inbound else c["local_port"],
                "remote_ip": rip,
                "remote_port": rp,
                "anchor_port": anchor_port,
                "service": svc,
                "state": c["state"],
                "connections": 1,
                "tx_queue_bytes": c["tx_queue"],
                "rx_queue_bytes": c["rx_queue"],
            }
        else:
            bucket["connections"] += 1
            bucket["tx_queue_bytes"] += c["tx_queue"]
            bucket["rx_queue_bytes"] += c["rx_queue"]

    state_rank = {"ESTABLISHED": 0, "SYN_RECV": 1, "SYN_SENT": 2, "CLOSE_WAIT": 3, "TIME_WAIT": 4}
    active_flows = sorted(
        flow_buckets.values(),
        key=lambda f: (
            state_rank.get(f["state"], 9),
            -(f["tx_queue_bytes"] + f["rx_queue_bytes"]),
            -f["connections"],
            f["anchor_port"],
        ),
    )[:80]

    listening_ports.sort(key=lambda x: x["port"])
    return {
        "listening_ports": listening_ports,
        "connection_stats": conn_counts,
        "active_flows": active_flows,
        "flow_summary": {
            "by_direction": direction_counts,
            "by_zone": zone_counts,
            "active_flows_count": len(active_flows),
        },
    }


def collect_network_interfaces_and_rates(now: float) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Collect all network interfaces plus live per-interface RX/s & TX/s rates.
    Prefers host `/proc/1/net/dev` + `/host/sys/class/net` on Linux so all physical NICs,
    Tailscale VPN, and Docker bridges are visible even from inside a bridge container.
    Excludes `lo` and `veth*` from primary server bandwidth totals to prevent double-counting.
    """
    global _last_net_totals, _last_net_time, _last_pernic_bytes, _last_pernic_time

    proc_net_dir = _resolve_host_proc_net_dir()
    sys_net_dir = next(
        (d for d in (os.getenv("HOST_SYS_NET_DIR", ""), "/host/sys/class/net", "/sys/class/net") if d and os.path.isdir(d)),
        "",
    )

    interfaces: List[Dict[str, Any]] = []
    elapsed_if = max(now - _last_pernic_time, 0.001) if _last_pernic_time else 0.0
    new_pernic_bytes: Dict[str, Tuple[int, int]] = {}

    if proc_net_dir:
        try:
            with open(os.path.join(proc_net_dir, "dev"), "r", encoding="utf-8", errors="ignore") as f:
                dev_map = parse_proc_net_dev(f.read())
        except Exception:
            dev_map = {}

        fib_ips = _parse_proc_fib_ipv4(proc_net_dir)
        ps_addrs = psutil.net_if_addrs()

        for iface_name, io in dev_map.items():
            # Hide ephemeral per-container veth pairs from main interface table to keep UI clean & fast,
            # while keeping physical NICs, Tailscale, WireGuard, bridges (docker0, br-*), and loopback.
            if iface_name.startswith("veth"):
                continue

            iface_type = _classify_interface_type(iface_name)
            oper = _read_sys_net_value(sys_net_dir, iface_name, "operstate", "up").lower() if sys_net_dir else "up"
            is_up = oper in ("up", "unknown") or (iface_type == "loopback")
            mtu_s = _read_sys_net_value(sys_net_dir, iface_name, "mtu", "1500") if sys_net_dir else "1500"
            speed_s = _read_sys_net_value(sys_net_dir, iface_name, "speed", "0") if sys_net_dir else "0"
            mac = _read_sys_net_value(sys_net_dir, iface_name, "address", "") if sys_net_dir else ""

            ipv4, netmask = fib_ips.get(iface_name, ("", ""))
            ipv6 = ""
            if iface_name in ps_addrs:
                for addr in ps_addrs[iface_name]:
                    if addr.family == socket.AF_INET and not ipv4:
                        ipv4 = addr.address
                        netmask = addr.netmask or ""
                    elif addr.family == socket.AF_INET6 and not ipv6:
                        ipv6 = addr.address.split("%")[0]
                    elif hasattr(psutil, "AF_LINK") and addr.family == psutil.AF_LINK and not mac:
                        mac = addr.address

            rx_b = io["rx_bytes"]
            tx_b = io["tx_bytes"]
            new_pernic_bytes[iface_name] = (rx_b, tx_b)

            rx_rate_if = 0.0
            tx_rate_if = 0.0
            if elapsed_if > 0 and iface_name in _last_pernic_bytes:
                prev_rx, prev_tx = _last_pernic_bytes[iface_name]
                rx_rate_if = max(0.0, (rx_b - prev_rx) / elapsed_if)
                tx_rate_if = max(0.0, (tx_b - prev_tx) / elapsed_if)

            try:
                speed_val = max(0, int(speed_s))
            except ValueError:
                speed_val = 0
            try:
                mtu_val = int(mtu_s)
            except ValueError:
                mtu_val = 1500

            interfaces.append({
                "name": iface_name,
                "type": iface_type,
                "is_up": is_up,
                "speed_mbps": speed_val,
                "mtu": mtu_val,
                "ipv4": ipv4,
                "netmask": netmask,
                "ipv6": ipv6,
                "mac": mac if mac != "00:00:00:00:00:00" else "",
                "rx_bytes": rx_b,
                "tx_bytes": tx_b,
                "rx_bytes_per_sec": round(rx_rate_if, 1),
                "tx_bytes_per_sec": round(tx_rate_if, 1),
                "rx_packets": io["rx_packets"],
                "tx_packets": io["tx_packets"],
                "err_in": io["err_in"],
                "err_out": io["err_out"],
            })

        _last_pernic_bytes = new_pernic_bytes
        _last_pernic_time = now

        # Aggregate true external/VPN host traffic (physical ethernet/wireless + VPN, excluding loopback & virtual bridges)
        primary_ifs = [
            i for i in interfaces if i["type"] in ("ethernet", "wireless", "vpn")
        ] or [i for i in interfaces if i["type"] != "loopback"]

        tot_rx = sum(i["rx_bytes"] for i in primary_ifs)
        tot_tx = sum(i["tx_bytes"] for i in primary_ifs)
        tot_pkts_rx = sum(i["rx_packets"] for i in primary_ifs)
        tot_pkts_tx = sum(i["tx_packets"] for i in primary_ifs)
        tot_err_in = sum(i["err_in"] for i in primary_ifs)
        tot_err_out = sum(i["err_out"] for i in primary_ifs)
        tot_drop_in = sum(dev_map.get(i["name"], {}).get("drop_in", 0) for i in primary_ifs)
        tot_drop_out = sum(dev_map.get(i["name"], {}).get("drop_out", 0) for i in primary_ifs)

        rx_rate = 0.0
        tx_rate = 0.0
        if _last_net_totals and _last_net_time:
            el = max(now - _last_net_time, 0.001)
            rx_rate = max(0.0, (tot_rx - _last_net_totals[0]) / el)
            tx_rate = max(0.0, (tot_tx - _last_net_totals[1]) / el)
        _last_net_totals = (tot_rx, tot_tx, tot_pkts_rx, tot_pkts_tx)
        _last_net_time = now

        type_order = {"ethernet": 0, "wireless": 1, "vpn": 2, "docker": 3, "loopback": 4}
        interfaces.sort(key=lambda x: (type_order.get(x["type"], 9), x["name"]))

        return interfaces, {
            "rx_rate": rx_rate,
            "tx_rate": tx_rate,
            "total_rx_bytes": tot_rx,
            "total_tx_bytes": tot_tx,
            "packets_recv": tot_pkts_rx,
            "packets_sent": tot_pkts_tx,
            "err_in": tot_err_in,
            "err_out": tot_err_out,
            "drop_in": tot_drop_in,
            "drop_out": tot_drop_out,
        }

    # Cross-platform psutil fallback (Windows / macOS / non-proc environments)
    try:
        addrs = psutil.net_if_addrs()
        stats = psutil.net_if_stats()
        pernic_io = psutil.net_io_counters(pernic=True)

        for iface_name, addr_list in addrs.items():
            ipv4, netmask, ipv6, mac = "", "", "", ""
            for addr in addr_list:
                if addr.family == socket.AF_INET:
                    ipv4 = addr.address
                    netmask = addr.netmask or ""
                elif addr.family == socket.AF_INET6 and not ipv6:
                    ipv6 = addr.address.split("%")[0]
                elif hasattr(psutil, "AF_LINK") and addr.family == psutil.AF_LINK:
                    mac = addr.address

            st = stats.get(iface_name)
            io = pernic_io.get(iface_name)
            iface_type = _classify_interface_type(iface_name)
            rx_b = int(io.bytes_recv) if io else 0
            tx_b = int(io.bytes_sent) if io else 0
            new_pernic_bytes[iface_name] = (rx_b, tx_b)

            rx_rate_if = 0.0
            tx_rate_if = 0.0
            if elapsed_if > 0 and iface_name in _last_pernic_bytes:
                prev_rx, prev_tx = _last_pernic_bytes[iface_name]
                rx_rate_if = max(0.0, (rx_b - prev_rx) / elapsed_if)
                tx_rate_if = max(0.0, (tx_b - prev_tx) / elapsed_if)

            interfaces.append({
                "name": iface_name,
                "type": iface_type,
                "is_up": bool(st.isup) if st else False,
                "speed_mbps": int(st.speed) if st and st.speed else 0,
                "mtu": int(st.mtu) if st and st.mtu else 1500,
                "ipv4": ipv4,
                "netmask": netmask,
                "ipv6": ipv6,
                "mac": mac,
                "rx_bytes": rx_b,
                "tx_bytes": tx_b,
                "rx_bytes_per_sec": round(rx_rate_if, 1),
                "tx_bytes_per_sec": round(tx_rate_if, 1),
                "rx_packets": int(io.packets_recv) if io else 0,
                "tx_packets": int(io.packets_sent) if io else 0,
                "err_in": int(io.errin) if io else 0,
                "err_out": int(io.errout) if io else 0,
            })
        _last_pernic_bytes = new_pernic_bytes
        _last_pernic_time = now
    except Exception:
        pass

    net_io = psutil.net_io_counters()
    rx_rate = 0.0
    tx_rate = 0.0
    if _last_net_totals and _last_net_time:
        el = max(now - _last_net_time, 0.001)
        rx_rate = max(0.0, (int(net_io.bytes_recv) - _last_net_totals[0]) / el)
        tx_rate = max(0.0, (int(net_io.bytes_sent) - _last_net_totals[1]) / el)
    _last_net_totals = (int(net_io.bytes_recv), int(net_io.bytes_sent), int(net_io.packets_recv), int(net_io.packets_sent))
    _last_net_time = now

    return interfaces, {
        "rx_rate": rx_rate,
        "tx_rate": tx_rate,
        "total_rx_bytes": int(net_io.bytes_recv),
        "total_tx_bytes": int(net_io.bytes_sent),
        "packets_recv": int(net_io.packets_recv),
        "packets_sent": int(net_io.packets_sent),
        "err_in": int(net_io.errin),
        "err_out": int(net_io.errout),
        "drop_in": int(net_io.dropin),
        "drop_out": int(net_io.dropout),
    }


def collect_listening_ports_and_flows() -> Dict[str, Any]:
    """
    Collect active listening ports, connection state breakdown, and real-time network flows.
    Uses sub-millisecond `/proc/1/net/{tcp,tcp6,udp,udp6}` parsing on Linux, with psutil fallback elsewhere.
    """
    proc_net_dir = _resolve_host_proc_net_dir()
    if proc_net_dir:
        entries: List[Tuple[str, bool, str]] = []
        for fname, label, is_v6 in (
            ("tcp", "TCP", False),
            ("tcp6", "TCP6", True),
            ("udp", "UDP", False),
            ("udp6", "UDP6", True),
        ):
            fpath = os.path.join(proc_net_dir, fname)
            if os.path.isfile(fpath):
                try:
                    with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                        entries.append((label, is_v6, f.read()))
                except Exception:
                    pass
        if entries:
            return parse_proc_net_sockets(entries)

    # Fallback via psutil (Windows / macOS)
    listening_ports = []
    seen_ports = set()
    listen_port_nums = set()
    conn_counts = {
        "established": 0,
        "listen": 0,
        "time_wait": 0,
        "close_wait": 0,
        "syn_active": 0,
        "total": 0,
    }
    flow_buckets: Dict[Tuple[str, str, int, str, str], Dict[str, Any]] = {}
    zone_counts = {"lan": 0, "tailscale": 0, "docker": 0, "internet": 0, "loopback": 0}
    direction_counts = {"inbound": 0, "internal": 0, "outbound": 0}
    raw_conns = []

    try:
        conns = psutil.net_connections(kind="inet")
        for c in conns:
            conn_counts["total"] += 1
            st = (c.status or "").upper()
            proto = "TCP" if c.type == socket.SOCK_STREAM else "UDP"
            if st == "ESTABLISHED":
                conn_counts["established"] += 1
            elif st == "LISTEN":
                conn_counts["listen"] += 1
            elif st == "TIME_WAIT":
                conn_counts["time_wait"] += 1
            elif st == "CLOSE_WAIT":
                conn_counts["close_wait"] += 1
            elif st in ("SYN_SENT", "SYN_RECV"):
                conn_counts["syn_active"] += 1

            if st == "LISTEN" and c.laddr:
                port = c.laddr.port
                listen_port_nums.add(port)
                key = f"{proto}:{port}"
                if key not in seen_ports:
                    seen_ports.add(key)
                    proc_name = KNOWN_SERVICES.get(port, "")
                    if c.pid and not proc_name:
                        try:
                            proc_name = psutil.Process(c.pid).name()
                        except Exception:
                            proc_name = "system-service"
                    elif not proc_name:
                        proc_name = "container/service"

                    listening_ports.append({
                        "port": port,
                        "protocol": proto,
                        "bind_address": c.laddr.ip,
                        "pid": c.pid or 0,
                        "service": proc_name,
                        "status": "LISTEN",
                    })
            elif c.laddr and c.raddr and st in ("ESTABLISHED", "SYN_SENT", "SYN_RECV", "CLOSE_WAIT", "TIME_WAIT"):
                raw_conns.append((proto, c.laddr.ip, c.laddr.port, c.raddr.ip, c.raddr.port, st))
    except Exception:
        pass

    for proto, lip, lp, rip, rp, st in raw_conns:
        zone = classify_ip_zone(rip)
        if zone in zone_counts:
            zone_counts[zone] += 1
        is_inbound = lp in listen_port_nums or lp in KNOWN_SERVICES or (3100 <= lp <= 3999 and rp > 3999)
        direction = "internal" if zone in ("docker", "loopback") else ("inbound" if is_inbound else "outbound")
        direction_counts[direction] += 1
        anchor_port = lp if is_inbound else rp
        svc = KNOWN_SERVICES.get(anchor_port) or (f"Port :{anchor_port}")
        peer_ep = rip if is_inbound else f"{rip}:{rp}"
        bkey = (proto, direction, anchor_port, peer_ep, st)
        if bkey not in flow_buckets:
            flow_buckets[bkey] = {
                "protocol": proto,
                "direction": direction,
                "zone": zone,
                "local_ip": lip,
                "local_port": lp,
                "remote_ip": rip,
                "remote_port": rp,
                "anchor_port": anchor_port,
                "service": svc,
                "state": st,
                "connections": 1,
                "tx_queue_bytes": 0,
                "rx_queue_bytes": 0,
            }
        else:
            flow_buckets[bkey]["connections"] += 1

    listening_ports.sort(key=lambda x: x["port"])
    active_flows = sorted(flow_buckets.values(), key=lambda f: (-f["connections"], f["anchor_port"]))[:80]
    return {
        "listening_ports": listening_ports,
        "connection_stats": conn_counts,
        "active_flows": active_flows,
        "flow_summary": {
            "by_direction": direction_counts,
            "by_zone": zone_counts,
            "active_flows_count": len(active_flows),
        },
    }


# Backward-compatible alias
def collect_network_interfaces() -> List[Dict[str, Any]]:
    ifs, _ = collect_network_interfaces_and_rates(time.time())
    return ifs


def collect_listening_ports_and_connections() -> Dict[str, Any]:
    return collect_listening_ports_and_flows()


def get_metrics_snapshot() -> Dict[str, Any]:
    global _last_disk_io, _last_disk_time

    now = time.time()

    # CPU
    cpu_percent = psutil.cpu_percent(interval=None)
    cpu_cores = psutil.cpu_count(logical=True) or 1

    # Load average (Linux/Unix)
    load_avg = [0.0, 0.0, 0.0]
    if hasattr(os, "getloadavg"):
        try:
            load_avg = [round(x, 2) for x in os.getloadavg()]
        except Exception:
            pass

    # Memory & Swap
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()

    # Primary Disk
    disk = psutil.disk_usage("/") if platform.system() != "Windows" else psutil.disk_usage("C:\\")

    # Disk I/O Read/Write
    disk_read_bytes = 0
    disk_write_bytes = 0
    disk_read_count = 0
    disk_write_count = 0
    disk_read_rate = 0.0
    disk_write_rate = 0.0
    try:
        dio = psutil.disk_io_counters()
        if dio:
            disk_read_bytes = int(dio.read_bytes)
            disk_write_bytes = int(dio.write_bytes)
            disk_read_count = int(dio.read_count)
            disk_write_count = int(dio.write_count)
            if _last_disk_io and _last_disk_time:
                elapsed_d = max(now - _last_disk_time, 0.001)
                disk_read_rate = max(0.0, (dio.read_bytes - _last_disk_io.read_bytes) / elapsed_d)
                disk_write_rate = max(0.0, (dio.write_bytes - _last_disk_io.write_bytes) / elapsed_d)
            _last_disk_io = dio
            _last_disk_time = now
    except Exception:
        pass

    # Disk partitions (Used & Unused/Free)
    partitions_data = []
    seen_mounts = set()
    try:
        for p in psutil.disk_partitions(all=False):
            if "cdrom" in p.opts or not p.fstype:
                continue
            if p.mountpoint in seen_mounts:
                continue
            seen_mounts.add(p.mountpoint)
            try:
                usage = psutil.disk_usage(p.mountpoint)
                partitions_data.append({
                    "device": p.device,
                    "mountpoint": p.mountpoint,
                    "fstype": p.fstype,
                    "opts": p.opts,
                    "total_bytes": usage.total,
                    "used_bytes": usage.used,
                    "free_bytes": usage.free,
                    "percent": round(usage.percent, 1),
                })
            except (PermissionError, Exception):
                pass
    except Exception:
        pass

    # Sub-millisecond host network interfaces, live per-NIC throughput, sockets, and active flows
    net_interfaces, net_totals = collect_network_interfaces_and_rates(now)
    ports_and_flows = collect_listening_ports_and_flows()

    # Uptime
    boot_time = psutil.boot_time()
    uptime_seconds = int(now - boot_time)

    # Per-container live stats from Docker (cached with 30s TTL + paused during active builds)
    container_stats = []
    try:
        container_stats = docker_manager.get_all_container_stats()
    except Exception:
        pass

    return {
        "cpu_usage": round(cpu_percent, 1),
        "cpu_cores": cpu_cores,
        "load_avg": load_avg,
        "memory_usage": round(mem.percent, 1),
        "memory_used_bytes": mem.used,
        "memory_total_bytes": mem.total,
        "memory_available_bytes": mem.available,
        "swap_used_bytes": swap.used,
        "swap_total_bytes": swap.total,
        "disk_usage": round(disk.percent, 1),
        "disk_used_bytes": disk.used,
        "disk_free_bytes": disk.free,
        "disk_total_bytes": disk.total,
        "disk_read_bytes": disk_read_bytes,
        "disk_write_bytes": disk_write_bytes,
        "disk_read_count": disk_read_count,
        "disk_write_count": disk_write_count,
        "disk_read_bytes_per_sec": round(disk_read_rate, 1),
        "disk_write_bytes_per_sec": round(disk_write_rate, 1),
        "disk_partitions": partitions_data,
        "network_rx_bytes_per_sec": round(net_totals["rx_rate"], 1),
        "network_tx_bytes_per_sec": round(net_totals["tx_rate"], 1),
        "network_total_rx_bytes": net_totals["total_rx_bytes"],
        "network_total_tx_bytes": net_totals["total_tx_bytes"],
        "network_packets_recv": net_totals["packets_recv"],
        "network_packets_sent": net_totals["packets_sent"],
        "network_err_in": net_totals["err_in"],
        "network_err_out": net_totals["err_out"],
        "network_drop_in": net_totals["drop_in"],
        "network_drop_out": net_totals["drop_out"],
        "network_interfaces": net_interfaces,
        "listening_ports": ports_and_flows["listening_ports"],
        "connection_stats": ports_and_flows["connection_stats"],
        "active_flows": ports_and_flows["active_flows"],
        "flow_summary": ports_and_flows["flow_summary"],
        "uptime_seconds": uptime_seconds,
        "container_stats": container_stats,
    }
