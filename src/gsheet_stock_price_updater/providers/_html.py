"""Shared HTML/XPath extraction for the scrape-based providers."""

from __future__ import annotations

from lxml import html as lxml_html

from . import ProviderError


def extract_text(html_text: str, xpath: str) -> str:
    """Return the first non-empty text matched by `xpath` in `html_text`.

    Accepts XPaths that select text nodes (`.../text()`) or elements (whose
    text content is used). Raises `ProviderError` if the XPath is invalid or
    matches nothing usable, so the row is treated as a per-row failure.
    """

    try:
        tree = lxml_html.fromstring(html_text)
    except (ValueError, lxml_html.etree.ParserError) as exc:
        raise ProviderError(f"could not parse HTML: {exc}") from exc

    try:
        matches = tree.xpath(xpath)
    except lxml_html.etree.XPathEvalError as exc:
        raise ProviderError(f"invalid XPath {xpath!r}: {exc}") from exc

    if not isinstance(matches, list):
        matches = [matches]

    for match in matches:
        if isinstance(match, str):
            # Covers text() nodes and string()/@attr results (str subclasses).
            text = match.strip()
        elif isinstance(match, bool | int | float):
            # count()/number()/boolean() yield a scalar. Refuse it: a price must
            # come from text to stay exact, never a coerced binary float.
            raise ProviderError(
                f"XPath {xpath!r} returned a scalar; select a text node or element instead."
            )
        else:
            text = match.text_content().strip()
        if text:
            return text

    raise ProviderError(f"XPath {xpath!r} matched no usable text.")
