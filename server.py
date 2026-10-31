#!/usr/bin/env python3
#server.py

#Each client will be handled in a separate thread, 
#so many clients can talk to the server at the same time. 
#The server will listen for incoming connections on a specified
#port and create a new thread for each client that connects.
import socket
import threading

# 0.0.0.0 basically mean it will listen on all available interfaces.
host = '0.0.0.0'
port = 3300
BUFFER_SIZE = 4096
dashes = '----->'


#Client connection handler function. This function will be run in a 
#separate thread for each client that connects to the server.
clients = {}
clients_lock = threading.Lock()
 
 
def handle_client(connection, addr):
    """Runs independently: talks to one client until they disconnect."""
    with clients_lock:
        clients[addr] = connection
        print(f'[*] Established connection from IP {addr[0]} port: {addr[1]} '
              f'(clients connected: {len(clients)})')
    try:
        with connection:
            while True:
                # receive bytes from this client
                data = connection.recv(BUFFER_SIZE)
                # empty result means the client disconnected
                if not data:
                    break
                print('[*] Data received from {}: {}'.format(addr[1], data.decode('utf-8')))
                # echo data back to origin (later: parse commands here)
                connection.send(dashes.encode('utf-8') + data)
    except ConnectionResetError:
        print(f'[!] Client {addr[1]} dropped the connection unexpectedly')
    finally:
        with clients_lock:
            clients.pop(addr, None)
            print(f'[*] Client {addr[1]} disconnected (clients connected: {len(clients)})')
 
 
def main():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_tcp:
        # lets you restart the server right away without an "address in use" error
        server_tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_tcp.bind((host, port))
        server_tcp.listen(6)
        print(f'[*] Server listening on {host}:{port}')
        # wait for client connections forever
        while True:
            # accept() pauses here until a client connects
            connection, addr = server_tcp.accept()
            # hand the client to a new thread, then go straight back to accept()
            thread = threading.Thread(target=handle_client, args=(connection, addr), daemon=True)
            thread.start()
 
 
if __name__ == '__main__':
    main()
 