"""OPDS protocol constants (namespaces, media types, link relations)."""

from __future__ import annotations

ATOM_NS = "http://www.w3.org/2005/Atom"
OPDS_NS = "http://opds-spec.org/2010/catalog"
DC_NS = "http://purl.org/dc/terms/"
OPENSEARCH_NS = "http://a9.com/-/spec/opensearch/1.1/"
XHTML_NS = "http://www.w3.org/1999/xhtml"

NAVIGATION_TYPE = "application/atom+xml;profile=opds-catalog;kind=navigation"
ACQUISITION_TYPE = "application/atom+xml;profile=opds-catalog;kind=acquisition"
OPENSEARCH_TYPE = "application/opensearchdescription+xml"
OPDS2_TYPE = "application/opds+json"
OPDS2_PUBLICATION_TYPE = "application/opds-publication+json"

# Link relations
REL_ACQUISITION = "http://opds-spec.org/acquisition"
REL_ACQUISITION_OPEN = "http://opds-spec.org/acquisition/open-access"
REL_IMAGE = "http://opds-spec.org/image"
REL_THUMBNAIL = "http://opds-spec.org/image/thumbnail"
REL_FACET = "http://opds-spec.org/facet"
REL_SORT_NEW = "http://opds-spec.org/sort/new"
REL_SORT_POPULAR = "http://opds-spec.org/sort/popular"
REL_SUBSECTION = "subsection"
REL_SEARCH = "search"
REL_SELF = "self"
REL_START = "start"
REL_UP = "up"
REL_NEXT = "next"
REL_PREV = "previous"

# MIME types for downloads (fallback when the stored media_type is unknown)
DOWNLOAD_TYPES = {
    "epub": "application/epub+zip",
    "mobi": "application/x-mobipocket-ebook",
    "azw": "application/x-mobipocket-ebook",
    "azw3": "application/x-mobipocket-ebook",
    "pdf": "application/pdf",
    "cbz": "application/vnd.comicbook+zip",
    "cbr": "application/vnd.comicbook-rar",
    "zip": "application/zip",
    "fb2": "application/x-fictionbook+xml",
    "txt": "text/plain",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
}

#: Preferred download format per device when several exist (OPDS clients use
#: the first acquisition link they consider usable).
FORMAT_PRIORITY = [
    "epub",
    "azw3",
    "mobi",
    "kepub",
    "cbz",
    "cbr",
    "pdf",
    "fb2",
    "txt",
    "jpg",
    "png",
]
