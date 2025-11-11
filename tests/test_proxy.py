# tests/test_proxy.py
from itertools import product

import pytest

from human_requests.abstraction import Proxy

# === Независимые наборы параметров ===
PROTOCOLS = ["http", "https", "socks5", None]  # None → без протокола в строке (fallback http)
HOSTS = ["127.0.0.1", "proxy.example.com", "localhost"]
PORTS = [8080, None]  # None → без порта
AUTH_VARIANTS = [
    None,  # без auth
    ("user123", "pass456"),  # full auth
    ("login", None),  # only username
    (None, "onlypass"),  # only password
]
INPUT_TYPES = ["str_with_proto", "str_without_proto", "playwright_dict", "direct_kwargs"]


def get_auth_id(auth):
    """Генерирует короткий ID для auth-варианта"""
    if auth is None:
        return "no"
    u, p = auth
    uid = str(u)[:4] if u else ""
    pid = str(p)[:4] if p else ""
    return f"{uid}_{pid}"


# Генерируем argsets и ids_list явно (чтобы избежать проблем с lambda в ids)
params = product(PROTOCOLS, HOSTS, PORTS, AUTH_VARIANTS, INPUT_TYPES)
filtered_params = [
    (proto, host, port, auth, inp_type)
    for proto, host, port, auth, inp_type in params
    if not (
        (inp_type == "str_with_proto" and proto is None)
        or (inp_type == "str_without_proto" and proto is not None)
    )
]

ids_list = []
for proto, host, port, auth, inp_type in filtered_params:
    p_id = (proto or "http")[:4]
    h_id = host.replace(".", "_").replace("example", "ex")[:8]  # укорачиваем
    po_id = str(port) if port is not None else "nop"
    a_id = get_auth_id(auth)
    i_id = inp_type[:3]  # str, pla, dir
    ids_list.append(f"{p_id}-{h_id}-{po_id}-{a_id}-{i_id}")


@pytest.mark.parametrize("protocol,host,port,auth,input_type", filtered_params, ids=ids_list)
def test_proxy_converter_full_matrix(protocol, host, port, auth, input_type):
    username, password = auth if auth else (None, None)

    # === Всегда fallback на http если protocol=None ===
    effective_proto = protocol or "http"

    # === Строим ожидаемые значения ===
    port_str = f":{port}" if port else ""
    expected_server = f"{effective_proto}://{host}{port_str}"

    expected_dict = {"server": expected_server}
    if username:
        expected_dict["username"] = username
    if password:
        expected_dict["password"] = password

    # Правильный auth_part: username[:password]@ без лишних :
    auth_part = f"{username or ''}"
    if password is not None:
        auth_part += f":{password}"
    expected_str_noauth = expected_server
    if username or password is not None:  # even if only password
        expected_str_full = expected_server.replace("://", f"://{auth_part}@")
    else:
        expected_str_full = expected_server

    # === Создаём input в зависимости от input_type ===
    if input_type == "str_with_proto":
        # Требует protocol не None (уже отфильтровано)
        port_part = f":{port}" if port else ""
        input_auth_part = ""
        if username or password is not None:
            input_auth_part = f"{username or ''}"
            if password is not None:
                input_auth_part += f":{password}"
            input_auth_part += "@"
        proxy_input = f"{protocol}://{input_auth_part}{host}{port_part}"

    elif input_type == "str_without_proto":
        # Всегда без протокола, becomes http:// внутри
        port_part = f":{port}" if port else ""
        input_auth_part = ""
        if username or password is not None:
            input_auth_part = f"{username or ''}"
            if password is not None:
                input_auth_part += f":{password}"
            input_auth_part += "@"
        proxy_input = f"{input_auth_part}{host}{port_part}"

    elif input_type == "playwright_dict":
        d = {"server": expected_server}
        if username:
            d["username"] = username
        if password:
            d["password"] = password
        proxy_input = d

    elif input_type == "direct_kwargs":
        proxy_input = None  # используем kwargs ниже

    # === Инициализация ===
    if input_type == "direct_kwargs":
        proxy = Proxy(server=expected_server, username=username, password=password)
    else:
        proxy = Proxy(proxy_input)

    # === Проверки ===
    assert (
        proxy.as_dict() == expected_dict
    ), f"as_dict mismatch for {input_type} | proto={protocol} auth={auth}"

    assert proxy.as_str() == expected_str_full, f"as_str full mismatch for {input_type}"
    assert proxy.as_str(include_auth=True) == expected_str_full, "as_str(True) mismatch"
    assert proxy.as_str(include_auth=False) == expected_str_noauth, "as_str(False) mismatch"

    # round-trip: dict → str → dict
    new_proxy = Proxy(proxy.as_dict())
    assert new_proxy.as_dict() == expected_dict, "roundtrip dict->str->dict failed"

    # round-trip: str → dict → str
    new_proxy2 = Proxy(proxy.as_str())
    assert new_proxy2.as_str() == expected_str_full, "roundtrip str->dict->str failed"

    # Проверка __bool__
    assert bool(proxy) is True
