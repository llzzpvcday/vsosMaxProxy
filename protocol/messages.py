"""Message structure definitions."""

from dataclasses import dataclass
import struct


@dataclass
class MessageHeader:
    """Header for protocol messages containing socket ID and command."""
    
    sock_id: int
    command: int
    
    @classmethod
    def from_bytes(cls, data: bytes, format_str: str) -> 'MessageHeader':
        """
        Parse message header from bytes.
        
        Args:
            data: Raw bytes containing header
            format_str: Struct format string for unpacking
            
        Returns:
            Parsed MessageHeader instance
        """
        sock_id, cmd = struct.unpack(format_str, data)
        return cls(sock_id=sock_id, command=cmd)
    
    def to_bytes(self, format_str: str) -> bytes:
        """
        Serialize header to bytes.
        
        Args:
            format_str: Struct format string for packing
            
        Returns:
            Serialized header bytes
        """
        return struct.pack(format_str, self.sock_id, self.command)


@dataclass
class Message:
    """Complete protocol message with header and payload."""
    
    header: MessageHeader
    payload: bytes
    
    @classmethod
    def parse(cls, data: bytes, header_format: str, header_size: int) -> 'Message':
        """
        Parse complete message from bytes.
        
        Args:
            data: Raw bytes containing complete message
            header_format: Struct format string for header
            header_size: Size of header in bytes
            
        Returns:
            Parsed Message instance
        """
        header = MessageHeader.from_bytes(data[:header_size], header_format)
        payload = data[header_size:]
        return cls(header=header, payload=payload)
    
    def serialize(self, header_format: str) -> bytes:
        """
        Serialize complete message to bytes.
        
        Args:
            header_format: Struct format string for header
            
        Returns:
            Serialized message bytes
        """
        return self.header.to_bytes(header_format) + self.payload
