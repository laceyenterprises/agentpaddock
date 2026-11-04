"""A small, strict ``multipart/form-data`` reader (RFC 7578).

The standard library lost its form parser with ``cgi``; the bundle upload only
needs named parts with optional file names, so this reads exactly that and
refuses anything ambiguous. Part bodies are kept as raw bytes: answer files
are hashed byte for byte.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from email.message import Message
from email.parser import HeaderParser

_BOUNDARY = re.compile(r"[0-9A-Za-z'()+_,\-./:=? ]{1,70}")


class MultipartError(ValueError):
    """The request body is not well-formed multipart form data."""


class TooManyParts(MultipartError):
    """The body holds more parts than the configured limit."""


@dataclass(frozen=True)
class Part:
    name: str
    filename: str | None
    data: bytes


def _boundary(content_type: str) -> bytes:
    message = Message()
    message["content-type"] = content_type
    if message.get_content_type() != "multipart/form-data":
        raise MultipartError("not multipart/form-data")
    boundary = message.get_param("boundary")
    if (
        not isinstance(boundary, str)
        or not _BOUNDARY.fullmatch(boundary)
        or boundary.endswith(" ")
    ):
        raise MultipartError("missing or invalid boundary")
    return boundary.encode("ascii")


def _headers(raw: bytes) -> Message:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise MultipartError("part headers are not UTF-8") from None
    return HeaderParser().parsestr(text + "\r\n\r\n")


def parse(body: bytes, content_type: str, *, max_parts: int) -> list[Part]:
    """The parts of ``body`` in order; raises :class:`MultipartError`."""
    delimiter = b"\r\n--" + _boundary(content_type)
    pieces = (b"\r\n" + body).split(delimiter)
    # pieces[0] is the preamble, which RFC 2046 says to ignore.
    if len(pieces) < 2:
        raise MultipartError("no parts")
    if not pieces[-1].startswith(b"--"):
        raise MultipartError("missing closing delimiter")
    parts: list[Part] = []
    for piece in pieces[1:-1]:
        if piece.startswith(b"--"):
            raise MultipartError("data after the closing delimiter")
        # Transport padding (spaces/tabs) may follow a delimiter before CRLF.
        piece = piece.lstrip(b" \t")
        if not piece.startswith(b"\r\n"):
            raise MultipartError("malformed delimiter line")
        head, separator, data = piece[2:].partition(b"\r\n\r\n")
        if not separator:
            raise MultipartError("part without a header terminator")
        headers = _headers(head)
        disposition = headers.get("content-disposition")
        if disposition is None or headers.get_content_disposition() != "form-data":
            raise MultipartError("part is not form-data")
        name = headers.get_param("name", header="content-disposition")
        if not isinstance(name, str) or not name:
            raise MultipartError("part without a name")
        filename = headers.get_filename()
        parts.append(Part(name=name, filename=filename, data=data))
        if len(parts) > max_parts:
            raise TooManyParts("too many parts")
    return parts
