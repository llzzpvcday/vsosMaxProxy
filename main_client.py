#!/usr/bin/env python3
"""
Main entry point for the client/proxy application.

This script initializes and runs the MaxSocket client and SOCKS5 proxy server.
Configuration is loaded from environment variables.
"""

import asyncio
import signal
import sys
import os

from config.settings import Config
from client.max_socket import MaxSocket
from proxy.socks5 import SOCKS5Proxy
from utils.logging import setup_logging, get_logger
from utils.exceptions import ConfigurationError

logger = get_logger(__name__)


class ClientApplication:
    """Main application class for client/proxy."""
    
    def __init__(self):
        """Initialize application."""
        self.config = Config()
        self.proxy: SOCKS5Proxy = None
        self.shutdown_event = asyncio.Event()
    
    async def run(self) -> None:
        """
        Run the client application.
        
        Loads configuration, initializes MaxSocket and SOCKS5 proxy,
        and handles graceful shutdown.
        """
        try:
            # Load configuration
            logger.info("Loading configuration...")
            client_config = self.config.load_client_config()
            proxy_config = self.config.load_proxy_config()
            protocol_config = self.config.load_protocol_config()
            
            logger.info(
                f"Configuration loaded: "
                f"client_phone={client_config.phone}, "
                f"proxy={proxy_config.host}:{proxy_config.port}"
            )
            
            # Initialize MaxSocket
            logger.info("Initializing MaxSocket client...")
            await MaxSocket.init(client_config, protocol_config)
            
            # Initialize and start SOCKS5 proxy
            logger.info("Starting SOCKS5 proxy...")
            self.proxy = SOCKS5Proxy(proxy_config)
            
            # Run proxy in background task
            proxy_task = asyncio.create_task(self.proxy.start())
            
            logger.info("Client application started successfully")
            logger.info(
                f"SOCKS5 proxy listening on {proxy_config.host}:{proxy_config.port}"
            )
            logger.info("Press Ctrl+C to stop")
            
            # Wait for shutdown signal
            await self.shutdown_event.wait()
            
            logger.info("Shutdown signal received, stopping...")
            
            # Stop proxy
            if self.proxy:
                await self.proxy.stop()
            
            # Shutdown MaxSocket
            await MaxSocket.shutdown()
            
            # Cancel proxy task if still running
            if not proxy_task.done():
                proxy_task.cancel()
                try:
                    await proxy_task
                except asyncio.CancelledError:
                    pass
            
            logger.info("Client application stopped successfully")
            logger.info("Exit")
            sys.exit(0)
            
        except ConfigurationError as e:
            logger.error(f"Configuration error: {e}")
            logger.error(
                "Please check your environment variables. "
                "See .env.example for required configuration."
            )
            sys.exit(1)
        except ValueError as e:
            logger.error(f"Configuration validation error: {e}")
            sys.exit(1)
        except KeyboardInterrupt:
            logger.info("Interrupted by user")
        except Exception as e:
            logger.error(f"Unexpected error: {e}", exc_info=True)
            sys.exit(1)
    
    def handle_shutdown(self, signum, frame):
        """
        Handle shutdown signals.
        
        Args:
            signum: Signal number
            frame: Current stack frame
        """
        logger.info(f"Received signal {signum}")
        self.shutdown_event.set()


async def main():
    """Main entry point."""
    # Get log level from environment
    log_level = os.getenv('LOG_LEVEL', 'INFO')
    
    # Setup logging
    setup_logging(log_level)
    
    logger.info("Starting client application...")
    
    # Create application
    app = ClientApplication()
    
    # Setup signal handlers for graceful shutdown
    loop = asyncio.get_event_loop()
    
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(
            sig,
            lambda s=sig: app.handle_shutdown(s, None)
        )
    
    # Run application
    await app.run()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutdown complete")
        sys.exit(0)
