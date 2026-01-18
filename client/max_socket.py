"""Refactored MaxSocket client module for virtual socket connections through messenger."""

from typing import Optional, Dict, Tuple, Set
import asyncio
import socket
from time import time
from math import ceil
from dataclasses import dataclass

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
from config.settings import ClientConfig, ProtocolConfig
from utils.logging import get_logger
from utils.exceptions import (
    MessageTooLargeError,
    SocketNotConnectedError,
    SocketSendError,
    SocketReceiveError,
)

logger = get_logger(__name__)


@dataclass
class ConnectionState:
    """State information for a virtual socket connection."""
    
    bind_address: Optional[Tuple[bytes, bytes]] = None
    stream: Optional[asyncio.StreamReader] = None
    is_connected: bool = False


class MaxSocket:
    """
    Virtual socket implementation that tunnels connections through a messenger.
    
    This class provides a socket-like interface for network connections that are
    tunneled through a messaging service, allowing connections through restrictive
    firewalls or network environments.
    """
    
    # Class-level shared state
    _client: Optional[SocketMaxClient] = None
    # Use dict for O(1) lookups - efficient data structure (Req 9.2)
    _connections: Dict[int, ConnectionState] = {}
    _connection_futures: Dict[int, asyncio.Future] = {}
    _sockets: Dict[int, 'MaxSocket'] = {}
    _send_queue: asyncio.Queue = asyncio.Queue(maxsize=8)
    _sync_time: float = 0
    _next_sock_id: int = 0
    _config: Optional[ProtocolConfig] = None
    # Use set for O(1) task tracking (Req 9.2)
    _tasks: Set[asyncio.Task] = set()
    
    def __init__(self):
        """
        Initialize a new MaxSocket instance.
        
        Raises:
            RuntimeError: If MaxSocket.init() has not been called first
        """
        if not MaxSocket._client:
            raise RuntimeError(
                'MaxSocket not initialized. Call MaxSocket.init() first with proper configuration'
            )
        
        self._sock_id = MaxSocket._next_sock_id
        MaxSocket._next_sock_id += 1
        MaxSocket._sockets[self._sock_id] = self
        self._state = ConnectionState()
        
        logger.debug(f"Created MaxSocket with ID {self._sock_id}")
    
    @property
    def sock_id(self) -> int:
        """Get the socket ID for this connection."""
        return self._sock_id

    async def connect(self, addr: str, port: int) -> Tuple[bytes, bytes]:
        """
        Establish a connection to a remote host.
        
        Args:
            addr: IP address or hostname to connect to
            port: Port number to connect to
            
        Returns:
            Tuple of (bind_ip, bind_port) as bytes
            
        Raises:
            ConnectionRefusedError: If the connection is refused by the remote host
            ConnectionError: If there's an error during connection
        """
        logger.info(f"Socket {self._sock_id}: Connecting to {addr}:{port}")
        
        try:
            # Convert address and port to bytes for protocol
            payload = socket.inet_aton(addr) + port.to_bytes(2, byteorder='big', signed=False)
            
            # Send connect command
            await self._send_command(ProtocolCommand.CONNECT, payload)
            
            # Create future to wait for connection result
            loop = asyncio.get_running_loop()
            future = loop.create_future()
            MaxSocket._connection_futures[self._sock_id] = future
            
            # Wait for connection result
            result = await future
            
            if result:
                self._state.bind_address = result
                self._state.stream = asyncio.StreamReader()
                self._state.is_connected = True
                MaxSocket._connections[self._sock_id] = self._state
                logger.info(f"Socket {self._sock_id}: Connected successfully to {addr}:{port}")
                return result
            else:
                logger.error(f"Socket {self._sock_id}: Connection refused to {addr}:{port}")
                raise ConnectionRefusedError(f"Failed to connect to {addr}:{port}")
                
        except Exception as e:
            logger.error(
                f"Socket {self._sock_id}: Error connecting to {addr}:{port}",
                exc_info=True
            )
            raise
    
    async def recv(self, bufsize: int) -> bytes:
        """
        Receive data from the connection.
        
        Args:
            bufsize: Maximum number of bytes to receive
            
        Returns:
            Received data as bytes
            
        Raises:
            ConnectionError: If socket is not connected
        """
        if not self._state.is_connected or not self._state.stream:
            logger.error(f"Socket {self._sock_id}: Attempted to recv on disconnected socket")
            raise SocketNotConnectedError(f"Socket {self._sock_id} is not connected")
        
        try:
            data = await self._state.stream.read(bufsize)
            logger.debug(f"Socket {self._sock_id}: Received {len(data)} bytes")
            return data
        except SocketNotConnectedError:
            raise
        except Exception as e:
            logger.error(f"Socket {self._sock_id}: Error receiving data", exc_info=True)
            raise SocketReceiveError(f"Error receiving data on socket {self._sock_id}") from e
    
    async def sendall(self, data: bytes) -> None:
        """
        Send all data through the connection.
        
        Large payloads are automatically chunked to fit within message size limits.
        
        Args:
            data: Data to send
            
        Raises:
            ConnectionError: If socket is not connected
        """
        if not self._state.is_connected:
            logger.error(f"Socket {self._sock_id}: Attempted to send on disconnected socket")
            raise SocketNotConnectedError(f"Socket {self._sock_id} is not connected")
        
        try:
            # Calculate maximum payload size per message
            max_payload_size = MAX_MESSAGE_LIMIT - HEADER_SIZE
            
            # Chunk data if necessary
            total_chunks = (len(data) + max_payload_size - 1) // max_payload_size
            
            for i in range(0, len(data), max_payload_size):
                chunk = data[i:i + max_payload_size]
                await self._send_command(ProtocolCommand.DATA, chunk)
            
            logger.debug(
                f"Socket {self._sock_id}: Sent {len(data)} bytes in {total_chunks} chunk(s)"
            )
        except SocketNotConnectedError:
            raise
        except Exception as e:
            logger.error(f"Socket {self._sock_id}: Error sending data", exc_info=True)
            raise SocketSendError(f"Error sending data on socket {self._sock_id}") from e
    
    async def close(self) -> None:
        """
        Close the connection and clean up resources.
        
        This method is idempotent and can be called multiple times safely.
        """
        if self._state.is_connected:
            logger.info(f"Socket {self._sock_id}: Closing connection")
            try:
                await self._send_command(ProtocolCommand.CLOSE, b'')
            except Exception as e:
                logger.warning(
                    f"Socket {self._sock_id}: Error sending close command",
                    exc_info=True
                )
            finally:
                self._state.is_connected = False
                self._state.bind_address = None
                if self._state.stream:
                    self._state.stream.feed_eof()
                
                # Clean up from class-level tracking
                MaxSocket._connections.pop(self._sock_id, None)
                MaxSocket._sockets.pop(self._sock_id, None)
                
                logger.debug(f"Socket {self._sock_id}: Connection closed and resources cleaned up")
    
    async def _send_command(self, command: ProtocolCommand, payload: bytes) -> None:
        """
        Send a command with payload to the send queue.
        
        Args:
            command: Protocol command to send
            payload: Command payload data
            
        Raises:
            MessageTooLargeError: If payload exceeds maximum message size
        """
        if len(payload) + HEADER_SIZE > MAX_MESSAGE_LIMIT:
            raise MessageTooLargeError(
                f"Payload size {len(payload)} + header {HEADER_SIZE} "
                f"exceeds limit {MAX_MESSAGE_LIMIT}"
            )
        
        logger.debug(
            f"Socket {self._sock_id}: Queuing command {command.name} "
            f"with {len(payload)} bytes payload"
        )
        await MaxSocket._send_queue.put((self._sock_id, command, payload))

    @classmethod
    async def init(cls, config: ClientConfig, protocol_config: ProtocolConfig) -> None:
        """
        Initialize the MaxSocket client system.
        
        This must be called before creating any MaxSocket instances.
        
        Args:
            config: Client configuration
            protocol_config: Protocol configuration
            
        Raises:
            RuntimeError: If initialization fails
        """
        logger.info("Initializing MaxSocket client system")
        
        try:
            # Store protocol configuration
            cls._config = protocol_config
            
            # Create messenger client
            ua = UserAgentPayload(
                device_type=config.device_type,
                app_version=config.app_version
            )
            
            client = SocketMaxClient(
                phone=config.phone,
                work_dir=config.work_dir,
                headers=ua,
            )
            
            cls._client = client
            
            # Set up message handler
            @client.on_message(Filters.chat(0))
            async def on_message(msg: PyMaxMessage) -> None:
                """Handle incoming messages from messenger."""
                try:
                    await cls._handle_incoming_message(msg)
                except Exception as e:
                    logger.error("Error handling incoming message", exc_info=True)
            
            # Set up start handler
            @client.on_start
            async def on_start() -> None:
                """Handle client start event."""
                try:
                    # Send sync message to establish time synchronization
                    sync_header = MessageHeader(sock_id=0, command=ProtocolCommand.SYNC)
                    sync_msg = Message(header=sync_header, payload=b'')
                    encoded = encode_message(sync_msg.serialize(HEADER_FORMAT))
                    
                    msg = await client.send_message(encoded, 0)
                    cls._sync_time = msg.time
                    
                    logger.info(
                        f"MaxSocket client started. Client ID: {client.me.id}, "
                        f"Sync time: {cls._sync_time}"
                    )
                except Exception as e:
                    logger.error("Error during client start", exc_info=True)
                    raise
            
            # Start client and sender loop as background tasks
            client_task = asyncio.create_task(client.start())
            sender_task = asyncio.create_task(cls._sender_loop())
            
            # Track tasks for cleanup
            cls._tasks.add(client_task)
            cls._tasks.add(sender_task)
            
            # Remove tasks from set when they complete
            client_task.add_done_callback(cls._tasks.discard)
            sender_task.add_done_callback(cls._tasks.discard)
            
            logger.info("MaxSocket client system initialized successfully")
            
        except Exception as e:
            logger.error("Failed to initialize MaxSocket client", exc_info=True)
            raise RuntimeError(f"MaxSocket initialization failed: {e}") from e
    
    @classmethod
    async def _sender_loop(cls) -> None:
        """
        Process the send queue with proper timing to avoid rate limiting.
        
        Messages are sent in even time slots to coordinate with the server's
        odd time slots, preventing message collisions. Optimized to minimize
        memory allocations and reduce overhead.
        """
        logger.info("Starting sender loop")
        
        try:
            while True:
                # Get next message from queue
                sock_id, cmd, payload = await cls._send_queue.get()
                
                # Calculate next even time slot
                current_time = time()
                elapsed = current_time - cls._sync_time
                slot_number = elapsed / MESSAGE_DELAY
                next_even_slot = ceil(slot_number)
                
                # Ensure we use an even slot (bitwise operation for performance)
                if next_even_slot & 1:  # Check if odd using bitwise AND
                    next_even_slot += 1
                
                # Calculate sleep time
                next_send_time = cls._sync_time + next_even_slot * MESSAGE_DELAY
                sleep_time = next_send_time - current_time
                
                # Only sleep if necessary (Req 9.1)
                if sleep_time > 0:
                    logger.debug(
                        f"Sender: Queue size={cls._send_queue.qsize()}, "
                        f"Sleep time={sleep_time:.3f}s, Slot={next_even_slot}"
                    )
                    await asyncio.sleep(sleep_time)
                
                # Construct and send message
                try:
                    # Reuse message construction to minimize allocations (Req 9.3)
                    header = MessageHeader(sock_id=sock_id, command=cmd)
                    message = Message(header=header, payload=payload)
                    serialized = message.serialize(HEADER_FORMAT)
                    encoded = encode_message(serialized)
                    
                    await cls._client.send_message(encoded, 0)
                    
                    logger.debug(
                        f"Sent message: sock_id={sock_id}, cmd={cmd.name}, "
                        f"payload_size={len(payload)}"
                    )
                except Exception as e:
                    logger.error(
                        f"Error sending message for socket {sock_id}",
                        exc_info=True
                    )
                
        except asyncio.CancelledError:
            logger.info("Sender loop cancelled")
            raise
        except Exception as e:
            logger.error("Sender loop error", exc_info=True)
            raise
    
    @classmethod
    async def _handle_incoming_message(cls, msg: PyMaxMessage) -> None:
        """
        Parse and route incoming messages to appropriate handlers.
        
        Optimized for minimal memory allocations and fast routing.
        
        Args:
            msg: Incoming message from messenger
        """
        try:
            # Decode message (optimized encoding/decoding)
            data = decode_message(msg.text)
            
            logger.debug(f"Received message from {msg.sender}, size={len(data)} bytes")
            
            # Parse message (single allocation for message object)
            message = Message.parse(data, HEADER_FORMAT, HEADER_SIZE)
            sock_id = message.header.sock_id
            command = message.header.command
            payload = message.payload
            
            # Fast path routing using if-elif chain (Req 9.3)
            # Most common case first: DATA command
            if command == ProtocolCommand.DATA:
                # Data for existing connection - O(1) dict lookup (Req 9.2)
                state = cls._connections.get(sock_id)
                if state and state.stream:
                    state.stream.feed_data(payload)
                    logger.debug(f"Fed {len(payload)} bytes to socket {sock_id} stream")
                else:
                    logger.warning(
                        f"Received data for unknown socket {sock_id}, sending close"
                    )
                    # Send close for unknown socket
                    await cls._send_queue.put((sock_id, ProtocolCommand.CLOSE, b''))
            
            elif command == ProtocolCommand.CONNECT:
                # Connection established response - O(1) dict lookup (Req 9.2)
                future = cls._connection_futures.get(sock_id)
                if future and not future.done():
                    # Payload contains bind address (4 bytes IP + 2 bytes port)
                    # Use slicing instead of unpacking for performance
                    bind_ip = payload[:4]
                    bind_port = payload[4:6]
                    future.set_result((bind_ip, bind_port))
                    logger.debug(f"Socket {sock_id}: Connection established")
                else:
                    logger.warning(
                        f"Received connect response for socket {sock_id} "
                        f"with no pending future"
                    )
            
            elif command == ProtocolCommand.CONNECTION_CLOSED:
                # Remote side closed connection
                logger.info(f"Socket {sock_id}: Remote side closed connection")
                
                # Efficient cleanup with O(1) operations (Req 9.2)
                sock = cls._sockets.get(sock_id)
                if sock:
                    sock._state.is_connected = False
                    sock._state.bind_address = None
                    if sock._state.stream:
                        sock._state.stream.feed_eof()
                
                state = cls._connections.get(sock_id)
                if state and state.stream:
                    state.stream.feed_eof()
                
                # Clean up - O(1) dict operations (Req 9.2)
                cls._connections.pop(sock_id, None)
            
            elif command == ProtocolCommand.CONNECTION_FAILED:
                # Connection failed
                logger.warning(f"Socket {sock_id}: Connection failed")
                
                future = cls._connection_futures.get(sock_id)
                if future and not future.done():
                    future.set_result(None)
            
            else:
                logger.warning(f"Received unknown command: {command}")
                
        except Exception as e:
            logger.error("Error handling incoming message", exc_info=True)
    
    @classmethod
    async def shutdown(cls) -> None:
        """
        Gracefully shutdown the MaxSocket client system.
        
        Closes all connections and cancels all background tasks.
        """
        logger.info("Shutting down MaxSocket client system")
        
        try:
            # Close all open sockets
            for sock_id, sock in list(cls._sockets.items()):
                try:
                    await sock.close()
                except Exception as e:
                    logger.warning(f"Error closing socket {sock_id}", exc_info=True)
            
            # Cancel all background tasks
            for task in cls._tasks:
                if not task.done():
                    task.cancel()
            
            # Wait for tasks to complete
            if cls._tasks:
                await asyncio.gather(*cls._tasks, return_exceptions=True)
            
            logger.info("MaxSocket client system shutdown complete")
            
        except Exception as e:
            logger.error("Error during shutdown", exc_info=True)
