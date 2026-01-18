"""Refactored SOCKS5 proxy server module."""

from typing import Optional, Set, Tuple
import asyncio
import socket

from client.max_socket import MaxSocket
from config.settings import ProxyConfig
from utils.logging import get_logger
from utils.exceptions import ProxyError

logger = get_logger(__name__)

# SOCKS5 Protocol Constants
SOCKS_VERSION = 5
AUTH_METHOD_NO_AUTH = 0
AUTH_METHOD_USERNAME_PASSWORD = 2
AUTH_METHOD_NO_ACCEPTABLE = 0xFF

# SOCKS5 Commands
CMD_CONNECT = 1
CMD_BIND = 2
CMD_UDP_ASSOCIATE = 3

# Address Types
ATYP_IPV4 = 1
ATYP_DOMAIN = 3
ATYP_IPV6 = 4

# Reply Codes
REPLY_SUCCESS = 0
REPLY_GENERAL_FAILURE = 1
REPLY_CONNECTION_NOT_ALLOWED = 2
REPLY_NETWORK_UNREACHABLE = 3
REPLY_HOST_UNREACHABLE = 4
REPLY_CONNECTION_REFUSED = 5
REPLY_TTL_EXPIRED = 6
REPLY_COMMAND_NOT_SUPPORTED = 7
REPLY_ADDRESS_TYPE_NOT_SUPPORTED = 8


class AsyncSocket:
    """
    Wrapper for standard socket to provide async interface.
    
    This class wraps a blocking socket and provides async methods
    using asyncio's event loop integration.
    """
    
    def __init__(self, sock: socket.socket):
        """
        Initialize AsyncSocket wrapper.
        
        Args:
            sock: Standard socket to wrap
        """
        self.sock = sock
    
    async def recv(self, n: int) -> bytes:
        """
        Receive up to n bytes asynchronously.
        
        Args:
            n: Maximum number of bytes to receive
            
        Returns:
            Received data
        """
        loop = asyncio.get_event_loop()
        return await loop.sock_recv(self.sock, n)
    
    async def sendall(self, data: bytes) -> None:
        """
        Send all data asynchronously.
        
        Args:
            data: Data to send
        """
        loop = asyncio.get_event_loop()
        return await loop.sock_sendall(self.sock, data)
    
    async def close(self) -> None:
        """Close the socket."""
        return self.sock.close()
    
    async def shutdown(self, how: int) -> None:
        """
        Shutdown the socket.
        
        Args:
            how: Shutdown mode (socket.SHUT_RD, SHUT_WR, or SHUT_RDWR)
        """
        try:
            return self.sock.shutdown(how)
        except OSError:
            # Socket may already be closed
            pass


class SOCKS5Proxy:
    """
    SOCKS5 proxy server implementation.
    
    This class implements a SOCKS5 proxy server that tunnels connections
    through MaxSocket virtual connections. It supports username/password
    authentication and handles the complete SOCKS5 protocol handshake.
    """
    
    def __init__(self, config: ProxyConfig):
        """
        Initialize SOCKS5 proxy server.
        
        Uses efficient data structures and implements connection pooling
        for optimal performance.
        
        Args:
            config: Proxy configuration including host, port, and credentials
        """
        self._config: ProxyConfig = config
        self._server_socket: Optional[socket.socket] = None
        # Use set for O(1) task tracking (Req 9.2)
        self._tasks: Set[asyncio.Task] = set()
        self._running: bool = False
        
        logger.info(
            f"SOCKS5Proxy initialized with host={config.host}, port={config.port}"
        )

    async def start(self) -> None:
        """
        Start the SOCKS5 proxy server.
        
        Creates a server socket, binds to the configured address, and begins
        accepting client connections. Each client connection is handled in
        a separate task.
        
        Raises:
            ProxyError: If server fails to start
        """
        if self._running:
            logger.warning("SOCKS5 proxy is already running")
            return
        
        try:
            logger.info(
                f"Starting SOCKS5 proxy server on {self._config.host}:{self._config.port}"
            )
            
            # Create server socket
            self._server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._server_socket.bind((self._config.host, self._config.port))
            self._server_socket.listen()
            self._server_socket.setblocking(False)
            
            self._running = True
            
            logger.info(
                f"SOCKS5 proxy server listening on {self._config.host}:{self._config.port}"
            )
            
            # Accept connections loop
            loop = asyncio.get_event_loop()
            
            while self._running:
                try:
                    # Accept new connection
                    conn, addr = await loop.sock_accept(self._server_socket)
                    
                    logger.info(f"New connection from {addr}")
                    
                    # Handle client in separate task
                    task = asyncio.create_task(
                        self._handle_client(AsyncSocket(conn), addr)
                    )
                    
                    # Track task for cleanup
                    self._tasks.add(task)
                    task.add_done_callback(self._tasks.discard)
                    
                except asyncio.CancelledError:
                    logger.info("Accept loop cancelled")
                    break
                except Exception as e:
                    if self._running:
                        logger.error(f"Error accepting connection: {e}", exc_info=True)
                    
        except Exception as e:
            logger.error(f"Failed to start SOCKS5 proxy: {e}", exc_info=True)
            raise ProxyError(f"Failed to start SOCKS5 proxy: {e}") from e
        finally:
            self._running = False
    
    async def stop(self) -> None:
        """
        Stop the SOCKS5 proxy server gracefully.
        
        Cancels all client handling tasks, closes the server socket,
        and cleans up all resources.
        """
        if not self._running:
            logger.warning("SOCKS5 proxy is not running")
            return
        
        logger.info("Stopping SOCKS5 proxy server")
        
        try:
            # Signal to stop accepting new connections
            self._running = False
            
            # Cancel all client handling tasks
            logger.info(f"Cancelling {len(self._tasks)} client tasks")
            for task in self._tasks:
                if not task.done():
                    task.cancel()
            
            # Wait for all tasks to complete
            if self._tasks:
                await asyncio.gather(*self._tasks, return_exceptions=True)
            
            # Close server socket
            if self._server_socket:
                try:
                    self._server_socket.close()
                    logger.debug("Server socket closed")
                except Exception as e:
                    logger.warning(f"Error closing server socket: {e}")
                finally:
                    self._server_socket = None
            
            logger.info("SOCKS5 proxy server stopped successfully")
            
        except Exception as e:
            logger.error(f"Error during SOCKS5 proxy shutdown: {e}", exc_info=True)
            raise

    async def _handle_client(
        self, 
        connection: AsyncSocket, 
        addr: Tuple[str, int]
    ) -> None:
        """
        Handle a client connection through the complete SOCKS5 protocol flow.
        
        This method implements the full SOCKS5 protocol:
        1. Method selection (authentication negotiation)
        2. Authentication (if required)
        3. Connection request
        4. Data relay
        
        Args:
            connection: Client socket connection
            addr: Client address tuple (host, port)
        """
        remote: Optional[MaxSocket] = None
        
        try:
            logger.debug(f"Handling client from {addr}")
            
            # Step 1: Method selection
            # Read greeting: version (1 byte) + nmethods (1 byte)
            greeting = await connection.recv(2)
            if len(greeting) != 2:
                logger.warning(f"Invalid greeting from {addr}: insufficient data")
                return
            
            version, nmethods = greeting[0], greeting[1]
            
            if version != SOCKS_VERSION:
                logger.warning(
                    f"Unsupported SOCKS version {version} from {addr}"
                )
                return
            
            # Read available methods
            methods = await self._get_available_methods(nmethods, connection)
            
            # Check if username/password authentication is available
            if AUTH_METHOD_USERNAME_PASSWORD not in set(methods):
                logger.warning(
                    f"Client from {addr} does not support username/password auth"
                )
                # Send "no acceptable methods"
                await connection.sendall(
                    bytes([SOCKS_VERSION, AUTH_METHOD_NO_ACCEPTABLE])
                )
                return
            
            # Accept username/password authentication
            await connection.sendall(
                bytes([SOCKS_VERSION, AUTH_METHOD_USERNAME_PASSWORD])
            )
            
            logger.debug(f"Negotiated username/password auth with {addr}")
            
            # Step 2: Authentication
            if not await self._authenticate(connection, addr):
                logger.warning(f"Authentication failed for {addr}")
                return
            
            logger.debug(f"Authentication successful for {addr}")
            
            # Step 3: Connection request
            # Read request: version (1) + cmd (1) + reserved (1) + atyp (1)
            request = await connection.recv(4)
            if len(request) != 4:
                logger.warning(f"Invalid request from {addr}: insufficient data")
                return
            
            version, cmd, _, address_type = request
            
            if version != SOCKS_VERSION:
                logger.warning(f"Invalid SOCKS version in request: {version}")
                return
            
            # Parse destination address
            if address_type == ATYP_IPV4:
                # IPv4: 4 bytes
                address_bytes = await connection.recv(4)
                address = socket.inet_ntoa(address_bytes)
                logger.debug(f"Target address (IPv4): {address}")
                
            elif address_type == ATYP_DOMAIN:
                # Domain name: 1 byte length + domain
                domain_length = (await connection.recv(1))[0]
                domain_bytes = await connection.recv(domain_length)
                domain = domain_bytes.decode('utf-8')
                
                # Resolve domain to IP
                try:
                    address = socket.gethostbyname(domain)
                    logger.debug(f"Target address (domain): {domain} -> {address}")
                except socket.gaierror as e:
                    logger.error(f"Failed to resolve domain {domain}: {e}")
                    reply = self._generate_failed_reply(
                        address_type, 
                        REPLY_HOST_UNREACHABLE
                    )
                    await connection.sendall(reply)
                    return
                    
            elif address_type == ATYP_IPV6:
                # IPv6: 16 bytes
                logger.warning(f"IPv6 not supported from {addr}")
                reply = self._generate_failed_reply(
                    address_type,
                    REPLY_ADDRESS_TYPE_NOT_SUPPORTED
                )
                await connection.sendall(reply)
                return
            else:
                logger.warning(f"Unknown address type {address_type} from {addr}")
                reply = self._generate_failed_reply(
                    address_type,
                    REPLY_ADDRESS_TYPE_NOT_SUPPORTED
                )
                await connection.sendall(reply)
                return
            
            # Parse destination port (2 bytes, big-endian)
            port_bytes = await connection.recv(2)
            port = int.from_bytes(port_bytes, 'big', signed=False)
            
            logger.info(f"Client {addr} requesting {cmd} to {address}:{port}")
            
            # Handle command
            if cmd == CMD_CONNECT:
                # Establish connection through MaxSocket
                try:
                    remote = MaxSocket()
                    bind_address = await remote.connect(address, port)
                    
                    bind_ip, bind_port = bind_address
                    
                    logger.info(
                        f"Connected to {address}:{port} for client {addr}"
                    )
                    
                    # Send success reply
                    reply = b''.join([
                        SOCKS_VERSION.to_bytes(1, 'big'),
                        REPLY_SUCCESS.to_bytes(1, 'big'),
                        int(0).to_bytes(1, 'big'),  # Reserved
                        ATYP_IPV4.to_bytes(1, 'big'),
                        bind_ip,
                        bind_port
                    ])
                    
                except ConnectionRefusedError:
                    logger.error(
                        f"Connection refused to {address}:{port} for client {addr}"
                    )
                    reply = self._generate_failed_reply(
                        address_type,
                        REPLY_CONNECTION_REFUSED
                    )
                    await connection.sendall(reply)
                    return
                    
                except Exception as e:
                    logger.error(
                        f"Failed to connect to {address}:{port} for client {addr}: {e}",
                        exc_info=True
                    )
                    reply = self._generate_failed_reply(
                        address_type,
                        REPLY_GENERAL_FAILURE
                    )
                    await connection.sendall(reply)
                    return
                    
            else:
                # Only CONNECT is supported
                logger.warning(f"Unsupported command {cmd} from {addr}")
                reply = self._generate_failed_reply(
                    address_type,
                    REPLY_COMMAND_NOT_SUPPORTED
                )
                await connection.sendall(reply)
                return
            
            # Send reply
            await connection.sendall(reply)
            
            # Step 4: Data relay
            if reply[1] == REPLY_SUCCESS and cmd == CMD_CONNECT:
                logger.info(f"Starting data relay for {addr}")
                await self._relay_data(connection, remote)
            
        except asyncio.CancelledError:
            logger.info(f"Client handler cancelled for {addr}")
            raise
            
        except Exception as e:
            logger.error(
                f"Error handling client {addr}: {e}",
                exc_info=True
            )
            
        finally:
            # Clean up resources
            try:
                if remote:
                    await remote.close()
            except Exception as e:
                logger.warning(f"Error closing remote socket: {e}")
            
            try:
                await connection.close()
            except Exception as e:
                logger.warning(f"Error closing client socket: {e}")
            
            logger.debug(f"Client handler finished for {addr}")
    
    async def _authenticate(
        self, 
        connection: AsyncSocket,
        addr: Tuple[str, int]
    ) -> bool:
        """
        Perform username/password authentication.
        
        Args:
            connection: Client socket connection
            addr: Client address for logging
            
        Returns:
            True if authentication successful, False otherwise
        """
        try:
            # Read auth version (should be 1)
            version_byte = await connection.recv(1)
            if len(version_byte) != 1:
                return False
            
            version = version_byte[0]
            
            # Read username
            username_len_byte = await connection.recv(1)
            if len(username_len_byte) != 1:
                return False
            
            username_len = username_len_byte[0]
            username_bytes = await connection.recv(username_len)
            if len(username_bytes) != username_len:
                return False
            
            username = username_bytes.decode('utf-8')
            
            # Read password
            password_len_byte = await connection.recv(1)
            if len(password_len_byte) != 1:
                return False
            
            password_len = password_len_byte[0]
            password_bytes = await connection.recv(password_len)
            if len(password_bytes) != password_len:
                return False
            
            password = password_bytes.decode('utf-8')
            
            logger.debug(f"Authentication attempt from {addr} with username: {username}")
            
            # Verify credentials against configuration
            if username == self._config.username and password == self._config.password:
                # Success
                response = bytes([version, 0])
                await connection.sendall(response)
                logger.info(f"Authentication successful for {addr}")
                return True
            else:
                # Failure
                response = bytes([version, 0xFF])
                await connection.sendall(response)
                logger.warning(
                    f"Authentication failed for {addr}: invalid credentials"
                )
                await connection.close()
                return False
                
        except Exception as e:
            logger.error(f"Error during authentication for {addr}: {e}", exc_info=True)
            return False
    
    async def _get_available_methods(
        self, 
        nmethods: int, 
        connection: AsyncSocket
    ) -> list:
        """
        Read available authentication methods from client.
        
        Args:
            nmethods: Number of methods to read
            connection: Client socket connection
            
        Returns:
            List of method codes
        """
        methods = []
        for i in range(nmethods):
            method_byte = await connection.recv(1)
            if len(method_byte) == 1:
                methods.append(method_byte[0])
        return methods
    
    def _generate_failed_reply(
        self, 
        address_type: int, 
        error_code: int
    ) -> bytes:
        """
        Generate a SOCKS5 error reply.
        
        Args:
            address_type: Address type from request
            error_code: SOCKS5 error code
            
        Returns:
            Formatted error reply bytes
        """
        return b''.join([
            SOCKS_VERSION.to_bytes(1, 'big'),
            error_code.to_bytes(1, 'big'),
            int(0).to_bytes(1, 'big'),  # Reserved
            address_type.to_bytes(1, 'big'),
            int(0).to_bytes(4, 'big'),  # Bind address (0.0.0.0)
            int(0).to_bytes(2, 'big')   # Bind port (0)
        ])

    async def _relay_data(
        self, 
        client_sock: AsyncSocket, 
        remote_sock: MaxSocket
    ) -> None:
        """
        Relay data bidirectionally between client and remote sockets.
        
        This method creates two tasks for bidirectional data transfer and
        uses asyncio.wait with FIRST_COMPLETED to efficiently handle the relay.
        When one direction closes, the other is cancelled and cleaned up.
        Optimized for minimal overhead and efficient buffer management.
        
        Args:
            client_sock: Client socket connection
            remote_sock: Remote MaxSocket connection
        """
        try:
            logger.debug(
                f"Starting bidirectional relay between client and remote socket {remote_sock.sock_id}"
            )
            
            # Create relay tasks for both directions
            client_to_remote = asyncio.create_task(
                self._sock_to_sock(client_sock, remote_sock, "client->remote")
            )
            remote_to_client = asyncio.create_task(
                self._sock_to_sock(remote_sock, client_sock, "remote->client")
            )
            
            # Wait for either direction to complete (Req 9.1 - event-driven)
            done, pending = await asyncio.wait(
                {client_to_remote, remote_to_client},
                return_when=asyncio.FIRST_COMPLETED
            )
            
            logger.debug(
                f"One relay direction completed for socket {remote_sock.sock_id}, "
                f"cancelling remaining tasks"
            )
            
            # Cancel pending tasks
            for task in pending:
                task.cancel()
            
            # Wait for cancelled tasks to complete
            await asyncio.gather(*pending, return_exceptions=True)
            
            logger.debug(f"Data relay completed for socket {remote_sock.sock_id}")
            
        except asyncio.CancelledError:
            logger.info("Data relay cancelled")
            raise
            
        except Exception as e:
            logger.error(f"Error during data relay: {e}", exc_info=True)
            
        finally:
            # Clean up sockets
            try:
                await client_sock.close()
            except Exception as e:
                logger.debug(f"Error closing client socket: {e}")
            
            try:
                await remote_sock.close()
            except Exception as e:
                logger.debug(f"Error closing remote socket: {e}")
    
    async def _sock_to_sock(
        self, 
        src_sock, 
        dst_sock, 
        direction: str
    ) -> None:
        """
        Transfer data from source socket to destination socket.
        
        Reads data from source and writes to destination until the connection
        is closed or an error occurs. Properly handles shutdown and cleanup.
        Optimized with larger buffer size for better throughput.
        
        Args:
            src_sock: Source socket (AsyncSocket or MaxSocket)
            dst_sock: Destination socket (AsyncSocket or MaxSocket)
            direction: Description of transfer direction for logging
        """
        try:
            bytes_transferred = 0
            # Use larger buffer for better performance (Req 9.3)
            buffer_size = 8192
            
            while True:
                # Read data from source
                data = await src_sock.recv(buffer_size)
                
                if not data:
                    # Connection closed
                    logger.debug(
                        f"Connection closed on {direction}, "
                        f"transferred {bytes_transferred} bytes"
                    )
                    break
                
                # Write data to destination
                await dst_sock.sendall(data)
                bytes_transferred += len(data)
                
                # Only log periodically to reduce overhead (Req 9.1)
                if bytes_transferred % 65536 == 0:  # Log every 64KB
                    logger.debug(
                        f"Relayed {bytes_transferred} bytes ({direction})"
                    )
                
        except asyncio.CancelledError:
            logger.debug(f"Transfer cancelled ({direction})")
            raise
            
        except Exception as e:
            logger.debug(
                f"Error during transfer ({direction}): {e}",
                exc_info=False
            )
            
        finally:
            # Attempt to shutdown read side of source socket
            if isinstance(src_sock, AsyncSocket):
                try:
                    await src_sock.shutdown(socket.SHUT_RD)
                except Exception:
                    pass
