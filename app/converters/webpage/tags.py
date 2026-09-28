"""HTML element/attribute tables.

Mirrors the constants of the reference implementation
(https://github.com/webpagetoepub/html2epub, MIT) so the cleaning and
replacement rules match it.
"""

from __future__ import annotations

# --- clean_document/remove_elements.ts ------------------------------------
EMBEDDED_ELEMENTS_TO_REMOVE = [
    "map", "area", "embed", "object", "video", "audio", "iframe", "canvas",
    "applet", "frameset", "track", "portal", "source", "frame", "param", "shadow",
]
FORM_ELEMENTS_TO_REMOVE = [
    "button", "input", "textarea", "select", "output", "datalist", "keygen",
    "optgroup", "option",
]
METADATA_SCRIPT_ELEMENTS_TO_REMOVE = [
    "base", "script", "meta", "link", "style", "template", "slot", "basefont", "font",
]
INTERACTIVE_ELEMENTS_TO_REMOVE = ["menu", "command", "nav", "menuitem"]
NOT_SUPPORTED_ELEMENTS_TO_REMOVE = ["math", "svg", "col", "colgroup", "dialog"]

ELEMENTS_TO_REMOVE = [
    *EMBEDDED_ELEMENTS_TO_REMOVE,
    *FORM_ELEMENTS_TO_REMOVE,
    *METADATA_SCRIPT_ELEMENTS_TO_REMOVE,
    *INTERACTIVE_ELEMENTS_TO_REMOVE,
    *NOT_SUPPORTED_ELEMENTS_TO_REMOVE,
]

# --- clean_document/remove_attributes.ts ----------------------------------
STYLE_ATTRIBUTES_TO_REMOVE = ["style", "class"]

EVENTS_ATTRIBUTES_TO_REMOVE = [
    "onafterprint", "onbeforeprint", "onbeforeunload", "onerror", "onhashchange",
    "onload", "onmessage", "onoffline", "ononline", "onpagehide", "onpageshow",
    "onpopstate", "onresize", "onstorage", "onunload",
    "onblur", "onchange", "oncontextmenu", "onfocus", "oninput", "oninvalid",
    "onreset", "onsearch", "onselect", "onsubmit",
    "onkeydown", "onkeypress", "onkeyup",
    "onclick", "ondblclick", "onmousedown", "onmousemove", "onmouseout",
    "onmouseover", "onmouseup", "onmousewheel", "onwheel",
    "ondrag", "ondragend", "ondragenter", "ondragleave", "ondragover",
    "ondragstart", "ondrop", "onscroll",
    "oncopy", "oncut", "onpaste",
    "ontoggle",
]

INTERATIVE_ATTRIBUTES_TO_REMOVE = [
    "contextmenu", "draggable", "tabindex", "for", "autocomplete", "capture",
    "contenteditable", "crossorigin", "dirname", "enterkeyhint", "form",
    "formaction", "formenctype", "formmethod", "formnovalidate", "formtarget",
    "inputmode", "list", "maxlength", "minlength", "max", "min", "novalidate",
    "pattern", "readonly", "required", "spellcheck", "step", "usemap", "autofocus",
]

STYLE_BREAK = ["background", "bgcolor", "border", "width", "heigth"]

FORM_SUBMISSION = ["accept", "accept-charset", "action", "enctype", "method"]

USELESS = [
    "loading", "ping", "slot", "sizes", "decoding", "crossorigin",
    "elementtiming", "fetchpriority", "referrerpolicy",
]

OFFLINE = ["srcset", "attributionsrc"]

ATTRIBUTES_TO_REMOVE = [
    *STYLE_ATTRIBUTES_TO_REMOVE,
    *STYLE_BREAK,
    *EVENTS_ATTRIBUTES_TO_REMOVE,
    *INTERATIVE_ATTRIBUTES_TO_REMOVE,
    *USELESS,
    *FORM_SUBMISSION,
    *OFFLINE,
]

# --- clean_document/remove_empty_elements.ts ------------------------------
TAGS_CAN_REMOVE_WHEN_EMPTY = {
    "span", "abbr", "cite", "em", "i", "b", "sub", "sup", "small", "strong",
    "mark", "del", "s", "code", "p", "ol", "ul", "li", "div", "pre", "blockquote",
    "label", "aside", "address", "h1", "h2", "h3", "h4", "h5", "h6", "main",
    "section", "header", "article", "footer", "summary", "details", "table",
    "caption", "thead", "tbody", "tfoot",
}

# --- replace_elements/replace_unknown_elements.ts -------------------------
HTML5_TAGS = {
    "address", "article", "aside", "footer", "header", "h1", "h2", "h3", "h4",
    "h5", "h6", "hgroup", "main", "section",
    "blockquote", "dd", "div", "dl", "dt", "figcaption", "figure", "hr", "li",
    "ol", "p", "pre", "ul",
    "a", "abbr", "b", "bdi", "bdo", "br", "cite", "code", "data", "dfn", "em",
    "i", "kbd", "mark", "q", "rp", "rt", "ruby", "s", "samp", "small", "span",
    "strong", "sub", "sup", "time", "u", "var", "wbr",
    "img",
    "picture", "portal",
    "noscript",
    "del", "ins",
    "caption", "table", "tbody", "td", "tfoot", "th", "thead", "tr",
    "fieldset", "form", "label", "legend", "meter", "progress",
    "details", "summary",
}

# --- replace_elements/replace_simple_elements_tag.ts ----------------------
SIMPLE_TAGS_TO_REPLACE = {
    "div": [
        "article", "aside", "details", "figure", "fieldset", "footer", "form",
        "header", "hgroup", "main", "noframes", "noscript", "picture", "search",
        "section",
    ],
    "ul": ["dir"],
    "span": ["bdi", "data", "label", "rt", "ruby", "time", "rp", "wbr"],
    "p": ["figcaption", "summary", "legend"],
    "del": ["s", "strike"],
}

# --- replace_elements/replace_elements_by_others_with_css.ts --------------
ELEMENTS_WITH_CSS = {
    "mark": {"tag": "span", "properties": {"background-color": "#ff0"}},
    "u": {
        "tag": "span",
        "properties": {
            "text-decoration-color": "red",
            "text-decoration-style": "wavy",
            "text-decoration-line": "underline",
            "text-decoration": "red wavy underline",
        },
    },
    "center": {"tag": "div", "properties": {"text-align": "center"}},
    "table": {"tag": "table", "properties": {"border-collapse": "collapse"}},
    "th": {"tag": "th", "properties": {"border": "1px solid black"}},
    "td": {"tag": "td", "properties": {"border": "1px solid black"}},
}

HEADING_MAP = {"h1": "h2", "h2": "h3", "h3": "h4", "h4": "h5", "h5": "h6"}

# --- get_metadata.ts ------------------------------------------------------
DATE_METATAGS = [
    "article:published_time", "article:modified_time", "book:release_date",
    "og:article:published_time", "og:article:modified_time", "og:book:release_date",
]
PUBLISHER_METATAGS = ["publisher", "owner", "copyright", "og:site_name"]
DESCRIPTION_METATAGS = ["description", "og:description", "subtitle", "abstract"]
TAGS_METATAGS = ["news_keywords", "keywords"]

#: Chapters are grouped into page files under this size (create_epub.ts).
MAX_PAGE_CONTENT_BYTES = 200 * 1024
#: Minimum leftover text to become its own chapter (split_main_content.ts).
REMAINING_TEXT_LIMIT = 80
