"""Protocol constants for message handling.

These are protocol-level constants that should not be changed
without updating both client and server implementations.
"""

import struct

# Maximum message size limit in bytes
# This is a protocol constraint, not a user configuration
MAX_MESSAGE_LIMIT = 4000

# Delay between messages in seconds
# This is required for proper time slot synchronization
MESSAGE_DELAY = 0.63

# Header format: little-endian int (sock_id) + signed byte (command)
# Format: '<' = little-endian, 'i' = int (4 bytes), 'b' = signed byte (1 byte)
HEADER_FORMAT = '<ib'

# Size of the message header in bytes
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)
