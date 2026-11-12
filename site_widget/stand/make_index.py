"""Собирает страницу стенда из настоящей разметки виджета и вендорных скриптов.

Разметка берётся из blade-шаблона заказчика: комментарии blade выкидываются,
остальное — как на сайте. Скрипты повторяют порядок `header.blade.php`; без
`js.cookie` собранный бандл падает на `Cookies is not defined`.
"""
import os
import pathlib
import re

SITE = pathlib.Path(os.environ["MBK_SITE_ROOT"])
VENDOR = ("jquery-3.3.1.min.js", "jquery-ui.min.js", "sweetalert.min.js",
          "js.cookie.min.js", "jquery.suggestions.min.js", "swiper-bundle.min.js",
          "svgxuse.min.js", "jquery.mCustomScrollbar.min.js", "app.js")
STYLES = ("suggestions.min.css", "swiper-bundle.min.css", "app.css")

blade = (SITE / "www/resources/views/ai-chat-widget.blade.php").read_text(encoding="utf-8")
markup = re.sub(r"\{\{--.*?--\}\}", "", blade, flags=re.S)
head = "\n".join(f'<link rel="stylesheet" href="/app/css/{name}">' for name in STYLES)
scripts = "\n".join(f'<script src="/app/js/{name}"></script>' for name in VENDOR)
page = f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Стенд виджета: собранный app.js с сайта</title>
{head}
<style>body{{margin:0;font:16px/1.5 Arial,sans-serif;background:#f4f4f4}}
.note{{padding:24px;color:#555}}</style></head>
<body>
<div class="note"><b>Локальный стенд.</b> Разметка и собранные файлы — с master
mbk-site, бот — локальный. Правка видна так, как её увидит клиент.</div>
{markup}
{scripts}
</body></html>
"""
pathlib.Path(__file__).with_name("index.html").write_text(page, encoding="utf-8")
print("index.html собран")
