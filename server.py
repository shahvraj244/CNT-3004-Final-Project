#!/usr/bin/env python3
#server.py

#Each client will be handled in a separate thread, 
#so many clients can talk to the server at the same time. 
#The server will listen for incoming connections on a specified
#port and create a new thread for each client that connects.
import socket
import threading
import time
from analysis import collector
from file_commands import handle_command
from protocol import ProtocolError, err, recv_msg, send_msg

# 0.0.0.0 basically mean it will listen on all available interfaces.
host = '0.0.0.0'
port = 3300


#Client connection handler function. This function will be run in a 
#separate thread for each client that connects to the server.
clients = {}
clients_lock = threading.Lock()
 
 
def handle_client(connection, addr):
    """Runs independently: talks to one client until they disconnect."""
    client_name = f'{addr[0]}:{addr[1]}'
    collector.record('connect', client=client_name)
    with clients_lock:
        clients[addr] = connection
        print(f'[*] Established connection from IP {addr[0]} port: {addr[1]} '
              f'(clients connected: {len(clients)})')
    try:
        with connection:
            while True:
                # wait for one complete message from this client
                message = recv_msg(connection)
                # None means the client disconnected
                if message is None:
                    break
                print('[*] Data received from {}: {}'.format(addr[1], message))
                start = time.perf_counter()
                try:
                    # connection is passed so upload/download can send and receive file bytes
                    reply = handle_command(message, connection, client=client_name)
                except (ConnectionError, ProtocolError):
                    raise  # this session can't continue, handled below
                except Exception as e:
                    print(f'[!] Error occurred while handling command: {e}')
                    reply = err('Error occurred while handling command.')
                # ok() replies start with 'OK ', err() replies with 'ERR '
                collector.record(
                    'command',
                    client=client_name,
                    duration_ms=(time.perf_counter() - start) * 1000,
                    success=reply.startswith('OK '),
                    details=message.strip().split()[0] if message.strip() else '',
                )
                send_msg(connection, reply)
    except ProtocolError as e:
        print(f'[!] Client {addr[1]} sent a bad message: {e}')
    except (ConnectionError, OSError):  # covers reset, aborted and broken-pipe disconnects
        print(f'[!] Client {addr[1]} dropped the connection unexpectedly')
    finally:
        with clients_lock:
            clients.pop(addr, None)
            print(f'[*] Client {addr[1]} disconnected (clients connected: {len(clients)})')
        collector.record('disconnect', client=client_name)
 
 
def main():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_tcp:
        #lets you restart the server right away without an address in use error
        server_tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_tcp.bind((host, port))
        server_tcp.listen(6)
        print(f'[*] Server listening on {host}:{port}')
        #wait for client connections forever
        while True:
            #accept() pauses here until a client connects
            connection, addr = server_tcp.accept()
            # hand the client to a new thread, then go straight back to accept()
            thread = threading.Thread(target=handle_client, args=(connection, addr), daemon=True)
            thread.start()
 
 
if __name__ == '__main__':
    main()