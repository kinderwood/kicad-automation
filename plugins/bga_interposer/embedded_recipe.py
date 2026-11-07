"""KiCad 10 embedded-file encoding for BGA Interposer recipes."""

import base64
import ctypes
import ctypes.util
import hashlib
import json


_ZSTD = None


def _zstd_library():
    """Return the Zstandard library already shipped with or used by KiCad."""
    global _ZSTD
    if _ZSTD is not None:
        return _ZSTD

    candidates = [
        None,
        ctypes.util.find_library("zstd"),
        "libzstd.so.1",
        "libzstd.so",
        "libzstd.1.dylib",
        "libzstd.dylib",
        "libzstd.dll",
        "zstd.dll",
    ]
    failures = []
    for candidate in candidates:
        if candidate in failures:
            continue
        try:
            library = ctypes.CDLL(candidate)
        except (OSError, TypeError):
            failures.append(candidate)
            continue
        if not all(
            hasattr(library, name)
            for name in (
                "ZSTD_compressBound",
                "ZSTD_compress",
                "ZSTD_decompress",
                "ZSTD_getFrameContentSize",
                "ZSTD_isError",
                "ZSTD_getErrorName",
            )
        ):
            failures.append(candidate)
            continue

        library.ZSTD_compressBound.argtypes = [ctypes.c_size_t]
        library.ZSTD_compressBound.restype = ctypes.c_size_t
        library.ZSTD_compress.argtypes = [
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_int,
        ]
        library.ZSTD_compress.restype = ctypes.c_size_t
        library.ZSTD_decompress.argtypes = [
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_void_p,
            ctypes.c_size_t,
        ]
        library.ZSTD_decompress.restype = ctypes.c_size_t
        library.ZSTD_getFrameContentSize.argtypes = [
            ctypes.c_void_p,
            ctypes.c_size_t,
        ]
        library.ZSTD_getFrameContentSize.restype = ctypes.c_ulonglong
        library.ZSTD_isError.argtypes = [ctypes.c_size_t]
        library.ZSTD_isError.restype = ctypes.c_uint
        library.ZSTD_getErrorName.argtypes = [ctypes.c_size_t]
        library.ZSTD_getErrorName.restype = ctypes.c_char_p
        _ZSTD = library
        return _ZSTD

    raise RuntimeError("KiCad's Zstandard library is unavailable")


def _zstd_error(library, result):
    if library.ZSTD_isError(result):
        message = library.ZSTD_getErrorName(result)
        raise RuntimeError(
            "Zstandard operation failed: {}".format(
                message.decode("utf-8", "replace") if message else "unknown error"
            )
        )


def zstd_compress(data):
    """Compress bytes using the same Zstandard level as KiCad 10."""
    library = _zstd_library()
    source = ctypes.create_string_buffer(data)
    capacity = library.ZSTD_compressBound(len(data))
    destination = ctypes.create_string_buffer(capacity)
    size = library.ZSTD_compress(
        destination, capacity, source, len(data), 15
    )
    _zstd_error(library, size)
    return destination.raw[:size]


def zstd_decompress(data):
    """Decompress a KiCad embedded-file payload for validation and reuse."""
    library = _zstd_library()
    source = ctypes.create_string_buffer(data)
    size = library.ZSTD_getFrameContentSize(source, len(data))
    if size in (2**64 - 1, 2**64 - 2):
        raise RuntimeError("embedded recipe has no usable decompressed size")
    destination = ctypes.create_string_buffer(size)
    actual_size = library.ZSTD_decompress(
        destination, size, source, len(data)
    )
    _zstd_error(library, actual_size)
    return destination.raw[:actual_size]


def compact_json_bytes(value):
    """Return deterministic UTF-8 JSON suitable for embedding."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def embedded_file_sexpr(filename, content):
    """Return a KiCad 10 embedded-files s-expression for one other file."""
    compressed = zstd_compress(content)
    encoded = base64.b64encode(compressed).decode("ascii")
    checksum = hashlib.sha256(content).hexdigest()
    return (
        "(embedded_files (file (name {name}) (type other) "
        "(data |{data}|) (checksum {checksum})))"
    ).format(
        name=json.dumps(filename),
        data=encoded,
        checksum=json.dumps(checksum),
    )
