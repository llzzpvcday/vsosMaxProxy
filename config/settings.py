"""Configuration management for the proxy system."""

from dataclasses import dataclass
from typing import Optional
import os
from pathlib import Path

from dotenv import load_dotenv


# Load .env file from project root
# This is called at module import time to ensure env vars are available
env_path = Path(__file__).parent.parent / '.env'
load_dotenv(dotenv_path=env_path, override=False)


@dataclass
class ClientConfig:
    """Configuration for MaxSocket client."""
    
    phone: str
    work_dir: str = "cache"
    app_version: str = "25.12.13"
    device_type: str = "DESKTOP"
    
    def validate(self) -> None:
        """Validate client configuration parameters."""
        if not self.phone:
            raise ValueError("Client phone number is required")
        if not self.phone.startswith('+'):
            raise ValueError("Phone number must start with '+' (e.g., +1234567890)")
        if not self.work_dir:
            raise ValueError("Work directory is required")


@dataclass
class ServerConfig:
    """Configuration for server component."""
    
    phone: str
    work_dir: str = "cache"
    app_version: str = "25.12.13"
    device_type: str = "DESKTOP"
    
    def validate(self) -> None:
        """Validate server configuration parameters."""
        if not self.phone:
            raise ValueError("Server phone number is required")
        if not self.phone.startswith('+'):
            raise ValueError("Phone number must start with '+' (e.g., +1234567890)")
        if not self.work_dir:
            raise ValueError("Work directory is required")


@dataclass
class ProxyConfig:
    """Configuration for SOCKS5 proxy."""
    
    host: str = "0.0.0.0"
    port: int = 1080
    username: str = "username"
    password: str = "password"
    
    def validate(self) -> None:
        """Validate proxy configuration parameters."""
        if not self.host:
            raise ValueError("Proxy host is required")
        if not isinstance(self.port, int) or self.port < 1 or self.port > 65535:
            raise ValueError("Proxy port must be between 1 and 65535")
        if not self.username:
            raise ValueError("Proxy username is required")
        if not self.password:
            raise ValueError("Proxy password is required")


@dataclass
class ProtocolConfig:
    """
    Configuration for protocol parameters.
    
    Note: Protocol constants (max_message_limit, message_delay, header_format)
    are now defined in protocol/constants.py as they are protocol-level
    constraints, not user configuration.
    
    This class is kept for future protocol-level configuration if needed.
    """
    
    def validate(self) -> None:
        """Validate protocol configuration parameters."""
        pass  # No user-configurable protocol parameters currently


class Config:
    """Main configuration loader and manager."""
    
    def __init__(self):
        """Initialize configuration manager."""
        self.protocol = ProtocolConfig()
        self.client: Optional[ClientConfig] = None
        self.server: Optional[ServerConfig] = None
        self.proxy: Optional[ProxyConfig] = None
    
    def load_client_config(self) -> ClientConfig:
        """
        Load client configuration from environment variables.
        
        Environment variables:
            CLIENT_PHONE: Phone number for client (required)
            CLIENT_WORK_DIR: Working directory (default: cache)
            CLIENT_APP_VERSION: App version (default: 25.12.13)
            CLIENT_DEVICE_TYPE: Device type (default: DESKTOP)
        
        Returns:
            Validated ClientConfig instance
            
        Raises:
            ValueError: If required configuration is missing or invalid
        """
        phone = os.getenv('CLIENT_PHONE')
        if not phone:
            raise ValueError(
                "CLIENT_PHONE environment variable is required. "
                "Example: CLIENT_PHONE=+1234567890"
            )
        
        config = ClientConfig(
            phone=phone,
            work_dir=os.getenv('CLIENT_WORK_DIR', 'cache'),
            app_version=os.getenv('CLIENT_APP_VERSION', '25.12.13'),
            device_type=os.getenv('CLIENT_DEVICE_TYPE', 'DESKTOP'),
        )
        config.validate()
        self.client = config
        return config
    
    def load_server_config(self) -> ServerConfig:
        """
        Load server configuration from environment variables.
        
        Environment variables:
            SERVER_PHONE: Phone number for server (required)
            SERVER_WORK_DIR: Working directory (default: cache)
            SERVER_APP_VERSION: App version (default: 25.12.13)
            SERVER_DEVICE_TYPE: Device type (default: DESKTOP)
        
        Returns:
            Validated ServerConfig instance
            
        Raises:
            ValueError: If required configuration is missing or invalid
        """
        phone = os.getenv('SERVER_PHONE')
        if not phone:
            raise ValueError(
                "SERVER_PHONE environment variable is required. "
                "Example: SERVER_PHONE=+1234567890"
            )
        
        config = ServerConfig(
            phone=phone,
            work_dir=os.getenv('SERVER_WORK_DIR', 'cache'),
            app_version=os.getenv('SERVER_APP_VERSION', '25.12.13'),
            device_type=os.getenv('SERVER_DEVICE_TYPE', 'DESKTOP'),
        )
        config.validate()
        self.server = config
        return config
    
    def load_proxy_config(self) -> ProxyConfig:
        """
        Load proxy configuration from environment variables.
        
        Environment variables:
            PROXY_HOST: Proxy bind host (default: 0.0.0.0)
            PROXY_PORT: Proxy bind port (default: 1080)
            PROXY_USERNAME: SOCKS5 authentication username (default: username)
            PROXY_PASSWORD: SOCKS5 authentication password (default: password)
        
        Returns:
            Validated ProxyConfig instance
            
        Raises:
            ValueError: If configuration is invalid
        """
        port_str = os.getenv('PROXY_PORT', '1080')
        try:
            port = int(port_str)
        except ValueError:
            raise ValueError(f"PROXY_PORT must be a valid integer, got: {port_str}")
        
        config = ProxyConfig(
            host=os.getenv('PROXY_HOST', '0.0.0.0'),
            port=port,
            username=os.getenv('PROXY_USERNAME', 'username'),
            password=os.getenv('PROXY_PASSWORD', 'password'),
        )
        config.validate()
        self.proxy = config
        return config
    
    def load_protocol_config(self) -> ProtocolConfig:
        """
        Load protocol configuration.
        
        Protocol constants are now defined in protocol/constants.py
        as they are protocol-level constraints, not user configuration.
        
        Returns:
            Validated ProtocolConfig instance
        """
        config = ProtocolConfig()
        config.validate()
        self.protocol = config
        return config
