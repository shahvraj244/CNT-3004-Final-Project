#file_commands.py
#Checks if the command is valid and returns the command and arguments as a list

import os
import threading

from protocol import err, ok, recv_msg, send_msg

STORAGE = 'server_storage'
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
        return err('Usage: upload <filename>')
    name = ' '.join(parts[1:-1])
    size = int(parts[-1])
    try:
        path = safe_path(name)
    except ValueError:
        return err('That file name is not allowed.')
    if not os.path.isdir(os.path.dirname(path)):
        return err('That folder does not exist on the server.')
    if os.path.isdir(path):
        return err(f'{name} is a folder, not a file.')
    if not claim(path):
        return err(f'{name} is being used right now. Try again in a moment.')
    temp = path + '.part'
    try:
        if os.path.exists(path):
            send_msg(connection, 'EXISTS')  # client asks the user yes/no and sends the answer back
            answer = (recv_msg(connection) or '').strip().lower()
            if answer not in ('yes', 'y'):
                return err('Upload cancelled.')
        send_msg(connection, 'READY')  # tells the client to start sending the file bytes
        # save to a temp file first so a failed upload never destroys the old file
        if not receive_file(connection, temp, size):
            return err('Upload failed: connection lost.')
        os.replace(temp, path)  # swaps the finished file into place, overwriting the old one
        return ok(f'{name} uploaded.')
    finally:
        if os.path.exists(temp):
            os.remove(temp)  # cleans up half-finished uploads
        release(path)

def do_download(message, connection):
    '''download <name>: sends the size, waits for GO, then sends the file'''
    parts = message.split()
    if len(parts) < 2:
        return err('Usage: download <filename>')
    name = ' '.join(parts[1:])
    try:
        path = safe_path(name)
    except ValueError:
        return err('That file name is not allowed.')
    if not os.path.isfile(path):
        return err(f'{name} does not exist on the server.')
    if not claim(path):
        return err(f'{name} is being used right now. Try again in a moment.')
    try:
        send_msg(connection, f'SIZE {os.path.getsize(path)}')  # client needs the size
        answer = (recv_msg(connection) or '').strip()
        if answer != 'GO':
            return err('Download cancelled.')
        with open(path, 'rb') as f:
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                connection.sendall(chunk)
        return ok(f'{name} downloaded.')
    finally:
        release(path)

def do_delete(message):
    '''delete <name>: removes a file unless it is missing or in use'''
    parts = message.split()
    if len(parts) < 2:
        return err('Usage: delete <filename>')
    name = ' '.join(parts[1:])
    try:
        path = safe_path(name)
    except ValueError:
        return err('That file name is not allowed.')
    if not os.path.isfile(path):
        return err(f'{name} does not exist.')
    if not claim(path):
        return err(f'{name} is being used right now, so it was not deleted.')
    try:
        os.remove(path)
        return ok(f'{name} deleted.')
    finally:
        release(path)

def handle_command(message, connection): #function to check what client's input and return the reply text
    '''checks what client's input and returns the reply text'''
    parts = message.strip().split()
    if not parts:
        return err('No command received')
    command = parts[0].lower() #first word set to command, rest are args

    if command == 'help':
            return ok('Commands: connect, upload <file>, download <file>, delete <file>, '
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
        return ok('\n'.join(sorted(items)) if items else 'The server has no files yet.')

    if command == 'subfolder': #function to create or delete subfolders in the server storage directory
        if len(parts) != 3 or parts[1].lower() not in ('create', 'delete'):
            return err('Usage: subfolder create <name>  or  subfolder delete <name>')
        action, name = parts[1].lower(), parts[2]
        try:
            path = safe_path(name)
        except ValueError:
            return err('That folder name is not allowed.')
        try:
            if action == 'create':
                os.makedirs(path)
                return ok(f'Folder {name} created.')
            os.rmdir(path)
            return ok(f'Folder {name} deleted.')
        except FileExistsError:
            return err(f'Folder {name} already exists.')
        except FileNotFoundError:
            return err(f'Folder {name} does not exist.')
        except OSError:
            return err(f'Folder {name} is not empty, so it was not deleted.')

    return err(f'Unknown command "{parts[0]}". Type help to see the commands.')