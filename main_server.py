#!/usr/bin/env python3
"""
Main entry point for the server application.

This script initializes and runs the server that handles remote connections
and message routing through the messenger.
"""

import asyncio
import signal
import sys
import os

from config.settings import Config
from server.server import Server
from utils.logging import setup_logging, get_logger
from utils.exceptions import ConfigurationError

logger = get_logger(__name__)


class ServerApplication:
    """Main application class for server."""
    
    def __init__(self):
        """Initialize application."""
        self.config = Config()
        self.server: Server = None
        self.shutdown_event = asyncio.Event()
    
    async def run(self) -> None:
        """
        Run the server application.
        
        Loads configuration, initializes and starts the server,
        and handles graceful shutdown.
        """
        try:
            # Load configuration
            logger.info("Loading configuration...")
            server_config = self.config.load_server_config()
            protocol_config = self.config.load_protocol_config()
            
            logger.info(
                f"Configuration loaded: "
                f"server_phone={server_config.phone}, "
                f"work_dir={server_config.work_dir}"
            )
            
            # Create and start server
            logger.info("Initializing server...")
            self.server = Server(server_config, protocol_config)
            
            # Start server in background task
            server_task = asyncio.create_task(self.server.start())
            
            logger.info("Server application started successfully")
            logger.info("Press Ctrl+C to stop")
            
            # Wait for shutdown signal
            await self.shutdown_event.wait()
            
            logger.info("Shutdown signal received, stopping...")
            
            # Stop server
            if self.server:
                await self.server.stop()
            
            # Cancel server task if still running
            if not server_task.done():
                server_task.cancel()
                try:
                    await server_task
                except asyncio.CancelledError:
                    pass
            
            logger.info("Server application stopped successfully")
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
    
    logger.info("Starting server application...")
    
    # Create application
    app = ServerApplication()
    
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
