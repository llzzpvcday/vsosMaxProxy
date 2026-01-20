"""Message encoding and decoding functions."""

# Precompute translation tables for fast C-level translation
_ENCODE_TRANS = str.maketrans({i: i + 162 for i in range(256)})
_DECODE_TRANS = str.maketrans({i + 162: i for i in range(256)})


def encode_message(data: bytes) -> str:
    """
    Encode bytes to string for transmission through messenger.
    Uses latin-1 decode then C-level str.translate for speed.
    """
    return data.decode('latin-1').translate(_ENCODE_TRANS)


def decode_message(text: str) -> bytes:
    """
    Decode string from messenger back to bytes.
    Use str.translate then latin-1 encode to get original bytes.
    """
    return text.translate(_DECODE_TRANS).encode('latin-1')
