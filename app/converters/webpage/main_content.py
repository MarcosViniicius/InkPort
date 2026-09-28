"""Main content selection (port of ``get_main_content.ts``)."""

from __future__ import annotations

from lxml import etree


def get_main_content(doc: etree._Element) -> etree._Element:
    main_articles = doc.xpath("//main//article")
    if len(main_articles) == 1:
        return main_articles[0]

    main = doc.xpath('//main | //*[@role="main"]')
    if main:
        return main[0]

    articles = doc.xpath("//article")
    if articles:
        return articles[0]

    body = doc.find("body")
    return body if body is not None else doc
