"""
Module de détection de l'environnement d'exécution et de la connectivité réseau CEA.
Permet d'identifier automatiquement si l'application s'exécute sur le site/réseau interne CEA
et si le service d'IA souverain Maïa Chat CEA est joignable.
"""

import socket
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

# Constantes du réseau CEA
CEA_MAIA_HOST = "maia-chat.intra.cea.fr"
CEA_MAIA_DEFAULT_PORT = 443
CEA_DOMAINS = (".cea.fr", "intra.cea.fr")
CEA_IP_PREFIXES = ("10.", "132.166.")

_ENV_CACHE: Optional[Dict[str, Any]] = None


def get_local_ip_addresses() -> List[str]:
    """Récupère l'ensemble des adresses IPv4 associées aux cartes réseau locales."""
    ips = []
    try:
        hostname = socket.gethostname()
        addr_info = socket.gethostbyname_ex(hostname)
        ips = [ip for ip in addr_info[2] if not ip.startswith("127.")]
    except Exception as err:
        logger.debug("Impossible d'obtenir les adresses via gethostbyname_ex: %s", err)

    if not ips:
        # Repli via connexion UDP factice
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("10.255.255.255", 1))
                ip = s.getsockname()[0]
                if ip and not ip.startswith("127."):
                    ips.append(ip)
        except Exception:
            pass

    return ips


def resolve_maia_host() -> Optional[str]:
    """Tente de résoudre l'hôte de l'API Maïa CEA en adresse IP."""
    try:
        return socket.gethostbyname(CEA_MAIA_HOST)
    except socket.gaierror:
        return None
    except Exception as err:
        logger.debug("Erreur résolution hôte Maïa: %s", err)
        return None


def is_maia_reachable(timeout_s: float = 2.0) -> bool:
    """Vérifie rapidement si le port HTTPS de Maïa Chat CEA est joignable."""
    try:
        with socket.create_connection((CEA_MAIA_HOST, CEA_MAIA_DEFAULT_PORT), timeout=timeout_s):
            return True
    except (socket.timeout, OSError):
        return False


def is_cea_network(refresh: bool = False) -> bool:
    """
    Détermine si la machine est connectée au réseau ou au site du CEA.
    Critères :
    1. Suffixe FQDN/DNS se terminant par .cea.fr ou intra.cea.fr
    2. Adresse IP locale dans la plage CEA (ex: 10.x.x.x ou 132.166.x.x)
    3. Résolution réussie du nom d'hôte interne maia-chat.intra.cea.fr
    """
    global _ENV_CACHE
    if not refresh and _ENV_CACHE is not None:
        return _ENV_CACHE.get("is_cea", False)

    env = get_environment_info(refresh=True)
    return env["is_cea"]


def get_environment_info(refresh: bool = False) -> Dict[str, Any]:
    """
    Retourne un diagnostic complet de l'environnement d'exécution (hôte, IP, CEA, Maïa).
    """
    global _ENV_CACHE
    if not refresh and _ENV_CACHE is not None:
        return _ENV_CACHE

    hostname = socket.gethostname()
    fqdn = socket.getfqdn()
    local_ips = get_local_ip_addresses()

    # Détection domaine CEA
    has_cea_fqdn = any(fqdn.lower().endswith(dom) or dom in fqdn.lower() for dom in CEA_DOMAINS)
    
    # Détection plage IP CEA
    cea_ips = [ip for ip in local_ips if any(ip.startswith(prefix) for prefix in CEA_IP_PREFIXES)]
    has_cea_ip = len(cea_ips) > 0

    # Test DNS et connectivité Maïa
    maia_ip = resolve_maia_host()
    maia_online = is_maia_reachable(timeout_s=1.5) if maia_ip else False

    is_cea = has_cea_fqdn or has_cea_ip or (maia_ip is not None)

    env_label = "Site / Réseau CEA (Intranet/VPN)" if is_cea else "Hors réseau CEA (Public / Externe)"

    _ENV_CACHE = {
        "is_cea": is_cea,
        "environment_label": env_label,
        "hostname": hostname,
        "fqdn": fqdn,
        "local_ips": local_ips,
        "cea_ips": cea_ips,
        "maia_host": CEA_MAIA_HOST,
        "maia_ip": maia_ip,
        "maia_reachable": maia_online,
    }
    return _ENV_CACHE
