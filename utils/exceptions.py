"""Custom exception classes for the proxy system."""


class ProxyError(Exception):
    """Base exception class for all proxy-related errors."""
    pass


class SocketNotConnectedError(ProxyError):
    """Exception raised when attempting to use a disconnected socket."""
    pass


class SocketSendError(ProxyError):
    """Exception raised when sending data through socket fails."""
    pass


class SocketReceiveError(ProxyError):
    """Exception raised when receiving data from socket fails."""
    pass


class MessageTooLargeError(ProxyError):
    """Exception raised when a message exceeds the maximum size limit."""
    pass


class ConfigurationError(ProxyError):
    """Exception raised when configuration is invalid or missing."""
    pass
