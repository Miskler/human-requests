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
    # если вдруг кто-то захочет использовать fail_counts для доп. источника
    return sorted(props)


def _classify_cell(br_data: Dict[str, Any], prop: str) -> Tuple[str, str]:
    """
    Возвращает (html_text, css_class) для ячейки конкретного браузера и свойства.
    Правила:
      1) Если нигде не падало -> ✅ (зелёный фон)
      2) Если падало только в base или только в stealth -> ❌ + '(base|stealth[, unstable])' (жёлтый фон)
      3) Если падало везде (или поднято в all.*) -> ❌ [+ 'unstable' если применимо] (красный фон)
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
        # теоретически не должно происходить (обычно переносится в all),
        # но на всякий случай считаем "падало везде"
        tag = "unstable" if (prop in b_un or prop in s_un) else ""
        return f"{CROSS} {tag}".strip(), RED

    # падало только в одном из режимов
    if base_failed:
        tag = "unstable" if prop in b_un else ""
        txt = f"{CROSS} base" + (f", {tag}" if tag else "")
        return txt, YELLOW
    else:
        tag = "unstable" if prop in s_un else ""
        txt = f"{CROSS} stealth" + (f", {tag}" if tag else "")
        return txt, YELLOW


def _build_html_table(matrix: Dict[str, Any], title: str | None) -> str:
    browsers = list(matrix.keys())
    props = _collect_all_props(matrix)
    # Если props пуст — ничего не валилось совсем; всё было зелёным. Покажем пустую таблицу с заглушкой?
    if not props:
        # посторим одно-зелёное поле «всё ок».
        props = []

    # Заголовок
    html = []
    if title:
        html.append(f'<h2 class="ab-title">{title}</h2>')

    # Легенда (маленькая)
    html.append(
        '<div class="ab-legend">'
        f'<span class="ab-cell {GREEN}">{CHECK} ok</span>'
        f'<span class="ab-cell {YELLOW}">{CROSS} partly (base/stealth[, unstable])</span>'
        f'<span class="ab-cell {RED}">{CROSS} everywhere [, unstable]</span>'
        "</div>"
    )

    # Таблица
    html.append('<table class="ab-table">')

    # Заголовок таблицы
    html.append("<thead><tr>")
    html.append('<th class="ab-th ab-left"></th>')
    for br in browsers:
        html.append(f'<th class="ab-th">{br}</th>')
    html.append("</tr></thead>")

    html.append("<tbody>")
    if not props:
        # нет падавших свойств — покажем одну строку-заглушку
        html.append('<tr><td class="ab-td ab-left">No failures — all checks are ok</td>')
        for _ in browsers:
            html.append(f'<td class="ab-td {GREEN}">{CHECK}</td>')
        html.append("</tr>")
    else:
        for prop in props:
            html.append(f'<tr><td class="ab-td ab-left"><code>{prop}</code></td>')
            for br in browsers:
                cell_txt, css = _classify_cell(matrix[br], prop)
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
