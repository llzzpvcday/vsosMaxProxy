"""Message encoding and decoding functions."""


def encode_message(data: bytes) -> str:
    """
    Encode bytes to string for transmission through messenger.
    
    Optimized to use list comprehension and join for better performance
    by minimizing memory allocations.
    
    Args:
        data: Raw bytes to encode
        
    Returns:
        Encoded string representation
    """
    # Pre-allocate list for better performance
    return ''.join([chr(b + 162) for b in data])


def decode_message(text: str) -> bytes:
    """
    Decode string from messenger back to bytes.
    
    Optimized to use bytearray for better performance by minimizing
    memory allocations during byte construction.
    
    Args:
        text: Encoded string from messenger
        
    Returns:
        Decoded bytes
    """
    # Use bytearray for efficient byte construction
    return bytes(bytearray(ord(c) - 162 for c in text))
