#file_commands.py
#Checks if the command is valid and returns the command and arguments as a list

import os
import threading

STORAGE = 'server_storage'
BUFFER_SIZE = 4096  # size for short text messages
CHUNK_SIZE = 65536  # size for file data, 64 KB at a time
os.makedirs(STORAGE, exist_ok=True) #creates the storage directory if it doesn't exist

# files being uploaded/downloaded right now, delete is blocked for these
in_use = set()
in_use_lock = threading.Lock()  # threads share in_use, so a lock protects it

def claim(path):
    '''marks a file as in use, returns False if another client already has it'''
    with in_use_lock:
        if path in in_use:
            return False
        in_use.add(path)
        return True

def release(path):
    '''marks a file as no longer in use'''
    with in_use_lock:
        in_use.discard(path)

def safe_path(name):
    '''converts a filename to a safe path within the STORAGE directory'''
    full = os.path.abspath(os.path.join(STORAGE, name)) #path to file/folder
    root = os.path.abspath(STORAGE)
    # root + os.sep stops sibling folders like server_storage_evil from slipping through
    if full == root or not full.startswith(root + os.sep):
        raise ValueError(f"Unsafe path: {name}")
    return full

def receive_file(connection, file_path, size):
    '''receives exactly size bytes from the client and writes them to file_path'''
    remaining = size
    with open(file_path, 'wb') as f:
        while remaining > 0:
            # never ask for more than what is left, so we don't read into the next message
            chunk = connection.recv(min(CHUNK_SIZE, remaining))
            if not chunk:
                return False  # client disconnected mid-upload
            f.write(chunk)
            remaining -= len(chunk)
    return True

def do_upload(message, connection):
    '''upload <name> <size>: asks before overwriting, then receives the file'''
    parts = message.split()
    if len(parts) < 3 or not parts[-1].isdigit():  # client adds the size as the last word
        return 'Usage: upload <filename>'
    name = ' '.join(parts[1:-1])
    size = int(parts[-1])
    try:
        path = safe_path(name)
    except ValueError:
        return 'That file name is not allowed.'
    if not os.path.isdir(os.path.dirname(path)):
        return 'That folder does not exist on the server.'
    if os.path.isdir(path):
        return f'{name} is a folder, not a file.'
    if not claim(path):
        return f'{name} is being used right now. Try again in a moment.'
    temp = path + '.part'
    try:
        if os.path.exists(path):
            connection.send(b'EXISTS')  # client asks the user yes/no and sends the answer back
            answer = connection.recv(BUFFER_SIZE).decode('utf-8').strip().lower()
            if answer not in ('yes', 'y'):
                return 'Upload cancelled.'
        connection.send(b'READY')  # tells the client to start sending the file bytes
        # save to a temp file first so a failed upload never destroys the old file
        if not receive_file(connection, temp, size):
            return 'Upload failed: connection lost.'
        os.replace(temp, path)  # swaps the finished file into place, overwriting the old one
        return f'{name} uploaded.'
    finally:
        if os.path.exists(temp):
            os.remove(temp)  # cleans up half-finished uploads
        release(path)

def do_download(message, connection):
    '''download <name>: sends the size, waits for GO, then sends the file'''
    parts = message.split()
    if len(parts) < 2:
        return 'Usage: download <filename>'
    name = ' '.join(parts[1:])
    try:
        path = safe_path(name)
    except ValueError:
        return 'That file name is not allowed.'
    if not os.path.isfile(path):
        return f'{name} does not exist on the server.'
    if not claim(path):
        return f'{name} is being used right now. Try again in a moment.'
    try:
        connection.send(f'OK {os.path.getsize(path)}'.encode('utf-8'))  # client needs the size
        answer = connection.recv(BUFFER_SIZE).decode('utf-8').strip()
        if answer != 'GO':
            return 'Download cancelled.'
        with open(path, 'rb') as f:
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                connection.sendall(chunk)
        return f'{name} downloaded.'
    finally:
        release(path)

def do_delete(message):
    '''delete <name>: removes a file unless it is missing or in use'''
    parts = message.split()
    if len(parts) < 2:
        return 'Usage: delete <filename>'
    name = ' '.join(parts[1:])
    try:
        path = safe_path(name)
    except ValueError:
        return 'That file name is not allowed.'
    if not os.path.isfile(path):
        return f'{name} does not exist.'
    if not claim(path):
        return f'{name} is being used right now, so it was not deleted.'
    try:
        os.remove(path)
        return f'{name} deleted.'
    finally:
        release(path)

def handle_command(message, connection): #function to check what client's input and return the reply text
    '''checks what client's input and returns the reply text'''
    parts = message.strip().split()
    if not parts:
        return "No command received"
    command = parts[0].lower() #first word set to command, rest are args

    if command == 'help':
            return ('Commands: connect, upload <file>, download <file>, delete <file>, '
            'dir, subfolder create <name>, subfolder delete <name>, quit')

    if command == 'upload':
        return do_upload(message, connection)

    if command == 'download':
        return do_download(message, connection)

    if command == 'delete':
        return do_delete(message)
    
    if command == 'dir':
        items = []
        for root, dirs, files in os.walk(STORAGE):
            for d in dirs:
                items.append(os.path.relpath(os.path.join(root, d), STORAGE) + '/')
            for f in files:
                if not f.endswith('.part'):  # hide unfinished uploads
                    items.append(os.path.relpath(os.path.join(root, f), STORAGE))
        return '\n'.join(sorted(items)) if items else 'The server has no files yet.'

    if command == 'subfolder': #function to create or delete subfolders in the server storage directory
        if len(parts) != 3 or parts[1].lower() not in ('create', 'delete'):
            return 'Usage: subfolder create <name>  or  subfolder delete <name>'
        action, name = parts[1].lower(), parts[2]
        try:
            path = safe_path(name)
        except ValueError:
            return 'That folder name is not allowed.'
        try:
            if action == 'create':
                os.makedirs(path)
                return f'Folder {name} created.'
            os.rmdir(path)
            return f'Folder {name} deleted.'
        except FileExistsError:
            return f'Folder {name} already exists.'
        except FileNotFoundError:
            return f'Folder {name} does not exist.'
        except OSError:
            return f'Folder {name} is not empty, so it was not deleted.'

    return f'Unknown command "{parts[0]}". Type help to see the commands.'