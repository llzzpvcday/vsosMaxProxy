"""Refactored server module for handling remote connections."""

from typing import Dict, Optional, Set
import asyncio
import socket
from time import time
from math import ceil

from pymax import SocketMaxClient
from pymax.payloads import UserAgentPayload
from pymax.filters import Filters
from pymax.types import Message as PyMaxMessage

from protocol.commands import ProtocolCommand
from protocol.messages import Message, MessageHeader
from protocol.encoding import encode_message, decode_message
from protocol.constants import (
    HEADER_FORMAT,
    HEADER_SIZE,
    MAX_MESSAGE_LIMIT,
    MESSAGE_DELAY,
)
from config.settings import ServerConfig, ProtocolConfig
from utils.logging import get_logger

logger = get_logger(__name__)


class Server:
    """Server class for handling remote connections and message routing."""
    
    def __init__(self, config: ServerConfig, protocol_config: ProtocolConfig):
        """
        Initialize server with configuration.
        
        Uses efficient data structures for optimal performance.
        
        Args:
            config: Server configuration
            protocol_config: Protocol configuration
        """
        self._config: ServerConfig = config
        self._protocol_config: ProtocolConfig = protocol_config
        self._client: Optional[SocketMaxClient] = None
        # Use dict for O(1) connection lookups (Req 9.2)
        self._connections: Dict[int, socket.socket] = {}
        self._send_queue: asyncio.Queue = asyncio.Queue(maxsize=8)
        self._sync_time: float = 0
        # Use set for O(1) task tracking (Req 9.2)
        self._tasks: Set[asyncio.Task] = set()
        # Pre-calculate constants for performance (Req 9.3)
        self._max_recv_size: int = MAX_MESSAGE_LIMIT - HEADER_SIZE
        
        logger.info(
            f"Server initialized with phone {config.phone}, "
            f"work_dir={config.work_dir}"
        )

    async def start(self) -> None:
        """
        Start the server and initialize messenger client.
        
        Initializes the messenger client, sets up message handlers,
        and starts the sender loop.
        """
        logger.info("Starting server...")
        
        try:
            # Initialize messenger client
            ua = UserAgentPayload(
                device_type=self._config.device_type,
                app_version=self._config.app_version
            )
            
            self._client = SocketMaxClient(
                phone=self._config.phone,
                work_dir=self._config.work_dir,
                headers=ua,
            )
            
            # Register message handler
            @self._client.on_message(Filters.chat(0))
            async def on_message(msg: PyMaxMessage) -> None:
                await self._handle_incoming_message(msg)
            
            # Register start handler
            @self._client.on_start
            async def on_start() -> None:
                logger.info(f"Client started. ID: {self._client.me.id}")
            
            # Start sender loop task
            sender_task = asyncio.create_task(self._sender_loop())
            self._tasks.add(sender_task)
            sender_task.add_done_callback(self._tasks.discard)
            
            # Start the client
            await self._client.start()
            
            logger.info("Server started successfully")
            
        except Exception as e:
            logger.error(f"Failed to start server: {e}", exc_info=True)
            await self.stop()
            raise
    
    async def stop(self) -> None:
        """
        Stop the server and cleanup all resources.
        
        Closes all connections, cancels all tasks, and performs
        graceful shutdown.
        """
        logger.info("Stopping server...")
        
        try:
            # Cancel all running tasks
            for task in self._tasks:
                if not task.done():
                    task.cancel()
            
            # Wait for tasks to complete
            if self._tasks:
                await asyncio.gather(*self._tasks, return_exceptions=True)
                self._tasks.clear()
            
            # Close all connections
            for sock_id, sock in list(self._connections.items()):
                try:
                    sock.close()
                    logger.debug(f"Closed connection {sock_id}")
                except Exception as e:
                    logger.warning(f"Error closing connection {sock_id}: {e}")
            
            self._connections.clear()
            
            # Stop the client
            if self._client:
                try:
                    await self._client.stop()
                except Exception as e:
                    logger.warning(f"Error stopping client: {e}")
            
            logger.info("Server stopped successfully")
            
        except Exception as e:
            logger.error(f"Error during server shutdown: {e}", exc_info=True)
        finally:
            self._client = None

    async def _handle_incoming_message(self, msg: PyMaxMessage) -> None:
        """
        Handle incoming messages from messenger client.
        
        Optimized for fast message routing and minimal allocations.
        
        Args:
            msg: Incoming message from messenger
        """
        try:
            # Decode message (optimized encoding/decoding)
            data = decode_message(msg.text)
            logger.debug(f"Received message from {msg.sender}, size: {len(data)} bytes")
            
            # Parse message (single allocation)
            message = Message.parse(data, HEADER_FORMAT, HEADER_SIZE)
            
            sock_id = message.header.sock_id
            command = message.header.command
            payload = message.payload
            
            # Fast path routing - most common commands first (Req 9.3)
            if command == ProtocolCommand.DATA:
                await self._handle_data_command(sock_id, payload)
            elif command == ProtocolCommand.CONNECT:
                await self._handle_connect_command(sock_id, payload)
            elif command == ProtocolCommand.CLOSE:
                await self._handle_close_command(sock_id)
            elif command == ProtocolCommand.SYNC:
                self._sync_time = msg.time
                logger.info(f"Synchronized time: {self._sync_time}")
            else:
                logger.warning(f"Unknown command: {command}")
                
        except Exception as e:
            logger.error(f"Error handling incoming message: {e}", exc_info=True)
    
    async def _handle_connect_command(self, sock_id: int, payload: bytes) -> None:
        """
        Handle connection establishment command.
        
        Creates a real socket connection to the target address and starts
        polling for data.
        
        Args:
            sock_id: Socket identifier
            payload: Connection parameters (IP address + port)
        """
        try:
            # Parse target address and port
            ip = socket.inet_ntoa(payload[:4])
            port = int.from_bytes(payload[4:6], byteorder='big', signed=False)
            
            logger.info(f"Socket {sock_id}: Connecting to {ip}:{port}")
            
            # Create non-blocking socket
            remote = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            remote.setblocking(False)
            
            loop = asyncio.get_event_loop()
            
            try:
                # Attempt connection
                await loop.sock_connect(remote, (ip, port))
                
                # Get local bind address
                addr, bind_port = remote.getsockname()
                
                # Close existing connection if any
                existing_conn = self._connections.get(sock_id)
                if existing_conn:
                    logger.warning(f"Socket {sock_id}: Closing existing connection")
                    existing_conn.close()
                
                # Store new connection
                self._connections[sock_id] = remote
                
                # Send success response
                response_payload = socket.inet_aton(addr) + bind_port.to_bytes(2, byteorder='big', signed=False)
                await self._send(sock_id, ProtocolCommand.CONNECT, response_payload)
                
                # Start polling task
                polling_task = asyncio.create_task(self._remote_polling(sock_id, remote))
                self._tasks.add(polling_task)
                polling_task.add_done_callback(self._tasks.discard)
                
                logger.info(f"Socket {sock_id}: Connected successfully to {ip}:{port}")
                
            except OSError as e:
                logger.error(f"Socket {sock_id}: Connection failed to {ip}:{port}: {e}")
                remote.close()
                await self._send(sock_id, ProtocolCommand.CONNECTION_FAILED, b'')
                
        except Exception as e:
            logger.error(f"Socket {sock_id}: Error in connect command: {e}", exc_info=True)
            await self._send(sock_id, ProtocolCommand.CONNECTION_FAILED, b'')
    
    async def _handle_data_command(self, sock_id: int, payload: bytes) -> None:
        """
        Handle data transmission command.
        
        Forwards data to the real socket connection.
        
        Args:
            sock_id: Socket identifier
            payload: Data to send
        """
        sock = self._connections.get(sock_id)
        
        if not sock:
            logger.warning(f"Socket {sock_id}: No active connection for data command")
            await self._send(sock_id, ProtocolCommand.CONNECTION_CLOSED, b'')
            return
        
        try:
            loop = asyncio.get_event_loop()
            await loop.sock_sendall(sock, payload)
            logger.debug(f"Socket {sock_id}: Sent {len(payload)} bytes to remote")
            
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError) as e:
            logger.warning(f"Socket {sock_id}: Connection error during send: {e}")
            await self._send(sock_id, ProtocolCommand.CONNECTION_CLOSED, b'')
            sock.close()
            self._connections.pop(sock_id, None)
    
    async def _handle_close_command(self, sock_id: int) -> None:
        """
        Handle connection close command.
        
        Closes the real socket and cleans up resources.
        
        Args:
            sock_id: Socket identifier
        """
        sock = self._connections.get(sock_id)
        
        if sock:
            try:
                sock.close()
                self._connections.pop(sock_id, None)
                logger.info(f"Socket {sock_id}: Connection closed by client")
            except Exception as e:
                logger.warning(f"Socket {sock_id}: Error closing connection: {e}")
        else:
            logger.debug(f"Socket {sock_id}: No connection to close")
    
    async def _remote_polling(self, sock_id: int, sock: socket.socket) -> None:
        """
        Poll remote socket for incoming data.
        
        Continuously reads data from the real socket and forwards it
        to the client through the messenger. Uses event-driven approach
        instead of polling with sleep for better performance.
        
        Args:
            sock_id: Socket identifier
            sock: Real socket to poll
        """
        loop = asyncio.get_event_loop()
        max_recv_size = MAX_MESSAGE_LIMIT - HEADER_SIZE
        
        logger.debug(f"Socket {sock_id}: Started polling")
        
        try:
            while True:
                # Event-driven approach: wait for data to be available
                # This eliminates unnecessary sleep calls in hot paths (Req 9.1)
                try:
                    data = await loop.sock_recv(sock, max_recv_size)
                    
                    if len(data) > 0:
                        await self._send(sock_id, ProtocolCommand.DATA, data)
                        logger.debug(f"Socket {sock_id}: Forwarded {len(data)} bytes to client")
                    else:
                        # Remote closed connection
                        logger.info(f"Socket {sock_id}: Remote closed connection")
                        await self._send(sock_id, ProtocolCommand.CONNECTION_CLOSED, b'')
                        break
                        
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError) as e:
                    logger.warning(f"Socket {sock_id}: Connection error during polling: {e}")
                    await self._send(sock_id, ProtocolCommand.CONNECTION_CLOSED, b'')
                    break
                    
        except asyncio.CancelledError:
            logger.debug(f"Socket {sock_id}: Polling cancelled")
            raise
        except Exception as e:
            logger.error(f"Socket {sock_id}: Unexpected error in polling: {e}", exc_info=True)
        finally:
            # Cleanup
            try:
                sock.close()
                self._connections.pop(sock_id, None)
                logger.debug(f"Socket {sock_id}: Polling stopped, connection cleaned up")
            except Exception as e:
                logger.warning(f"Socket {sock_id}: Error during polling cleanup: {e}")

    async def _send(self, sock_id: int, command: ProtocolCommand, payload: bytes) -> None:
        """
        Queue a message for sending.
        
        Args:
            sock_id: Socket identifier
            command: Protocol command
            payload: Message payload
            
        Raises:
            RuntimeError: If payload is too large
        """
        if len(payload) + HEADER_SIZE > MAX_MESSAGE_LIMIT:
            raise RuntimeError(
                f"Payload size {len(payload)} + header {HEADER_SIZE} "
                f"exceeds limit {MAX_MESSAGE_LIMIT}"
            )
        
        await self._send_queue.put((sock_id, command, payload))
        logger.debug(f"Queued message: sock_id={sock_id}, command={command}, size={len(payload)}")
    
    async def _sender_loop(self) -> None:
        """
        Process send queue with proper timing.
        
        Implements odd time slot scheduling to avoid rate limiting
        and ensure proper message timing coordination with client.
        Optimized to minimize memory allocations and overhead.
        """
        logger.info("Sender loop started")
        
        try:
            while True:
                # Get next message from queue
                sock_id, cmd, payload = await self._send_queue.get()
                
                # Calculate next odd time slot
                current_time = time()
                elapsed = current_time - self._sync_time
                slot_number = elapsed / MESSAGE_DELAY
                next_odd_slot = ceil(slot_number)
                
                # Ensure odd slot using bitwise operation (Req 9.3)
                if not (next_odd_slot & 1):  # Check if even using bitwise AND
                    next_odd_slot += 1
                
                # Calculate sleep time
                next_send_time = self._sync_time + next_odd_slot * MESSAGE_DELAY
                sleep_time = next_send_time - current_time
                
                # Only sleep if necessary (Req 9.1)
                logger.debug(
                    f"Queue size: {self._send_queue.qsize()}, "
                    f"sleep time: {sleep_time:.3f}s"
                )
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)
                
                # Construct and encode message (minimize allocations - Req 9.3)
                header = MessageHeader(sock_id=sock_id, command=cmd)
                message = Message(header=header, payload=payload)
                serialized = message.serialize(HEADER_FORMAT)
                encoded = encode_message(serialized)
                
                # Send through messenger
                if self._client:
                    await self._client.send_message(encoded, 0)
                    logger.debug(
                        f"Sent message: sock_id={sock_id}, command={cmd}, "
                        f"size={len(payload)} bytes"
                    )
                else:
                    logger.warning("Client not initialized, cannot send message")
                    
        except asyncio.CancelledError:
            logger.info("Sender loop cancelled")
            raise
        except Exception as e:
            logger.error(f"Error in sender loop: {e}", exc_info=True)
            raise
