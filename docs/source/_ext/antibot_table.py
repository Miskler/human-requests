from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

from docutils import nodes
from docutils.parsers.rst import Directive, directives


GREEN = "ab-green"
YELLOW = "ab-yellow"
RED = "ab-red"

CHECK = "✅"
CROSS = "❌"

# Явно фиксируем группы и базовый порядок колонок:
BASIC_BROWSERS_ORDER = ["firefox", "chromium", "webkit"]
STEALTH_BROWSERS_ORDER = ["camoufox", "patchright"]
STEALTH_SET = set(STEALTH_BROWSERS_ORDER)


def _read_json(p: str) -> Dict[str, Any]:
    path = Path(p)
    if not path.exists():
        raise FileNotFoundError(f"JSON not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _collect_all_props(matrix: Dict[str, Any]) -> List[str]:
    """
    Возвращает отсортированный список проверок, которые падали хотя бы где-то.
    Берём объединение из all/base/stealth (stable+unstable) по всем браузерам.
    """
    props: Set[str] = set()
    for br_data in matrix.values():
        for section in ("all", "base", "stealth"):
            sec = br_data.get(section, {})
            for k in ("stable", "unstable"):
                props.update(sec.get(k, []))
    return sorted(props)


def _classify_cell_basic(br_data: Dict[str, Any], prop: str) -> Tuple[str, str]:
    """
    Классификация для базовых браузеров (firefox/chromium/webkit):
      1) Нигде не падало -> ✅ (зелёный)
      2) Падало только в base или только в stealth -> ❌ (+ пометка), жёлтый
      3) Падало везде или отмечено в all.* -> ❌ [+ 'unstable' если нужно], красный
    """
    # all.*
    all_stable = set(br_data.get("all", {}).get("stable", []))
    all_unstable = set(br_data.get("all", {}).get("unstable", []))
    if prop in all_stable:
        return f"{CROSS}", RED
    if prop in all_unstable:
        return f"{CROSS} unstable", RED

    # base/stealth
    b_st = set(br_data.get("base", {}).get("stable", []))
    b_un = set(br_data.get("base", {}).get("unstable", []))
    s_st = set(br_data.get("stealth", {}).get("stable", []))
    s_un = set(br_data.get("stealth", {}).get("unstable", []))

    base_failed = prop in b_st or prop in b_un
    stealth_failed = prop in s_st or prop in s_un

    if not base_failed and not stealth_failed:
        return f"{CHECK}", GREEN

    if base_failed and stealth_failed:
        tag = "unstable" if (prop in b_un or prop in s_un) else ""
        return f"{CROSS} {tag}".strip(), RED

    # падало только в одном из режимов → partly (жёлтый)
    if base_failed:
        tag = "unstable" if prop in b_un else ""
        txt = f"{CROSS} base" + (f", {tag}" if tag else "")
        return txt, YELLOW
    else:
        tag = "unstable" if prop in s_un else ""
        txt = f"{CROSS} stealth" + (f", {tag}" if tag else "")
        return txt, YELLOW


def _classify_cell_stealth(br_data: Dict[str, Any], prop: str) -> Tuple[str, str]:
    """
    Классификация для стелс-браузеров (camoufox/patchright):
    'partly' НЕ бывает — либо прошёл (зелёный), либо упал (красный).
    Если есть 'unstable', помечаем текстом, но цвет остаётся красным.
    """
    # all.*
    all_stable = set(br_data.get("all", {}).get("stable", []))
    all_unstable = set(br_data.get("all", {}).get("unstable", []))
    if prop in all_stable:
        return f"{CROSS}", RED
    if prop in all_unstable:
        return f"{CROSS} unstable", RED

    # Любой фейл в base/stealth трактуем как "упал" (красный), без состояния partly.
    b_st = set(br_data.get("base", {}).get("stable", []))
    b_un = set(br_data.get("base", {}).get("unstable", []))
    s_st = set(br_data.get("stealth", {}).get("stable", []))
    s_un = set(br_data.get("stealth", {}).get("unstable", []))

    failed_unstable = prop in b_un or prop in s_un
    failed_any = (prop in b_st or prop in s_st or failed_unstable)

    if failed_any:
        return (f"{CROSS} unstable" if failed_unstable else f"{CROSS}"), RED

    return f"{CHECK}", GREEN


def _classify_cell(br_name: str, br_data: Dict[str, Any], prop: str) -> Tuple[str, str]:
    if br_name in STEALTH_SET:
        return _classify_cell_stealth(br_data, prop)
    return _classify_cell_basic(br_data, prop)


def _order_browsers(matrix: Dict[str, Any]) -> List[str]:
    """
    Возвращает список браузеров в порядке:
      [BASIC...] + [STEALTH...] + [прочие, если вдруг встретятся]
    Пропускает те, которых нет в данных.
    """
    present = set(matrix.keys())

    basics = [b for b in BASIC_BROWSERS_ORDER if b in present]
    stealths = [b for b in STEALTH_BROWSERS_ORDER if b in present]

    known = set(basics + stealths)
    others = sorted(present - known)  # детерминированность

    return basics + stealths + others


def _build_html_table(matrix: Dict[str, Any], title: str | None) -> str:
    browsers = _order_browsers(matrix)
    props = _collect_all_props(matrix)

    html = []
    if title:
        html.append(f'<h2 class="ab-title">{title}</h2>')

    # Легенда
    html.append(
        '<div class="ab-legend">'
        f'<span class="ab-cell {GREEN}">{CHECK} ok</span>'
        f'<span class="ab-cell {YELLOW}">{CROSS} partly (base/stealth[, unstable])</span>'
        f'<span class="ab-cell {RED}">{CROSS} everywhere [, unstable]</span>'
        "</div>"
    )

    # Группы для шапки
    basics = [b for b in browsers if b in BASIC_BROWSERS_ORDER]
    stealths = [b for b in browsers if b in STEALTH_BROWSERS_ORDER]
    others = [b for b in browsers if b not in BASIC_BROWSERS_ORDER + STEALTH_BROWSERS_ORDER]

    html.append('<table class="ab-table">')

    # Первая строка шапки: групповые заголовки
    html.append("<thead>")
    html.append("<tr>")
    html.append('<th class="ab-th ab-left"></th>')  # пустая левая верхняя ячейка
    if basics:
        html.append(f'<th class="ab-th" colspan="{len(basics)}">Basic Playwright</th>')
    if stealths:
        html.append(f'<th class="ab-th" colspan="{len(stealths)}">Stealth builds</th>')
    if others:
        html.append(f'<th class="ab-th" colspan="{len(others)}">Other</th>')
    html.append("</tr>")

    # Вторая строка шапки: конкретные браузеры
    html.append("<tr>")
    html.append('<th class="ab-th ab-left"></th>')
    for br in browsers:
        html.append(f'<th class="ab-th">{br}</th>')
    html.append("</tr>")
    html.append("</thead>")

    # Тело таблицы
    html.append("<tbody>")
    if not props:
        html.append('<tr><td class="ab-td ab-left">No failures — all checks are ok</td>')
        for _ in browsers:
            html.append(f'<td class="ab-td {GREEN}">{CHECK}</td>')
        html.append("</tr>")
    else:
        for prop in props:
            html.append(f'<tr><td class="ab-td ab-left"><code>{prop}</code></td>')
            for br in browsers:
                cell_txt, css = _classify_cell(br, matrix[br], prop)
                html.append(f'<td class="ab-td {css}">{cell_txt}</td>')
            html.append("</tr>")

    html.append("</tbody></table>")
    return "\n".join(html)


class AntibotTableDirective(Directive):
    """
    .. antibot-table:: path/to/browser_antibot_sannysoft.json
       :title: Sannysoft Anti-bot Matrix
    """
    required_arguments = 1
    optional_arguments = 0
    final_argument_whitespace = True
    option_spec = {
        "title": directives.unchanged,
    }

    def run(self):
        env = self.state.document.settings.env
        srcdir = Path(env.app.srcdir)
        json_arg = self.arguments[0]
        json_path = (srcdir / json_arg).resolve()

        data = _read_json(str(json_path))
        title = self.options.get("title")

        html = _build_html_table(data, title)
        raw = nodes.raw("", html, format="html")
        return [raw]


def setup(app):
    app.add_directive("antibot-table", AntibotTableDirective)
    return {
        "version": "1.0",
        "parallel_read_safe": True,
        "parallel_write_safe": True,
    }
