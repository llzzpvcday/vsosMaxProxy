"""Protocol command definitions."""

from enum import IntEnum


class ProtocolCommand(IntEnum):
    """Enumeration of protocol commands for client-server communication."""
    
    DATA = 0x00                 # Transmit data through connection
    CONNECT = 0x01              # Establish new connection
    CLOSE = 0x02                # Close existing connection
    SYNC = 0x03                 # Synchronize time between client and server
    CONNECTION_CLOSED = -1      # Connection closed by remote side
    CONNECTION_FAILED = -2      # Failed to establish connection
